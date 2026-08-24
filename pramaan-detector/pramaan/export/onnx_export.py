"""
ONNX and TorchScript export for ImageForensicNet and AudioForensicNet.
"""
from __future__ import annotations
import torch
from pathlib import Path


def export_image_model(weights_path: str, output_path: str, opset: int = 17) -> None:
    """Export ImageForensicNet to ONNX."""
    from pramaan.detectors.image_detector import ImageForensicNet
    model = ImageForensicNet(pretrained=False)
    state = torch.load(weights_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()

    dummy = torch.zeros(1, 3, 380, 380)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, output_path,
        input_names=["image"], output_names=["logit"],
        dynamic_axes={"image": {0: "batch"}},
        opset_version=opset,
    )
    print(f"Exported image model (ONNX) → {output_path}")


def export_audio_model(weights_path: str, output_path: str, opset: int = 17) -> None:
    """Export AudioForensicNet (CNN fallback head only) to ONNX."""
    from pramaan.detectors.audio_detector import AudioForensicNet
    model = AudioForensicNet(pretrained=False)
    state = torch.load(weights_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()

    sr = 16000
    chunk = int(3.0 * sr)
    dummy = torch.zeros(1, chunk)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model, dummy, output_path,
        input_names=["waveform"], output_names=["logit"],
        dynamic_axes={"waveform": {0: "batch"}},
        opset_version=opset,
    )
    print(f"Exported audio model (ONNX) → {output_path}")


def torchscript_image_model(weights_path: str, output_path: str) -> None:
    """Export ImageForensicNet to TorchScript (torch.jit.trace)."""
    from pramaan.detectors.image_detector import ImageForensicNet
    model = ImageForensicNet(pretrained=False)
    state = torch.load(weights_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()

    dummy = torch.zeros(1, 3, 380, 380)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    traced = torch.jit.trace(model, dummy)
    traced.save(output_path)
    print(f"Exported image model (TorchScript) → {output_path}")


def torchscript_audio_model(weights_path: str, output_path: str) -> None:
    """Export AudioForensicNet to TorchScript (torch.jit.trace)."""
    from pramaan.detectors.audio_detector import AudioForensicNet
    model = AudioForensicNet(pretrained=False)
    state = torch.load(weights_path, map_location="cpu")
    model.load_state_dict(state)
    model.eval()

    dummy = torch.zeros(1, int(3.0 * 16000))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    traced = torch.jit.trace(model, dummy)
    traced.save(output_path)
    print(f"Exported audio model (TorchScript) → {output_path}")
