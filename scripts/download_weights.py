"""PRAMAAN Model Weights Provisioning Script.

Ensures pramaan-detector/weights/ contains image_detector.pt and audio_detector.pt.
In local development, weights are already present locally.
On cloud platforms (Render/CI), weights can be fetched via HTTP from a release URL or object store.
"""

from __future__ import annotations

import os
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_DIR = PROJECT_ROOT / "pramaan-detector" / "weights"
WEIGHTS_DIR.mkdir(parents=True, exist_ok=True)

IMAGE_PATH = WEIGHTS_DIR / "image_detector.pt"
AUDIO_PATH = WEIGHTS_DIR / "audio_detector.pt"
MANIFEST_PATH = WEIGHTS_DIR / "model_manifest.json"

IMAGE_WEIGHTS_URL = os.getenv("PRAMAAN_IMAGE_WEIGHTS_URL", "").strip()
AUDIO_WEIGHTS_URL = os.getenv("PRAMAAN_AUDIO_WEIGHTS_URL", "").strip()


def verify_or_download():
    print(f"Checking model weights in {WEIGHTS_DIR}...")

    if IMAGE_PATH.is_file():
        print(f"[OK] Image weights present: {IMAGE_PATH} ({IMAGE_PATH.stat().st_size / 1e6:.1f} MB)")
    elif IMAGE_WEIGHTS_URL:
        print(f"Downloading image_detector.pt from {IMAGE_WEIGHTS_URL}...")
        urllib.request.urlretrieve(IMAGE_WEIGHTS_URL, IMAGE_PATH)
        print(f"[OK] Image weights downloaded: {IMAGE_PATH.stat().st_size / 1e6:.1f} MB")
    else:
        print(f"[NOTE] Image weights file {IMAGE_PATH} not found and PRAMAAN_IMAGE_WEIGHTS_URL not set.")

    if AUDIO_PATH.is_file():
        print(f"[OK] Audio weights present: {AUDIO_PATH} ({AUDIO_PATH.stat().st_size / 1e6:.1f} MB)")
    elif AUDIO_WEIGHTS_URL:
        print(f"Downloading audio_detector.pt from {AUDIO_WEIGHTS_URL}...")
        urllib.request.urlretrieve(AUDIO_WEIGHTS_URL, AUDIO_PATH)
        print(f"[OK] Audio weights downloaded: {AUDIO_PATH.stat().st_size / 1e6:.1f} MB")
    else:
        print(f"[NOTE] Audio weights file {AUDIO_PATH} not found and PRAMAAN_AUDIO_WEIGHTS_URL not set.")

    if MANIFEST_PATH.is_file():
        print(f"[OK] Model manifest present: {MANIFEST_PATH}")
    else:
        print(f"[NOTE] Model manifest {MANIFEST_PATH} not found.")


if __name__ == "__main__":
    verify_or_download()
