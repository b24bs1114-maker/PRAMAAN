"""
CLI: detect a single file.

Usage:
    python scripts/detect.py path/to/file.jpg
    python scripts/detect.py path/to/clip.mp4 --image-weights weights/image.pt
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pramaan.service import DetectorService


def main():
    p = argparse.ArgumentParser(description="PRAMAAN single-file detector")
    p.add_argument("file", help="Path to image, video, or audio file")
    p.add_argument("--image-weights", default=None)
    p.add_argument("--video-weights", default=None)
    p.add_argument("--audio-weights", default=None)
    p.add_argument("--device", default="cpu")
    p.add_argument("--json", action="store_true", help="Output raw JSON")
    args = p.parse_args()

    service = DetectorService(
        image_weights=args.image_weights,
        video_weights=args.video_weights,
        audio_weights=args.audio_weights,
        device=args.device,
    )
    result = service.detect(args.file)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"Label      : {result.label}")
        print(f"Score      : {result.manipulation_score}")
        print(f"Confidence : {result.confidence}")
        print(f"Abstained  : {result.abstained}")
        print(f"Latency    : {result.latency_ms:.1f} ms")
        print(f"Explanation: {result.explanation}")


if __name__ == "__main__":
    main()
