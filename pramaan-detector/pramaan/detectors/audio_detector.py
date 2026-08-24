"""
PRAMAAN Audio Detector
======================
Backbone : garystafford/wav2vec2-deepfake-voice-detector (Wav2Vec2 fine-tuned for deepfake voice detection)
Task     : Binary — Real voice (0) vs Fake/Cloned voice (1)
Evidence : Spectrogram STFT statistics + suspicious window extraction
Supports : WAV, MP3, M4A/AAC
"""
from __future__ import annotations
import hashlib, time
from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn

from pramaan.schema import make_result, DetectionResult

AUDIO_MODEL_VERSION = "2.0.0"
AUDIO_MODEL_NAME = "Wav2Vec2-GaryStafford-DeepfakeVoiceDetector"
SUPPORTED_EXTS = {".wav", ".mp3", ".m4a", ".aac"}
TARGET_SR = 16000          # wav2vec2 expects 16 kHz
CHUNK_SEC = 3.0            # analyse in 3-second windows
MIN_DURATION = 0.5         # seconds

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "audio_detector.pt"
_HF_MODEL_NAME = "garystafford/wav2vec2-deepfake-voice-detector"


class AudioForensicNet(nn.Module):
    """
    Wav2Vec2 model fine-tuned for deepfake voice classification.
    """

    def __init__(self, weights_path: Optional[str] = None):
        super().__init__()
        self.hf_model = None

        try:
            from transformers import AutoModelForAudioClassification
            resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
            if resolved_path.exists():
                try:
                    self.hf_model = AutoModelForAudioClassification.from_pretrained(_HF_MODEL_NAME)
                    state = torch.load(resolved_path, map_location="cpu")
                    if isinstance(state, dict) and "state_dict" in state:
                        state = state["state_dict"]
                    self.hf_model.load_state_dict(state, strict=False)
                except Exception:
                    self.hf_model = AutoModelForAudioClassification.from_pretrained(_HF_MODEL_NAME)
            else:
                self.hf_model = AutoModelForAudioClassification.from_pretrained(_HF_MODEL_NAME)
        except Exception:
            self.fallback = _LightweightAudioCNN()

    def forward(self, waveform: torch.Tensor) -> torch.Tensor:
        """waveform: (batch, samples) at 16 kHz"""
        if self.hf_model is not None:
            outputs = self.hf_model(waveform)
            return outputs.logits   # (B, 2)
        return self.fallback(waveform)


class _LightweightAudioCNN(nn.Module):
    def __init__(self):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv1d(1, 32, 64, stride=16), nn.ReLU(),
            nn.Conv1d(32, 64, 32, stride=8), nn.ReLU(),
            nn.Conv1d(64, 128, 16, stride=4), nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.proj = nn.Linear(128, 2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.net(x.unsqueeze(1)).squeeze(-1)
        return self.proj(out)


class AudioDetector:
    """
    Audio Deepfake / Voice Clone Detector.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        self.device = torch.device(device)
        self.model  = AudioForensicNet(weights_path=weights_path)
        self.model.to(self.device).eval()
        self.weights_hash = "uninitialised"

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if resolved_path.exists():
            self.weights_hash = _file_hash(str(resolved_path))
        else:
            self.weights_hash = "pretrained-hf-hub"

    def detect(self, audio_path: str) -> DetectionResult:
        t0   = time.perf_counter()
        path = Path(audio_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, t0, self.weights_hash)

        try:
            waveform, sr = _load_audio(audio_path)
        except Exception as exc:
            return _error_result(str(exc), t0, self.weights_hash)

        duration = len(waveform) / sr
        if duration < MIN_DURATION:
            return make_result(
                media_type="audio", score=None, confidence=None,
                model=AUDIO_MODEL_NAME, model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=(time.perf_counter()-t0)*1000,
                explanation="Audio too short for reliable analysis.",
            )

        chunk_scores, chunk_times = self._score_chunks(waveform, sr)
        manip_score, confidence   = _aggregate_audio(chunk_scores)
        suspicious_ts = [
            {"start_s": round(t, 2), "end_s": round(t + CHUNK_SEC, 2), "score": round(s, 4)}
            for t, s in zip(chunk_times, chunk_scores) if s >= 0.5
        ]

        spec_evidence = _spectrogram_evidence(waveform, sr)
        latency_ms    = (time.perf_counter() - t0) * 1000
        explanation   = _explain_audio(manip_score, chunk_scores, suspicious_ts)

        return make_result(
            media_type="audio",
            score=manip_score,
            confidence=confidence,
            model=AUDIO_MODEL_NAME,
            model_version=AUDIO_MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=latency_ms,
            explanation=explanation,
            evidence={
                "chunk_scores": [round(s, 4) for s in chunk_scores],
                "duration_s": round(duration, 2),
                **spec_evidence,
            },
            heatmap_available=bool(spec_evidence),
            timestamps=suspicious_ts,
        )

    def _score_chunks(
        self, waveform: np.ndarray, sr: int
    ) -> tuple[list[float], list[float]]:
        chunk_len = int(CHUNK_SEC * sr)
        scores, times = [], []
        for start in range(0, len(waveform), chunk_len):
            chunk = waveform[start : start + chunk_len]
            if len(chunk) < sr // 4:
                continue
            if len(chunk) < chunk_len:
                chunk = np.pad(chunk, (0, chunk_len - len(chunk)))
            t = torch.tensor(chunk, dtype=torch.float32).unsqueeze(0).to(self.device)
            with torch.no_grad():
                logits = self.model(t)
                probs = torch.softmax(logits, dim=1)[0]
                # Class 0 = real, Class 1 = fake
                score = probs[1].item() if probs.shape[0] > 1 else torch.sigmoid(logits[0, 0]).item()
            scores.append(float(score))
            times.append(start / sr)
        return scores, times


_global_audio_detector: Optional[AudioDetector] = None

def get_audio_detector(weights_path: Optional[str] = None) -> AudioDetector:
    global _global_audio_detector
    if _global_audio_detector is None or (weights_path and _global_audio_detector.weights_hash == "uninitialised"):
        _global_audio_detector = AudioDetector(weights_path=weights_path)
    return _global_audio_detector


def detect_audio(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_audio_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _load_audio(path: str) -> tuple[np.ndarray, int]:
    try:
        import librosa
        wav, sr = librosa.load(path, sr=TARGET_SR, mono=True)
        return wav.astype(np.float32), sr
    except ImportError:
        pass

    try:
        import soundfile as sf
        wav, sr = sf.read(path, always_2d=False)
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if sr != TARGET_SR:
            from scipy.signal import resample
            wav = resample(wav, int(len(wav) * TARGET_SR / sr)).astype(np.float32)
        return wav.astype(np.float32), TARGET_SR
    except ImportError:
        pass

    raise RuntimeError(
        "Neither librosa nor soundfile is installed. Cannot load audio."
    )


def _spectrogram_evidence(waveform: np.ndarray, sr: int) -> dict:
    try:
        import librosa
        S = np.abs(librosa.stft(waveform))
        db = librosa.amplitude_to_db(S, ref=np.max)
        return {
            "spectral_mean_db": float(db.mean()),
            "spectral_std_db":  float(db.std()),
            "spectral_flatness": float(librosa.feature.spectral_flatness(y=waveform).mean()),
        }
    except Exception:
        return {}


def _aggregate_audio(chunk_scores: list[float]) -> tuple[float, float]:
    if not chunk_scores:
        return 0.5, 0.0
    arr = np.array(chunk_scores)
    score = float(arr.mean())
    confidence = min(abs(score - 0.5) * 2.0, 1.0) * min(len(chunk_scores) / 5.0, 1.0)
    return float(np.clip(score, 0.0, 1.0)), float(confidence)


def _explain_audio(score: float, chunk_scores: list, suspicious: list) -> str:
    if abs(score - 0.5) < 0.15:
        return "Evidence is ambiguous; insufficient confidence to classify audio."
    direction = "synthetic/cloned voice" if score >= 0.5 else "authentic voice"
    parts = [f"Audio classified as {direction} (manipulation likelihood={score:.3f})."]
    if suspicious:
        parts.append(f"{len(suspicious)} suspicious segment(s) detected.")
    return " ".join(parts)


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _unsupported(ext: str, t0: float, wh: str) -> DetectionResult:
    return make_result(
        media_type="audio", score=None, confidence=None,
        model=AUDIO_MODEL_NAME, model_version=AUDIO_MODEL_VERSION,
        weights_hash=wh, latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Unsupported file extension: {ext}",
    )


def _error_result(msg: str, t0: float, wh: str) -> DetectionResult:
    return make_result(
        media_type="audio", score=None, confidence=None,
        model=AUDIO_MODEL_NAME, model_version=AUDIO_MODEL_VERSION,
        weights_hash=wh, latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Error loading audio: {msg}",
    )

