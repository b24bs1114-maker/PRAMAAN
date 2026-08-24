"""Perceptual hashing tests (TASK 4).

Checks the property that matters for near-duplicate retrieval: hashes must stay
close under benign transformations (resize, recompression, small brightness
changes) and stay far apart for unrelated images.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image, ImageEnhance

from app.services.hashing import (
    HASH_BITS,
    calculate_dhash,
    calculate_image_hashes,
    calculate_phash,
    hamming_distance,
    similarity_from_distance,
)
from tests.helpers import encode, make_image

# Prototype thresholds, chosen empirically on the synthetic corpus and stated as
# such: they are not validated against a real-world forensic dataset.
NEAR_DUPLICATE_MAX = 12
UNRELATED_MIN = 16


def _reencode(image: Image.Image, fmt: str = "JPEG", quality: int = 92) -> Image.Image:
    return Image.open(io.BytesIO(encode(image, fmt, quality)))


def test_identical_images_have_distance_zero() -> None:
    image = make_image(seed=201)
    assert hamming_distance(calculate_phash(image), calculate_phash(image)) == 0
    assert hamming_distance(calculate_dhash(image), calculate_dhash(image)) == 0


def test_resized_image_stays_close() -> None:
    original = make_image(640, 480, seed=202)
    resized = original.resize((320, 240), Image.Resampling.LANCZOS)

    phash_distance = hamming_distance(
        calculate_phash(original), calculate_phash(resized)
    )
    dhash_distance = hamming_distance(
        calculate_dhash(original), calculate_dhash(resized)
    )
    assert phash_distance <= NEAR_DUPLICATE_MAX, phash_distance
    assert dhash_distance <= NEAR_DUPLICATE_MAX, dhash_distance


def test_jpeg_recompression_stays_close() -> None:
    original = make_image(400, 300, seed=203)
    recompressed = _reencode(original, "JPEG", quality=35)

    distance = hamming_distance(
        calculate_phash(original), calculate_phash(recompressed)
    )
    assert distance <= NEAR_DUPLICATE_MAX, distance


def test_brightness_change_stays_close() -> None:
    original = make_image(400, 300, seed=204)
    brighter = ImageEnhance.Brightness(original).enhance(1.25)

    distance = hamming_distance(calculate_phash(original), calculate_phash(brighter))
    assert distance <= NEAR_DUPLICATE_MAX, distance


def test_unrelated_images_are_far_apart() -> None:
    a = make_image(320, 240, seed=205)
    b = make_image(320, 240, seed=987)

    distance = hamming_distance(calculate_phash(a), calculate_phash(b))
    assert distance >= UNRELATED_MIN, distance


def test_hash_format_is_stable_hex() -> None:
    image = make_image(seed=206)
    for value in (calculate_phash(image), calculate_dhash(image)):
        assert len(value) == HASH_BITS // 4
        int(value, 16)


def test_hashes_are_deterministic_across_calls(tmp_path) -> None:
    path = tmp_path / "det.jpg"
    path.write_bytes(encode(make_image(seed=207), "JPEG"))
    assert calculate_image_hashes(path) == calculate_image_hashes(path)


def test_similarity_is_derived_from_distance() -> None:
    assert similarity_from_distance(0) == 1.0
    assert similarity_from_distance(HASH_BITS) == 0.0
    assert similarity_from_distance(16) == pytest.approx(0.75)
    assert similarity_from_distance(None) is None


def test_hamming_distance_handles_missing_hashes() -> None:
    """Videos have no perceptual hash; comparison must return None, not raise."""
    assert hamming_distance(None, "0" * 16) is None
    assert hamming_distance("0" * 16, None) is None


def test_hamming_distance_rejects_mismatched_widths() -> None:
    with pytest.raises(ValueError):
        hamming_distance("0" * 16, "0" * 8)
