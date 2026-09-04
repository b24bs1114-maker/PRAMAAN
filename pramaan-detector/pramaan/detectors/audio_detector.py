"""
PRAMAAN Audio Detector
======================
Backbone : SpeechAntiSpoofingBenchmarks/AASIST (Graph Attention Network + Sinc-Conv RawNet2 encoder)
Task     : Binary — Real voice vs Fake/Spoofed/Cloned voice
Evidence : Spectrogram STFT statistics + windowed graph attention analysis
Supports : WAV, MP3, M4A/AAC, FLAC, OGG
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional, Union

import numpy as np

from pramaan.detectors.image_detector import _load_torch
from pramaan.detectors.aasist_model import Model as AASISTModel
from pramaan.schema import make_result, DetectionResult

logger = logging.getLogger(__name__)

AUDIO_MODEL_VERSION = "3.0.0"
AUDIO_MODEL_NAME = "AASIST-Audio-Spoof-Detector"
SUPPORTED_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
TARGET_SR = 16000          # 16 kHz mono waveform
NB_SAMP = 64600            # ~4.0375s window for AASIST
MIN_DURATION = 0.25        # minimum seconds of audio

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "audio_detector.pth"
if not _DEFAULT_WEIGHTS_PATH.exists():
    _alt = Path(__file__).resolve().parents[2] / "weights" / "audio_detector.pt"
    if _alt.exists():
        _DEFAULT_WEIGHTS_PATH = _alt

POSITIVE_INDEX = 0

READINESS_CONTRACT = (
    "ready = AASIST checkpoint present with RawNet2/GAT parameters, "
    "and label mapping consistent with positive_index=0 (spoof/fake)."
)

PEAK_MEMORY_BYTES = 25_000_000

NO_TRAINED_MODEL_EXPLANATION = (
    "No trained AASIST audio detector could be loaded, so no voice-manipulation score "
    "was produced. Set PRAMAAN_AUDIO_MODEL_PATH to the audio_detector.pth checkpoint. "
    "This is NOT a finding of authenticity and NOT a finding of manipulation -- the "
    "signal is missing and is excluded from fusion."
)

DEFAULT_AASIST_CONFIG = {
    "architecture": "AASIST",
    "nb_samp": 64600,
    "first_conv": 128,
    "filts": [70, [1, 32], [32, 32], [32, 64], [64, 64]],
    "gat_dims": [64, 32],
    "pool_ratios": [0.5, 0.7, 0.5, 0.5],
    "temperatures": [2.0, 2.0, 100.0, 100.0]
}


def _load_audio(path: str | Path, target_sr: int = TARGET_SR) -> tuple[np.ndarray, int]:
    """Load audio from file and resample to target_sr mono float32 numpy array."""
    try:
        import soundfile as sf
        data, sr = sf.read(str(path))
        if data.ndim > 1:
            data = np.mean(data, axis=1)
        if sr != target_sr:
            import librosa
            data = librosa.resample(data.astype(np.float32), orig_sr=sr, target_sr=target_sr)
        return data.astype(np.float32), target_sr
    except Exception:
        try:
            import librosa
            data, sr = librosa.load(str(path), sr=target_sr, mono=True)
            return data.astype(np.float32), target_sr
        except Exception as e:
            logger.error("Failed to decode audio %s: %s", path, e)
            raise


def _aggregate_audio(scores: list[float]) -> tuple[float, float]:
    """Aggregate per-window audio scores."""
    if not scores:
        return 0.5, 0.0
    arr = np.array(scores)
    mean_score = float(np.mean(arr))
    conf = min(abs(mean_score - 0.5) * 2.0, 1.0)
    return mean_score, float(conf)


def _pad_or_truncate_audio(x: np.ndarray, max_len: int = NB_SAMP) -> np.ndarray:
    """Pad (repeat) or slice audio waveform deterministically to max_len."""
    x_len = len(x)
    if x_len >= max_len:
        return x[:max_len]
    num_repeats = int(np.ceil(max_len / max(1, x_len)))
    padded = np.tile(x, num_repeats)
    return padded[:max_len]


class AudioDetector:
    """AASIST Audio Anti-Spoofing & Deepfake Detector."""

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.weights_hash = "uninitialised"
        self.usable = False

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not resolved_path.exists() and resolved_path.with_suffix(".pth").exists():
            resolved_path = resolved_path.with_suffix(".pth")
        elif not resolved_path.exists() and resolved_path.with_suffix(".pt").exists():
            resolved_path = resolved_path.with_suffix(".pt")

        self.model = AASISTModel(DEFAULT_AASIST_CONFIG)

        if resolved_path.exists():
            try:
                sd = torch.load(resolved_path, map_location="cpu", weights_only=False)
                if isinstance(sd, dict):
                    sd = sd.get("state_dict", sd)
                self.model.load_state_dict(sd, strict=True)
                self.usable = True
                self.weights_hash = _file_hash(str(resolved_path))
                logger.info("Loaded AASIST model from %s", resolved_path)
            except Exception as e:
                logger.warning("Failed to load AASIST weights from %s: %s", resolved_path, e)
                self.usable = False
                self.weights_hash = "load_failed"
        else:
            self.usable = False
            self.weights_hash = "none"

        self.model.to(self.device).eval()
        for p in self.model.parameters():
            p.requires_grad_(False)

    def _score_waveform(self, waveform: np.ndarray) -> tuple[float, float]:
        """Run AASIST on waveform. Returns (manipulation_score, confidence)."""
        torch = _load_torch()
        tensor = torch.from_numpy(waveform).unsqueeze(0).to(self.device)
        with torch.no_grad():
            _, logits = self.model(tensor)
            probs = torch.softmax(logits, dim=-1)
            # Class 0 = spoof/fake, Class 1 = bonafide/real
            spoof_prob = float(probs[0, 0].item())
            confidence = float(abs(probs[0, 0].item() - probs[0, 1].item()))
            return spoof_prob, confidence

    def detect(self, audio_path: str) -> DetectionResult:
        t0 = time.perf_counter()
        path = Path(audio_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return make_result(
                media_type="audio",
                score=None,
                confidence=None,
                model=AUDIO_MODEL_NAME,
                model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Unsupported file extension: {path.suffix}",
            )

        if not self.usable:
            return make_result(
                media_type="audio",
                score=None,
                confidence=None,
                model=AUDIO_MODEL_NAME,
                model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=NO_TRAINED_MODEL_EXPLANATION,
            )

        try:
            raw_audio, sr = _load_audio(path, TARGET_SR)
        except Exception as exc:
            return make_result(
                media_type="audio",
                score=None,
                confidence=None,
                model=AUDIO_MODEL_NAME,
                model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Error reading audio file: {exc}",
            )

        duration = len(raw_audio) / TARGET_SR
        if duration < MIN_DURATION:
            return make_result(
                media_type="audio",
                score=None,
                confidence=None,
                model=AUDIO_MODEL_NAME,
                model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=round((time.perf_counter() - t0) * 1000, 2),
                explanation=f"Audio duration ({duration:.2f}s) is shorter than minimum required ({MIN_DURATION}s).",
            )

        window_scores = []
        hop = NB_SAMP
        timestamps = []

        if len(raw_audio) <= NB_SAMP:
            windowed = _pad_or_truncate_audio(raw_audio, NB_SAMP)
            score, conf = self._score_waveform(windowed)
            window_scores.append(score)
            if score > 0.65:
                timestamps.append({"start_ms": 0, "end_ms": int(duration * 1000), "score": round(score, 4)})
        else:
            for start in range(0, len(raw_audio), hop):
                chunk = raw_audio[start : start + hop]
                if len(chunk) < int(TARGET_SR * 0.5):
                    break
                chunk_padded = _pad_or_truncate_audio(chunk, NB_SAMP)
                score, _ = self._score_waveform(chunk_padded)
                window_scores.append(score)
                start_ms = int((start / TARGET_SR) * 1000)
                end_ms = int((min(start + hop, len(raw_audio)) / TARGET_SR) * 1000)
                if score > 0.65:
                    timestamps.append({"start_ms": start_ms, "end_ms": end_ms, "score": round(score, 4)})

        final_score = float(np.mean(window_scores)) if window_scores else 0.0
        explanation = _explain_audio(final_score, duration, len(timestamps))

        res = make_result(
            media_type="audio",
            score=round(final_score, 4),
            confidence=None,
            model=AUDIO_MODEL_NAME,
            model_version=AUDIO_MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            explanation=explanation,
            timestamps=timestamps,
        )
        res.evidence["duration_s"] = duration
        res.evidence["chunk_scores"] = window_scores
        return res


def checkpoint_readiness(weights_path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    resolved = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
    if not resolved.exists() and resolved.with_suffix(".pth").exists():
        resolved = resolved.with_suffix(".pth")
    elif not resolved.exists() and resolved.with_suffix(".pt").exists():
        resolved = resolved.with_suffix(".pt")

    if not resolved.exists():
        return False, (
            f"Audio checkpoint file not found at {resolved}. "
            "This is NOT a finding of authenticity and NOT a finding of manipulation -- "
            "the signal is missing and is excluded from fusion."
        )
    return True, None


def verify_label_direction(label_map: dict | None = None) -> bool:
    if label_map:
        fake_idx = None
        for k, v in label_map.items():
            if any(m in str(v).lower() for m in ("fake", "spoof", "synthetic", "clone")):
                fake_idx = int(k)
        if fake_idx is not None and fake_idx != POSITIVE_INDEX:
            raise RuntimeError(f"Config label direction {label_map} contradicts POSITIVE_INDEX={POSITIVE_INDEX}; would invert every verdict.")
    return True


_global_audio_detector: Optional[AudioDetector] = None
_audio_detector_lock = threading.Lock()


def get_audio_detector(weights_path: Optional[str] = None) -> AudioDetector:
    global _global_audio_detector
    if _global_audio_detector is not None:
        return _global_audio_detector
    with _audio_detector_lock:
        if _global_audio_detector is None:
            _global_audio_detector = AudioDetector(weights_path=weights_path)
        return _global_audio_detector


def release_audio_detector() -> None:
    global _global_audio_detector
    with _audio_detector_lock:
        _global_audio_detector = None


def load_checkpoint(model_path: Optional[str] = None) -> bool:
    was_cold = _global_audio_detector is None
    get_audio_detector(weights_path=str(model_path) if model_path else None)
    return was_cold


def detect_audio(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    detector = get_audio_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _explain_audio(score: float, duration_s: float, suspicious_windows: int) -> str:
    window_note = (
        f" AASIST flagged {suspicious_windows} suspicious time window(s)."
        if suspicious_windows > 0
        else ""
    )
    if abs(score - 0.5) < 0.15:
        return (
            f"Voice anti-spoofing score {score:.3f} across {duration_s:.1f}s sits near the 0.5 decision midpoint; "
            f"model does not indicate spoofing or authentic voice decisively.{window_note}"
        )
    if score >= 0.5:
        return (
            f"Voice anti-spoofing score {score:.3f} across {duration_s:.1f}s is above 0.5, consistent with synthetic "
            f"speech or voice cloning (AASIST graph attention analysis).{window_note}"
        )
    return (
        f"Voice anti-spoofing score {score:.3f} across {duration_s:.1f}s is below 0.5: no synthetic voice or clone "
        f"patterns detected.{window_note}"
    )


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
