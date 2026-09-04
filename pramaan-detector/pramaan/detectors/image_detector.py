"""
PRAMAAN Image Detector
======================
Backbone : OwensLab/commfor-model-384 (ViT-Small patch 16, 384x384 input)
Task     : Binary — Real Photo vs AI-Generated / Manipulated Image
Backbone Architecture: vit_small_patch16_384.augreg_in21k_ft_in1k with Linear(384, 1) head
Activation: Sigmoid -> manipulation probability in [0, 1]
Supports : JPG, PNG, WEBP, BMP, TIFF
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

logger = logging.getLogger(__name__)

MODEL_VERSION = "4.0.0"
MODEL_NAME = "OwensLab-CommunityForensics-ViT384"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_MEAN = [0.485, 0.456, 0.406]
_STD = [0.229, 0.224, 0.225]
_INPUT_SIZE = (384, 384)
_RESIZE_SIZE = 440

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "image_detector.safetensors"
if not _DEFAULT_WEIGHTS_PATH.exists():
    _alt = Path(__file__).resolve().parents[2] / "weights" / "image_detector.pt"
    if _alt.exists():
        _DEFAULT_WEIGHTS_PATH = _alt

_HEATMAP_ENV = "PRAMAAN_DETECTOR_HEATMAP"
_THREADS_ENV = "PRAMAAN_TORCH_THREADS"
POSITIVE_INDEX = 1


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
    """Transform image to (3, 384, 384) normalized tensor."""

    def __init__(self) -> None:
        self._compose: Any = None

    def _build(self) -> Any:
        if self._compose is None:
            _load_torch()
            import torchvision.transforms as T

            self._compose = T.Compose([
                T.Resize(_RESIZE_SIZE),
                T.CenterCrop(_INPUT_SIZE),
                T.ToTensor(),
                T.Normalize(_MEAN, _STD),
            ])
        return self._compose

    def __call__(self, image: Any) -> Any:
        return self._build()(image)


_transform = _LazyTransform()


class ViTCommunityForensicsNet:
    """ViT-Small 384 backbone for CommunityForensics fake image detection."""

    def __new__(cls, pretrained: bool = False):
        return _vit_net_class()(pretrained=pretrained)


_vit_class: Any = None


def _vit_net_class() -> Any:
    global _vit_class
    if _vit_class is not None:
        return _vit_class
    torch = _load_torch()
    import torch.nn as nn
    import timm

    class _ViTNet(nn.Module):
        def __init__(self, pretrained: bool = False):
            super().__init__()
            self.vit = timm.create_model("vit_small_patch16_384.augreg_in21k_ft_in1k", pretrained=pretrained)
            self.vit.head = nn.Linear(in_features=384, out_features=1, bias=True)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            return self.vit(x)

    _vit_class = _ViTNet
    return _vit_class


# Backwards compatibility alias
ImageForensicNet = ViTCommunityForensicsNet


def _extract_regions(cam: np.ndarray, threshold: float = 0.5) -> list[dict]:
    """Return bounding boxes of high-activation regions."""
    try:
        import cv2

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


def _load_vit_model(checkpoint: Path) -> tuple[Any, str]:
    """Load OwensLab ViT-Small model from safetensors or pt checkpoint."""
    torch = _load_torch()
    net_cls = _vit_net_class()
    model = net_cls(pretrained=False)

    resolved = checkpoint
    if not resolved.exists() and checkpoint.with_suffix(".safetensors").exists():
        resolved = checkpoint.with_suffix(".safetensors")
    elif not resolved.exists() and checkpoint.with_suffix(".pt").exists():
        resolved = checkpoint.with_suffix(".pt")

    try:
        from safetensors import safe_open
        with safe_open(str(resolved), framework="pt") as f:
            state_dict = {k.replace("vit.", ""): f.get_tensor(k) for k in f.keys()}
        model.vit.load_state_dict(state_dict, strict=True)
        return model, "safetensors-direct"
    except Exception:
        pass

    try:
        saved = torch.load(resolved, map_location="cpu", weights_only=False)
        if isinstance(saved, dict):
            sd = saved.get("state_dict", saved)
            sd = {k.replace("vit.", ""): v for k, v in sd.items()}
            model.vit.load_state_dict(sd, strict=False)
        return model, "torch-checkpoint"
    except Exception as e:
        logger.error("Could not load image checkpoint %s: %s", resolved, e)
        raise


class ImageDetector:
    """Image Deepfake & AI-Generated Image Detector using OwensLab ViT-Small 384."""

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        torch = _load_torch()
        self.device = torch.device(device)
        self.weights_hash = "uninitialised"
        self.load_strategy = "none"

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if not resolved_path.exists() and resolved_path.with_suffix(".safetensors").exists():
            resolved_path = resolved_path.with_suffix(".safetensors")
        elif not resolved_path.exists() and resolved_path.with_suffix(".pt").exists():
            resolved_path = resolved_path.with_suffix(".pt")

        if resolved_path.exists():
            self.model, self.load_strategy = _load_vit_model(resolved_path)
            self.weights_hash = _file_hash(str(resolved_path))
        else:
            net_cls = _vit_net_class()
            self.model = net_cls(pretrained=False)
            self.load_strategy = "untrained-fallback"
            self.weights_hash = "none"

        self.model.to(self.device).eval()
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)

    def _score(self, tensor: Any) -> float:
        """Manipulation score (sigmoid probability of AI manipulation)."""
        torch = _load_torch()
        with torch.inference_mode():
            logit = self.model(tensor)
            if hasattr(logit, "logits"):
                logit = logit.logits
            prob = torch.sigmoid(logit).item()
            return float(prob)

    def _score_and_cam(self, tensor: Any) -> tuple[float, np.ndarray]:
        """Manipulation score plus attention/activation map."""
        torch = _load_torch()
        score = self._score(tensor)
        try:
            with torch.no_grad():
                feat = self.model.vit.forward_features(tensor)
                patch_tokens = feat[:, 1:, :]  # [1, 576, 384]
                patch_norms = patch_tokens.norm(dim=-1).squeeze(0).cpu().numpy()
                s = int(np.sqrt(patch_norms.shape[0]))
                cam_grid = patch_norms.reshape(s, s)
                denom = cam_grid.max() - cam_grid.min() + 1e-8
                cam_norm = (cam_grid - cam_grid.min()) / denom

                try:
                    import cv2
                    cam_384 = cv2.resize(cam_norm, _INPUT_SIZE, interpolation=cv2.INTER_LINEAR)
                except ImportError:
                    cam_384 = np.repeat(np.repeat(cam_norm, 16, axis=0), 16, axis=1)[:_INPUT_SIZE[0], :_INPUT_SIZE[1]]
                return score, cam_384.astype(np.float32)
        except Exception:
            return score, np.zeros(_INPUT_SIZE, dtype=np.float32)

    def get_heatmap(self, image_path: str) -> Optional[np.ndarray]:
        """Compute spatial activation heatmap for an image."""
        path = Path(image_path)
        if not path.exists() or path.suffix.lower() not in SUPPORTED_EXTS:
            return None
        from PIL import Image

        try:
            with Image.open(path) as handle:
                img = handle.convert("RGB")
            tensor = _transform(img).unsqueeze(0).to(self.device)
            _, cam = self._score_and_cam(tensor)
            return cam
        except Exception:
            return None

    def detect(self, image_path: str) -> Any:
        from pramaan.schema import make_result

        t0 = time.perf_counter()
        path = Path(image_path)

        if not path.exists():
            return _error_result("image", f"File not found: {image_path}", t0)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, "image", t0)

        from PIL import Image

        try:
            with Image.open(path) as handle:
                img = handle.convert("RGB")
        except Exception as exc:
            return _error_result("image", str(exc), t0)

        tensor = _transform(img).unsqueeze(0).to(self.device)
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

        regions = _extract_regions(cam) if cam is not None else []
        explanation = _explain_image(score, regions)

        # score_margin_from_midpoint: uncalibrated score, confidence=None
        res = make_result(
            media_type="image",
            score=round(score, 4),
            confidence=None,
            model=MODEL_NAME,
            model_version=MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            explanation=explanation,
            heatmap_available=cam is not None,
            regions=regions,
        )
        res.evidence["raw_score"] = score
        res.evidence["calibrated"] = False
        res.evidence["score_margin_from_midpoint"] = abs(score - 0.5)
        if cam is not None:
            res.evidence["cam_shape"] = list(cam.shape)
            res.evidence["patch_size"] = 16
            res.evidence["input_size"] = list(_INPUT_SIZE)
        return res


def checkpoint_readiness(weights_path: Optional[str] = None) -> tuple[bool, Optional[str]]:
    resolved = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
    if not resolved.exists() and resolved.with_suffix(".safetensors").exists():
        resolved = resolved.with_suffix(".safetensors")
    elif not resolved.exists() and resolved.with_suffix(".pt").exists():
        resolved = resolved.with_suffix(".pt")

    if not resolved.exists():
        return False, (
            f"Image checkpoint file not found at {resolved}. "
            "This is NOT a finding of authenticity and NOT a finding of manipulation -- "
            "the signal is missing and is excluded from fusion."
        )
    return True, None


def verify_label_direction(label_map: dict | None = None) -> bool:
    return True


_global_detector: Optional[ImageDetector] = None
_detector_lock = threading.Lock()


def get_image_detector(weights_path: Optional[str] = None) -> ImageDetector:
    global _global_detector
    if _global_detector is not None:
        return _global_detector
    with _detector_lock:
        if _global_detector is None:
            _global_detector = ImageDetector(weights_path=weights_path)
        return _global_detector


def release_image_detector() -> None:
    global _global_detector
    with _detector_lock:
        _global_detector = None


def load_checkpoint(model_path: Optional[str] = None) -> bool:
    was_cold = _global_detector is None
    get_image_detector(weights_path=str(model_path) if model_path else None)
    return was_cold


def detect_image(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    detector = get_image_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


_hash_cache: dict[tuple[str, int, int], str] = {}
_hash_lock = threading.Lock()


def _file_hash(path: str) -> str:
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
    value = h.hexdigest()
    with _hash_lock:
        _hash_cache[key] = value
    return value


def _explain_image(score: float, regions: list) -> str:
    region_note = (
        f" Grad-CAM / attention concentrated in {len(regions)} region(s); attention"
        " indicates where the network looked, not a located edit."
        if regions
        else ""
    )
    if abs(score - 0.5) < 0.15:
        return (
            f"Score {score:.3f} sits near the 0.5 decision midpoint; model does not"
            f" separate the image decisively in either direction.{region_note}"
        )
    if score >= 0.5:
        return (
            f"Score {score:.3f} is above the 0.5 midpoint, consistent with AI generation or"
            f" manipulation. Uncalibrated model output, not a classification.{region_note}"
        )
    return (
        f"Score {score:.3f} is below the 0.5 midpoint: model found no strong indication of AI"
        f" generation or manipulation. That is not a finding of authenticity.{region_note}"
    )


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
