"""DINOv2 visual embedding index with persistent storage and cosine retrieval.

Maintains a persistent vector index of L2-normalized DINOv2 visual embeddings
for the evidence corpus. Follows the exact architectural conventions of PRAMAAN's
PerceptualIndex:

* Embeddings are stored in `<index_dir>/dinov2_embeddings.npy` (N x 384 float32).
* Metadata and vector-to-evidence_id mappings in `<index_dir>/dinov2_meta.json`.
* Atomic disk writes (.tmp replacement) protect against crash corruption.
* Cosine similarity via inner product: exhaustive (exact) linear search.
* Thread-safe with threading.RLock.
* Survives application restarts without needing recomputation.
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
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.dinov2_index")

EMBEDDING_DIM = 384
INDEX_FORMAT_VERSION = 1


class DinoV2Index:
    """Persistent vector index for DINOv2 visual embeddings."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.dir = settings.index_dir
        self.embeddings_path = self.dir / "dinov2_embeddings.npy"
        self.sidecar_path = self.dir / "dinov2_meta.json"
        self._lock = threading.RLock()
        self._ids: list[str] = []
        self._embeddings = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        self._version = 0
        self._last_updated: str | None = None
        self._loaded = False
        #: Identity of the files this in-memory copy was read from, so a change
        #: made by another process can be noticed. See ``refresh_if_stale``.
        self._stamp: tuple[int, int, int, int] | None = None

    @property
    def count(self) -> int:
        return len(self._ids)

    def status(self) -> dict[str, Any]:
        """Machine-readable index state for status reporting."""
        with self._lock:
            self._ensure_loaded()
            return {
                "indexed_count": self.count,
                "last_updated": self._last_updated,
                "index_version": self._version,
                "embedding_dim": EMBEDDING_DIM,
                "persisted": self.embeddings_path.is_file(),
                "index_path": str(self.dir),
                "format_version": INDEX_FORMAT_VERSION,
                "backend": "numpy-flat-inner-product",
            }

    # ------------------------------------------------------------ load/save --
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _disk_stamp(self) -> tuple[int, int, int, int] | None:
        """Identity of the two persisted files: ``(mtime_ns, size)`` of each.

        ``None`` means at least one file is absent -- a state in its own right
        that compares unequal to any present one.
        """
        try:
            embeddings = self.embeddings_path.stat()
            sidecar = self.sidecar_path.stat()
        except OSError:
            return None
        return (
            embeddings.st_mtime_ns,
            embeddings.st_size,
            sidecar.st_mtime_ns,
            sidecar.st_size,
        )

    def _read_persisted(self) -> tuple[list[str], np.ndarray, int, str | None] | None:
        """Parse the persisted index, or ``None`` if absent/unreadable/invalid.

        Nothing here mutates the live index, so the caller decides whether a
        failure should empty it (first load) or leave the working in-memory copy
        standing (reload).
        """
        if not (self.embeddings_path.is_file() and self.sidecar_path.is_file()):
            return None
        try:
            embeddings = np.load(self.embeddings_path)
            meta = json.loads(self.sidecar_path.read_text(encoding="utf-8"))
            ids = list(meta.get("ids", []))
            if embeddings.ndim != 2 or embeddings.shape[1] != EMBEDDING_DIM:
                raise ValueError(f"Unexpected embedding shape {embeddings.shape}")
            if len(ids) != embeddings.shape[0]:
                raise ValueError(
                    f"DINOv2 sidecar has {len(ids)} ids for {embeddings.shape[0]} embeddings"
                )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Could not read DINOv2 embedding index (%s: %s).",
                exc.__class__.__name__,
                exc,
            )
            return None
        return (
            ids,
            np.ascontiguousarray(embeddings, dtype=np.float32),
            int(meta.get("index_version", 0)),
            meta.get("last_updated"),
        )

    def load(self) -> None:
        """Read the persisted embedding index from disk, tolerating missing files."""
        with self._lock:
            self._loaded = True
            # Stamp before reading: if the files change mid-read the stamp kept is
            # the older one, so the next staleness check reloads rather than
            # trusting a torn read.
            stamp = self._disk_stamp()
            parsed = self._read_persisted()
            self._stamp = stamp
            if parsed is None:
                self._ids = []
                self._embeddings = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
                return

            self._ids, self._embeddings, self._version, self._last_updated = parsed
            logger.info(
                "Loaded DINOv2 visual index: %d embeddings, version %d",
                self.count,
                self._version,
            )

    def refresh_if_stale(self) -> bool:
        """Reload if the files on disk changed since they were last read.

        Same reason as the perceptual index: ``scripts/build_index.py`` writes
        these files from another process, and a server that never re-reads them
        keeps answering visual-similarity queries from an index built before the
        corpus was ingested. Returns True if the in-memory index was replaced.
        """
        with self._lock:
            if not self._loaded:
                self.load()
                return True
            stamp = self._disk_stamp()
            if stamp == self._stamp:
                return False

            parsed = self._read_persisted()
            # Record the stamp either way, so an unreadable index is retried when
            # its bytes change again rather than on every subsequent query.
            self._stamp = stamp
            if parsed is None:
                # A failed *reload* keeps what is already in memory; only a first
                # load starts empty.
                logger.warning(
                    "DINOv2 index at %s changed but could not be read; keeping "
                    "the %d embedding(s) already in memory.",
                    self.dir,
                    self.count,
                )
                return False

            self._ids, self._embeddings, self._version, self._last_updated = parsed
            logger.info(
                "Reloaded DINOv2 visual index from disk: %d embeddings, version %d",
                self.count,
                self._version,
            )
            return True

    def save(self) -> None:
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp_embeddings = self.embeddings_path.with_suffix(".npy.tmp")
            tmp_sidecar = self.sidecar_path.with_suffix(".json.tmp")

            with open(tmp_embeddings, "wb") as handle:
                np.save(handle, self._embeddings)

            tmp_sidecar.write_text(
                json.dumps(
                    {
                        "format_version": INDEX_FORMAT_VERSION,
                        "index_version": self._version,
                        "last_updated": self._last_updated,
                        "embedding_dim": EMBEDDING_DIM,
                        "count": len(self._ids),
                        "model": getattr(self.settings, "dinov2_model_name", "facebook/dinov2-small"),
                        "ids": self._ids,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp_embeddings.replace(self.embeddings_path)
            tmp_sidecar.replace(self.sidecar_path)
            # This process is now the last writer, so adopt the stamp of what it
            # just wrote rather than re-reading it on the next staleness check.
            self._stamp = self._disk_stamp()

    def _touch(self) -> None:
        self._version += 1
        self._last_updated = iso(utcnow())

    # -------------------------------------------------------------- mutation --
    def clear(self) -> None:
        with self._lock:
            self._ids = []
            self._embeddings = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            self._touch()
            self.save()

    def add(self, evidence_id: str, embedding: np.ndarray) -> bool:
        """Add one embedding vector. Returns False when id is already indexed."""
        with self._lock:
            self._ensure_loaded()
            if evidence_id in self._ids:
                return False

            vector = np.ascontiguousarray(embedding.reshape(1, EMBEDDING_DIM), dtype=np.float32)
            # Ensure unit length
            norm = np.linalg.norm(vector)
            if norm > 0:
                vector = vector / norm

            self._embeddings = (
                vector.copy()
                if self.count == 0
                else np.ascontiguousarray(np.vstack([self._embeddings, vector]))
            )
            self._ids.append(evidence_id)
            self._touch()
            self.save()
            return True

    def replace_all(self, entries: list[tuple[str, np.ndarray]]) -> int:
        """Rebuild from (evidence_id, embedding) pairs. Returns count added."""
        with self._lock:
            ids: list[str] = []
            vectors: list[np.ndarray] = []
            seen: set[str] = set()

            for evidence_id, emb in entries:
                if evidence_id in seen or emb is None:
                    continue
                try:
                    vec = np.asarray(emb, dtype=np.float32).reshape(EMBEDDING_DIM)
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        vec = vec / norm
                    vectors.append(vec)
                except Exception:
                    logger.warning("Skipping %s: invalid embedding vector", evidence_id)
                    continue
                seen.add(evidence_id)
                ids.append(evidence_id)

            self._ids = ids
            self._embeddings = (
                np.ascontiguousarray(np.vstack(vectors), dtype=np.float32)
                if vectors
                else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            )
            self._loaded = True
            self._touch()
            self.save()
            return len(ids)

    def remove(self, evidence_ids: Iterable[str]) -> int:
        """Remove specific ids from the visual index."""
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
            self._embeddings = (
                np.ascontiguousarray(self._embeddings[keep], dtype=np.float32)
                if keep
                else np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
            )
            self._touch()
            self.save()
            return removed

    def contains(self, evidence_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            return evidence_id in self._ids

    def get_embedding(self, evidence_id: str) -> np.ndarray | None:
        """Retrieve the stored embedding vector for an evidence id."""
        with self._lock:
            self._ensure_loaded()
            if evidence_id not in self._ids:
                return None
            idx = self._ids.index(evidence_id)
            return self._embeddings[idx].copy()

    # --------------------------------------------------------------- search --
    def query(
        self,
        query_embedding: np.ndarray,
        *,
        top_k: int = 25,
        min_similarity: float = 0.50,
        exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Exact cosine nearest-neighbour search over indexed corpus embeddings.

        Returns top candidates sorted by similarity descending.
        """
        with self._lock:
            self._ensure_loaded()
            if self.count == 0:
                return []

            vec = np.asarray(query_embedding, dtype=np.float32).reshape(EMBEDDING_DIM)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm

            exclude = exclude or set()

            # Cosine similarity is dot product of normalized vectors
            similarities = np.dot(self._embeddings, vec)

            # Order descending
            order = np.argsort(-similarities, kind="stable")

            results = []
            for position in order:
                evidence_id = self._ids[position]
                if evidence_id in exclude:
                    continue
                sim = float(similarities[position])
                if sim < min_similarity:
                    break
                results.append(
                    {
                        "evidence_id": evidence_id,
                        "vector_id": int(position),
                        "similarity": round(sim, 4),
                    }
                )
                if len(results) >= top_k:
                    break

            return results


# --------------------------------------------------------------------------- #
# Process-wide singleton
# --------------------------------------------------------------------------- #
_instance: DinoV2Index | None = None
_instance_dir: Path | None = None
_instance_lock = threading.Lock()


def get_dinov2_index(settings: Settings) -> DinoV2Index:
    """Return the shared index, reloading it if the files on disk have changed.

    Freshness is re-checked on every hand-out because the instance is
    process-wide and long-lived while the files behind it are rewritten by
    ``scripts/build_index.py`` and by rebuilds in other workers.
    """
    global _instance, _instance_dir
    with _instance_lock:
        if _instance is None or _instance_dir != settings.index_dir:
            _instance = DinoV2Index(settings)
            _instance_dir = settings.index_dir
            _instance.load()
        else:
            _instance.refresh_if_stale()
        return _instance


def reset_dinov2_index_singleton() -> None:
    """Drop the cached DinoV2Index singleton."""
    global _instance, _instance_dir
    with _instance_lock:
        _instance = None
        _instance_dir = None
