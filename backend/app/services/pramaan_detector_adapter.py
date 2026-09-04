"""Rahul's PRAMAAN AI Detector Adapter Bridge.

Connects Rahul's multi-modal AI detector engine (pramaan-detector) to the
PRAMAAN backend detector plugin interface for Image, Video, and Audio.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("pramaan.detector.rahul_adapter")

_detector_instances: dict[tuple[str, str | None], Any] = {}


def _get_detector(modality: str, model_path: Path | str | None = None) -> Any:
    """Retrieve or instantiate a cached Rahul detector for the given modality."""
    weights_str = str(Path(model_path).expanduser()) if model_path and str(model_path).strip() else None
    key = (modality, weights_str)

    if key in _detector_instances:
        return _detector_instances[key]

    try:
        if modality == "image":
            from pramaan.detectors.image_detector import ImageDetector
            instance = ImageDetector(weights_path=weights_str)
        elif modality == "video":
            from pramaan.detectors.video_detector import VideoDetector
            instance = VideoDetector(weights_path=weights_str)
        elif modality == "audio":
            from pramaan.detectors.audio_detector import AudioDetector
            instance = AudioDetector(weights_path=weights_str)
        else:
            raise ValueError(f"Unsupported modality for Rahul detector: {modality}")

        _detector_instances[key] = instance
        logger.info("Initialized Rahul %s detector with weights: %s", modality, weights_str or "pretrained")
        return instance
    except Exception as exc:
        logger.error("Failed to initialize Rahul %s detector: %s", modality, exc)
        raise


def infer_image(
    path: Path | str,
    media_type: str = "image",
    model_path: Path | str | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inference entrypoint for Image AI detection."""
    detector = _get_detector("image", model_path)
    res = detector.detect(str(path))
    return res.to_dict()


def infer_video(
    path: Path | str,
    media_type: str = "video",
    model_path: Path | str | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inference entrypoint for Video AI detection."""
    detector = _get_detector("video", model_path)
    res = detector.detect(str(path))
    return res.to_dict()


def infer_audio(
    path: Path | str,
    media_type: str = "audio",
    model_path: Path | str | None = None,
    spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Inference entrypoint for Audio AI detection."""
    detector = _get_detector("audio", model_path)
    res = detector.detect(str(path))
    return res.to_dict()


def checkpoint_readiness(model_path: Path | str | None = None) -> tuple[bool, str | None]:
    """Check readiness of the configured checkpoint."""
    if model_path is None or not str(model_path).strip():
        return True, None
    p = Path(model_path).expanduser()
    if p.is_file():
        return True, None
    if p.with_suffix(".safetensors").is_file() or p.with_suffix(".pt").is_file() or p.with_suffix(".pth").is_file():
        return True, None
    return False, f"Model checkpoint not found at {model_path}"


def load_checkpoint(model_path: Path | str | None = None) -> bool:
    """Pre-warm or load detector instance."""
    if model_path is None:
        return False
    path_str = str(model_path)
    if "image" in path_str:
        _get_detector("image", model_path)
        return True
    elif "video" in path_str:
        _get_detector("video", model_path)
        return True
    elif "audio" in path_str:
        _get_detector("audio", model_path)
        return True
    return False


__all__ = [
    "checkpoint_readiness",
    "infer_audio",
    "infer_image",
    "infer_video",
    "load_checkpoint",
]
