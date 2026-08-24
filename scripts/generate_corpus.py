#!/usr/bin/env python3
"""Synthetic demo corpus generator (TASK 5).

Builds a deterministic, fully local image corpus for exercising near-duplicate
retrieval and propagation reconstruction: ~20 original images, each with 5-10
derived variants (resize, crop, JPEG recompression, screenshot-like capture,
watermark, brightness/contrast), plus a manifest recording the parent/child
lineage and a synthetic redistribution timeline.

    .venv/bin/python scripts/generate_corpus.py
    .venv/bin/python scripts/generate_corpus.py --originals 5 --output /tmp/corpus

EVERYTHING PRODUCED HERE IS **SYNTHETIC DEMO DATA**. No image is scraped,
downloaded or derived from real-world media, and no timestamp, platform or
lineage entry describes a real event. The manifest states this in its header and
every record carries ``synthetic: true`` so downstream reports cannot present it
as real evidence.

Determinism: given the same arguments, filenames, image bytes, identifiers
(UUIDv5 over a fixed namespace) and timestamps are identical across runs and
machines.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter  # noqa: E402

DATASET_LABEL = "SYNTHETIC DEMO DATA"
DATASET_NOTE = (
    "Synthetic demonstration corpus generated locally by "
    "scripts/generate_corpus.py. Images, timestamps, platforms and lineage are "
    "fabricated for testing near-duplicate retrieval and propagation "
    "reconstruction. This is NOT real-world evidence and must never be "
    "presented as such."
)
GENERATOR = "pramaan-corpus-generator/1.0"
MANIFEST_VERSION = 1

# Fixed namespace -> stable UUIDv5 identifiers across runs and machines.
CORPUS_NAMESPACE = uuid.UUID("6f0f2b8e-4d5e-5f3a-9c21-0c4d7a1b8e55")

# All timestamps derive from this fixed instant; nothing depends on the clock.
EPOCH = datetime(2026, 1, 5, 8, 0, 0)

PLATFORMS = (
    "original_capture",
    "whatsapp",
    "telegram",
    "x",
    "instagram",
    "facebook",
    "reddit",
    "screenshot",
    "email",
)

SCENES = (
    "protest_banner",
    "flooded_street",
    "election_poster",
    "crowd_gathering",
    "traffic_junction",
    "market_stall",
    "hospital_corridor",
    "school_ground",
    "bridge_collapse",
    "fire_smoke",
    "police_barricade",
    "railway_platform",
    "night_rally",
    "riverbank",
    "warehouse_fire",
    "power_outage",
    "queue_line",
    "storm_damage",
    "press_conference",
    "border_fence",
)


# --------------------------------------------------------------------------- #
# Image synthesis
# --------------------------------------------------------------------------- #
def synth_original(index: int, size: tuple[int, int] = (960, 720)) -> Image.Image:
    """A structured synthetic 'photograph'.

    Structure matters: perceptual hashing of a flat colour field yields
    degenerate hashes, so each image gets a gradient, blocks, ellipses and a
    label derived deterministically from ``index``.
    """
    width, height = size
    rng = random.Random(1000 + index * 37)
    image = Image.new("RGB", size)
    draw = ImageDraw.Draw(image)

    top = (rng.randrange(30, 200), rng.randrange(30, 200), rng.randrange(60, 230))
    bottom = (rng.randrange(20, 120), rng.randrange(20, 120), rng.randrange(20, 140))
    for y in range(height):
        ratio = y / max(1, height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[c] + (bottom[c] - top[c]) * ratio) for c in range(3)),
        )

    for _ in range(9):
        x0 = rng.randrange(0, width - width // 5)
        y0 = rng.randrange(0, height - height // 5)
        draw.rectangle(
            (
                x0,
                y0,
                x0 + rng.randrange(width // 10, width // 4),
                y0 + rng.randrange(height // 10, height // 4),
            ),
            fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)),
            outline=(15, 15, 15),
        )
    for i in range(4):
        cx, cy = rng.randrange(width), rng.randrange(height)
        r = rng.randrange(30, 120)
        draw.ellipse(
            (cx - r, cy - r, cx + r, cy + r),
            outline=(255, 255 - i * 40, i * 50),
            width=4,
        )
    for _ in range(14):
        draw.line(
            [
                (rng.randrange(width), rng.randrange(height)),
                (rng.randrange(width), rng.randrange(height)),
            ],
            fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)),
            width=rng.choice((1, 2, 3)),
        )

    draw.rectangle((0, 0, width, 30), fill=(10, 10, 10))
    draw.text(
        (8, 10),
        f"{DATASET_LABEL} - scene {SCENES[index % len(SCENES)]}",
        fill=(240, 240, 240),
    )
    return image


def t_resize(image: Image.Image, factor: float) -> Image.Image:
    return image.resize(
        (max(16, int(image.width * factor)), max(16, int(image.height * factor))),
        Image.Resampling.LANCZOS,
    )


def t_crop(image: Image.Image, keep: float) -> Image.Image:
    dx = int(image.width * (1 - keep) / 2)
    dy = int(image.height * (1 - keep) / 2)
    return image.crop((dx, dy, image.width - dx, image.height - dy))


def t_screenshot(image: Image.Image) -> Image.Image:
    """Approximate a phone screenshot: status bar, chat chrome, slight rescale."""
    scaled = t_resize(image, 0.78)
    canvas = Image.new("RGB", (scaled.width, scaled.height + 96), (245, 245, 245))
    canvas.paste(scaled, (0, 58))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, canvas.width, 26), fill=(20, 20, 20))
    draw.text((8, 8), "09:41   LTE  87%", fill=(235, 235, 235))
    draw.rectangle((0, 26, canvas.width, 58), fill=(7, 94, 84))
    draw.text((10, 36), "Group chat (SYNTHETIC)", fill=(255, 255, 255))
    draw.text((10, canvas.height - 30), "Forwarded many times", fill=(90, 90, 90))
    return canvas


def t_watermark(image: Image.Image, label: str) -> Image.Image:
    marked = image.copy()
    draw = ImageDraw.Draw(marked)
    band = max(28, marked.height // 14)
    draw.rectangle(
        (0, marked.height - band, marked.width, marked.height), fill=(15, 15, 15)
    )
    draw.text((10, marked.height - band + band // 4), label, fill=(255, 255, 255))
    return marked


def t_brightness(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Brightness(image).enhance(factor)


def t_contrast(image: Image.Image, factor: float) -> Image.Image:
    return ImageEnhance.Contrast(image).enhance(factor)


def t_sharpen(image: Image.Image) -> Image.Image:
    return image.filter(ImageFilter.SHARPEN)


# (transformation name, callable, JPEG quality used when saving)
VARIANT_RECIPES: tuple[tuple[str, Any, int], ...] = (
    ("resize_50pct", lambda im: t_resize(im, 0.5), 88),
    ("resize_75pct", lambda im: t_resize(im, 0.75), 90),
    ("jpeg_recompress_q40", lambda im: im.copy(), 40),
    ("jpeg_recompress_q20", lambda im: im.copy(), 20),
    ("crop_90pct", lambda im: t_crop(im, 0.90), 88),
    ("crop_80pct", lambda im: t_crop(im, 0.80), 85),
    ("screenshot_like", t_screenshot, 80),
    ("watermark_overlay", lambda im: t_watermark(im, "SYNTHETIC DEMO - PRAMAAN"), 85),
    ("brightness_increase", lambda im: t_brightness(im, 1.25), 88),
    ("brightness_decrease", lambda im: t_brightness(im, 0.8), 88),
    ("contrast_increase", lambda im: t_contrast(im, 1.3), 88),
    ("sharpen_filter", t_sharpen, 88),
)


# --------------------------------------------------------------------------- #
# Manifest construction
# --------------------------------------------------------------------------- #
def _evidence_id(key: str) -> str:
    return str(uuid.uuid5(CORPUS_NAMESPACE, key))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds") + "Z"


def build_corpus(
    output_dir: Path,
    *,
    originals: int = 20,
    min_variants: int = 5,
    max_variants: int = 10,
    clean: bool = True,
) -> dict[str, Any]:
    """Generate images on disk and return the manifest dictionary."""
    images_dir = output_dir / "images"
    if clean and images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    lineage_counts: list[int] = []

    for index in range(originals):
        scene = SCENES[index % len(SCENES)]
        source_key = f"source-{index:03d}-{scene}"
        source_id = _evidence_id(source_key)
        rng = random.Random(5000 + index)

        original = synth_original(index)
        origin_time = EPOCH + timedelta(days=index, hours=rng.randrange(0, 9))
        original_name = f"{index:03d}_{scene}_original.jpg"
        original.save(images_dir / original_name, format="JPEG", quality=95)

        records.append(
            {
                "evidence_id": source_id,
                "source_id": source_id,
                "parent_id": None,
                "generation": 0,
                "filename": f"images/{original_name}",
                "timestamp": _iso(origin_time),
                "platform": "original_capture",
                "transformation": "none",
                "scene": scene,
                "width": original.width,
                "height": original.height,
                "synthetic": True,
            }
        )

        # Deterministic 5-10 variants. Parents are drawn from items produced so
        # far, so lineage has depth (generation 2+) instead of a flat star.
        variant_count = min_variants + (index % (max_variants - min_variants + 1))
        recipes = list(VARIANT_RECIPES)
        rng.shuffle(recipes)
        chain: list[tuple[str, Image.Image, int, datetime]] = [
            (source_id, original, 0, origin_time)
        ]

        for step in range(variant_count):
            name, transform, quality = recipes[step % len(recipes)]
            parent_index = (
                0 if step < 2 or rng.random() < 0.5 else rng.randrange(len(chain))
            )
            parent_id, parent_image, parent_generation, parent_time = chain[parent_index]

            variant = transform(parent_image)
            if variant.mode != "RGB":
                variant = variant.convert("RGB")
            generation = parent_generation + 1
            observed = parent_time + timedelta(
                hours=1 + step * 3, minutes=rng.randrange(0, 59)
            )
            platform = PLATFORMS[(index + step + 1) % len(PLATFORMS)]
            variant_id = _evidence_id(f"{source_key}-v{step:02d}-{name}")
            variant_name = f"{index:03d}_{scene}_g{generation}_{step:02d}_{name}.jpg"
            variant.save(images_dir / variant_name, format="JPEG", quality=quality)

            records.append(
                {
                    "evidence_id": variant_id,
                    "source_id": source_id,
                    "parent_id": parent_id,
                    "generation": generation,
                    "filename": f"images/{variant_name}",
                    "timestamp": _iso(observed),
                    "platform": platform,
                    "transformation": name,
                    "scene": scene,
                    "width": variant.width,
                    "height": variant.height,
                    "synthetic": True,
                }
            )
            chain.append((variant_id, variant, generation, observed))

        lineage_counts.append(variant_count)

    return {
        "dataset": DATASET_LABEL,
        "warning": DATASET_NOTE,
        "manifest_version": MANIFEST_VERSION,
        "generator": GENERATOR,
        "deterministic": True,
        "namespace_uuid": str(CORPUS_NAMESPACE),
        "base_timestamp": _iso(EPOCH),
        "originals": originals,
        "variants_per_original": {
            "minimum": min(lineage_counts) if lineage_counts else 0,
            "maximum": max(lineage_counts) if lineage_counts else 0,
        },
        "total_items": len(records),
        "transformations": sorted({r[0] for r in VARIANT_RECIPES}),
        "platforms": list(PLATFORMS),
        "items": records,
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=f"Generate the PRAMAAN {DATASET_LABEL} corpus"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "corpus",
        help="Corpus directory (default: <repo>/corpus)",
    )
    parser.add_argument("--originals", type=int, default=20)
    parser.add_argument("--min-variants", type=int, default=5)
    parser.add_argument("--max-variants", type=int, default=10)
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not delete the existing images directory first.",
    )
    args = parser.parse_args()

    if args.min_variants < 1 or args.max_variants < args.min_variants:
        print("Invalid variant bounds.", file=sys.stderr)
        return 2

    manifest = build_corpus(
        args.output,
        originals=args.originals,
        min_variants=args.min_variants,
        max_variants=args.max_variants,
        clean=not args.keep_existing,
    )
    manifest_path = args.output / "manifest.json"
    write_manifest(manifest, manifest_path)

    print(f"{DATASET_LABEL}: {manifest['total_items']} images written")
    print(f"  originals : {manifest['originals']}")
    print(
        "  variants  : "
        f"{manifest['variants_per_original']['minimum']}-"
        f"{manifest['variants_per_original']['maximum']} per original"
    )
    print(f"  images    : {args.output / 'images'}")
    print(f"  manifest  : {manifest_path}")
    print(
        "  reminder  : synthetic data only -- not real-world evidence; lineage "
        "and timestamps are fabricated."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
