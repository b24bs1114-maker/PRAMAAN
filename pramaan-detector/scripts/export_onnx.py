"""
CLI: export a model to ONNX or TorchScript.

Usage:
    python scripts/export_onnx.py image weights/image.pt weights/image.onnx
    python scripts/export_onnx.py audio weights/audio.pt weights/audio.onnx
    python scripts/export_onnx.py image weights/image.pt weights/image.pt --format torchscript
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pramaan.export.onnx_export import (
    export_image_model, export_audio_model,
    torchscript_image_model, torchscript_audio_model,
)


def main():
    p = argparse.ArgumentParser(description="Export PRAMAAN model to ONNX or TorchScript")
    p.add_argument("modality", choices=["image", "audio"])
    p.add_argument("weights", help="Input .pt weights file")
    p.add_argument("output",  help="Output file (.onnx or .pt)")
    p.add_argument("--format", choices=["onnx", "torchscript"], default="onnx")
    p.add_argument("--opset", type=int, default=17)
    args = p.parse_args()

    if args.format == "onnx":
        if args.modality == "image":
            export_image_model(args.weights, args.output, opset=args.opset)
        else:
            export_audio_model(args.weights, args.output, opset=args.opset)
    else:
        if args.modality == "image":
            torchscript_image_model(args.weights, args.output)
        else:
            torchscript_audio_model(args.weights, args.output)


if __name__ == "__main__":
    main()
