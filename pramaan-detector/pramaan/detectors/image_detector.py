"""
PRAMAAN Image Detector
======================
Backbone : Swin-B Transformer (SwinForImageClassification)
Task     : Binary — Real Photo (0) vs AI-Generated / Manipulated Image (1)
Heatmap  : Grad-CAM on Swin encoder final block (7x7 patch grid interpolated to 224x224)
Supports : JPG, PNG, WEBP
"""
from __future__ import annotations
import hashlib, time
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

import torch
import torch.nn as nn
import torchvision.transforms as T
from transformers import SwinConfig, SwinForImageClassification, AutoImageProcessor

from pramaan.schema import make_result, DetectionResult

MODEL_VERSION = "3.0.0"
MODEL_NAME = "SwinB-AI-Image-Detector"
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

_MEAN = [0.485, 0.456, 0.406]
_STD  = [0.229, 0.224, 0.225]

_transform = T.Compose([
    T.Resize((224, 224)),
    T.ToTensor(),
    T.Normalize(_MEAN, _STD),
])

_DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "weights" / "image_detector.pt"


class ImageForensicNet(nn.Module):
    """EfficientNet-B0 fine-tuned for video frame forgery detection (2 classes)."""

    def __init__(self, pretrained: bool = False):
        super().__init__()
        from torchvision.models import efficientnet_b0, EfficientNet_B0_Weights
        weights = EfficientNet_B0_Weights.DEFAULT if pretrained else None
        base = efficientnet_b0(weights=weights)
        self.features = base.features
        self.avgpool  = base.avgpool
        in_features   = base.classifier[1].in_features
        self.classifier = nn.Sequential(
            base.classifier[0],
            nn.Linear(in_features, 2),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.features(x)
        pooled = self.avgpool(feat).flatten(1)
        return self.classifier(pooled)


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
            regions.append({
                "x": int(x / w * 100), "y": int(y / h * 100),
                "w": int(bw / w * 100), "h": int(bh / h * 100),
                "activation": float(cam[y:y+bh, x:x+bw].mean()),
            })
        return sorted(regions, key=lambda r: -r["activation"])[:5]
    except ImportError:
        return []


class ImageDetector:
    """
    Image Deepfake & AI-Generated Image Detector using Swin-B Transformer.
    """

    def __init__(self, weights_path: Optional[str] = None, device: str = "cpu"):
        self.device = torch.device(device)
        self.weights_hash = "uninitialised"

        resolved_path = Path(weights_path) if weights_path else _DEFAULT_WEIGHTS_PATH
        if resolved_path.exists():
            saved = torch.load(resolved_path, map_location=self.device)
            if isinstance(saved, dict) and "config" in saved and "state_dict" in saved:
                config = SwinConfig(**saved["config"])
                self.model = SwinForImageClassification(config)
                self.model.load_state_dict(saved["state_dict"])
            elif isinstance(saved, dict) and "state_dict" in saved:
                config = SwinConfig.from_pretrained("umm-maybe/AI-image-detector")
                self.model = SwinForImageClassification(config)
                self.model.load_state_dict(saved["state_dict"])
            else:
                self.model = SwinForImageClassification.from_pretrained("umm-maybe/AI-image-detector")
            self.weights_hash = _file_hash(str(resolved_path))
        else:
            # Pretrained fallback
            self.model = SwinForImageClassification.from_pretrained("umm-maybe/AI-image-detector")
            self.weights_hash = "pretrained-fallback"

        self.model.to(self.device).eval()

    def _compute_cam(self, tensor: torch.Tensor) -> tuple[float, np.ndarray]:
        """Compute manipulation score and spatial Grad-CAM heatmap."""
        last_block = self.model.swin.encoder.layers[-1].blocks[-1]
        activation = None
        gradient = None

        def forward_hook(module, input, output):
            nonlocal activation
            activation = output[0] if isinstance(output, tuple) else output

        def backward_hook(module, grad_input, grad_output):
            nonlocal gradient
            gradient = grad_output[0]

        h1 = last_block.register_forward_hook(forward_hook)
        h2 = last_block.register_full_backward_hook(backward_hook)

        tensor.requires_grad = True
        logits = self.model(tensor).logits
        probs = torch.softmax(logits, dim=-1)[0]

        # id2label: {0: 'artificial', 1: 'human'}
        # Score = probability of class 0 ('artificial')
        score = probs[0].item()

        score_tensor = logits[0, 0]
        self.model.zero_grad()
        score_tensor.backward(retain_graph=True)

        h1.remove()
        h2.remove()

        if activation is not None and gradient is not None:
            act = activation.detach()
            grad = gradient.detach()
            weights = grad.mean(dim=(1, 2), keepdim=True)
            cam_1d = (weights * act).sum(dim=-1).squeeze(0)
            cam_1d = torch.relu(cam_1d).cpu().numpy()
            s = int(np.sqrt(cam_1d.shape[0])) if cam_1d.ndim == 1 else 7
            cam_grid = cam_1d[:s*s].reshape(s, s)
            denom = (cam_grid.max() - cam_grid.min() + 1e-8)
            cam_norm = (cam_grid - cam_grid.min()) / denom
        else:
            cam_norm = np.zeros((7, 7), dtype=np.float32)

        try:
            import cv2
            cam_224 = cv2.resize(cam_norm, (224, 224), interpolation=cv2.INTER_LINEAR)
        except ImportError:
            cam_224 = np.repeat(np.repeat(cam_norm, 32, axis=0), 32, axis=1)[:224, :224]

        return score, cam_224

    def detect(self, image_path: str) -> DetectionResult:
        t0 = time.perf_counter()
        path = Path(image_path)

        if path.suffix.lower() not in SUPPORTED_EXTS:
            return _unsupported(path.suffix, "image", t0)

        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:
            return _error_result("image", str(exc), t0)

        tensor = _transform(img).unsqueeze(0).to(self.device)

        with torch.enable_grad():
            score, cam = self._compute_cam(tensor)

        confidence = min(abs(score - 0.5) * 2.0, 1.0)
        regions = _extract_regions(cam)
        latency_ms = (time.perf_counter() - t0) * 1000

        explanation = _explain_image(score, regions)

        return make_result(
            media_type="image",
            score=score,
            confidence=confidence,
            model=MODEL_NAME,
            model_version=MODEL_VERSION,
            weights_hash=self.weights_hash,
            latency_ms=latency_ms,
            explanation=explanation,
            evidence={"raw_score": score, "cam_shape": list(cam.shape)},
            heatmap_available=True,
            regions=regions,
        )

    def get_heatmap(self, image_path: str) -> Optional[np.ndarray]:
        path = Path(image_path)
        if path.suffix.lower() not in SUPPORTED_EXTS:
            return None
        img = Image.open(path).convert("RGB")
        tensor = _transform(img).unsqueeze(0).to(self.device)
        with torch.enable_grad():
            _, cam = self._compute_cam(tensor)
        return cam


_global_detector: Optional[ImageDetector] = None

def get_image_detector(weights_path: Optional[str] = None) -> ImageDetector:
    global _global_detector
    if _global_detector is None or (weights_path and _global_detector.weights_hash == "uninitialised"):
        _global_detector = ImageDetector(weights_path=weights_path)
    return _global_detector


def detect_image(path: str | Path, model_path: Optional[str] = None, **kwargs) -> dict:
    """Entrypoint function for backend plugin detector interface."""
    detector = get_image_detector(weights_path=str(model_path) if model_path else None)
    result = detector.detect(str(path))
    return result.to_dict()


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _explain_image(score: float, regions: list) -> str:
    if abs(score - 0.5) < 0.15:
        return "Evidence is ambiguous; insufficient confidence to classify image."
    direction = "AI-generated or manipulated" if score >= 0.5 else "authentic"
    region_note = (
        f" Suspicious regions detected at {len(regions)} location(s)."
        if regions else ""
    )
    return f"Image classified as {direction} (manipulation likelihood={score:.3f}).{region_note}"


def _unsupported(ext: str, media_type: str, t0: float) -> DetectionResult:
    return make_result(
        media_type=media_type, score=None, confidence=None,
        model=MODEL_NAME, model_version=MODEL_VERSION,
        weights_hash="n/a", latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Unsupported file extension: {ext}",
    )


def _error_result(media_type: str, msg: str, t0: float) -> DetectionResult:
    return make_result(
        media_type=media_type, score=None, confidence=None,
        model=MODEL_NAME, model_version=MODEL_VERSION,
        weights_hash="n/a", latency_ms=(time.perf_counter()-t0)*1000,
        explanation=f"Error loading media: {msg}",
    )
