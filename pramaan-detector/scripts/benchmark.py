"""
CLI: run benchmark against a labelled CSV.

Usage:
    python scripts/benchmark.py data/test.csv --output results.json
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pramaan.evaluation.benchmark import run_benchmark


def main():
    p = argparse.ArgumentParser(description="PRAMAAN benchmark runner")
    p.add_argument("csv", help="CSV file: path,label (1=manipulated, 0=authentic)")
    p.add_argument("--image-weights", default=None)
    p.add_argument("--video-weights", default=None)
    p.add_argument("--audio-weights", default=None)
    p.add_argument("--device", default="cpu")
    p.add_argument("--output", default=None, help="Save results to JSON file")
    args = p.parse_args()

    output = run_benchmark(
        csv_path=args.csv,
        image_weights=args.image_weights,
        video_weights=args.video_weights,
        audio_weights=args.audio_weights,
        device=args.device,
        output_json=args.output,
    )
    print(json.dumps(output["overall_metrics"], indent=2))
    if args.output:
        print(f"\nFull results saved → {args.output}")


if __name__ == "__main__":
    main()
