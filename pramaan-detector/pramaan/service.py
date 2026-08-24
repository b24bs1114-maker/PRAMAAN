"""
PRAMAAN Detector Service
========================
Single entry point that routes to ImageDetector / VideoDetector / AudioDetector
based on file extension.

Does NOT compute: C2PA, metadata, pHash, FAISS, provenance, propagation,
or the final PRAMAAN verdict — those remain independent backend signals.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Optional

from pramaan.schema import make_result, DetectionResult
from pramaan.detectors.image_detector import ImageDetector
from pramaan.detectors.video_detector import VideoDetector
from pramaan.detectors.audio_detector import AudioDetector

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov"}
_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac"}


class DetectorService:
    """
    Unified detector service.

    Parameters
    ----------
    image_weights : path to fine-tuned image model weights (optional)
    video_weights : path to fine-tuned video/frame model weights (optional)
    audio_weights : path to fine-tuned audio model weights (optional)
    device        : "cpu" or "cuda"
    """

    def __init__(
        self,
        image_weights: Optional[str] = None,
        video_weights: Optional[str] = None,
        audio_weights: Optional[str] = None,
        device: str = "cpu",
    ):
        self._image = ImageDetector(weights_path=image_weights, device=device)
        self._video = VideoDetector(weights_path=video_weights, device=device)
        self._audio = AudioDetector(weights_path=audio_weights, device=device)

    def detect(self, file_path: str) -> DetectionResult:
        """
        Detect manipulation in the given file.
        Routes automatically by extension.
        Returns DetectionResult with label INSUFFICIENT_EVIDENCE if
        the modality cannot be determined or an error occurs.
        """
        t0  = time.perf_counter()
        ext = Path(file_path).suffix.lower()

        if ext in _IMAGE_EXTS:
            return self._image.detect(file_path)
        if ext in _VIDEO_EXTS:
            return self._video.detect(file_path)
        if ext in _AUDIO_EXTS:
            return self._audio.detect(file_path)

        return make_result(
            media_type="unknown",
            score=None, confidence=None,
            model="DetectorService", model_version="1.0.0",
            weights_hash="n/a",
            latency_ms=(time.perf_counter()-t0)*1000,
            explanation=f"Cannot determine media type for extension '{ext}'.",
        )
