"""Modality detectors, exported lazily.

Importing this package used to import all three detectors, and with them torch,
torchvision, transformers, librosa and OpenCV -- roughly 370 MB of RSS. The
backend imports ``pramaan.detectors.image_detector`` merely to *check whether a
detector is installed* (``GET /api/detector/status``, the dashboard), which made
an availability probe as expensive as loading a model. PEP 562 lazy attribute
access keeps that probe cheap and pulls in only the modality actually used.
"""

from typing import TYPE_CHECKING, Any

__all__ = ["ImageDetector", "VideoDetector", "AudioDetector"]

_MODULES = {
    "ImageDetector": "pramaan.detectors.image_detector",
    "VideoDetector": "pramaan.detectors.video_detector",
    "AudioDetector": "pramaan.detectors.audio_detector",
}

if TYPE_CHECKING:  # pragma: no cover - typing only, never executed at runtime
    from pramaan.detectors.audio_detector import AudioDetector
    from pramaan.detectors.image_detector import ImageDetector
    from pramaan.detectors.video_detector import VideoDetector


def __getattr__(name: str) -> Any:
    module_name = _MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)
