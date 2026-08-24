#!/usr/bin/env python3
"""Perceptual hashing demonstration (TASK 4).

Generates a source image, applies the transformations PRAMAAN expects to survive
(resize, crop, JPEG recompression, brightness, watermark) plus an unrelated
image, and prints pHash/dHash Hamming distances for each.

Run from the repository root::

    .venv/bin/python scripts/test_phash.py
    .venv/bin/python scripts/test_phash.py --image path/to/photo.jpg

The thresholds printed are the prototype defaults from ``app.config`` and are
empirical, not validated against a forensic reference dataset.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from PIL import Image, ImageDraw, ImageEnhance  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.hashing import (  # noqa: E402
    PERCEPTUAL_ALGORITHM,
    calculate_dhash,
    calculate_phash,
    hamming_distance,
    similarity_from_distance,
)


def _synthetic(seed: int = 7, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """Structured synthetic image (no external assets, no network)."""
    width, height = size
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = int(255 * y / max(1, height - 1))
        draw.line(
            [(0, y), (width, y)],
            fill=((shade + seed * 13) % 256, (shade * 2 + seed * 7) % 256, shade),
        )
    for i in range(7):
        offset = (seed * (i + 3)) % max(1, width // 3)
        draw.rectangle(
            (
                offset + i * 18,
                offset + i * 13,
                offset + i * 18 + width // 4,
                offset + i * 13 + height // 5,
            ),
            fill=((seed * 29 + i * 40) % 256, (i * 47) % 256, 90 + i * 20),
        )
    draw.ellipse(
        (width // 4, height // 4, width // 4 + width // 3, height // 4 + height // 3),
        outline=(255, 255, 0),
        width=5,
    )
    return image


def _recompress(image: Image.Image, quality: int) -> Image.Image:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer)


def _watermark(image: Image.Image) -> Image.Image:
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    draw.rectangle(
        (0, marked.height - 34, marked.width, marked.height), fill=(20, 20, 20)
    )
    draw.text((12, marked.height - 26), "SYNTHETIC DEMO - PRAMAAN", fill=(255, 255, 255))
    return marked


def _crop(image: Image.Image, fraction: float = 0.9) -> Image.Image:
    width, height = image.size
    dx = int(width * (1 - fraction) / 2)
    dy = int(height * (1 - fraction) / 2)
    return image.crop((dx, dy, width - dx, height - dy))


def variants(source: Image.Image) -> list[tuple[str, Image.Image]]:
    half = (max(1, source.width // 2), max(1, source.height // 2))
    return [
        ("identical copy", source.copy()),
        ("resize 50%", source.resize(half, Image.Resampling.LANCZOS)),
        ("resize 150%", source.resize(
            (int(source.width * 1.5), int(source.height * 1.5)),
            Image.Resampling.LANCZOS,
        )),
        ("crop 90%", _crop(source, 0.9)),
        ("crop 70%", _crop(source, 0.7)),
        ("JPEG q=90", _recompress(source, 90)),
        ("JPEG q=50", _recompress(source, 50)),
        ("JPEG q=20", _recompress(source, 20)),
        ("brightness +25%", ImageEnhance.Brightness(source).enhance(1.25)),
        ("contrast -20%", ImageEnhance.Contrast(source).enhance(0.8)),
        ("watermark banner", _watermark(source)),
        ("grayscale", source.convert("L").convert("RGB")),
        ("unrelated image", _synthetic(seed=987, size=source.size)),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Perceptual hash distance demo")
    parser.add_argument(
        "--image", type=Path, help="Optional source image; synthetic if omitted."
    )
    args = parser.parse_args()

    settings = get_settings()
    near = settings.near_duplicate_max_distance
    strong = settings.strong_duplicate_max_distance

    if args.image:
        if not args.image.is_file():
            print(f"No such file: {args.image}", file=sys.stderr)
            return 2
        source = Image.open(args.image).convert("RGB")
        label = str(args.image)
    else:
        source = _synthetic()
        label = "synthetic 640x480 (SYNTHETIC DEMO DATA)"

    base_phash = calculate_phash(source)
    base_dhash = calculate_dhash(source)

    print("PRAMAAN perceptual hash demonstration")
    print(f"algorithm      : {PERCEPTUAL_ALGORITHM}")
    print(f"source         : {label}")
    print(f"source pHash   : {base_phash}")
    print(f"source dHash   : {base_dhash}")
    print(
        f"thresholds     : strong <= {strong}, near-duplicate <= {near} "
        "(prototype defaults, empirically chosen -- not clinically validated)"
    )
    print()
    print(f"{'transformation':<22}{'pHash':>7}{'dHash':>7}{'sim':>8}  classification")
    print("-" * 68)

    for name, variant in variants(source):
        phash_distance = hamming_distance(base_phash, calculate_phash(variant))
        dhash_distance = hamming_distance(base_dhash, calculate_dhash(variant))
        combined = min(phash_distance, dhash_distance)
        similarity = similarity_from_distance(phash_distance)
        if combined <= strong:
            verdict = "strong near-duplicate candidate"
        elif combined <= near:
            verdict = "near-duplicate candidate"
        else:
            verdict = "not a candidate"
        print(
            f"{name:<22}{phash_distance:>7}{dhash_distance:>7}"
            f"{similarity:>8.3f}  {verdict}"
        )

    print()
    print(
        "Note: 'candidate' means perceptually similar, not the same file and not "
        "a confirmed shared origin. Distances above are for the transformations "
        "listed only."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
