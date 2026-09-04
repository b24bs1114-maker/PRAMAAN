"""Multi-Modal AI-manipulation detector interface (Image, Video, Audio).

The detector is a **replaceable component**. Everything the rest of the backend
touches is the adapter contract:

    result = get_detector(settings).analyse(path, media_type="image | video | audio")
    result.to_dict()  ->  {
        "media_type": "image",
        "label": "ai_manipulation_likelihood",
        "manipulation_score": null | 0.85,
        "confidence": null | 0.92,
        "abstained": true | false,
        "model": "...",
        "model_version": "...",
        "weights_hash": "...",
        "latency_ms": 12.4,
        "explanation": "...",
        "heatmap_available": false,
        "regions": [],
        "timestamps": []
    }

Guarantees enforced by this module:

1. **No invented scores.** If no model is installed for a modality,
   ``manipulation_score`` is ``None``, ``abstained`` is ``True`` and the status
   says why. A missing detector is never reported as 0.0, and never as 0.5.
2. **No invented confidence.** ``confidence`` is ``None`` unless the model itself
   reported one. A number derived from the score is not a confidence.
3. **No crashes.** ``analyse()`` catches everything a model runtime can raise and
   returns an abstained error state.
4. **Truthful unavailability.** ``UNAVAILABLE`` means "this deployment has no
   detector for that modality" -- the signal is missing. ``UNSUPPORTED_MEDIA``
   means "this media type is not something the adapter can analyse at all". An
   unconfigured video model is the former, not the latter: video becomes
   measurable the moment a video detector is plugged in.

Plugging a model in
-------------------
Two sockets, both additive -- no code in this module changes when a model
arrives:

* **Configuration**, for a model shipped as a file plus an inference module::

      PRAMAAN_IMAGE_MODEL_PATH=/models/img.onnx           # optional
      PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT=mypkg.infer:run   # module:callable

* **In-process registration**, for an engine imported directly (and for tests)::

      detector.register_inference("video", run, model_name="x", model_version="1")

The callable receives whichever of these keyword arguments it declares --
``path``, ``media_type``, ``model_path``, ``spec`` -- and returns either a float
score in 0..1 or a mapping::

      {"score": 0.87,              # required; None means "I cannot say"
       "confidence": 0.9,          # optional, model's own confidence
       "model": "...",             # optional identity overrides
       "model_version": "...",
       "weights_hash": "...",      # optional; else sha256 of the model file
       "explanation": "...",       # optional, shown in the analysis UI
       "heatmap_available": false,
       "regions": [...],           # image/video spatial findings
       "timestamps": [...]}        # video/audio temporal findings

Anything else in the mapping is carried through untouched in ``extras``.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import logging
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.config import Settings

logger = logging.getLogger("pramaan.detector")

INTERFACE_VERSION = "2.0"

STATUS_OK = "OK"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_ERROR = "ERROR"
STATUS_UNSUPPORTED = "UNSUPPORTED_MEDIA"

LABEL_AUTHENTIC = "AUTHENTIC"
LABEL_MANIPULATED = "MANIPULATED"
LABEL_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
# Those three are verdict tokens for consumers that render a *fused* verdict.
# Nothing in this module assigns them: turning a score into AUTHENTIC or
# MANIPULATED is fusion's decision, and duplicating the thresholds here would let
# the detector disagree with the case verdict.

SCORE_SEMANTICS = (
    "0.0 = no indication of AI generation or manipulation; 1.0 = strong "
    "indication. The value is a model output, not a probability of guilt."
)

UNAVAILABLE_EXPLANATION = (
    "No AI-manipulation detector is available in this deployment, so no detection "
    "score was produced. This is NOT a finding of authenticity and NOT a finding "
    "of manipulation -- the signal is simply missing and is excluded from fusion."
)

DISABLED_REASON = (
    "Detector disabled by configuration (PRAMAAN_DETECTOR_BACKEND=null); no score "
    "is produced."
)

DECLINED_EXPLANATION = (
    "The detector ran but returned no score for this input, so the AI-detection "
    "signal is missing. This is NOT a finding of authenticity and NOT a finding of "
    "manipulation -- it is excluded from fusion exactly as an uninstalled detector "
    "would be."
)


def clean_confidence(value: Any) -> float | None:
    """The model's own confidence as a 0..1 ratio, or ``None``.

    Never derived from the score: a number computed from the score is a second
    presentation of the same output, not a confidence. A value that is not a
    usable ratio is dropped to ``None`` (unknown) rather than clamped, and it
    never invalidates an otherwise valid score.
    """
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring non-numeric detector confidence %r", value)
        return None
    if not 0.0 <= confidence <= 1.0:
        logger.warning("Ignoring out-of-range detector confidence %r", value)
        return None
    return round(confidence, 4)

# Supported extensions by modality
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

MODALITIES = ("image", "video", "audio")

#: Environment variables that configure each modality, quoted verbatim in the
#: unavailability reasons so an operator is told exactly what to set.
ENV_HINTS: dict[str, dict[str, str]] = {
    "image": {
        "model": "PRAMAAN_IMAGE_MODEL_PATH",
        "entrypoint": "PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT",
    },
    "video": {
        "model": "PRAMAAN_VIDEO_MODEL_PATH",
        "entrypoint": "PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT",
    },
    "audio": {
        "model": "PRAMAAN_AUDIO_MODEL_PATH",
        "entrypoint": "PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT",
    },
}

DEFAULT_SPEC: dict[str, Any] = {
    "input_size": [224, 224],
    "mean": [0.485, 0.456, 0.406],
    "std": [0.229, 0.224, 0.225],
    "layout": "NCHW",
    "positive_index": 1,
    "output_activation": "softmax",
    "model_name": "",
    "model_version": "",
}


# --------------------------------------------------------------------------- #
# Model identity: weights hash
# --------------------------------------------------------------------------- #
_digest_cache: dict[tuple[str, int, int], str] = {}
_digest_lock = threading.Lock()


def weights_digest(model_path: Path | str | None) -> str:
    """SHA-256 of the model file, so a result names the weights that produced it.

    Cached on (path, size, mtime) -- hashing a multi-hundred-megabyte model on
    every inference would dominate the latency the same result reports. Returns
    ``""`` when there is no readable file: an unknown hash is empty, never a
    placeholder that looks like a digest.
    """
    if not model_path:
        return ""
    path = Path(model_path).expanduser()
    try:
        stat = path.stat()
    except OSError:
        return ""
    if not path.is_file():
        return ""
    key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    with _digest_lock:
        cached = _digest_cache.get(key)
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        logger.warning("Could not hash detector weights %s: %s", path, exc)
        return ""
    value = digest.hexdigest()
    with _digest_lock:
        _digest_cache[key] = value
    return value


# --------------------------------------------------------------------------- #
# Plug-in socket: inference callables supplied from outside this module
# --------------------------------------------------------------------------- #
#: modality -> (callable, model_name, model_version)
_registry: dict[str, tuple[Callable[..., Any], str | None, str | None]] = {}
_registry_lock = threading.Lock()


def register_inference(
    modality: str,
    fn: Callable[..., Any],
    *,
    model_name: str | None = None,
    model_version: str | None = None,
) -> None:
    """Install an inference callable for one modality, in this process.

    The alternative to the ``*_DETECTOR_ENTRYPOINT`` settings, for an engine that
    is imported rather than configured. Registration replaces any entrypoint for
    that modality; nothing else in the backend changes.
    """
    key = (modality or "").lower().strip()
    if key not in MODALITIES:
        raise ValueError(f"modality must be one of {MODALITIES}, got {modality!r}")
    with _registry_lock:
        _registry[key] = (fn, model_name, model_version)
    logger.info(
        "Registered %s detector inference: %s (%s v%s)",
        key,
        getattr(fn, "__qualname__", repr(fn)),
        model_name or "unnamed",
        model_version or "unknown",
    )


def unregister_inference(modality: str) -> None:
    with _registry_lock:
        _registry.pop((modality or "").lower().strip(), None)


def clear_inference_registry() -> None:
    with _registry_lock:
        _registry.clear()


def registered_inference(
    modality: str,
) -> tuple[Callable[..., Any], str | None, str | None] | None:
    with _registry_lock:
        return _registry.get((modality or "").lower().strip())


_installed_cache: dict[str, bool] = {}


def _module_installed(name: str) -> bool:
    """Is ``name`` importable, *without* importing it?

    ``available()`` is called on every status probe and on every dashboard
    refresh, and it used to answer "is torch installed?" by importing torch --
    154 MB of RSS (measured) to answer a yes/no question, in a process that may
    never run a model. ``find_spec`` reads the module metadata only. Cached
    because the answer cannot change within a process.
    """
    cached = _installed_cache.get(name)
    if cached is not None:
        return cached
    try:
        found = importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        found = False
    _installed_cache[name] = found
    return found


def load_entrypoint(spec: str) -> tuple[Callable[..., Any] | None, str | None]:
    """Resolve ``"package.module:callable"`` (or dotted path) to a callable.

    Returns ``(None, reason)`` rather than raising: a mis-typed entrypoint must
    surface as an honest UNAVAILABLE, not a 500.
    """
    target = (spec or "").strip()
    if not target:
        return None, "No inference entrypoint configured."
    module_name, _, attribute = target.partition(":")
    if not attribute:
        module_name, _, attribute = target.rpartition(".")
    if not module_name or not attribute:
        return None, (
            f"Detector entrypoint {target!r} is not in 'module:callable' form, so "
            "it could not be resolved."
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - any import problem is a config fact
        return None, (
            f"Detector entrypoint module {module_name!r} could not be imported "
            f"({exc.__class__.__name__}: {exc})."
        )
    fn = getattr(module, attribute, None)
    if fn is None:
        return None, (
            f"Detector entrypoint {target!r} was imported but {module_name!r} has "
            f"no attribute {attribute!r}."
        )
    if not callable(fn):
        return None, f"Detector entrypoint {target!r} is not callable."
    return fn, None


def call_inference(
    fn: Callable[..., Any],
    *,
    path: Path,
    media_type: str,
    model_path: Path | None,
    spec: dict[str, Any],
) -> tuple[Any, float | None, dict[str, Any]]:
    """Invoke a plug-in inference callable and normalise whatever it returns.

    Only the keyword arguments the callable actually declares are passed, so a
    one-argument ``run(path)`` plugs in as readily as a full signature. A returned
    mapping is carried through as-is except for the fields the adapter contract
    names, so a model can report regions, timestamps or its own explanation
    without this module knowing about them in advance. The score and confidence
    are returned unvalidated -- judging them is ``analyse()``'s job.
    """
    try:
        signature: inspect.Signature | None = inspect.signature(fn)
    except (TypeError, ValueError):  # builtins and C callables
        signature = None

    if signature is None:
        result = fn(path)
    else:
        parameters = signature.parameters
        accepts_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()
        )
        optional = {"media_type": media_type, "model_path": model_path, "spec": spec}
        kwargs = {
            name: value
            for name, value in optional.items()
            if accepts_var_kw or name in parameters
        }
        first = next(iter(parameters.values()), None)
        takes_path_positionally = first is not None and first.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        if takes_path_positionally:
            result = fn(path, **kwargs)
        else:
            path_kw = "file_path" if "file_path" in parameters else "path"
            result = fn(**{path_kw: path}, **kwargs)

    # Values are returned exactly as the model gave them. Coercing here would
    # raise inside the plugin call and be reported as a model crash; the score is
    # validated once, in analyse(), and the confidence once, in
    # clean_confidence() -- where an unusable confidence is dropped instead of
    # discarding a perfectly good score with it.
    if isinstance(result, dict):
        extras = {k: v for k, v in result.items() if k not in {"score", "confidence"}}
        score = result.get("score", result.get("manipulation_score"))
        return score, result.get("confidence"), extras
    if isinstance(result, tuple):
        if len(result) == 3:
            score, confidence, extras = result
            return score, confidence, dict(extras or {})
        if len(result) == 2:
            score, extras = result
            return score, None, dict(extras or {})
        raise ValueError(
            f"Detector inference returned a {len(result)}-tuple; expected "
            "(score, extras) or (score, confidence, extras)."
        )
    return result, None, {}


@dataclass
class DetectorResult:
    """Standardized multi-modal detector result contract for Rahul's AI Engine."""

    media_type: str = "image"
    label: str = "ai_manipulation_likelihood"
    manipulation_score: float | None = None
    confidence: float | None = None
    abstained: bool = True
    model: str = "none"
    model_version: str = "0"
    weights_hash: str = ""
    latency_ms: float | None = None
    #: Time spent loading weights into memory, when a load happened on this call.
    #:
    #: Adapters load lazily, so the first ``analyse()`` of a process pays the
    #: checkpoint load. Folding that into ``latency_ms`` reported a multi-second
    #: model load as this file's inference time, which is not a property of the
    #: file. ``None`` means no load occurred on this call (already resident) or
    #: the adapter does not load weights at all.
    model_load_ms: float | None = None
    explanation: str = UNAVAILABLE_EXPLANATION
    heatmap_available: bool = False
    regions: list[dict[str, Any]] = field(default_factory=list)
    timestamps: list[dict[str, Any]] = field(default_factory=list)

    # Legacy fields maintained for 100% backwards compatibility with fusion engine & existing tests
    status: str = STATUS_UNAVAILABLE
    detail: str | None = None
    score_semantics: str = SCORE_SEMANTICS
    interface_version: str = INTERFACE_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float | None:
        return self.manipulation_score

    @property
    def inference_ms(self) -> float | None:
        return self.latency_ms

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = self.manipulation_score
        d["inference_ms"] = self.latency_ms
        return d


class DetectorAdapter(ABC):
    """Base class for modality-specific detector backends."""

    id: str = "abstract"
    modality: str = "generic"
    model_name: str = "none"
    model_version: str = "0"
    model_path: Path | None = None

    @abstractmethod
    def available(self) -> tuple[bool, str | None]:
        """(usable, reason_if_not)"""

    def prepare(self) -> bool:
        """Load weights ahead of inference. Return True if a load happened.

        Adapters load lazily inside ``_infer``, which meant the checkpoint load
        was measured as inference time on the first call of a process. Overriding
        this lets ``analyse()`` time the two separately. The default is a no-op
        for adapters that hold no weights.
        """
        return False

    def _infer(
        self, file_path: Path
    ) -> tuple[Any, float | None, dict[str, Any]] | tuple[Any, dict[str, Any]]:
        """Return (score in 0..1, confidence in 0..1, extras) or (score, extras).

        ``analyse()`` validates the score, so an adapter that wraps foreign code
        may return whatever that code produced rather than pre-checking it.
        """
        raise NotImplementedError

    def weights_hash(self) -> str:
        """SHA-256 of this adapter's weights file, or ``""`` when unknown."""
        return weights_digest(self.model_path)

    def reported_identity(self) -> tuple[str, str, str]:
        """``(model, model_version, weights_hash)`` as this adapter may claim them.

        One source for both ``describe()`` and ``_abstain()``. They disagreed:
        ``describe()`` withheld a name the adapter had no right to while
        ``_abstain()`` stamped it onto every result, so the status endpoint and
        the detection record for the same empty socket named different models.
        """
        return self.model_name, self.model_version, self.weights_hash()

    def describe(self) -> dict[str, Any]:
        usable, reason = self.available()
        model, model_version, digest = self.reported_identity()
        return {
            "adapter": self.id,
            "modality": self.modality,
            "model": model,
            "model_version": model_version,
            "model_path": str(self.model_path) if self.model_path else None,
            "weights_hash": digest,
            "available": usable,
            "reason": reason,
            "interface_version": INTERFACE_VERSION,
            "score_semantics": SCORE_SEMANTICS,
        }

    def _abstain(
        self,
        *,
        media_type: str,
        status: str,
        detail: str,
        latency_ms: float | None = None,
        model_load_ms: float | None = None,
    ) -> DetectorResult:
        """One abstention shape for every reason to abstain.

        ``manipulation_score`` and ``confidence`` are ``None`` -- not 0.0, not
        0.5. An abstention carries the reason, never a number that could be read
        as a measurement.

        The identity comes from ``reported_identity()``, so a socket that cannot
        score anything names no model here either. It used to read
        ``self.model_name``/``self.weights_hash()`` directly: with
        ``PRAMAAN_VIDEO_MODEL_PATH`` pointed at the image checkpoint, every video
        abstention was recorded -- in the analysis record, in the audit row and
        in the PDF's MODEL RECORD -- as coming from "SwinB-AI-Image-Detector"
        with that checkpoint's SHA-256, asserting that an image model had been
        involved in examining a video it never touched.
        """
        model, model_version, digest = self.reported_identity()
        return DetectorResult(
            media_type=media_type,
            label="ai_manipulation_likelihood",
            manipulation_score=None,
            confidence=None,
            abstained=True,
            model=model,
            model_version=model_version,
            weights_hash=digest,
            latency_ms=latency_ms,
            model_load_ms=model_load_ms,
            status=status,
            detail=detail,
            explanation=detail,
        )

    def analyse(self, file_path: Path, *, media_type: str = "image") -> DetectorResult:
        if media_type != "image" and self.modality not in {"video", "audio", "multimodal"}:
            # A genuine media mismatch: this adapter analyses images, and the
            # input is not one. Distinct from "no model installed".
            return self._abstain(
                media_type=media_type,
                status=STATUS_UNSUPPORTED,
                detail=(
                    f"This detector adapter analyses images, so the {media_type} "
                    "input was not analysed. That is a limit of the installed "
                    "detector, not a finding about the file."
                ),
            )

        usable, reason = self.available()
        if not usable:
            detail = reason or UNAVAILABLE_EXPLANATION
            if UNAVAILABLE_EXPLANATION not in detail:
                detail = f"{detail} {UNAVAILABLE_EXPLANATION}"
            return self._abstain(
                media_type=media_type, status=STATUS_UNAVAILABLE, detail=detail
            )

        # Load weights outside the inference timer. Adapters load lazily, so
        # without this the first call of a process reported several seconds of
        # checkpoint load as this file's inference latency.
        load_ms: float | None = None
        load_started = time.perf_counter()
        try:
            if self.prepare():
                load_ms = round((time.perf_counter() - load_started) * 1000, 2)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Detector %s could not load weights: %s: %s",
                self.id,
                exc.__class__.__name__,
                exc,
            )
            return self._abstain(
                media_type=media_type,
                status=STATUS_ERROR,
                detail=f"Detector weights failed to load ({exc.__class__.__name__}).",
                model_load_ms=round((time.perf_counter() - load_started) * 1000, 2),
            )

        started = time.perf_counter()
        try:
            res = self._infer(Path(file_path))
            if len(res) == 2:
                score, extras = res  # type: ignore[misc]
                confidence = None
            else:
                score, confidence, extras = res  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Detector %s failed on %s: %s: %s",
                self.id,
                file_path,
                exc.__class__.__name__,
                exc,
            )
            return self._abstain(
                media_type=media_type,
                status=STATUS_ERROR,
                detail=f"Detector inference failed ({exc.__class__.__name__}).",
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                model_load_ms=load_ms,
            )

        elapsed = round((time.perf_counter() - started) * 1000, 2)
        extras = dict(extras or {})

        if score is None:
            # The model ran and declined to answer. Distinct from a crash and
            # distinct from an uninstalled model, but the same consequence: there
            # is no score, so nothing is invented to stand in for one.
            return self._abstain(
                media_type=media_type,
                status=STATUS_UNAVAILABLE,
                detail=DECLINED_EXPLANATION,
                latency_ms=elapsed,
                model_load_ms=load_ms,
            )

        try:
            score_val = round(float(score), 6)
        except (TypeError, ValueError):
            return self._abstain(
                media_type=media_type,
                status=STATUS_ERROR,
                detail=f"Detector returned a non-numeric score ({score!r}).",
                latency_ms=elapsed,
                model_load_ms=load_ms,
            )

        if not 0.0 <= score_val <= 1.0:
            # Out of range is an error, not something to clamp: clamping would
            # turn a broken model into a plausible-looking measurement.
            return self._abstain(
                media_type=media_type,
                status=STATUS_ERROR,
                detail=f"Detector returned an out-of-range score ({score!r}).",
                latency_ms=elapsed,
                model_load_ms=load_ms,
            )

        model = str(extras.get("model") or self.model_name)
        model_version = str(extras.get("model_version") or self.model_version)
        explanation = str(
            extras.get("explanation")
            or (
                f"Model {model} v{model_version} evaluated this {media_type} and "
                f"returned {score_val} for ai_manipulation_likelihood. "
                f"{SCORE_SEMANTICS}"
            )
        )

        return DetectorResult(
            media_type=media_type,
            label="ai_manipulation_likelihood",
            manipulation_score=score_val,
            # No fallback: a confidence the model did not report is unknown. A
            # value derived from the score would be a second, invented output.
            confidence=clean_confidence(confidence),
            abstained=False,
            model=model,
            model_version=model_version,
            weights_hash=str(extras.get("weights_hash") or self.weights_hash()),
            latency_ms=elapsed,
            model_load_ms=load_ms,
            explanation=explanation,
            heatmap_available=bool(extras.get("heatmap_available", False)),
            regions=list(extras.get("regions") or []),
            timestamps=list(extras.get("timestamps") or []),
            status=STATUS_OK,
            extras=extras,
        )


# --------------------------------------------------------------------------- #
# Inference admission control
# --------------------------------------------------------------------------- #
#: A single Swin-B forward pass costs ~26 MB of activations, and a Grad-CAM pass
#: ~206 MB, on top of 347 MB of resident weights (all measured -- see
#: DETECTOR_DEPLOYMENT.md). Two analyses arriving together therefore add their
#: peaks together, which is how a process that survives one request gets killed
#: by the second. Inference is admitted through a bounded semaphore so the peak
#: is a function of the configured limit rather than of client behaviour.
#:
#: A semaphore, not a job queue: the API contract is synchronous and the
#: measurements do not show a need for anything more. Waiters are bounded by
#: ``detector_queue_timeout_s`` so a backlog abstains instead of holding the
#: worker until the proxy gives up on it.
_inference_semaphore: threading.BoundedSemaphore | None = None
_inference_semaphore_limit = 0
_semaphore_lock = threading.Lock()

QUEUE_TIMEOUT_EXPLANATION = (
    "The detector was busy with another analysis and this request timed out "
    "waiting for it, so no score was produced. This is NOT a finding of "
    "authenticity and NOT a finding of manipulation -- the signal is missing and "
    "is excluded from fusion. Retrying will produce a score."
)


def _semaphore_for(limit: int) -> threading.BoundedSemaphore:
    global _inference_semaphore, _inference_semaphore_limit
    with _semaphore_lock:
        if _inference_semaphore is None or _inference_semaphore_limit != limit:
            _inference_semaphore = threading.BoundedSemaphore(limit)
            _inference_semaphore_limit = limit
        return _inference_semaphore


class InferenceBusy(RuntimeError):
    """Raised when the bounded inference slot could not be acquired in time."""


class _inference_slot:
    """Context manager admitting one inference at a time (per configured limit)."""

    def __init__(self, limit: int, timeout: float) -> None:
        self._limit = max(1, int(limit or 1))
        self._timeout = max(0.0, float(timeout or 0.0))
        self._semaphore: threading.BoundedSemaphore | None = None

    def __enter__(self) -> None:
        semaphore = _semaphore_for(self._limit)
        if not semaphore.acquire(timeout=self._timeout):
            raise InferenceBusy(
                f"No detector slot became available within {self._timeout:g}s "
                f"(concurrency limit {self._limit})."
            )
        self._semaphore = semaphore

    def __exit__(self, *exc_info: Any) -> None:
        if self._semaphore is not None:
            self._semaphore.release()
            self._semaphore = None


class NullDetector(DetectorAdapter):
    """Abstains. Used whenever no model is configured or installed for a modality."""

    id = "null"

    def __init__(self, modality: str = "generic", reason: str | None = None) -> None:
        self.modality = modality
        self.model_name = "none"
        self.model_version = "0"
        self._reason = reason or UNAVAILABLE_EXPLANATION

    def available(self) -> tuple[bool, str | None]:
        return False, self._reason


# --------------------------------------------------------------------------- #
# Helper functions for preprocessing/postprocessing
# --------------------------------------------------------------------------- #
def load_spec(model_path: Path) -> dict[str, Any]:
    spec = dict(DEFAULT_SPEC)
    sidecar = model_path.with_suffix(model_path.suffix + ".json")
    if not sidecar.is_file():
        sidecar = model_path.with_suffix(".json")
    if sidecar.is_file():
        try:
            spec.update(json.loads(sidecar.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring unreadable detector sidecar %s: %s", sidecar, exc)
    return spec


def preprocess(image_path: Path, spec: dict[str, Any]) -> np.ndarray:
    from PIL import Image

    width, height = spec["input_size"]
    with Image.open(image_path) as handle:
        image = handle.convert("RGB").resize(
            (int(width), int(height)), Image.Resampling.BILINEAR
        )
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - np.asarray(spec["mean"], dtype=np.float32)) / np.asarray(
        spec["std"], dtype=np.float32
    )
    if str(spec.get("layout", "NCHW")).upper() == "NCHW":
        array = array.transpose(2, 0, 1)
    return np.ascontiguousarray(array[None, ...], dtype=np.float32)


def postprocess(outputs: np.ndarray, spec: dict[str, Any]) -> float:
    values = np.asarray(outputs, dtype=np.float64).reshape(-1)
    activation = str(spec.get("output_activation", "softmax")).lower()
    if values.size == 1:
        value = float(values[0])
        if activation == "sigmoid":
            return float(1.0 / (1.0 + np.exp(-value)))
        return min(max(value, 0.0), 1.0)
    if activation == "softmax":
        shifted = values - values.max()
        probabilities = np.exp(shifted) / np.exp(shifted).sum()
    elif activation == "sigmoid":
        probabilities = 1.0 / (1.0 + np.exp(-values))
    else:
        probabilities = values
    index = int(spec.get("positive_index", 1))
    index = index if 0 <= index < probabilities.size else probabilities.size - 1
    return float(probabilities[index])


# --------------------------------------------------------------------------- #
# Image Detector Adapters
# --------------------------------------------------------------------------- #
class OnnxDetector(DetectorAdapter):
    """Runs a local pretrained ONNX classifier through ONNX Runtime."""

    id = "onnxruntime"
    modality = "image"

    def __init__(self, model_path: str) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        self._session: Any | None = None
        self._spec: dict[str, Any] = dict(DEFAULT_SPEC)
        if self.model_path is not None and self.model_path.is_file():
            self._spec = load_spec(self.model_path)
        self.model_name = self._spec.get("model_name") or (
            self.model_path.name if self.model_path else "onnx-model"
        )
        self.model_version = str(self._spec.get("model_version") or "unknown")

    def available(self) -> tuple[bool, str | None]:
        if self.model_path is None or not str(self.model_path):
            return False, (
                "No detector model configured (set PRAMAAN_DETECTOR_MODEL_PATH to a "
                "local pretrained .onnx file)."
            )
        if not self.model_path.is_file():
            return False, f"Configured detector model was not found: {self.model_path}"
        if self.model_path.suffix.lower() != ".onnx":
            return False, (
                f"{self.model_path.name} is not an .onnx file; this adapter loads "
                "ONNX models."
            )
        if not _module_installed("onnxruntime"):
            return False, (
                "onnxruntime is not installed in this environment, so the ONNX "
                "detector cannot run."
            )
        return True, None

    def prepare(self) -> bool:
        already_resident = self._session is not None
        self._load()
        return not already_resident

    def _load(self) -> Any:
        if self._session is None:
            import onnxruntime

            self._session = onnxruntime.InferenceSession(
                str(self.model_path), providers=["CPUExecutionProvider"]
            )
            logger.info("Loaded ONNX detector model %s", self.model_path)
        return self._session

    def _infer(self, image_path: Path) -> tuple[float, dict[str, Any]]:
        session = self._load()
        tensor = preprocess(image_path, self._spec)
        input_name = session.get_inputs()[0].name
        outputs = session.run(None, {input_name: tensor})
        score = postprocess(outputs[0], self._spec)
        return score, {
            "runtime": "onnxruntime",
            "model_path": str(self.model_path),
            "input_size": self._spec["input_size"],
        }


class TorchScriptDetector(DetectorAdapter):
    """Runs a local pretrained TorchScript classifier through PyTorch."""

    id = "torchscript"
    modality = "image"

    def __init__(self, model_path: str) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        self._module: Any | None = None
        self._spec: dict[str, Any] = dict(DEFAULT_SPEC)
        if self.model_path is not None and self.model_path.is_file():
            self._spec = load_spec(self.model_path)
        self.model_name = self._spec.get("model_name") or (
            self.model_path.name if self.model_path else "torchscript-model"
        )
        self.model_version = str(self._spec.get("model_version") or "unknown")

    def available(self) -> tuple[bool, str | None]:
        if self.model_path is None or not str(self.model_path):
            return False, (
                "No detector model configured (set PRAMAAN_DETECTOR_MODEL_PATH to a "
                "local pretrained TorchScript .pt file)."
            )
        if not self.model_path.is_file():
            return False, f"Configured detector model was not found: {self.model_path}"
        if self.model_path.suffix.lower() not in {".pt", ".pth", ".torchscript"}:
            return False, (
                f"{self.model_path.name} is not a TorchScript file; this adapter "
                "loads .pt/.pth models."
            )
        if not _module_installed("torch"):
            return False, (
                "torch is not installed in this environment, so the TorchScript "
                "detector cannot run."
            )
        return True, None

    def prepare(self) -> bool:
        already_resident = self._module is not None
        self._load()
        return not already_resident

    def _load(self) -> Any:
        if self._module is None:
            import torch

            self._module = torch.jit.load(str(self.model_path), map_location="cpu")
            self._module.eval()
            logger.info("Loaded TorchScript detector model %s", self.model_path)
        return self._module

    def _infer(self, image_path: Path) -> tuple[float, dict[str, Any]]:
        import torch

        module = self._load()
        tensor = torch.from_numpy(preprocess(image_path, self._spec))
        with torch.no_grad():
            outputs = module(tensor)
        score = postprocess(outputs.detach().cpu().numpy(), self._spec)
        return score, {
            "runtime": f"torch {torch.__version__}",
            "model_path": str(self.model_path),
            "input_size": self._spec["input_size"],
        }


# --------------------------------------------------------------------------- #
# Plug-in adapter (video, audio, and any model supplied as an entrypoint)
# --------------------------------------------------------------------------- #
class PluginDetector(DetectorAdapter):
    """Runs an inference callable supplied from outside this module.

    This adapter contains no model of its own and never produces a score by
    itself. With nothing installed it abstains and says which of the two sockets
    is empty -- a configured model file with no inference code to run it is
    reported as exactly that, not silently treated as a working detector.
    """

    id = "plugin"
    modality = "generic"

    def __init__(
        self,
        modality: str,
        *,
        model_path: str | None = None,
        entrypoint: str | None = None,
        adapter_id: str | None = None,
    ) -> None:
        self.modality = modality
        self.id = adapter_id or f"{modality}-plugin"
        self.entrypoint = (entrypoint or "").strip()
        self.model_path = Path(model_path).expanduser() if model_path else None
        self._spec: dict[str, Any] = dict(DEFAULT_SPEC)
        if self.model_path is not None and self.model_path.is_file():
            self._spec = load_spec(self.model_path)

        registered = registered_inference(modality)
        spec_name = self._spec.get("model_name")
        self.model_name = str(
            (registered[1] if registered else None)
            or spec_name
            or (self.model_path.name if self.model_path else "none")
        )
        self.model_version = str(
            (registered[2] if registered else None)
            or self._spec.get("model_version")
            or ("unknown" if (registered or self.entrypoint) else "0")
        )

    # -- resolution ---------------------------------------------------------- #
    def _hints(self) -> dict[str, str]:
        return ENV_HINTS.get(self.modality, {"model": "model path", "entrypoint": "entrypoint"})

    def _inference(self) -> tuple[Callable[..., Any] | None, str | None]:
        """The installed inference callable, or a precise reason there is none."""
        registered = registered_inference(self.modality)
        if registered is not None:
            return registered[0], None
        if self.entrypoint:
            return load_entrypoint(self.entrypoint)
        hints = self._hints()
        if self.model_path is not None and self.model_path.is_file():
            return None, (
                f"A {self.modality} model file is configured "
                f"({self.model_path.name}) but no inference code is installed to "
                f"run it: set {hints['entrypoint']} to 'module:callable'. No "
                f"{self.modality} score can be produced until then."
            )
        return None, (
            f"No {self.modality} detector is installed in this deployment: "
            f"neither {hints['model']} nor {hints['entrypoint']} is configured."
        )

    def available(self) -> tuple[bool, str | None]:
        if self.model_path is not None and str(self.model_path) and not self.model_path.is_file():
            return False, (
                f"The configured {self.modality} detector model was not found: "
                f"{self.model_path}"
            )
        fn, reason = self._inference()
        if fn is None:
            return False, reason
        return self._readiness(fn)

    def reported_identity(self) -> tuple[str, str, str]:
        """As ``DetectorAdapter.reported_identity``, minus any borrowed identity.

        ``model_name``, ``model_version`` and ``weights_hash`` are all derived
        from whatever file sits at ``model_path`` -- via its sidecar spec for the
        first two and its bytes for the third. When that file is not one this
        plug-in can use, reporting them names the wrong model: the video slot
        advertised "SwinB-AI-Image-Detector" 3.0.0 and the image checkpoint's
        digest while being unable to score a single frame. An unusable socket
        names no model, no version and no weights.

        ``model_path`` is deliberately *not* blanked by ``describe()``: it states
        what this deployment configured, and hiding a video slot pointed at an
        image checkpoint would hide the misconfiguration itself.
        """
        usable, _ = self.available()
        if not usable:
            return "none", "", ""
        return super().reported_identity()

    def _readiness(self, fn: Callable[..., Any]) -> tuple[bool, str | None]:
        """Ask the plug-in whether its checkpoint can actually produce a score.

        A file that exists and a module that imports are not the same thing as a
        working detector: the video plug-in was reported as available while
        holding an image checkpoint it cannot use, so the dashboard showed a
        deepfake detector that abstained on every request. A plug-in may export
        ``checkpoint_readiness(model_path) -> (bool, reason)`` to answer that;
        one that does not is treated as ready, exactly as before.

        The hook is advisory. It must not load the model -- it runs on every
        status probe -- and if it raises, that is not evidence about the
        checkpoint, so the adapter stays available and the loader reports the
        problem per request.
        """
        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        hook = getattr(module, "checkpoint_readiness", None)
        if not callable(hook):
            return True, None
        try:
            usable, reason = hook(str(self.model_path) if self.model_path else None)
        except Exception:
            return True, None
        if usable:
            return True, None
        return False, reason or (
            f"The configured {self.modality} detector cannot produce a score."
        )

    def prepare(self) -> bool:
        """Warm the plug-in's checkpoint, if the plug-in offers a way to.

        The inference callable lives outside this module and loads its own
        weights, so the only honest way to time that load separately is to ask
        the plug-in to do it first. A plug-in may export
        ``load_checkpoint(model_path) -> bool`` returning True when the call
        actually loaded weights (rather than finding them already resident). A
        plug-in that exports no such hook is left alone and the load stays
        inside the inference measurement, as before -- an unmeasurable split is
        reported as ``model_load_ms=None``, never as a guess.
        """
        fn, _ = self._inference()
        if fn is None:
            return False
        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        hook = getattr(module, "load_checkpoint", None)
        if not callable(hook):
            return False
        return bool(hook(str(self.model_path) if self.model_path else None))

    def _infer(self, file_path: Path) -> tuple[Any, Any, dict[str, Any]]:
        fn, reason = self._inference()
        if fn is None:  # pragma: no cover - available() gates this
            raise RuntimeError(reason or "No inference callable is installed.")
        score, confidence, extras = call_inference(
            fn,
            path=Path(file_path),
            media_type=self.modality,
            model_path=self.model_path,
            spec=self._spec,
        )
        extras.setdefault(
            "runtime", f"{self.modality}-plugin:{getattr(fn, '__qualname__', 'callable')}"
        )
        if self.model_path is not None:
            extras.setdefault("model_path", str(self.model_path))
        return score, confidence, extras


class VideoDetector(PluginDetector):
    """Adapter for a video deepfake / manipulation detector."""

    id = "video_classifier"

    def __init__(self, model_path: str | None = None, entrypoint: str | None = None) -> None:
        super().__init__("video", model_path=model_path, entrypoint=entrypoint,
                         adapter_id="video_classifier")


class AudioDetector(PluginDetector):
    """Adapter for an audio voice-clone / synthetic-speech detector."""

    id = "audio_classifier"

    def __init__(self, model_path: str | None = None, entrypoint: str | None = None) -> None:
        super().__init__("audio", model_path=model_path, entrypoint=entrypoint,
                         adapter_id="audio_classifier")


class RemoteDetector(DetectorAdapter):
    """Forwards inference to a separate detector service.

    Exists because the model does not fit in the API container: Swin-B is 347 MB
    of fp32 weights and the torch/transformers runtime is another ~355 MB, so a
    512 MB instance cannot host it however carefully the loading is done (the
    measurements are in DETECTOR_DEPLOYMENT.md). Moving the model out is the
    only way to keep the API process small *and* run the real detector.

    Nothing about the contract changes: the remote service returns the same
    payload ``analyse()`` would have produced locally, and this adapter passes it
    through. It never substitutes a score of its own -- a service that is down,
    slow or malformed is an unavailable signal, reported as an abstention exactly
    like an uninstalled model.
    """

    id = "remote"
    modality = "multimodal"

    def __init__(self, url: str, *, timeout: float = 120.0, token: str = "") -> None:
        self.url = (url or "").strip()
        self.timeout = max(1.0, float(timeout or 1.0))
        self._token = (token or "").strip()
        self.model_name = "remote"
        self.model_version = "unknown"
        self.model_path = None

    def available(self) -> tuple[bool, str | None]:
        if not self.url:
            return False, (
                "No remote detector service is configured (set "
                "PRAMAAN_DETECTOR_REMOTE_URL to the /detect endpoint of a "
                "detector service)."
            )
        if not self.url.lower().startswith(("http://", "https://")):
            return False, (
                f"PRAMAAN_DETECTOR_REMOTE_URL ({self.url!r}) is not an http(s) URL."
            )
        return True, None

    def weights_hash(self) -> str:
        # The weights live on the remote host; its response carries their hash.
        return ""

    def _post(self, file_path: Path, media_type: str) -> dict[str, Any]:
        """Multipart POST of one file, using only the standard library."""
        import mimetypes
        import urllib.error
        import urllib.request
        import uuid as _uuid

        boundary = f"----pramaan{_uuid.uuid4().hex}"
        mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        prologue = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="media_type"\r\n\r\n{media_type}\r\n'
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        epilogue = f"\r\n--{boundary}--\r\n".encode()
        body = prologue + file_path.read_bytes() + epilogue

        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(
                f"Remote detector returned {type(payload).__name__}, expected a JSON object."
            )
        return payload

    def _infer(self, file_path: Path) -> tuple[Any, Any, dict[str, Any]]:
        media_type = "image"
        suffix = file_path.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            media_type = "video"
        elif suffix in AUDIO_EXTENSIONS:
            media_type = "audio"

        payload = self._post(file_path, media_type)
        extras = {
            key: value
            for key, value in payload.items()
            if key not in {"score", "manipulation_score", "confidence"}
        }
        extras.setdefault("runtime", f"remote:{self.url}")
        return (
            payload.get("manipulation_score", payload.get("score")),
            payload.get("confidence"),
            extras,
        )


class ImageDetector(DetectorAdapter):
    """Composite adapter for image AI-manipulation detectors.

    Three sockets, tried in order of specificity: an inference callable installed
    by Rahul (registry or entrypoint), a local ONNX model, a local TorchScript
    model. Whichever is usable is selected; when none is, this adapter abstains
    and reports every reason so an operator can see all three.
    """

    id = "image_classifier"
    modality = "image"

    def __init__(self, model_path: str | None = None, entrypoint: str | None = None) -> None:
        self.model_path = Path(model_path).expanduser() if model_path else None
        self.plugin = PluginDetector(
            "image", model_path=model_path, entrypoint=entrypoint, adapter_id="image-plugin"
        )
        self.onnx = OnnxDetector(model_path or "")
        self.torch = TorchScriptDetector(model_path or "")
        self.candidates: tuple[DetectorAdapter, ...] = (self.plugin, self.onnx, self.torch)
        self.active: DetectorAdapter = self._active() or self.onnx

    def _active(self) -> DetectorAdapter | None:
        """First usable backend, re-resolved on each call.

        Not cached: a model can be registered after this object was constructed
        (``register_inference``) or a weights file dropped into place, and a stale
        "nothing installed" answer would keep reporting UNAVAILABLE for a
        detector that is now present.
        """
        for adapter in self.candidates:
            usable, _ = adapter.available()
            if usable:
                return adapter
        return None

    @property
    def model_name(self) -> str:  # type: ignore[override]
        active = self._active()
        return str(getattr(active, "model_name", "none")) if active else "none"

    @property
    def model_version(self) -> str:  # type: ignore[override]
        active = self._active()
        return str(getattr(active, "model_version", "0")) if active else "0"

    def weights_hash(self) -> str:
        active = self._active()
        return active.weights_hash() if active else weights_digest(self.model_path)

    def available(self) -> tuple[bool, str | None]:
        reasons: list[str] = []
        for adapter in self.candidates:
            usable, reason = adapter.available()
            if usable:
                return True, None
            reasons.append(f"{adapter.id}: {reason}")
        return False, "; ".join(reasons)

    def prepare(self) -> bool:
        """Delegate to whichever backend will run, so its load is timed as a load."""
        active = self._active()
        if active is None:
            return False
        self.active = active
        return active.prepare()

    def _infer(
        self, file_path: Path
    ) -> tuple[float, float | None, dict[str, Any]] | tuple[float, dict[str, Any]]:
        active = self._active()
        if active is None:  # pragma: no cover - available() gates this
            raise RuntimeError("No image detector backend is installed.")
        self.active = active
        return active._infer(file_path)


# --------------------------------------------------------------------------- #
# Multi-modal dispatcher
# --------------------------------------------------------------------------- #
#: Media-type tokens accepted from callers, mapped to the canonical modality.
#: Callers pass ``evidence.media_type``, a form field, or a query string, and all
#: three should route the same way.
MEDIA_ALIASES: dict[str, str] = {
    "image": "image",
    "img": "image",
    "photo": "image",
    "picture": "image",
    "still": "image",
    "video": "video",
    "vid": "video",
    "movie": "video",
    "clip": "video",
    "audio": "audio",
    "aud": "audio",
    "sound": "audio",
    "speech": "audio",
    "voice": "audio",
}


class MultiModalDetectorService(DetectorAdapter):
    """Routes one file to the adapter for its modality.

    The dispatcher decides *where* a file goes; it never decides what the answer
    is. Each modality adapter abstains for itself, so the abstention carries that
    adapter's own model identity, weights hash and precise reason instead of a
    generic "unavailable".
    """

    id = "multimodal_dispatcher"
    modality = "multimodal"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.image_detector = ImageDetector(
            settings.image_model_path or settings.detector_model_path,
            entrypoint=getattr(settings, "image_detector_entrypoint", "") or None,
        )
        self.video_detector = VideoDetector(
            settings.video_model_path or None,
            entrypoint=getattr(settings, "video_detector_entrypoint", "") or None,
        )
        self.audio_detector = AudioDetector(
            settings.audio_model_path or None,
            entrypoint=getattr(settings, "audio_detector_entrypoint", "") or None,
        )
        self.adapters: dict[str, DetectorAdapter] = {
            "image": self.image_detector,
            "video": self.video_detector,
            "audio": self.audio_detector,
        }

    def available(self) -> tuple[bool, str | None]:
        """Usable when *any* modality has a detector installed.

        The per-modality reasons are all reported when none does, because "no
        image model" and "no audio model" are different operational facts and
        collapsing them would hide one of them.
        """
        reasons: list[str] = []
        for name, adapter in self.adapters.items():
            usable, reason = adapter.available()
            if usable:
                return True, None
            reasons.append(f"{name}: {reason}")
        return False, "; ".join(reasons)

    def modality_availability(self) -> dict[str, dict[str, Any]]:
        """Per-modality usability, for status reporting."""
        return {name: adapter.describe() for name, adapter in self.adapters.items()}

    def resolve_modality(
        self, media_type: str | None = None, file_path: Path | None = None
    ) -> str | None:
        """Canonical modality for this input, or ``None`` if it is not one of the
        three this interface covers.

        The declared media type wins; the extension is only a fallback, because
        the ingestion layer already decided the type from magic bytes and a
        filename is client-supplied.
        """
        token = (media_type or "").lower().strip()
        if token in MEDIA_ALIASES:
            return MEDIA_ALIASES[token]
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix in IMAGE_EXTENSIONS:
            return "image"
        if suffix in VIDEO_EXTENSIONS:
            return "video"
        if suffix in AUDIO_EXTENSIONS:
            return "audio"
        return None

    def get_adapter_for(
        self, media_type: str, file_path: Path | None = None
    ) -> DetectorAdapter:
        """The adapter that would handle this input.

        Unrecognised media falls back to the image adapter so callers always get a
        handle. ``analyse()`` does not depend on that fallback -- it refuses
        unrecognised media outright rather than handing it to a detector that
        cannot read it.
        """
        modality = self.resolve_modality(media_type, file_path)
        return self.adapters.get(modality or "image", self.image_detector)

    def analyse(self, file_path: Path, *, media_type: str = "image") -> DetectorResult:
        if self.settings.detector_backend == "null":
            return NullDetector("generic", DISABLED_REASON).analyse(
                file_path, media_type=media_type
            )

        path = Path(file_path)
        modality = self.resolve_modality(media_type, path)
        if modality is None:
            # Outside image/video/audio entirely: no detector in this interface
            # claims to read it. A limit of the interface, not a finding.
            return self._abstain(
                media_type=media_type or "unknown",
                status=STATUS_UNSUPPORTED,
                detail=(
                    f"'{media_type or Path(file_path).suffix or 'unknown'}' is not "
                    "one of the media types this detector interface covers (image, "
                    "video, audio), so no detection was attempted. That is a limit "
                    "of the interface, not a finding about the file."
                ),
            )

        adapter = self.adapters[modality]
        try:
            with _inference_slot(
                getattr(self.settings, "detector_max_concurrency", 1),
                getattr(self.settings, "detector_queue_timeout_s", 120.0),
            ):
                result = adapter.analyse(path, media_type=modality)
        except InferenceBusy as exc:
            logger.warning("Detector admission control rejected a request: %s", exc)
            # Abstain rather than 503: the caller asked what this deployment can
            # measure about the file, and the honest answer is "nothing, right
            # now" -- carried in the same shape as every other missing signal so
            # fusion excludes it instead of scoring it.
            return self._abstain(
                media_type=modality,
                status=STATUS_UNAVAILABLE,
                detail=f"{exc} {QUEUE_TIMEOUT_EXPLANATION}",
            )
        # Record which socket actually handled the file: the caller sees
        # "multimodal_dispatcher" as the adapter id, and that alone would not say
        # whether the image, video or audio detector produced this.
        result.extras.setdefault("routed_modality", modality)
        result.extras.setdefault("routed_adapter", adapter.id)
        return result


def build_detector(settings: Settings) -> DetectorAdapter:
    """Construct the detector for these settings, without touching the singleton."""
    if settings.detector_backend == "null":
        return NullDetector("generic", DISABLED_REASON)
    remote_url = getattr(settings, "detector_remote_url", "") or ""
    if remote_url.strip():
        token = getattr(settings, "detector_remote_token", None)
        return RemoteDetector(
            remote_url,
            timeout=getattr(settings, "detector_remote_timeout_s", 120.0),
            token=token.get_secret_value() if token is not None else "",
        )
    return MultiModalDetectorService(settings)


# --------------------------------------------------------------------------- #
# Process Singleton Management
# --------------------------------------------------------------------------- #
_instance: DetectorAdapter | None = None
_lock = threading.Lock()


def get_detector(settings: Settings) -> DetectorAdapter:
    """The process-wide detector. Built once; replaceable via ``set_detector``."""
    global _instance
    with _lock:
        if _instance is None:
            if not settings.enable_ai_detector or settings.detector_backend == "null":
                reason = (
                    "AI detector disabled for demo/stability mode"
                    if not settings.enable_ai_detector
                    else DISABLED_REASON
                )
                _instance = NullDetector("generic", reason)
            else:
                _instance = build_detector(settings)
        return _instance


_status_cache: tuple[float, dict[str, Any]] | None = None
_status_cache_lock = threading.Lock()
_STATUS_CACHE_TTL = 10.0


def clear_status_cache() -> None:
    global _status_cache
    with _status_cache_lock:
        _status_cache = None


def set_detector(adapter: Any | None) -> None:
    global _instance
    with _lock:
        _instance = adapter
    clear_status_cache()


def reset_detector_singleton() -> None:
    set_detector(None)


def _configured_candidates(settings: Settings) -> list[DetectorAdapter]:
    """Every image socket this configuration could load, in precedence order.

    Built from the supplied settings rather than from the live singleton, so the
    report answers "what would this configuration load?" -- which is what an
    operator checking a model path needs to know.
    """
    model_path = settings.image_model_path or settings.detector_model_path
    entrypoint = getattr(settings, "image_detector_entrypoint", "") or None
    return [
        PluginDetector(
            "image", model_path=model_path, entrypoint=entrypoint, adapter_id="image-plugin"
        ),
        OnnxDetector(model_path),
        TorchScriptDetector(model_path),
    ]


def prewarm(settings: Settings) -> str:
    """Load the model now instead of on the first analysis. Never raises.

    Explicitly opt-in (``PRAMAAN_DETECTOR_PREWARM``). It trades startup time for
    first-request latency, and on a platform whose health check has a short
    deadline that trade can cost the deployment, so the default is to load
    lazily. Returns a human-readable outcome for the startup log.
    """
    if not settings.enable_ai_detector or settings.detector_backend == "null":
        return "skipped (detector disabled)"
    detector = get_detector(settings)
    if isinstance(detector, RemoteDetector):
        return "skipped (inference is remote; nothing to load locally)"
    usable, reason = detector.available()
    if not usable:
        return f"skipped (no detector installed: {reason})"
    entrypoint = getattr(settings, "image_detector_entrypoint", "") or ""
    if not entrypoint:
        return "skipped (no image entrypoint configured)"
    fn, load_reason = load_entrypoint(entrypoint)
    if fn is None:
        return f"failed ({load_reason})"
    return f"imported {entrypoint}; weights load on first inference"


def status(settings: Settings) -> dict[str, Any]:
    """Report what the detector layer can actually do right now.

    ``available`` refers to the detector that will run, not to the configuration:
    a configured model that cannot be loaded is unavailable, and the reason says
    which of the sockets failed and why.
    """
    global _status_cache
    now = time.monotonic()
    with _status_cache_lock:
        if _status_cache is not None:
            cached_time, cached_val = _status_cache
            if now - cached_time < _STATUS_CACHE_TTL:
                return cached_val

    if not settings.enable_ai_detector:
        reason = "AI detector disabled for demo/stability mode"
        disabled_detail = f"{reason} {UNAVAILABLE_EXPLANATION}"
        res = {
            "adapter": "null",
            "model": "none",
            "model_version": "0",
            "interface_version": INTERFACE_VERSION,
            "score_semantics": SCORE_SEMANTICS,
            "available": False,
            "reason": disabled_detail,
            "unavailable_because": reason,
            "interpretation": UNAVAILABLE_EXPLANATION,
            "configured_backend": settings.detector_backend,
            "configured_model_path": None,
            "image_model_path": None,
            "video_model_path": None,
            "audio_model_path": None,
            "entrypoints": {name: None for name in MODALITIES},
            "registered_inference": {name: None for name in MODALITIES},
            "candidate_adapters": [],
            "modalities": {
                name: {
                    "adapter": "null",
                    "modality": name,
                    "available": False,
                    "reason": reason,
                }
                for name in MODALITIES
            },
            "notes": (
                "AI detector is disabled for demo/stability mode, so AI-detection results are "
                "reported as UNAVAILABLE and excluded from fusion. Missing detection "
                "is not evidence of authenticity."
            ),
        }
        with _status_cache_lock:
            _status_cache = (now, res)
        return res
    detector = get_detector(settings)
    usable, reason = detector.available()

    # An unavailable multi-modal dispatcher reports itself as "null": from a
    # consumer's point of view nothing is installed, and naming the dispatcher
    # would suggest a working detector.
    active_id = str(getattr(detector, "id", "null"))
    if not usable and active_id == "multimodal_dispatcher":
        active_id = "null"

    image_adapter = getattr(detector, "image_detector", None)
    identity_source = image_adapter if image_adapter is not None else detector

    modalities: dict[str, Any] = {}
    for name in MODALITIES:
        adapter = getattr(detector, f"{name}_detector", None)
        if adapter is None and getattr(detector, "modality", "") in (name, "generic"):
            # A single-modality adapter was injected in place of the dispatcher.
            adapter = detector
        if adapter is not None:
            modalities[name] = adapter.describe()
        else:
            modalities[name] = {
                "adapter": active_id,
                "modality": name,
                "available": False,
                "reason": (
                    f"The active detector ({active_id}) does not handle {name} media."
                ),
            }

    registered = {
        name: (
            {
                "callable": getattr(entry[0], "__qualname__", repr(entry[0])),
                "model": entry[1],
                "model_version": entry[2],
            }
            if (entry := registered_inference(name)) is not None
            else None
        )
        for name in MODALITIES
    }

    # The operational reason (which socket failed) and the forensic
    # interpretation (what a missing signal means) are both reported, and
    # ``reason`` carries both -- the same composition ``analyse()`` uses for an
    # abstention detail, so a caller that only renders ``reason`` still shows the
    # caveat rather than an unqualified "no detector".
    detail: str | None = None
    if not usable:
        detail = f"{reason} {UNAVAILABLE_EXPLANATION}" if reason else UNAVAILABLE_EXPLANATION

    result = {
        "adapter": active_id,
        "model": str(getattr(identity_source, "model_name", "none")),
        "model_version": str(getattr(identity_source, "model_version", "0")),
        "interface_version": INTERFACE_VERSION,
        "score_semantics": SCORE_SEMANTICS,
        "available": usable,
        "reason": detail,
        "unavailable_because": reason if not usable else None,
        "interpretation": UNAVAILABLE_EXPLANATION if not usable else None,
        "configured_backend": settings.detector_backend,
        "configured_model_path": settings.detector_model_path or None,
        "image_model_path": settings.image_model_path or settings.detector_model_path or None,
        "video_model_path": settings.video_model_path or None,
        "audio_model_path": settings.audio_model_path or None,
        "entrypoints": {
            name: getattr(settings, f"{name}_detector_entrypoint", "") or None
            for name in MODALITIES
        },
        "registered_inference": registered,
        # Skipped when inference is remote: the local sockets are not what would
        # run, and probing them would report "no model here" as if it mattered.
        "candidate_adapters": (
            []
            if isinstance(detector, RemoteDetector)
            else [c.describe() for c in _configured_candidates(settings)]
        ),
        "modalities": modalities,
        "notes": None if usable else (
            "No pretrained detector is installed, so AI-detection results are "
            "reported as UNAVAILABLE and excluded from fusion. Missing detection "
            "is not evidence of authenticity."
        ),
    }
    # This assignment used to sit after the ``return`` above, so nothing was
    # ever cached on the enabled path: every status call -- and the dashboard
    # calls it, and so does every analysis -- rebuilt three candidate adapters,
    # re-read the model sidecar and re-resolved availability. Caching it is the
    # difference between a status probe costing nothing and costing model file
    # I/O on every dashboard refresh.
    with _status_cache_lock:
        _status_cache = (now, result)
    return result


__all__ = [
    "AUDIO_EXTENSIONS",
    "AudioDetector",
    "DECLINED_EXPLANATION",
    "DISABLED_REASON",
    "DetectorAdapter",
    "DetectorResult",
    "IMAGE_EXTENSIONS",
    "INTERFACE_VERSION",
    "ImageDetector",
    "LABEL_AUTHENTIC",
    "LABEL_INSUFFICIENT_EVIDENCE",
    "LABEL_MANIPULATED",
    "MODALITIES",
    "MultiModalDetectorService",
    "NullDetector",
    "InferenceBusy",
    "OnnxDetector",
    "PluginDetector",
    "QUEUE_TIMEOUT_EXPLANATION",
    "RemoteDetector",
    "SCORE_SEMANTICS",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_UNAVAILABLE",
    "STATUS_UNSUPPORTED",
    "TorchScriptDetector",
    "UNAVAILABLE_EXPLANATION",
    "VIDEO_EXTENSIONS",
    "VideoDetector",
    "build_detector",
    "clean_confidence",
    "clear_inference_registry",
    "get_detector",
    "load_entrypoint",
    "prewarm",
    "register_inference",
    "registered_inference",
    "reset_detector_singleton",
    "set_detector",
    "status",
    "unregister_inference",
    "weights_digest",
]
