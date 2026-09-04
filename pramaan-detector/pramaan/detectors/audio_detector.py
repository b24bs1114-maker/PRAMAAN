"""
PRAMAAN Audio Detector
======================
Backbone : garystafford/wav2vec2-deepfake-voice-detector (Wav2Vec2 fine-tuned for deepfake voice detection)
Task     : Binary — Real voice (0) vs Fake/Cloned voice (1)
Evidence : Spectrogram STFT statistics + suspicious window extraction
Supports : WAV, MP3, M4A/AAC

Loading notes (measured)
------------------------
The local checkpoint is the complete fine-tune: 315.7 M fp32 parameters over 426
tensors, which is the whole 1.26 GB file and not optimizer state. It matches the
hub model key-for-key (0 missing, 0 unexpected).

The architecture is therefore built from the model *config* alone, and every
tensor comes from the local checkpoint. Calling ``from_pretrained`` first --
which is what this module used to do -- downloaded 1.26 GB of hub weights only
to overwrite all 426 of them, needed network access or a warm HF cache to get
the architecture at all, and pushed peak RSS to 2.9 GB (measured).

There is no untrained fallback. A previous ``except Exception`` branch swapped in
a randomly-initialised CNN while the result still reported
``Wav2Vec2-GaryStafford-DeepfakeVoiceDetector`` and the real checkpoint's hash,
so a machine with no cached hub config would have produced confident,
meaningless scores under the name of a real model. A checkpoint that cannot be
loaded is now an abstention.
"""
from __future__ import annotations
import hashlib, threading, time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from pramaan.detectors.image_detector import _load_torch
from pramaan.schema import make_result, DetectionResult

AUDIO_MODEL_VERSION = "2.0.0"
AUDIO_MODEL_NAME = "Wav2Vec2-GaryStafford-DeepfakeVoiceDetector"
SUPPORTED_EXTS = {".wav", ".mp3", ".m4a", ".aac"}
TARGET_SR = 16000          # wav2vec2 expects 16 kHz
CHUNK_SEC = 3.0            # analyse in 3-second windows
MIN_DURATION = 0.5         # seconds

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "audio_detector.pt"
_HF_MODEL_NAME = "garystafford/wav2vec2-deepfake-voice-detector"

#: The class index whose softmax probability is reported as the manipulation
#: score. The upstream config declares ``id2label {0: "real", 1: "fake"}`` and
#: ``weights/audio_detector.pt.json`` declares ``positive_index: 1`` to match.
#: Both are checked at load time rather than assumed: reading ``probs[1]`` from a
#: checkpoint whose config labels index 1 "real" would invert every verdict --
#: authentic speech reported as a clone and a clone reported as authentic -- with
#: nothing in the output to show that it had happened.
POSITIVE_INDEX = 1

#: Substrings that identify a config label as the synthetic/manipulated class.
_FAKE_LABEL_MARKERS = ("fake", "spoof", "synthetic", "clone", "deepfake", "generated")

#: Parameter-name fragments a checkpoint must contain to be usable here. These are
#: ``Wav2Vec2ForSequenceClassification``'s three parts: the wav2vec2 encoder, the
#: projection layer, and the classification head.
_REQUIRED_PARAM_FRAGMENTS = (b"wav2vec2.", b"projector.", b"classifier.")

#: What "ready" means for this detector, in one place, because three separate
#: things must hold and only the first is obvious:
#:
#: 1. ``audio_detector.pt`` is present and holds wav2vec2 sequence-classifier
#:    parameters (checked by ``checkpoint_readiness`` without loading them);
#: 2. the architecture ``config.json`` for ``_HF_MODEL_NAME`` is reachable -- it
#:    is fetched from the hub unless ``PRAMAAN_DETECTOR_OFFLINE`` is set, in which
#:    case it must already be in the local Hugging Face cache;
#: 3. that config's ``id2label`` agrees with :data:`POSITIVE_INDEX`.
#:
#: Readiness is not a claim about accuracy. This detector's direction and label
#: mapping are verified; its error rate on Indian-language speech, on telephony
#: codecs, or on any generator not in its training set is unmeasured here. See
#: ``docs/AUDIO_READINESS.md``.
READINESS_CONTRACT = (
    "ready = checkpoint present with wav2vec2 sequence-classifier parameters, "
    "architecture config reachable (cached when offline), and config id2label "
    "consistent with positive_index=1. Readiness is not a claim of accuracy."
)

#: Peak resident memory to build and hold this model, measured on this
#: checkpoint: ~1.67 GB. Recorded here because it is the number that decides
#: whether a deployment can run audio at all, and a 512 MB instance cannot.
PEAK_MEMORY_BYTES = 1_670_000_000

NO_TRAINED_MODEL_EXPLANATION = (
    "No trained audio detector could be loaded, so no voice-manipulation score "
    "was produced. Set PRAMAAN_AUDIO_MODEL_PATH to the audio_detector.pt "
    "checkpoint. This is NOT a finding of authenticity and NOT a finding of "
    "manipulation -- the signal is missing and is excluded from fusion."
)


def _fake_index(id2label: dict) -> Optional[int]:
    """The index the config says is the synthetic class, or ``None`` if unclear."""
    matches = [
        int(index)
        for index, label in id2label.items()
        if any(marker in str(label).lower() for marker in _FAKE_LABEL_MARKERS)
    ]
    return matches[0] if len(matches) == 1 else None


def verify_label_direction(id2label: Optional[dict]) -> None:
    """Raise unless ``id2label`` agrees that :data:`POSITIVE_INDEX` is synthetic.

    A config with no usable labels is left alone: the upstream model publishes
    them, but a locally fine-tuned checkpoint might not, and refusing to run over
    a missing annotation would be stricter than the evidence warrants. A config
    that *contradicts* the assumed direction is a hard failure -- it means the
    score this module would report is the probability of the wrong class.
    """
    if not id2label:
        return
    index = _fake_index(dict(id2label))
    if index is None or index == POSITIVE_INDEX:
        return
    raise RuntimeError(
        f"checkpoint config maps index {index} to the synthetic class and "
        f"index {POSITIVE_INDEX} to something else (id2label={dict(id2label)!r}), "
        f"but this module reports index {POSITIVE_INDEX} as the manipulation "
        f"score. Loading it would invert every verdict"
    )


def _build_wav2vec2(checkpoint: Path) -> tuple[Any, str]:
    """Build the classifier and fill it from ``checkpoint``. Raises on failure.

    The config is read from the local HF cache if present and otherwise from the
    hub; only ``config.json`` is ever fetched, never weights. ``assign=True``
    adopts the checkpoint's storages rather than copying them into freshly
    initialised parameters, so the process never holds two full copies.
    """
    torch = _load_torch()
    from transformers import AutoConfig, AutoModelForAudioClassification

    config = AutoConfig.from_pretrained(_HF_MODEL_NAME)
    # Before any tensor is read: the config states which class index means
    # "fake", and this module reports index POSITIVE_INDEX as the manipulation
    # score. If those disagree the score is the probability of the wrong class,
    # so the load fails instead of producing inverted verdicts.
    verify_label_direction(getattr(config, "id2label", None))
    with torch.device("meta"):
        model = AutoModelForAudioClassification.from_config(config)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    try:
        report = model.load_state_dict(state, strict=False, assign=True)
        if report.missing_keys:
            raise RuntimeError(
                f"checkpoint is missing {len(report.missing_keys)} parameters, "
                f"e.g. {report.missing_keys[0]}; those would stay on the meta "
                "device and every forward pass would fail"
            )
    finally:
        del state
    return model, "checkpoint"


class AudioForensicNet:
    """Wav2Vec2 fine-tuned for deepfake voice classification.

    A thin wrapper rather than an ``nn.Module`` subclass so that importing this
    module does not import torch: the backend's status probe imports every
    configured entrypoint just to ask whether a detector exists, and torch costs
    ~370 MB of RSS to answer that question.
    """

    def __init__(self, weights_path: Optional[str] = None):
        self.hf_model: Any = None
        self.load_strategy = "none"
        self.unavailable_reason = NO_TRAINED_MODEL_EXPLANATION

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not resolved_path.exists():
            self.load_strategy = "no-checkpoint"
            return
        try:
            self.hf_model, self.load_strategy = _build_wav2vec2(resolved_path)
        except Exception as exc:
            self.load_strategy = f"checkpoint-unloadable:{resolved_path.name}"
            self.unavailable_reason = (
                f"The configured audio checkpoint {resolved_path.name} could not be "
                f"loaded ({exc.__class__.__name__}: {exc}). "
                f"{NO_TRAINED_MODEL_EXPLANATION}"
            )
            return
        self.unavailable_reason = ""

    @property
    def usable(self) -> bool:
        return self.hf_model is not None

    def to(self, device: Any) -> "AudioForensicNet":
        if self.hf_model is not None:
            self.hf_model.to(device)
        return self

    def eval(self) -> "AudioForensicNet":
        if self.hf_model is not None:
            self.hf_model.eval()
            for parameter in self.hf_model.parameters():
                parameter.requires_grad_(False)
        return self

    def __call__(self, waveform: Any) -> Any:
        """waveform: (batch, samples) at 16 kHz"""
        if self.hf_model is None:  # pragma: no cover - detect() gates this
            raise RuntimeError(self.unavailable_reason)
        return self.hf_model(waveform).logits   # (B, 2)

    forward = __call__


class AudioDetector:
    """
    Audio Deepfake / Voice Clone Detector.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.model  = AudioForensicNet(weights_path=weights_path)
        self.model.to(self.device).eval()
        self.load_strategy = self.model.load_strategy
        self.usable = self.model.usable
        self.unavailable_reason = self.model.unavailable_reason

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        # The hash names the bytes a report is derived from. With no loaded
        # checkpoint there are none, so it must not name a file that was read
        # and rejected.
        if self.usable and resolved_path.exists():
            self.weights_hash = _file_hash(str(resolved_path))
        else:
            self.weights_hash = "none"

    def detect(self, audio_path: str) -> DetectionResult:
        t0   = time.perf_counter()
        path = Path(audio_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, t0, self.weights_hash)

        if not self.usable:
            # Abstain before decoding: there is nothing to score the waveform
            # with, and loading it would spend the time anyway.
            return make_result(
                media_type="audio", score=None, confidence=None,
                model=AUDIO_MODEL_NAME, model_version=AUDIO_MODEL_VERSION,
                weights_hash=self.weights_hash,
                latency_ms=(time.perf_counter()-t0)*1000,
                explanation=self.unavailable_reason,
                evidence={"load_strategy": self.load_strategy,
                          "trained_model_loaded": False},
            )

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
                "load_strategy": self.load_strategy,
                "trained_model_loaded": True,
                **spec_evidence,
            },
            heatmap_available=bool(spec_evidence),
            timestamps=suspicious_ts,
        )

    def _score_chunks(
        self, waveform: np.ndarray, sr: int
    ) -> tuple[list[float], list[float]]:
        torch = _load_torch()
        chunk_len = int(CHUNK_SEC * sr)
        scores, times = [], []
        for start in range(0, len(waveform), chunk_len):
            chunk = waveform[start : start + chunk_len]
            if len(chunk) < sr // 4:
                continue
            if len(chunk) < chunk_len:
                chunk = np.pad(chunk, (0, chunk_len - len(chunk)))
            t = torch.tensor(chunk, dtype=torch.float32).unsqueeze(0).to(self.device)
            try:
                with torch.inference_mode():
                    logits = self.model(t)
                    probs = torch.softmax(logits, dim=1)[0]
                    # POSITIVE_INDEX, not a literal 1: the index is verified
                    # against the checkpoint config's id2label at load time, and
                    # naming it here keeps the verified value and the value that
                    # is actually read from drifting apart.
                    score = (
                        probs[POSITIVE_INDEX].item() if probs.shape[0] > 1
                        else torch.sigmoid(logits[0, 0]).item()
                    )
            finally:
                # A long file otherwise keeps every 3-second activation set
                # reachable until the loop ends.
                del t
            scores.append(float(score))
            times.append(start / sr)
        return scores, times


_global_audio_detector: Optional[AudioDetector] = None
_audio_lock = threading.Lock()


def get_audio_detector(weights_path: Optional[str] = None) -> AudioDetector:
    """The process-wide audio detector. Built at most once per worker.

    Double-checked under a lock: without it two concurrent analyses each build
    their own 1.26 GB model.
    """
    global _global_audio_detector
    if _global_audio_detector is not None:
        return _global_audio_detector
    with _audio_lock:
        if _global_audio_detector is None:
            _global_audio_detector = AudioDetector(weights_path=weights_path)
        return _global_audio_detector


def release_audio_detector() -> None:
    """Drop the loaded model. Used by tests and by shutdown paths."""
    global _global_audio_detector
    with _audio_lock:
        _global_audio_detector = None


def load_checkpoint(model_path: Optional[str] = None) -> bool:
    """Build the detector now, so the caller can time the load separately.

    Returns True when this call actually loaded the checkpoint, False when the
    model was already resident. See the identical hook in ``image_detector``:
    the wav2vec2 load is the larger of the two, so folding it into the inference
    timer misreported it by whole seconds on the first call of a process.
    """
    was_cold = _global_audio_detector is None
    get_audio_detector(weights_path=str(model_path) if model_path else None)
    return was_cold


def detect_audio(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_audio_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _offline() -> bool:
    """Whether this process is forbidden from reaching a model hub."""
    import os

    return any(
        os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("PRAMAAN_DETECTOR_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    )


def _cached_config_paths() -> list[Path]:
    """Local Hugging Face cache locations of ``_HF_MODEL_NAME``'s ``config.json``.

    Globbed on the filesystem rather than resolved through ``huggingface_hub``:
    this runs on every status poll, and importing the hub client (which imports
    ``requests`` and, transitively for ``transformers``, torch) to answer "is a
    4 KB json file on disk" is what this module exists to avoid.
    """
    import os

    roots = [
        os.getenv("HF_HUB_CACHE", "").strip(),
        os.path.join(os.getenv("HF_HOME", "").strip(), "hub") if os.getenv("HF_HOME", "").strip() else "",
        os.path.expanduser("~/.cache/huggingface/hub"),
    ]
    slug = "models--" + _HF_MODEL_NAME.replace("/", "--")
    found: list[Path] = []
    for root in roots:
        if not root:
            continue
        base = Path(root) / slug / "snapshots"
        if not base.is_dir():
            continue
        found.extend(sorted(base.glob("*/config.json")))
    return found


def checkpoint_readiness(model_path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Can this module ever produce an audio score with ``model_path``?

    Read by the backend's status probe. Without this hook the probe reports a
    detector as available whenever the configured file exists and this module
    imports -- and both are true of a truncated download, of the *image*
    checkpoint, and of a machine with no cached architecture config -- so the
    dashboard advertised a working voice-clone detector that abstained on every
    request. See :data:`READINESS_CONTRACT` for what is being asserted, and what
    is not: this establishes that the model can run, never that it is accurate.

    Kept cheap, because status is polled: the checkpoint is not loaded, torch and
    transformers are not imported, and the parameter names are read from the
    ``data.pkl`` member of the zip (a few hundred KB) rather than from the 1.26 GB
    of tensors beside it.
    """
    resolved = Path(model_path) if model_path else _DEFAULT_WEIGHTS_PATH
    if not resolved.exists():
        return False, NO_TRAINED_MODEL_EXPLANATION

    import zipfile
    try:
        with zipfile.ZipFile(resolved) as archive:
            pickles = [n for n in archive.namelist() if n.endswith("data.pkl")]
            if not pickles:
                return False, (
                    f"{resolved.name} is not a torch.save archive, so its "
                    f"parameter names cannot be read without loading 1.26 GB and "
                    f"readiness cannot be established. {NO_TRAINED_MODEL_EXPLANATION}"
                )
            blob = archive.read(pickles[0])
    except Exception as exc:
        return False, (
            f"{resolved.name} could not be read as a checkpoint archive "
            f"({exc.__class__.__name__}), so readiness could not be established. "
            f"{NO_TRAINED_MODEL_EXPLANATION}"
        )

    # An allowlist, not a denylist: the checkpoint must contain the parameters
    # this architecture needs. A denylist passes every wrong checkpoint nobody
    # has thought to exclude -- which is how the video slot came to accept this
    # very file.
    missing = [f.decode() for f in _REQUIRED_PARAM_FRAGMENTS if f not in blob]
    if missing:
        return False, (
            f"{resolved.name} does not hold weights for a wav2vec2 sequence "
            f"classifier: it declares no {' or '.join(missing)} parameters. "
            f"{NO_TRAINED_MODEL_EXPLANATION}"
        )

    # The architecture itself comes from the hub's config.json. Online that is a
    # fetch; offline it has to already be cached, and if it is not, the load
    # fails at the first request rather than here -- which is exactly the
    # available-then-always-abstains state this hook exists to prevent.
    if _offline() and not _cached_config_paths():
        return False, (
            f"the architecture config for {_HF_MODEL_NAME} is not in the local "
            f"Hugging Face cache and this process is configured offline "
            f"(PRAMAAN_DETECTOR_OFFLINE), so the model cannot be built. Warm the "
            f"cache during the build, or unset the offline flag. "
            f"{NO_TRAINED_MODEL_EXPLANATION}"
        )
    return True, None


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
    """The checkpoint's full SHA-256.

    Not truncated: the backend's ``weights_digest`` and the weights manifest both
    record all 64 characters, so a 16-char prefix here named the same file
    differently in the same report and could not be checked against either.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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

