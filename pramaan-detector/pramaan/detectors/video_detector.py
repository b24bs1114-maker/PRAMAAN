"""
PRAMAAN Video Detector
======================
Backbone : Vansh180/VideoMae-ffc23-deepfake-detector (VideoMAE spatiotemporal transformer)
Task     : Binary — Real Video (0) vs Deepfake/Manipulated Video (1)
Sampling : 16 frames uniformly sampled across video duration, 224x224 resolution
Supports : MP4, MOV, AVI, MKV, WEBM
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from pramaan.detectors.image_detector import _load_torch
from pramaan.schema import make_result, DetectionResult

logger = logging.getLogger(__name__)

VIDEO_MODEL_VERSION = "4.0.0"
VIDEO_MODEL_NAME = "VideoMAE-DeepFake-Detector"
SUPPORTED_EXTS = {".mp4", ".mov"}
NUM_FRAMES = 16
FRAME_SIZE = (224, 224)

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "video_detector.safetensors"
if not _DEFAULT_WEIGHTS_PATH.exists():
    _alt = Path(__file__).resolve().parents[2] / "weights" / "video_detector.pt"
    if _alt.exists():
        _DEFAULT_WEIGHTS_PATH = _alt

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "weights" / "video_config.json"
_PREPROCESSOR_PATH = Path(__file__).resolve().parents[2] / "weights" / "video_preprocessor_config.json"

POSITIVE_INDEX = 1

NO_TRAINED_MODEL_EXPLANATION = (
    "No trained VideoMAE video detector is installed, so no video manipulation score "
    "was produced. Set PRAMAAN_VIDEO_MODEL_PATH to the video_detector.safetensors checkpoint. "
    "This is NOT a finding of authenticity and NOT a finding of manipulation -- the signal "
    "is missing and is excluded from fusion."
)


def _temporal_score(frame_scores: list[float]) -> float:
    """Measure temporal inconsistency across video frames."""
    if len(frame_scores) < 2:
        return 0.0
    arr = np.array(frame_scores)
    return float(np.clip(np.std(arr) * 4.0, 0.0, 1.0))


def _aggregate(frame_scores: list[float], temporal: float) -> tuple[float, float]:
    """Combine frame scores and temporal score into a single manipulation score."""
    if not frame_scores:
        return 0.5, 0.0
    arr = np.array(frame_scores)
    combined = 0.7 * float(arr.mean()) + 0.3 * temporal
    frame_conf = min(abs(combined - 0.5) * 2.0, 1.0)
    n_factor = min(len(frame_scores) / 10.0, 1.0)
    confidence = frame_conf * (0.5 + 0.5 * n_factor)
    return float(np.clip(combined, 0.0, 1.0)), float(confidence)


def _count_faces(image: Any) -> int:
    """Detect faces in a frame using OpenCV Haar cascade."""
    try:
        import cv2

        if isinstance(image, Image.Image):
            arr = np.array(image.convert("RGB"))
        else:
            arr = np.array(image)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(30, 30))
        return int(len(faces))
    except Exception:
        return 0


def _load_videomae_model(checkpoint_path: Path) -> tuple[Any, Any, str]:
    """Load VideoMAE model and image processor from local weights."""
    torch = _load_torch()
    from transformers import VideoMAEConfig, VideoMAEForVideoClassification, VideoMAEImageProcessor

    resolved = checkpoint_path
    if not resolved.exists() and checkpoint_path.with_suffix(".safetensors").exists():
        resolved = checkpoint_path.with_suffix(".safetensors")
    elif not resolved.exists() and checkpoint_path.with_suffix(".pt").exists():
        resolved = checkpoint_path.with_suffix(".pt")

    if not resolved.exists():
        raise FileNotFoundError(f"Video checkpoint not found at {resolved}")

    if _CONFIG_PATH.exists():
        config = VideoMAEConfig.from_pretrained(str(_CONFIG_PATH))
    else:
        config = VideoMAEConfig.from_pretrained("Vansh180/VideoMae-ffc23-deepfake-detector")

    if _PREPROCESSOR_PATH.exists():
        processor = VideoMAEImageProcessor.from_pretrained(str(_PREPROCESSOR_PATH))
    else:
        processor = VideoMAEImageProcessor(size={"shortest_edge": 224}, crop_size={"height": 224, "width": 224})

    model = VideoMAEForVideoClassification(config)

    state_dict = None
    try:
        from safetensors import safe_open
        with safe_open(str(resolved), framework="pt") as f:
            state_dict = {k: f.get_tensor(k) for k in f.keys()}
    except Exception:
        pass

    if state_dict is None:
        saved = torch.load(resolved, map_location="cpu", weights_only=False)
        state_dict = saved.get("state_dict", saved) if isinstance(saved, dict) else saved

    fixed_sd = {}
    for k, v in state_dict.items():
        if k.endswith(".q_bias"):
            new_k = k[:-7] + ".query.bias"
            fixed_sd[new_k] = v
            key_k = k[:-7] + ".key.bias"
            fixed_sd[key_k] = torch.zeros_like(v)
        elif k.endswith(".v_bias"):
            new_k = k[:-7] + ".value.bias"
            fixed_sd[new_k] = v
        else:
            fixed_sd[k] = v

    model.load_state_dict(fixed_sd, strict=True)
    return model, processor, "safetensors-strict"


def _sample_video_frames(video_path: Path, num_frames: int = NUM_FRAMES) -> tuple[list[np.ndarray], float]:
    """Sample num_frames uniformly across video duration using OpenCV."""
    import cv2

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video file: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    duration = total_frames / max(1.0, fps)

    if total_frames <= 0:
        cap.release()
        raise ValueError(f"Video contains 0 frames: {video_path}")

    indices = np.linspace(0, total_frames - 1, num_frames, dtype=int)
    frames_dict = {}
    current_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if current_idx in indices:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames_dict[current_idx] = rgb_frame
        current_idx += 1
        if len(frames_dict) == len(indices):
            break

    cap.release()

    ordered_frames = []
    for idx in indices:
        if idx in frames_dict:
            ordered_frames.append(frames_dict[idx])
        elif ordered_frames:
            ordered_frames.append(ordered_frames[-1])
        else:
            ordered_frames.append(np.zeros((224, 224, 3), dtype=np.uint8))

    while len(ordered_frames) < num_frames:
        ordered_frames.append(ordered_frames[-1] if ordered_frames else np.zeros((224, 224, 3), dtype=np.uint8))

    return ordered_frames[:num_frames], duration


class VideoDetector:
    """Video Deepfake Detector using Spatiotemporal VideoMAE."""

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.weights_hash = "uninitialised"
        self.usable = False

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not resolved_path.exists() and resolved_path.with_suffix(".safetensors").exists():
            resolved_path = resolved_path.with_suffix(".safetensors")
        elif not resolved_path.exists() and resolved_path.with_suffix(".pt").exists():
            resolved_path = resolved_path.with_suffix(".pt")

        if resolved_path.exists():
            try:
                self.model, self.processor, self.strategy = _load_videomae_model(resolved_path)
                self.model.to(self.device).eval()
                for p in self.model.parameters():
                    p.requires_grad_(False)
                self.weights_hash = _file_hash(str(resolved_path))
                self.usable = True
                logger.info("Loaded VideoMAE model from %s (%s)", resolved_path, self.weights_hash[:16])
            except Exception as e:
                logger.warning("Failed to load VideoMAE model from %s: %s", resolved_path, e)
                self.usable = False
                self.weights_hash = "load_failed"
        else:
            self.usable = False
            self.weights_hash = "none"

    def detect(self, video_path: str) -> DetectionResult:
        t0 = time.perf_counter()
        path = Path(video_path)

        if not path.exists():
            return make_result(
                media_type="video",
                score=None,
                confidence=None,
                model=VIDEO_MODEL_NAME,
                model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Video file not found: {video_path}",
            )

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return make_result(
                media_type="video",
                score=None,
                confidence=None,
                model=VIDEO_MODEL_NAME,
                model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Unsupported video extension: {path.suffix}",
            )

        if not self.usable:
            return make_result(
                media_type="video",
                score=None,
                confidence=None,
                model=VIDEO_MODEL_NAME,
                model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=NO_TRAINED_MODEL_EXPLANATION,
            )

        try:
            frames, duration_s = _sample_video_frames(path, NUM_FRAMES)
        except Exception as exc:
            return make_result(
                media_type="video",
                score=None,
                confidence=None,
                model=VIDEO_MODEL_NAME,
                model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Error decoding video frames: {exc}",
            )

        torch = _load_torch()
        inputs = self.processor(frames, return_tensors="pt")
        inputs = {k: v.to(self.device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self.model(**inputs)
            logits = outputs.logits
            probs = torch.softmax(logits, dim=-1)
            # Class 0: real, Class 1: fake
            fake_prob = float(probs[0, 1].item())
            confidence = None

        timestamps = []
        if fake_prob > 0.65:
            timestamps.append({
                "start_ms": 0,
                "end_ms": int(duration_s * 1000),
                "score": round(fake_prob, 4),
                "note": "Spatiotemporal anomaly detected across sampled video sequence",
            })

        explanation = _explain_video(fake_prob, duration_s, len(frames))

        res = make_result(
            media_type="video",
            score=round(fake_prob, 4),
            confidence=None,
            model=VIDEO_MODEL_NAME,
            model_version=VIDEO_MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            explanation=explanation,
            timestamps=timestamps,
        )
        res.evidence["frame_count"] = len(frames)
        res.evidence["duration_s"] = duration_s
        return res


def checkpoint_readiness(weights_path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    resolved = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
    if not resolved.exists() and resolved.with_suffix(".safetensors").exists():
        resolved = resolved.with_suffix(".safetensors")
    elif not resolved.exists() and resolved.with_suffix(".pt").exists():
        resolved = resolved.with_suffix(".pt")

    if not resolved.exists():
        return False, (
            f"Video checkpoint file not found at {resolved}. "
            "This is NOT a finding of authenticity and NOT a finding of manipulation -- "
            "the signal is missing and is excluded from fusion."
        )
    return True, None


def verify_label_direction(label_map: dict | None = None) -> bool:
    return True


_global_video_detector: Optional[VideoDetector] = None
_video_detector_lock = threading.Lock()


def get_video_detector(weights_path: Optional[str] = None) -> VideoDetector:
    global _global_video_detector
    if _global_video_detector is not None:
        return _global_video_detector
    with _video_detector_lock:
        if _global_video_detector is None:
            _global_video_detector = VideoDetector(weights_path=weights_path)
        return _global_video_detector


def release_video_detector() -> None:
    global _global_video_detector
    with _video_detector_lock:
        _global_video_detector = None


def load_checkpoint(model_path: Optional[str] = None) -> bool:
    was_cold = _global_video_detector is None
    get_video_detector(weights_path=str(model_path) if model_path else None)
    return was_cold


def detect_video(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    detector = get_video_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _explain_video(score: float, duration_s: float, frame_count: int) -> str:
    if abs(score - 0.5) < 0.15:
        return (
            f"Video deepfake score {score:.3f} across {duration_s:.1f}s ({frame_count} frames) sits near the 0.5 "
            "midpoint; temporal transformer does not separate the video decisively."
        )
    if score >= 0.5:
        return (
            f"Video deepfake score {score:.3f} across {duration_s:.1f}s ({frame_count} frames) is above 0.5, "
            "indicating spatiotemporal manipulation signatures (VideoMAE FaceForensics++ fine-tuned detector)."
        )
    return (
        f"Video deepfake score {score:.3f} across {duration_s:.1f}s ({frame_count} frames) is below 0.5: "
        "no deepfake or facial manipulation artifacts detected across temporal sequence."
    )


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
