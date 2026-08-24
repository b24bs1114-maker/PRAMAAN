"""
PRAMAAN Video Detector
======================
Architecture:
  MP4/MOV → frame sampling → EfficientNet-B0 per frame
           → temporal consistency analysis (inter-frame variance)
           → score aggregation → final result

Uses frame-based EfficientNet-B0 + OpenCV face detection + temporal aggregation.
Supports: MP4, MOV
"""
from __future__ import annotations
import hashlib, time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

import torch
import torchvision.transforms as T

from pramaan.schema import make_result, DetectionResult
from pramaan.detectors.image_detector import (
    ImageForensicNet, _transform, _file_hash
)

VIDEO_MODEL_VERSION = "3.0.0"
VIDEO_MODEL_NAME = "VideoDetector-EfficientNetB0"
SUPPORTED_EXTS = {".mp4", ".mov"}
FRAME_SAMPLE_RATE = 1          # 1 frame per second
MAX_FRAMES = 60                # cap at 60 frames for prototype

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "image_detector.pt"


def _temporal_score(frame_scores: list[float]) -> float:
    """
    Measure temporal inconsistency.
    High variance in per-frame scores → temporal anomaly.
    Returns 0–1 (higher = more inconsistent).
    """
    if len(frame_scores) < 2:
        return 0.0
    arr = np.array(frame_scores)
    return float(np.clip(np.std(arr) * 4.0, 0.0, 1.0))


def _aggregate(frame_scores: list[float], temporal: float) -> tuple[float, float]:
    """
    Combine frame scores and temporal score into a single manipulation score.
    Returns (manipulation_score, confidence).
    """
    if not frame_scores:
        return 0.5, 0.0
    arr = np.array(frame_scores)
    combined = 0.7 * float(arr.mean()) + 0.3 * temporal
    frame_conf = min(abs(combined - 0.5) * 2.0, 1.0)
    n_factor   = min(len(frame_scores) / 10.0, 1.0)
    confidence = frame_conf * (0.5 + 0.5 * n_factor)
    return float(np.clip(combined, 0.0, 1.0)), float(confidence)


class VideoDetector:
    """
    Runs per-frame forensic analysis + temporal consistency check.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        self.device = torch.device(device)
        self.frame_model = ImageForensicNet(pretrained=False)
        self.frame_model.to(self.device).eval()
        self.weights_hash = "uninitialised"

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if resolved_path.exists():
            state = torch.load(resolved_path, map_location=self.device)
            if isinstance(state, dict) and "state_dict" in state:
                state = state["state_dict"]
            # Check if weights belong to ImageForensicNet vs Swin transformer
            if isinstance(state, dict) and any("swin." in k for k in state.keys()):
                self.frame_model = ImageForensicNet(pretrained=True)
                self.frame_model.to(self.device).eval()
                self.weights_hash = _file_hash(str(resolved_path))
            else:
                try:
                    self.frame_model.load_state_dict(state, strict=False)
                    self.weights_hash = _file_hash(str(resolved_path))
                except Exception:
                    self.frame_model = ImageForensicNet(pretrained=True)
                    self.frame_model.to(self.device).eval()
                    self.weights_hash = _file_hash(str(resolved_path))
        else:
            self.frame_model = ImageForensicNet(pretrained=True)
            self.frame_model.to(self.device).eval()
            self.weights_hash = "pretrained-fallback"

    def detect(self, video_path: str) -> DetectionResult:
        t0   = time.perf_counter()
        path = Path(video_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, t0, self.weights_hash)

        try:
            frames, timestamps = _sample_frames(video_path)
        except Exception as exc:
            return _error_result(str(exc), t0, self.weights_hash)

        if not frames:
            return make_result(
                media_type="video", score=None, confidence=None,
                model=VIDEO_MODEL_NAME, model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=(time.perf_counter()-t0)*1000,
                explanation="No frames could be extracted from the video.",
            )

        frame_scores = []
        suspicious_ts = []
        faces_detected = 0

        for frame_img, ts in zip(frames, timestamps):
            score, n_faces = self._score_frame(frame_img)
            frame_scores.append(score)
            faces_detected = max(faces_detected, n_faces)
            if score >= 0.5:
                suspicious_ts.append({"timestamp_s": ts, "frame_score": round(score, 4)})

        temporal = _temporal_score(frame_scores)
        manip_score, confidence = _aggregate(frame_scores, temporal)
        latency_ms = (time.perf_counter() - t0) * 1000

        explanation = _explain_video(manip_score, temporal, frame_scores, suspicious_ts)

        return make_result(
            media_type="video",
            score=manip_score,
            confidence=confidence,
            model=VIDEO_MODEL_NAME,
            model_version=VIDEO_MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=latency_ms,
            explanation=explanation,
            evidence={
                "frame_scores":    [round(s, 4) for s in frame_scores],
                "temporal_score":  round(temporal, 4),
                "frames_analysed": len(frames),
                "faces_detected":  faces_detected,
            },
            heatmap_available=False,
            timestamps=suspicious_ts,
        )

    def _score_frame(self, img: Image.Image) -> tuple[float, int]:
        tensor = _transform(img).unsqueeze(0).to(self.device)
        with torch.no_grad():
            logits = self.frame_model(tensor)
            probs = torch.softmax(logits, dim=1)[0]
            score = probs[1].item() if probs.shape[0] > 1 else torch.sigmoid(logits[0, 0]).item()
        n_faces = _count_faces(img)
        return float(score), n_faces


_global_video_detector: Optional[VideoDetector] = None

def get_video_detector(weights_path: Optional[str] = None) -> VideoDetector:
    global _global_video_detector
    if _global_video_detector is None or (weights_path and _global_video_detector.weights_hash == "uninitialised"):
        _global_video_detector = VideoDetector(weights_path=weights_path)
    return _global_video_detector


def detect_video(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_video_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _count_faces(img: Image.Image) -> int:
    try:
        import cv2
        import numpy as np
        gray = np.array(img.convert("L"))
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        return len(faces) if len(faces) else 0
    except Exception:
        return 0


def _sample_frames(video_path: str) -> tuple[list[Image.Image], list[float]]:
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        step = max(1, int(fps / FRAME_SAMPLE_RATE))
        frames, timestamps = [], []
        idx = 0
        while len(frames) < MAX_FRAMES:
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ret, frame = cap.read()
            if not ret:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(rgb))
            timestamps.append(round(idx / fps, 2))
            idx += step
        cap.release()
        return frames, timestamps
    except ImportError:
        pass

    try:
        from decord import VideoReader, cpu
        vr = VideoReader(video_path, ctx=cpu(0))
        fps = vr.get_avg_fps() or 25.0
        step = max(1, int(fps / FRAME_SAMPLE_RATE))
        indices = list(range(0, min(len(vr), MAX_FRAMES * step), step))[:MAX_FRAMES]
        frames, timestamps = [], []
        for i in indices:
            arr = vr[i].asnumpy()
            frames.append(Image.fromarray(arr))
            timestamps.append(round(i / fps, 2))
        return frames, timestamps
    except ImportError:
        raise RuntimeError("Neither cv2 nor decord is installed. Cannot decode video.")


def _explain_video(score: float, temporal: float, frame_scores: list, suspicious: list) -> str:
    if abs(score - 0.5) < 0.15:
        return "Evidence is ambiguous; insufficient confidence to classify video."
    direction = "AI-generated or manipulated" if score >= 0.5 else "authentic"
    parts = [f"Video classified as {direction} (manipulation likelihood={score:.3f})."]
    parts.append(f"Temporal inconsistency score: {temporal:.3f}.")
    if suspicious:
        parts.append(f"{len(suspicious)} suspicious frame(s) detected.")
    return " ".join(parts)


def _unsupported(ext: str, t0: float, wh: str) -> DetectionResult:
    return make_result(
        media_type="video", score=None, confidence=None,
        model=VIDEO_MODEL_NAME, model_version=VIDEO_MODEL_VERSION,
        weights_hash=wh, latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Unsupported file extension: {ext}",
    )


def _error_result(msg: str, t0: float, wh: str) -> DetectionResult:
    return make_result(
        media_type="video", score=None, confidence=None,
        model=VIDEO_MODEL_NAME, model_version=VIDEO_MODEL_VERSION,
        weights_hash=wh, latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Error processing video: {msg}",
    )

