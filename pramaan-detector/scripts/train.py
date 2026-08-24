"""
CLI: train image, video, or audio detector.

Usage:
    python scripts/train.py image --train data/image_train.csv --val data/image_val.csv
    python scripts/train.py audio --train data/audio_train.csv --epochs 20 --device cuda
    python scripts/train.py video --train data/video_train.csv --val data/video_val.csv
"""
import argparse, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    p = argparse.ArgumentParser(description="Train a PRAMAAN detector")
    p.add_argument("modality", choices=["image", "video", "audio"])
    p.add_argument("--train",   required=True, help="Training CSV (path,label)")
    p.add_argument("--val",     default=None,  help="Validation CSV (path,label)")
    p.add_argument("--weights", default=None,  help="Resume from existing weights")
    p.add_argument("--output",  default=None,  help="Output weights path")
    p.add_argument("--device",  default="cpu")
    p.add_argument("--epochs",  type=int, default=10)
    p.add_argument("--batch",   type=int, default=None)
    p.add_argument("--lr",      type=float, default=1e-4)
    args = p.parse_args()

    if args.modality == "image":
        from pramaan.training.image_trainer import ImageTrainer
        kwargs = dict(
            train_csv=args.train, val_csv=args.val,
            weights_path=args.weights,
            output_path=args.output or "weights/image_detector.pt",
            device=args.device, epochs=args.epochs, lr=args.lr,
        )
        if args.batch: kwargs["batch_size"] = args.batch
        ImageTrainer(**kwargs).train()

    elif args.modality == "video":
        from pramaan.training.video_trainer import VideoTrainer
        kwargs = dict(
            train_csv=args.train, val_csv=args.val,
            weights_path=args.weights,
            output_path=args.output or "weights/video_detector.pt",
            device=args.device, epochs=args.epochs, lr=args.lr,
        )
        if args.batch: kwargs["batch_size"] = args.batch
        VideoTrainer(**kwargs).train()

    else:
        from pramaan.training.audio_trainer import AudioTrainer
        kwargs = dict(
            train_csv=args.train, val_csv=args.val,
            weights_path=args.weights,
            output_path=args.output or "weights/audio_detector.pt",
            device=args.device, epochs=args.epochs, lr=args.lr,
        )
        if args.batch: kwargs["batch_size"] = args.batch
        AudioTrainer(**kwargs).train()


if __name__ == "__main__":
    main()
