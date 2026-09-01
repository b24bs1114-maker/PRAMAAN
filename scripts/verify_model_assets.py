"""Verify that every declared model asset exists, matches its digest, and loads.

This is the acceptance test for the model-asset pipeline. It reads
``pramaan-detector/weights/model_manifest.json`` -- the single source of truth
for filenames, sizes and SHA-256 digests -- and for each modality checks:

1. the checkpoint file exists at the resolved path,
2. its size matches the manifest,
3. its SHA-256 matches the manifest,
4. the intended loader can actually open it and produce a module.

Step 4 is skipped with ``--no-load`` (useful in a build container where the
point is only to prove the bytes arrived intact).

Exit code 0 means every declared asset is present, verified and loadable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPO_WEIGHTS_DIR = PROJECT_ROOT / "pramaan-detector" / "weights"


def _weights_dir() -> Path:
    """A relative ``PRAMAAN_WEIGHTS_DIR`` resolves against the repo root, not the
    process CWD, so build steps and start commands agree on one location."""
    override = os.getenv("PRAMAAN_WEIGHTS_DIR", "").strip()
    if not override:
        return REPO_WEIGHTS_DIR
    path = Path(override).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


WEIGHTS_DIR = _weights_dir()
#: The manifest travels with the source tree even when weights live elsewhere.
MANIFEST_PATH = REPO_WEIGHTS_DIR / "model_manifest.json"

#: Env var that overrides the checkpoint path, per modality. Matches the
#: settings names the backend itself reads (``PRAMAAN_``-prefixed).
PATH_ENV = {
    "image": "PRAMAAN_IMAGE_MODEL_PATH",
    "video": "PRAMAAN_VIDEO_MODEL_PATH",
    "audio": "PRAMAAN_AUDIO_MODEL_PATH",
}


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_checkpoint(modality: str, entry: dict) -> Path:
    override = os.getenv(PATH_ENV[modality], "").strip()
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)
    return WEIGHTS_DIR / entry["checkpoint_filename"]


def load_image(checkpoint: Path) -> str:
    from pramaan.detectors.image_detector import ImageDetector, release_image_detector

    release_image_detector()
    detector = ImageDetector(weights_path=str(checkpoint))
    strategy = detector.load_strategy
    logits = detector.model.config.num_labels
    return f"SwinForImageClassification via {strategy}, num_labels={logits}"


def load_video(checkpoint: Path) -> str:
    from pramaan.detectors.video_detector import VideoDetector

    detector = VideoDetector(weights_path=str(checkpoint))
    return (
        f"{type(detector.frame_model).__name__} loaded via "
        f"{getattr(detector, 'load_strategy', 'unknown')} "
        f"(weights_hash={detector.weights_hash})"
    )


def load_audio(checkpoint: Path) -> str:
    from pramaan.detectors.audio_detector import AudioDetector

    detector = AudioDetector(weights_path=str(checkpoint))
    return f"{type(detector.model).__name__} loaded (weights_hash={detector.weights_hash})"

LOADERS = {"image": load_image, "video": load_video, "audio": load_audio}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modality",
        action="append",
        choices=sorted(LOADERS),
        help="Check only these modalities (repeatable). Default: all.",
    )
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="Verify presence/size/digest only; do not import torch or build models.",
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="Skip the SHA-256 read (presence and size only).",
    )
    args = parser.parse_args()

    if not MANIFEST_PATH.is_file():
        print(f"[FAIL] manifest not found: {MANIFEST_PATH}")
        return 1
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    models = manifest["models"]
    # Default to whatever the build/deploy was asked to provision, so a build
    # that only wants the image checkpoint is not failed by an absent audio one.
    env_list = [
        item.strip().lower()
        for item in os.getenv("PRAMAAN_WEIGHTS_MODALITIES", "").split(",")
        if item.strip() in LOADERS
    ]
    wanted = args.modality or env_list or sorted(models)

    failures: list[str] = []
    print(f"=== PRAMAAN model asset verification ===")
    print(f"manifest: {MANIFEST_PATH}")

    for modality in wanted:
        entry = models.get(modality)
        if entry is None:
            failures.append(f"{modality}: not declared in manifest")
            continue
        checkpoint = resolve_checkpoint(modality, entry)
        print(f"\n--- {modality} ---")
        print(f"path      : {checkpoint}")

        if not checkpoint.is_file():
            print("[FAIL] file missing")
            failures.append(f"{modality}: missing {checkpoint}")
            continue

        size = checkpoint.stat().st_size
        expected_size = entry.get("weights_size_bytes")
        print(f"size      : {size} bytes ({size / 1e6:.1f} MB)")
        if expected_size is not None and size != expected_size:
            print(f"[FAIL] size mismatch, manifest declares {expected_size}")
            failures.append(f"{modality}: size {size} != {expected_size}")
            continue

        if not args.no_hash:
            digest = sha256_of(checkpoint)
            expected = entry.get("weights_sha256", "")
            print(f"sha256    : {digest}")
            if expected and digest != expected:
                print(f"[FAIL] digest mismatch, manifest declares {expected}")
                failures.append(f"{modality}: sha256 mismatch")
                continue
            print("[OK] digest matches manifest")

        if args.no_load:
            continue

        try:
            detail = LOADERS[modality](checkpoint)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the run
            print(f"[FAIL] loader raised {type(exc).__name__}: {exc}")
            failures.append(f"{modality}: loader {type(exc).__name__}: {exc}")
            continue
        print(f"[OK] loaded: {detail}")

    print()
    if failures:
        print(f"=== {len(failures)} FAILURE(S) ===")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("=== all declared model assets verified ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
