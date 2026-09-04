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

    def load(self) -> None:
        """Read the persisted embedding index from disk, tolerating missing files."""
        with self._lock:
            self._loaded = True
            if not (self.embeddings_path.is_file() and self.sidecar_path.is_file()):
                self._ids = []
                self._embeddings = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
                return

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
                    "Could not load DINOv2 embedding index (%s: %s); starting empty.",
                    exc.__class__.__name__,
                    exc,
                )
                self._ids = []
                self._embeddings = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
                return

            self._ids = ids
            self._embeddings = np.ascontiguousarray(embeddings, dtype=np.float32)
            self._version = int(meta.get("index_version", 0))
            self._last_updated = meta.get("last_updated")
            logger.info(
                "Loaded DINOv2 visual index: %d embeddings, version %d",
                self.count,
                self._version,
            )

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
    """Return the shared DinoV2Index singleton."""
    global _instance, _instance_dir
    with _instance_lock:
        if _instance is None or _instance_dir != settings.index_dir:
            _instance = DinoV2Index(settings)
            _instance_dir = settings.index_dir
            _instance.load()
        return _instance


def reset_dinov2_index_singleton() -> None:
    """Drop the cached DinoV2Index singleton."""
    global _instance, _instance_dir
    with _instance_lock:
        _instance = None
        _instance_dir = None
