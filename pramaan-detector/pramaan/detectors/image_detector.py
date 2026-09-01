"""
PRAMAAN Image Detector
======================
Backbone : Swin-B Transformer (SwinForImageClassification)
Task     : Binary — Real Photo (1) vs AI-Generated / Manipulated Image (0)
Heatmap  : Grad-CAM on Swin encoder final block (7x7 patch grid interpolated to 224x224)
Supports : JPG, PNG, WEBP

Memory notes (measured)
-----------------------
The model is 86.7 M fp32 parameters = 347 MB of weights. Three things in the
original implementation multiplied that figure, and all three are avoided here:

1. ``torch.load()`` of the whole checkpoint (+331 MB) *while* a freshly
   random-initialised model was also allocated (+336 MB) and then overwritten.
   Peak model-load RSS was 1052 MB for a 347 MB model. The loader below prefers
   a memory-mapped safetensors directory (measured peak 396 MB) and falls back
   to ``mmap=True`` + ``assign=True`` (measured peak 721 MB), which never holds
   two copies of the weights.

2. Grad-CAM implemented as a full ``backward()``, which populates ``.grad`` on
   every one of the 86.7 M parameters -- another 347 MB that is never released
   for the lifetime of the process. The CAM only needs the gradient of one
   logit with respect to one hooked activation, so ``torch.autograd.grad`` is
   used instead: same heatmap, no parameter gradient buffers.

3. Importing torch / torchvision / transformers at module import time (+369 MB
   RSS) even when the caller only wanted to ask whether a detector is
   installed. Every heavy import here is deferred to first use.

Scores are unchanged by all of this: the same checkpoint through the same
architecture produces bit-identical logits on every path above.
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

logger = logging.getLogger(__name__)

MODEL_VERSION = "3.0.0"
MODEL_NAME = "SwinB-AI-Image-Detector"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_MEAN = [0.485, 0.456, 0.406]
_STD = [0.229, 0.224, 0.225]
_INPUT_SIZE = (224, 224)

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "image_detector.pt"

#: Grad-CAM costs an extra ~206 MB of peak RSS (measured) because the forward
#: graph must be retained for the backward pass. The score does not depend on
#: it, so a memory-constrained deployment can turn the heatmap off and still get
#: the same number -- with ``heatmap_available: false`` reported honestly rather
#: than an empty heatmap presented as a real one.
_HEATMAP_ENV = "PRAMAAN_DETECTOR_HEATMAP"
#: Threads for CPU inference. Left at torch's default (one per core) a single
#: forward pass will saturate every core it can see, which on a shared instance
#: starves the event loop that has to answer /health.
_THREADS_ENV = "PRAMAAN_TORCH_THREADS"


def heatmap_enabled() -> bool:
    return os.getenv(_HEATMAP_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


# --------------------------------------------------------------------------- #
# Lazy heavy imports
# --------------------------------------------------------------------------- #
_torch: Any = None
_import_lock = threading.Lock()


def _load_torch() -> Any:
    """Import torch once, and cap its thread pool before any op runs."""
    global _torch
    if _torch is None:
        with _import_lock:
            if _torch is None:
                import torch

                try:
                    threads = int(os.getenv(_THREADS_ENV, "1"))
                except ValueError:
                    threads = 1
                if threads > 0:
                    torch.set_num_threads(threads)
                _torch = torch
    return _torch


class _LazyTransform:
    """``_transform(img)`` with torchvision imported on first call.

    Kept as a module-level callable because ``video_detector`` imports this name
    directly; turning it into a function would break that import.
    """

    def __init__(self) -> None:
        self._compose: Any = None

    def _build(self) -> Any:
        if self._compose is None:
            _load_torch()
            import torchvision.transforms as T

            self._compose = T.Compose(
                [T.Resize(_INPUT_SIZE), T.ToTensor(), T.Normalize(_MEAN, _STD)]
            )
        return self._compose

    def __call__(self, image: Any) -> Any:
        return self._build()(image)


_transform = _LazyTransform()


class ImageForensicNet:
    """EfficientNet-B0 fine-tuned for video frame forgery detection (2 classes).

    Defined lazily via ``__new__`` so that importing this module does not import
    torch. The real ``nn.Module`` subclass is built on first instantiation.
    """

    def __new__(cls, pretrained: bool = False):  # type: ignore[misc]
        return _image_forensic_net_class()(pretrained=pretrained)


_ifn_class: Any = None


def _image_forensic_net_class() -> Any:
    global _ifn_class
    if _ifn_class is not None:
        return _ifn_class
    torch = _load_torch()
    import torch.nn as nn

    class _ImageForensicNet(nn.Module):
        """EfficientNet-B0 fine-tuned for video frame forgery detection (2 classes)."""

        def __init__(self, pretrained: bool = False):
            super().__init__()
            from torchvision.models import EfficientNet_B0_Weights, efficientnet_b0

            weights = EfficientNet_B0_Weights.DEFAULT if pretrained else None
            base = efficientnet_b0(weights=weights)
            self.features = base.features
            self.avgpool = base.avgpool
            in_features = base.classifier[1].in_features
            self.classifier = nn.Sequential(base.classifier[0], nn.Linear(in_features, 2))

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # noqa: F821
            feat = self.features(x)
            pooled = self.avgpool(feat).flatten(1)
            return self.classifier(pooled)

    _ifn_class = _ImageForensicNet
    return _ifn_class


def _extract_regions(cam, threshold: float = 0.5) -> list[dict]:
    """Return bounding boxes of high-activation regions."""
    try:
        import cv2
        import numpy as np

        h, w = cam.shape
        mask = (cam > threshold).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions = []
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            regions.append(
                {
                    "x": int(x / w * 100),
                    "y": int(y / h * 100),
                    "w": int(bw / w * 100),
                    "h": int(bh / h * 100),
                    "activation": float(cam[y : y + bh, x : x + bw].mean()),
                }
            )
        return sorted(regions, key=lambda r: -r["activation"])[:5]
    except ImportError:
        return []


# --------------------------------------------------------------------------- #
# Checkpoint loading
# --------------------------------------------------------------------------- #
def _stamp_matches(candidate: Path, checkpoint: Path) -> bool:
    """Whether ``candidate`` was derived from *this* checkpoint.

    ``scripts/convert_detector_weights.py`` writes ``source.json`` recording the
    size and SHA-256 of the ``.pt`` it converted. Without this check a converted
    directory left behind by a previous checkpoint would silently win over the
    checkpoint actually configured, and every verdict would come from weights
    nobody asked for -- with the loader reporting the new file's hash.

    A directory with no stamp (hand-provisioned, or a plain HF snapshot) is
    accepted: there is nothing to contradict. A stamp that names different bytes
    is rejected.
    """
    stamp_path = candidate / "source.json"
    if not stamp_path.is_file():
        return True
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    if not isinstance(stamp, dict):
        return True

    declared_size = stamp.get("size_bytes")
    if declared_size is not None and checkpoint.stat().st_size != declared_size:
        return False
    declared_digest = str(stamp.get("sha256") or "")
    if declared_digest:
        # _file_hash returns the first 16 hex chars and is cached on
        # (path, size, mtime), and the caller hashes this checkpoint anyway,
        # so this costs one read at most.
        if _file_hash(str(checkpoint)) != declared_digest[:16]:
            return False
    return True


def _safetensors_dir(checkpoint: Path) -> Path | None:
    """A converted HF directory for this checkpoint, if one was provisioned.

    ``scripts/convert_detector_weights.py`` writes ``<stem>_hf/`` next to the
    ``.pt``. Loading from it is the cheapest path by a wide margin (measured
    peak 396 MB vs 1052 MB) because safetensors is memory-mapped and the model
    is built on the meta device, so the weights are never copied.

    A directory whose ``source.json`` names a different checkpoint is skipped,
    so the ``.pt`` that was configured is always the one that gets loaded.
    """
    override = os.getenv("PRAMAAN_IMAGE_MODEL_HF_DIR", "").strip()
    candidates = []
    if override:
        candidates.append(Path(override).expanduser())
    candidates.append(checkpoint.with_name(f"{checkpoint.stem}_hf"))
    for candidate in candidates:
        if not (
            (candidate / "model.safetensors").is_file()
            and (candidate / "config.json").is_file()
        ):
            continue
        if not _stamp_matches(candidate, checkpoint):
            logger.warning(
                "Ignoring %s: source.json does not match %s; "
                "loading the checkpoint directly.",
                candidate,
                checkpoint.name,
            )
            continue
        return candidate
    return None


def _load_swin(checkpoint: Path) -> tuple[Any, str]:
    """Load the Swin classifier from ``checkpoint``, cheapest strategy first.

    Returns ``(model, strategy_name)``. Every strategy produces the same
    parameters, so the strategy affects memory and load time only.
    """
    torch = _load_torch()
    from transformers import SwinConfig, SwinForImageClassification

    hf_dir = _safetensors_dir(checkpoint)
    if hf_dir is not None:
        model = SwinForImageClassification.from_pretrained(hf_dir, local_files_only=True)
        return model, f"safetensors-mmap:{hf_dir.name}"

    # Memory-map the checkpoint and *adopt* its storages instead of copying
    # them into freshly initialised parameters. Without assign=True the process
    # holds the checkpoint and the model at the same time.
    try:
        saved = torch.load(checkpoint, map_location="cpu", mmap=True, weights_only=False)
        strategy = "torch-mmap-assign"
    except (TypeError, RuntimeError, ValueError):
        # mmap needs a zipfile-serialised checkpoint and torch >= 2.1.
        saved = torch.load(checkpoint, map_location="cpu", weights_only=False)
        strategy = "torch-load-copy"

    if isinstance(saved, dict) and "config" in saved and "state_dict" in saved:
        config = SwinConfig(**saved["config"])
        model = SwinForImageClassification(config)
        model.load_state_dict(saved["state_dict"], assign=strategy == "torch-mmap-assign")
    elif isinstance(saved, dict) and "state_dict" in saved:
        config = SwinConfig.from_pretrained("umm-maybe/AI-image-detector")
        model = SwinForImageClassification(config)
        model.load_state_dict(saved["state_dict"], assign=strategy == "torch-mmap-assign")
    else:
        model = SwinForImageClassification.from_pretrained("umm-maybe/AI-image-detector")
        strategy = "hub-pretrained"

    del saved
    return model, strategy


class ImageDetector:
    """
    Image Deepfake & AI-Generated Image Detector using Swin-B Transformer.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.weights_hash = "uninitialised"
        self.load_strategy = "none"

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if resolved_path.exists():
            self.model, self.load_strategy = _load_swin(resolved_path)
            self.weights_hash = _file_hash(str(resolved_path))
        else:
            # Pretrained fallback
            from transformers import SwinForImageClassification

            self.model = SwinForImageClassification.from_pretrained("umm-maybe/AI-image-detector")
            self.load_strategy = "hub-pretrained"
            self.weights_hash = "pretrained-fallback"

        self.model.to(self.device).eval()
        # Inference only: nothing here ever trains, and leaving requires_grad on
        # is what lets a stray backward pass allocate 347 MB of .grad buffers.
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)
        self._last_block = self.model.swin.encoder.layers[-1].blocks[-1]

    # -- scoring ----------------------------------------------------------- #
    def _score(self, tensor: Any) -> float:
        """Manipulation score with no autograd graph built at all."""
        torch = _load_torch()
        with torch.inference_mode():
            logits = self.model(tensor).logits
            # id2label: {0: 'artificial', 1: 'human'} -- score is P(artificial).
            return torch.softmax(logits, dim=-1)[0, 0].item()

    def _score_and_cam(self, tensor: Any) -> tuple[float, Any]:
        """Manipulation score plus the Grad-CAM activation map.

        The gradient is taken of one logit with respect to one hooked
        activation via ``torch.autograd.grad``. The original implementation
        called ``backward()``, which walks all the way back to the inputs and
        leaves a ``.grad`` tensor on all 86.7 M parameters (+347 MB, retained
        for the process lifetime). This produces the identical CAM without
        allocating any of them.
        """
        torch = _load_torch()
        import numpy as np

        activation: Any = None

        def forward_hook(module, inputs, output):
            nonlocal activation
            activation = output[0] if isinstance(output, tuple) else output
            if activation.requires_grad:
                activation.retain_grad()

        handle = self._last_block.register_forward_hook(forward_hook)
        try:
            with torch.enable_grad():
                # The parameters are frozen (requires_grad=False) so that no
                # backward pass can ever allocate .grad buffers for them. A graph
                # is still needed to reach the hooked activation, and requiring
                # grad on the input is what creates one. autograd.grad stops at
                # the activation, so no input gradient is actually computed.
                tensor = tensor.detach().requires_grad_(True)
                logits = self.model(tensor).logits
                score = torch.softmax(logits, dim=-1)[0, 0].item()
                if activation is None or not activation.requires_grad:
                    gradient = None
                else:
                    (gradient,) = torch.autograd.grad(logits[0, 0], activation)
        finally:
            handle.remove()

        if activation is not None and gradient is not None:
            act = activation.detach()
            weights = gradient.mean(dim=(1, 2), keepdim=True)
            cam_1d = torch.relu((weights * act).sum(dim=-1).squeeze(0)).cpu().numpy()
            del gradient, act
            s = int(np.sqrt(cam_1d.shape[0])) if cam_1d.ndim == 1 else 7
            cam_grid = cam_1d[: s * s].reshape(s, s)
            denom = cam_grid.max() - cam_grid.min() + 1e-8
            cam_norm = (cam_grid - cam_grid.min()) / denom
        else:
            cam_norm = np.zeros((7, 7), dtype=np.float32)

        # Release the retained graph before the CAM is upsampled.
        del activation, logits

        try:
            import cv2

            cam_224 = cv2.resize(cam_norm, (224, 224), interpolation=cv2.INTER_LINEAR)
        except ImportError:
            cam_224 = np.repeat(np.repeat(cam_norm, 32, axis=0), 32, axis=1)[:224, :224]

        return score, cam_224

    # -- public API -------------------------------------------------------- #
    def detect(self, image_path: str) -> Any:
        from pramaan.schema import make_result

        t0 = time.perf_counter()
        path = Path(image_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, "image", t0)

        from PIL import Image

        try:
            with Image.open(path) as handle:
                img = handle.convert("RGB")
        except Exception as exc:
            return _error_result("image", str(exc), t0)

        tensor = _transform(img).unsqueeze(0).to(self.device)
        # The decoded full-resolution image is dead once the 224x224 tensor
        # exists; a 24 MP JPEG is ~72 MB of RGB that would otherwise stay alive
        # for the whole inference.
        img.close()
        del img

        want_heatmap = heatmap_enabled()
        try:
            if want_heatmap:
                score, cam = self._score_and_cam(tensor)
            else:
                score, cam = self._score(tensor), None
        finally:
            del tensor

        confidence = min(abs(score - 0.5) * 2.0, 1.0)
        regions = _extract_regions(cam) if cam is not None else []
        latency_ms = (time.perf_counter() - t0) * 1000

        explanation = _explain_image(score, regions)
        evidence: dict[str, Any] = {"raw_score": score, "load_strategy": self.load_strategy}
        if cam is not None:
            evidence["cam_shape"] = list(cam.shape)
        else:
            evidence["heatmap"] = (
                f"Grad-CAM disabled by configuration ({_HEATMAP_ENV}=0); the "
                "manipulation score is unaffected."
            )
        del cam

        return make_result(
            media_type="image",
            score=score,
            confidence=confidence,
            model=MODEL_NAME,
            model_version=MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=latency_ms,
            explanation=explanation,
            evidence=evidence,
            heatmap_available=want_heatmap,
            regions=regions,
        )

    def get_heatmap(self, image_path: str):
        path = Path(image_path)
        if path.suffix.lower() not in SUPPORTED_EXTS:
            return None
        from PIL import Image

        with Image.open(path) as handle:
            img = handle.convert("RGB")
        tensor = _transform(img).unsqueeze(0).to(self.device)
        img.close()
        try:
            _, cam = self._score_and_cam(tensor)
        finally:
            del tensor
        return cam


_global_detector: Optional[ImageDetector] = None
_detector_lock = threading.Lock()


def get_image_detector(weights_path: Optional[str] = None) -> ImageDetector:
    """The process-wide detector. Built at most once per worker.

    Double-checked under a lock: two concurrent analyses previously raced here
    and each built its own 347 MB model.
    """
    global _global_detector
    if _global_detector is not None:
        return _global_detector
    with _detector_lock:
        if _global_detector is None:
            _global_detector = ImageDetector(weights_path=weights_path)
        return _global_detector


def release_image_detector() -> None:
    """Drop the loaded model. Used by tests and by shutdown paths."""
    global _global_detector
    with _detector_lock:
        _global_detector = None


def detect_image(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_image_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


_hash_cache: dict[tuple[str, int, int], str] = {}
_hash_lock = threading.Lock()


def _file_hash(path: str) -> str:
    """First 16 hex chars of the file's SHA-256, cached on (path, size, mtime).

    A 347 MB read per model construction is cheap once and wasteful thereafter.
    """
    try:
        stat = os.stat(path)
        key = (str(path), int(stat.st_size), int(stat.st_mtime_ns))
    except OSError:
        key = (str(path), -1, -1)
    with _hash_lock:
        cached = _hash_cache.get(key)
    if cached is not None:
        return cached
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    value = h.hexdigest()[:16]
    with _hash_lock:
        _hash_cache[key] = value
    return value


def _explain_image(score: float, regions: list) -> str:
    if abs(score - 0.5) < 0.15:
        return "Evidence is ambiguous; insufficient confidence to classify image."
    direction = "AI-generated or manipulated" if score >= 0.5 else "authentic"
    region_note = (
        f" Suspicious regions detected at {len(regions)} location(s)." if regions else ""
    )
    return f"Image classified as {direction} (manipulation likelihood={score:.3f}).{region_note}"


def _unsupported(ext: str, media_type: str, t0: float):
    from pramaan.schema import make_result

    return make_result(
        media_type=media_type,
        score=None,
        confidence=None,
        model=MODEL_NAME,
        model_version=MODEL_VERSION,
        weights_hash="n/a",
        latency_ms=(time.perf_counter() - t0) * 1000,
        explanation=f"Unsupported file extension: {ext}",
    )


def _error_result(media_type: str, msg: str, t0: float):
    from pramaan.schema import make_result

    return make_result(
        media_type=media_type,
        score=None,
        confidence=None,
        model=MODEL_NAME,
        model_version=MODEL_VERSION,
        weights_hash="n/a",
        latency_ms=(time.perf_counter() - t0) * 1000,
        explanation=f"Error loading media: {msg}",
    )
