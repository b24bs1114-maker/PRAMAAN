"""DINOv2 visual embedding extraction and inference service.

Provides lightweight DINOv2 visual feature extraction for PRAMAAN's
provenance and retrieval pipeline. Uses ViT-S/14 (facebook/dinov2-small)
by default: 21M parameters, 384-dimensional feature vector, highly fast on
CPU and Apple Silicon MPS.

Key properties:
* Embeddings are L2-normalized so inner product equals cosine similarity.
* In-memory LRU caching keyed by SHA-256 prevents redundant forward passes.
* Thread-safe lazy initialization of model and processor.
* Graceful fallback/abstention if model cannot be loaded.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

logger = logging.getLogger("pramaan.dinov2")

_model = None
_processor = None
_device = None
_lock = threading.Lock()
_embedding_cache: dict[str, np.ndarray] = {}
_CACHE_MAX_SIZE = 2048


def _get_device(preferred: str = "auto") -> str:
    """Determine best available torch device."""
    import torch

    pref = preferred.lower() if preferred else "auto"
    if pref in {"cpu", "cuda", "mps"}:
        if pref == "cuda" and torch.cuda.is_available():
            return "cuda"
        if pref == "mps" and hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    # Auto detection
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def get_dinov2_components(
    model_name: str = "facebook/dinov2-small",
    device_pref: str = "auto",
) -> tuple[Any, Any, str] | tuple[None, None, str]:
    """Lazy thread-safe loader for DINOv2 model and image processor."""
    global _model, _processor, _device
    with _lock:
        if _model is not None and _processor is not None:
            return _model, _processor, _device

        try:
            import torch
            from transformers import AutoImageProcessor, AutoModel

            target_device = _get_device(device_pref)
            logger.info("Loading DINOv2 model '%s' on device %s ...", model_name, target_device)

            processor = AutoImageProcessor.from_pretrained(model_name)
            model = AutoModel.from_pretrained(model_name)
            model.to(target_device)
            model.eval()

            _model = model
            _processor = processor
            _device = target_device
            logger.info("DINOv2 model '%s' loaded successfully.", model_name)
            return _model, _processor, _device
        except Exception as exc:
            logger.warning("Could not load DINOv2 model '%s': %s", model_name, exc)
            return None, None, "unavailable"


def extract_embedding(
    image_or_path: Image.Image | str | Path | bytes,
    *,
    model_name: str = "facebook/dinov2-small",
    device_pref: str = "auto",
    cache_key: str | None = None,
) -> np.ndarray | None:
    """Extract an L2-normalized 1-D visual embedding from an image.

    Returns a 1-D float32 numpy array of shape (384,) or None if extraction fails.
    """
    import torch

    # Check cache first if key is provided or computable
    key = cache_key
    if key and key in _embedding_cache:
        return _embedding_cache[key].copy()

    # Load image
    image = None
    close_image = False
    try:
        if isinstance(image_or_path, Image.Image):
            image = image_or_path
        elif isinstance(image_or_path, bytes):
            if not key:
                key = hashlib.sha256(image_or_path).hexdigest()
                if key in _embedding_cache:
                    return _embedding_cache[key].copy()
            import io
            image = Image.open(io.BytesIO(image_or_path))
            close_image = True
        elif isinstance(image_or_path, (str, Path)):
            path = Path(image_or_path)
            if not path.is_file():
                logger.warning("Image file not found: %s", path)
                return None
            image = Image.open(path)
            close_image = True
        else:
            logger.warning("Unsupported image input type: %s", type(image_or_path))
            return None

        # Convert to RGB (DINOv2 expects 3 channels)
        if image.mode != "RGB":
            image = image.convert("RGB")

        model, processor, device = get_dinov2_components(model_name=model_name, device_pref=device_pref)
        if model is None or processor is None:
            logger.warning("DINOv2 components unavailable.")
            return None

        inputs = processor(images=image, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = model(**inputs)
            # ViT CLS token is at index 0 of last_hidden_state
            cls_token = outputs.last_hidden_state[:, 0, :]
            # L2 normalize
            norm_cls = cls_token / torch.norm(cls_token, p=2, dim=-1, keepdim=True)
            embedding = norm_cls.squeeze(0).cpu().to(torch.float32).numpy()

        # Cache result
        if key:
            if len(_embedding_cache) >= _CACHE_MAX_SIZE:
                # Remove arbitrary item to keep bounded
                _embedding_cache.pop(next(iter(_embedding_cache)))
            _embedding_cache[key] = embedding.copy()

        return embedding
    except Exception as exc:
        logger.error("Failed to extract DINOv2 embedding: %s", exc)
        return None
    finally:
        if close_image and image is not None:
            try:
                image.close()
            except Exception:
                pass


def clear_embedding_cache() -> None:
    """Clear the in-memory embedding cache."""
    _embedding_cache.clear()


def status(model_name: str = "facebook/dinov2-small", device_pref: str = "auto") -> dict[str, Any]:
    """Report DINOv2 service status."""
    model, _, device = get_dinov2_components(model_name=model_name, device_pref=device_pref)
    available = model is not None
    return {
        "available": available,
        "model_name": model_name,
        "embedding_dim": 384,
        "device": device if available else None,
        "cached_embeddings_count": len(_embedding_cache),
    }
