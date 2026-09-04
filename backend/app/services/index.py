"""Perceptual-hash index with exact (flat) search.

Two interchangeable backends behind one class:

* **faiss** -- ``IndexBinaryFlat`` over 64-bit hashes, exact Hamming search.
* **numpy** -- XOR + popcount over the same packed vectors, also exact.

Both are *flat*: every vector is compared, so recall is 100% and results are
identical between backends. FAISS is a speed optimisation, not an accuracy one,
which is why the fallback is acceptable for a prototype corpus of this size.
``faiss-cpu`` is not installable in this offline environment, so the numpy
backend is what actually runs here -- ``GET /api/index/status`` reports which
backend served a request rather than implying FAISS is present.

Persistence is backend-independent: packed vectors in a ``.npy`` file plus a JSON
sidecar holding the vector-position -> ``evidence_id`` mapping, the index version
and the last-updated timestamp. The FAISS backend rebuilds its in-memory flat
index from those vectors on load (a flat index is a contiguous copy, so this is
cheap) which keeps one on-disk format for both paths. The index therefore
survives restarts without a rebuild.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from app.config import Settings
from app.services.hashing import HASH_BITS
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.index")

try:  # pragma: no cover - depends on whether faiss-cpu is installed
    import faiss  # type: ignore

    FAISS_AVAILABLE = True
    FAISS_VERSION = getattr(faiss, "__version__", "unknown")
except Exception:  # noqa: BLE001
    faiss = None  # type: ignore[assignment]
    FAISS_AVAILABLE = False
    FAISS_VERSION = None

BACKEND_FAISS = "faiss-IndexBinaryFlat"
BACKEND_NUMPY = "numpy-flat-hamming"
VECTOR_BYTES = HASH_BITS // 8
INDEX_FORMAT_VERSION = 1

# popcount lookup for the numpy backend: bits set in each byte value 0..255.
_POPCOUNT = np.unpackbits(
    np.arange(256, dtype=np.uint8)[:, None], axis=1
).sum(axis=1).astype(np.uint16)


def hex_to_vector(hex_hash: str) -> np.ndarray:
    """Pack a hex hash string into a ``(VECTOR_BYTES,) uint8`` vector."""
    if not hex_hash or len(hex_hash) != HASH_BITS // 4:
        raise ValueError(
            f"Expected a {HASH_BITS // 4}-character hex hash, got {hex_hash!r}."
        )
    return np.frombuffer(bytes.fromhex(hex_hash), dtype=np.uint8)


class PerceptualIndex:
    """Flat, exact perceptual-hash index over pHash vectors."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dir = settings.index_dir
        self.vectors_path = self.dir / "phash_vectors.npy"
        self.sidecar_path = self.dir / "index_meta.json"
        self._lock = threading.RLock()
        self._ids: list[str] = []
        self._vectors = np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
        self._faiss_index: Any | None = None
        self._version = 0
        self._last_updated: str | None = None
        self._loaded = False

    # ---------------------------------------------------------------- state --
    @property
    def backend(self) -> str:
        return BACKEND_FAISS if FAISS_AVAILABLE else BACKEND_NUMPY

    @property
    def count(self) -> int:
        return len(self._ids)

    def status(self) -> dict[str, Any]:
        """Machine-readable index state for ``GET /api/index/status``."""
        with self._lock:
            self._ensure_loaded()
            notes = (
                None
                if FAISS_AVAILABLE
                else (
                    "faiss-cpu is not installed in this environment; the numpy "
                    "flat backend is in use. Both are exhaustive (exact) Hamming "
                    "search, so results are identical -- only speed differs."
                )
            )
            return {
                "indexed_count": self.count,
                "last_updated": self._last_updated,
                "index_version": self._version,
                "backend": self.backend,
                "exact_search": True,
                "hash_bits": HASH_BITS,
                "dimensions": VECTOR_BYTES,
                "persisted": self.vectors_path.is_file(),
                "index_path": str(self.dir),
                "faiss_available": FAISS_AVAILABLE,
                "faiss_version": FAISS_VERSION,
                "format_version": INDEX_FORMAT_VERSION,
                "notes": notes,
            }

    # ------------------------------------------------------------ load/save --
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def load(self) -> None:
        """Read the persisted index from disk, tolerating a missing/bad file."""
        with self._lock:
            self._loaded = True
            if not (self.vectors_path.is_file() and self.sidecar_path.is_file()):
                self._ids = []
                self._vectors = np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
                self._faiss_index = None
                return
            try:
                vectors = np.load(self.vectors_path)
                meta = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
                ids = list(meta.get("ids", []))
                if vectors.ndim != 2 or vectors.shape[1] != VECTOR_BYTES:
                    raise ValueError(f"unexpected vector shape {vectors.shape}")
                if len(ids) != vectors.shape[0]:
                    raise ValueError(
                        f"sidecar has {len(ids)} ids for {vectors.shape[0]} vectors"
                    )
            except Exception as exc:  # noqa: BLE001
                # A corrupt index must not take the service down: start empty and
                # say so. POST /api/index/rebuild restores it from the database.
                logger.error(
                    "Could not load perceptual index (%s: %s); starting empty. "
                    "Rebuild with POST /api/index/rebuild.",
                    exc.__class__.__name__,
                    exc,
                )
                self._ids = []
                self._vectors = np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
                self._faiss_index = None
                return

            self._ids = ids
            self._vectors = np.ascontiguousarray(vectors, dtype=np.uint8)
            self._version = int(meta.get("index_version", 0))
            self._last_updated = meta.get("last_updated")
            self._rebuild_faiss()
            logger.info(
                "Loaded perceptual index: %d vectors, version %d, backend %s",
                self.count,
                self._version,
                self.backend,
            )

    def save(self) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            # Write to temporary files then replace, so a crash mid-write cannot
            # leave a half-written index behind.
            tmp_vectors = self.vectors_path.with_suffix(".npy.tmp")
            tmp_sidecar = self.sidecar_path.with_suffix(".json.tmp")
            # Write through a handle: np.save would otherwise append a second
            # ".npy" to the temporary name.
            with open(tmp_vectors, "wb") as handle:
                np.save(handle, self._vectors)
            tmp_sidecar.write_text(
                json.dumps(
                    {
                        "format_version": INDEX_FORMAT_VERSION,
                        "index_version": self._version,
                        "last_updated": self._last_updated,
                        "hash_bits": HASH_BITS,
                        "vector_bytes": VECTOR_BYTES,
                        "count": len(self._ids),
                        "written_by_backend": self.backend,
                        "ids": self._ids,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp_vectors.replace(self.vectors_path)
            tmp_sidecar.replace(self.sidecar_path)

    def _rebuild_faiss(self) -> None:
        if not FAISS_AVAILABLE:
            self._faiss_index = None
            return
        index = faiss.IndexBinaryFlat(HASH_BITS)  # type: ignore[union-attr]
        if len(self._ids):
            index.add(self._vectors)
        self._faiss_index = index

    def _touch(self) -> None:
        self._version += 1
        self._last_updated = iso(utcnow())

    # -------------------------------------------------------------- mutation --
    def clear(self) -> None:
        with self._lock:
            self._ids = []
            self._vectors = np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
            self._rebuild_faiss()
            self._touch()
            self.save()

    def add(self, evidence_id: str, phash: str) -> bool:
        """Add one hash. Returns False when the id is already indexed."""
        with self._lock:
            self._ensure_loaded()
            if evidence_id in self._ids:
                return False
            vector = hex_to_vector(phash).reshape(1, VECTOR_BYTES)
            self._vectors = (
                vector.copy()
                if self.count == 0
                else np.ascontiguousarray(np.vstack([self._vectors, vector]))
            )
            self._ids.append(evidence_id)
            if self._faiss_index is not None:
                self._faiss_index.add(vector)
            self._touch()
            self.save()
            return True

    def replace_all(self, entries: list[tuple[str, str]]) -> int:
        """Rebuild from ``(evidence_id, phash)`` pairs. Returns the count added."""
        with self._lock:
            ids: list[str] = []
            vectors: list[np.ndarray] = []
            seen: set[str] = set()
            for evidence_id, phash in entries:
                if evidence_id in seen or not phash:
                    continue
                try:
                    vectors.append(hex_to_vector(phash))
                except ValueError:
                    logger.warning(
                        "Skipping %s: malformed pHash %r", evidence_id, phash
                    )
                    continue
                seen.add(evidence_id)
                ids.append(evidence_id)

            self._ids = ids
            self._vectors = (
                np.ascontiguousarray(np.vstack(vectors), dtype=np.uint8)
                if vectors
                else np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
            )
            self._rebuild_faiss()
            self._loaded = True
            self._touch()
            self.save()
            return len(ids)

    def remove(self, evidence_ids: Iterable[str]) -> int:
        """Drop specific ids from the index. Returns how many were removed.

        Needed because evidence can be deleted (a case is deleted) while the
        index is a *global* artefact shared with every other case. Leaving the
        vectors behind would make the index return candidates whose evidence rows
        no longer exist, which reads as a match the database cannot explain.

        Ids that are not present are ignored -- removal is idempotent, so a
        partially-indexed case deletes cleanly. Every surviving vector keeps its
        hash; only the removed rows are dropped, and the save is atomic.
        """
        wanted = {row for row in evidence_ids if row}
        if not wanted:
            return 0
        with self._lock:
            self._ensure_loaded()
            keep = [i for i, row in enumerate(self._ids) if row not in wanted]
            removed = self.count - len(keep)
            if removed == 0:
                return 0
            self._ids = [self._ids[i] for i in keep]
            self._vectors = (
                np.ascontiguousarray(self._vectors[keep], dtype=np.uint8)
                if keep
                else np.zeros((0, VECTOR_BYTES), dtype=np.uint8)
            )
            # A flat FAISS index has no stable id-based removal, and positions
            # shift after a delete, so it is rebuilt from the surviving vectors.
            self._rebuild_faiss()
            self._touch()
            self.save()
            return removed

    def contains(self, evidence_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return evidence_id in self._ids

    # --------------------------------------------------------------- search --
    def query(
        self,
        phash: str,
        *,
        top_k: int = 25,
        exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Exact nearest neighbours by Hamming distance, closest first.

        ``top_k`` bounds how many candidates are *returned*; the search itself is
        exhaustive, so nothing is missed by a coarse quantiser.
        """
        with self._lock:
            self._ensure_loaded()
            if self.count == 0:
                return []
            vector = hex_to_vector(phash).reshape(1, VECTOR_BYTES)
            exclude = exclude or set()
            # Ask for more than top_k so excluded ids cannot squeeze out real hits.
            want = min(self.count, max(top_k + len(exclude), top_k))

            if self._faiss_index is not None:  # pragma: no cover - faiss absent here
                distances, positions = self._faiss_index.search(vector, want)
                pairs = [
                    (int(pos), int(dist))
                    for pos, dist in zip(positions[0], distances[0], strict=False)
                    if pos >= 0
                ]
            else:
                xor = np.bitwise_xor(self._vectors, vector)
                all_distances = _POPCOUNT[xor].sum(axis=1)
                order = np.argsort(all_distances, kind="stable")[:want]
                pairs = [(int(pos), int(all_distances[pos])) for pos in order]

            results = []
            for position, distance in pairs:
                evidence_id = self._ids[position]
                if evidence_id in exclude:
                    continue
                results.append(
                    {
                        "evidence_id": evidence_id,
                        "vector_id": position,
                        "distance": distance,
                    }
                )
                if len(results) >= top_k:
                    break
            return results


# --------------------------------------------------------------------------- #
# Process-wide singleton
# --------------------------------------------------------------------------- #
_instance: PerceptualIndex | None = None
_instance_dir: Path | None = None
_instance_lock = threading.Lock()


def get_index(settings: Settings) -> PerceptualIndex:
    """Return the shared index, rebuilt if the configured directory changed."""
    global _instance, _instance_dir
    with _instance_lock:
        if _instance is None or _instance_dir != settings.index_dir:
            _instance = PerceptualIndex(settings)
            _instance_dir = settings.index_dir
            _instance.load()
        return _instance


def reset_index_singleton() -> None:
    """Drop the cached instance (tests, and after a settings change)."""
    global _instance, _instance_dir
    with _instance_lock:
        _instance = None
        _instance_dir = None
