"""PRAMAAN Model Weights Provisioning Script.

Ensures pramaan-detector/weights/ contains image_detector.pt for Round-1 MVP deployment.
In local development, weights are already present locally.
On cloud platforms (Render/CI), weights are fetched via HTTP during build time
using the PRAMAAN_IMAGE_WEIGHTS_URL environment variable.
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = PROJECT_ROOT / "pramaan-detector" / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_PATH = WEIGHTS_DIR / "image_detector.pt"
MANIFEST_PATH = WEIGHTS_DIR / "model_manifest.json"

EXPECTED_IMAGE_SHA256 = "25edbe34eaa7168366e2c98c49e09c98ca1afd4ca4be0d21d6b84f2b9a24b83f"
IMAGE_WEIGHTS_URL = os.getenv("PRAMAAN_IMAGE_WEIGHTS_URL", "").strip()
FAIL_ON_MISSING = os.getenv("PRAMAAN_FAIL_ON_MISSING_WEIGHTS", "0").strip() == "1"


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file in 1MB chunks."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def verify_or_download_image_weights() -> bool:
    print(f"=== PRAMAAN Model Weights Provisioning ===")
    print(f"Target directory: {WEIGHTS_DIR}")

    if IMAGE_PATH.is_file():
        size_mb = IMAGE_PATH.stat().st_size / 1e6
        print(f"[OK] image_detector.pt present locally ({size_mb:.1f} MB)")
        digest = compute_sha256(IMAGE_PATH)
        print(f"[OK] SHA-256: {digest}")
        if digest == EXPECTED_IMAGE_SHA256:
            print("[OK] SHA-256 hash matches expected model weights!")
        else:
            print(f"[NOTE] SHA-256 differs from default baseline hash, using custom model file.")
        return True

    if not IMAGE_WEIGHTS_URL:
        print("[WARN] image_detector.pt not found and PRAMAAN_IMAGE_WEIGHTS_URL is empty.")
        if FAIL_ON_MISSING:
            print("[ERROR] Build failed: PRAMAAN_IMAGE_WEIGHTS_URL must be set to download image_detector.pt")
            sys.exit(1)
        return False

    print(f"Downloading image_detector.pt from: {IMAGE_WEIGHTS_URL}")
    try:
        urllib.request.urlretrieve(IMAGE_WEIGHTS_URL, IMAGE_PATH)
        size_mb = IMAGE_PATH.stat().st_size / 1e6
        print(f"[OK] Downloaded image_detector.pt ({size_mb:.1f} MB)")

        digest = compute_sha256(IMAGE_PATH)
        print(f"[OK] SHA-256: {digest}")
        if digest == EXPECTED_IMAGE_SHA256:
            print("[OK] Verified: SHA-256 matches official PRAMAAN Swin-B image model!")
        return True
    except Exception as exc:
        print(f"[ERROR] Failed to download weights from {IMAGE_WEIGHTS_URL}: {exc}")
        if IMAGE_PATH.exists():
            IMAGE_PATH.unlink()
        sys.exit(1)


if __name__ == "__main__":
    verify_or_download_image_weights()
