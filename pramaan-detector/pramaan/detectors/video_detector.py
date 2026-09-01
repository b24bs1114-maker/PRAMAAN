"""
PRAMAAN Video Detector
======================
Architecture:
  MP4/MOV → frame sampling → EfficientNet-B0 per frame
           → temporal consistency analysis (inter-frame variance)
           → score aggregation → final result

Uses frame-based EfficientNet-B0 + OpenCV face detection + temporal aggregation.
Supports: MP4, MOV

Why this detector abstains without a trained checkpoint
------------------------------------------------------
``ImageForensicNet`` is EfficientNet-B0 with a **new** ``Linear(1280, 2)`` head.
ImageNet pre-training supplies the feature extractor but nothing supplies that
head, so with no trained checkpoint its weights are drawn from the default
initialiser -- a fresh, different random projection on every process start.

Scoring frames through it produces a number, and that number is noise: measured
on one 3-second clip, two runs of the same file gave 0.327 ("AUTHENTIC") and
then an abstention, differing only by process. A forensic verdict that changes
when the worker restarts is worse than no verdict, so this module now reports
the missing signal instead of fabricating one. Every other stage -- frame
sampling, face counting, temporal consistency, aggregation -- is untouched and
starts producing real scores the moment a trained ``video_detector.pt`` is
configured.
"""
from __future__ import annotations
import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from pramaan.schema import make_result, DetectionResult
from pramaan.detectors.image_detector import (
    ImageForensicNet, _transform, _file_hash, _load_torch
)

VIDEO_MODEL_VERSION = "3.0.0"
VIDEO_MODEL_NAME = "VideoDetector-EfficientNetB0"
SUPPORTED_EXTS = {".mp4", ".mov"}
FRAME_SAMPLE_RATE = 1          # 1 frame per second
MAX_FRAMES = 60                # cap at 60 frames for prototype

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "video_detector.pt"

#: Explanation used whenever no trained frame classifier is loaded. It names the
#: setting an operator has to populate, and states plainly that the absence of a
#: score is not a finding about the file.
NO_TRAINED_MODEL_EXPLANATION = (
    "No trained video detector is installed, so no video manipulation score was "
    "produced. The frame classifier's 2-class head is only defined by a trained "
    "checkpoint; without one it would be randomly initialised and its output "
    "would change on every restart, so no number is reported. Set "
    "PRAMAAN_VIDEO_MODEL_PATH to a video_detector.pt trained for this "
    "architecture. This is NOT a finding of authenticity and NOT a finding of "
    "manipulation -- the signal is missing and is excluded from fusion."
)


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

    ``usable`` is False when no trained frame classifier could be loaded. In that
    state no model is constructed at all -- building an untrained one would cost
    20 MB of ImageNet weights to produce numbers this class refuses to report.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.frame_model: Any = None
        self.weights_hash = "none"
        self.load_strategy = "none"
        self.usable = False
        self.unavailable_reason = NO_TRAINED_MODEL_EXPLANATION

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not resolved_path.exists():
            self.load_strategy = "no-checkpoint"
            return

        try:
            state = torch.load(resolved_path, map_location=self.device, weights_only=False)
        except Exception as exc:
            self.load_strategy = f"checkpoint-unreadable:{resolved_path.name}"
            self.unavailable_reason = (
                f"The configured video checkpoint {resolved_path.name} could not be "
                f"read ({exc.__class__.__name__}). {NO_TRAINED_MODEL_EXPLANATION}"
            )
            return
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]

        # The image checkpoint holds Swin-B parameters, which do not fit this
        # EfficientNet-B0 frame model. Sharing PRAMAAN_IMAGE_MODEL_PATH with
        # PRAMAAN_VIDEO_MODEL_PATH therefore installs no video detector at all,
        # and that is reported rather than papered over with an ImageNet head.
        if isinstance(state, dict) and any("swin." in k for k in state.keys()):
            self.load_strategy = f"checkpoint-wrong-architecture:{resolved_path.name}"
            self.unavailable_reason = (
                f"{resolved_path.name} contains Swin-B image-classifier parameters, "
                f"not weights for this EfficientNet-B0 frame model, so it was not "
                f"loaded. {NO_TRAINED_MODEL_EXPLANATION}"
            )
            del state
            return

        try:
            model = ImageForensicNet(pretrained=False)
            report = model.load_state_dict(state, strict=False)
            # strict=False is needed because a checkpoint may legitimately carry
            # extra keys, but a checkpoint that populates *none* of the classifier
            # head leaves that head random -- the exact failure this class exists
            # to refuse. So the head is required to have been loaded.
            head_missing = [k for k in report.missing_keys if k.startswith("classifier.")]
            if head_missing:
                raise RuntimeError(
                    f"checkpoint supplied no classifier head ({len(head_missing)} "
                    f"missing keys, e.g. {head_missing[0]})"
                )
            model.to(self.device).eval()
            for parameter in model.parameters():
                parameter.requires_grad_(False)
        except Exception as exc:
            self.load_strategy = f"checkpoint-incompatible:{resolved_path.name}"
            self.unavailable_reason = (
                f"The configured video checkpoint {resolved_path.name} does not fit "
                f"this frame model ({exc}). {NO_TRAINED_MODEL_EXPLANATION}"
            )
            return
        finally:
            del state

        self.frame_model = model
        self.weights_hash = _file_hash(str(resolved_path))
        self.load_strategy = "checkpoint"
        self.usable = True
        self.unavailable_reason = ""

    def detect(self, video_path: str) -> DetectionResult:
        t0   = time.perf_counter()
        path = Path(video_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, t0, self.weights_hash)

        if not self.usable:
            # Abstain before decoding: without a trained classifier there is
            # nothing to learn from the frames, and sampling 60 of them would
            # spend the time anyway.
            return make_result(
                media_type="video", score=None, confidence=None,
                model=VIDEO_MODEL_NAME, model_version=VIDEO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=(time.perf_counter()-t0)*1000,
                explanation=self.unavailable_reason,
                evidence={"load_strategy": self.load_strategy, "trained_model_loaded": False},
            )

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
                "load_strategy":   self.load_strategy,
                "trained_model_loaded": True,
            },
            heatmap_available=False,
            timestamps=suspicious_ts,
        )

    def _score_frame(self, img: Image.Image) -> tuple[float, int]:
        torch = _load_torch()
        tensor = _transform(img).unsqueeze(0).to(self.device)
        try:
            with torch.inference_mode():
                logits = self.frame_model(tensor)
                probs = torch.softmax(logits, dim=1)[0]
                score = (
                    probs[1].item() if probs.shape[0] > 1
                    else torch.sigmoid(logits[0, 0]).item()
                )
        finally:
            # A 60-frame clip otherwise keeps every decoded tensor reachable
            # until the loop ends.
            del tensor
        n_faces = _count_faces(img)
        return float(score), n_faces


_global_video_detector: Optional[VideoDetector] = None
_video_lock = threading.Lock()


def get_video_detector(weights_path: Optional[str] = None) -> VideoDetector:
    """The process-wide video detector. Built at most once per worker.

    Double-checked under a lock: without it two concurrent analyses each build
    their own model.
    """
    global _global_video_detector
    if _global_video_detector is not None:
        return _global_video_detector
    with _video_lock:
        if _global_video_detector is None:
            _global_video_detector = VideoDetector(weights_path=weights_path)
        return _global_video_detector


def release_video_detector() -> None:
    """Drop the loaded model. Used by tests and by shutdown paths."""
    global _global_video_detector
    with _video_lock:
        _global_video_detector = None


def detect_video(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_video_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def checkpoint_readiness(model_path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Can this module ever produce a video score with ``model_path``?

    Read by the backend's status probe, which otherwise reports a detector as
    available whenever a file exists and this module imports -- so a checkpoint
    for a different architecture showed up as a working video deepfake detector
    that then abstained on every request.

    The answer has to be cheap: status is polled by the dashboard. So the
    checkpoint is *not* loaded. A ``.pt`` file is a zip archive whose ``data.pkl``
    member holds the state-dict's key names as plain strings, a few hundred KB,
    and that is enough to tell Swin-B parameters from this EfficientNet-B0 frame
    model's. Loading the tensors instead costs ~520 MB of RSS (measured).
    """
    resolved = Path(model_path) if model_path else _DEFAULT_WEIGHTS_PATH
    if not resolved.exists():
        return False, NO_TRAINED_MODEL_EXPLANATION

    import zipfile
    try:
        with zipfile.ZipFile(resolved) as archive:
            pickles = [n for n in archive.namelist() if n.endswith("data.pkl")]
            if not pickles:
                return True, None      # not a zip-format checkpoint; let load decide
            blob = archive.read(pickles[0])
    except Exception:
        return True, None              # unreadable here is reported by the loader
    if b"swin." in blob:
        return False, (
            f"{resolved.name} contains Swin-B image-classifier parameters, not "
            f"weights for this EfficientNet-B0 frame model. "
            f"{NO_TRAINED_MODEL_EXPLANATION}"
        )
    if b"classifier." not in blob:
        return False, (
            f"{resolved.name} supplies no classifier head for this frame model, "
            f"so the head would stay randomly initialised. "
            f"{NO_TRAINED_MODEL_EXPLANATION}"
        )
    return True, None


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

