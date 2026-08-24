"""Cryptographic and perceptual hashing.

Two very different jobs live here, deliberately side by side:

* **SHA-256** -- exact integrity hashing of the stored bytes. This is the
  evidentiary fingerprint: any single-bit change produces a different digest.
* **Perceptual hashes** (pHash / dHash / aHash) -- similarity fingerprints that
  survive resizing, recompression and mild edits. They are *not* integrity
  hashes and are never used to assert that two files are the same file.

``imagehash`` is unavailable in this offline environment, so the algorithms are
implemented directly on Pillow + numpy. They follow the same construction as the
``imagehash`` reference implementation (DCT-II low-frequency median for pHash,
horizontal gradient for dHash, mean threshold for aHash) so distances are
comparable, and the code is auditable in-repo -- an advantage for a forensic
tool. If ``imagehash`` is installed later, ``IMAGEHASH_AVAILABLE`` reports it;
the hash format stays ours so stored hashes remain valid.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

try:  # pragma: no cover - depends on deployment
    import imagehash as _imagehash

    IMAGEHASH_AVAILABLE = True
    IMAGEHASH_VERSION = getattr(_imagehash, "__version__", "unknown")
except ImportError:  # pragma: no cover
    _imagehash = None
    IMAGEHASH_AVAILABLE = False
    IMAGEHASH_VERSION = None

HASH_SIZE = 8                 # 8x8 grid -> 64-bit hashes
HASH_BITS = HASH_SIZE * HASH_SIZE
HIGHFREQ_FACTOR = 4           # pHash works on a 32x32 grayscale image
CHUNK_SIZE = 1024 * 1024      # 1 MiB streaming reads

PERCEPTUAL_ALGORITHM = (
    "pramaan-native: pHash=DCT-II 32x32 low-freq 8x8 median; "
    "dHash=9x8 horizontal gradient; aHash=8x8 mean threshold"
)


# --------------------------------------------------------------------------- #
# SHA-256
# --------------------------------------------------------------------------- #
def sha256_file(path: str | Path) -> str:
    """SHA-256 of the bytes on disk, read in chunks (never loads whole file)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Perceptual hashing
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def _dct_matrix(n: int) -> np.ndarray:
    """Unnormalised DCT-II matrix, matching ``scipy.fftpack.dct(norm=None)``."""
    k = np.arange(n).reshape(-1, 1)
    m = np.arange(n).reshape(1, -1)
    return 2.0 * np.cos(np.pi * k * (2 * m + 1) / (2 * n))


def _bits_to_hex(bits: np.ndarray) -> str:
    """Pack a boolean array (row-major, MSB first) into a hex string."""
    flat = bits.flatten()
    value = 0
    for bit in flat:
        value = (value << 1) | int(bool(bit))
    width = (flat.size + 3) // 4
    return format(value, f"0{width}x")


def _load_grayscale(image: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """Grayscale + resize with a high-quality filter, as float64 pixels."""
    resized = image.convert("L").resize(size, Image.Resampling.LANCZOS)
    return np.asarray(resized, dtype=np.float64)


def calculate_phash(image: Image.Image, hash_size: int = HASH_SIZE) -> str:
    """Perceptual hash: median threshold of low-frequency DCT coefficients.

    Robust to rescaling and JPEG recompression, which is exactly what is needed
    to recognise a re-shared copy of the same picture.
    """
    img_size = hash_size * HIGHFREQ_FACTOR
    pixels = _load_grayscale(image, (img_size, img_size))
    matrix = _dct_matrix(img_size)
    # 2-D DCT-II: transform rows, then columns.
    dct = matrix @ pixels @ matrix.T
    low_freq = dct[:hash_size, :hash_size]
    return _bits_to_hex(low_freq > np.median(low_freq))


def calculate_dhash(image: Image.Image, hash_size: int = HASH_SIZE) -> str:
    """Difference hash: sign of the horizontal pixel gradient."""
    pixels = _load_grayscale(image, (hash_size + 1, hash_size))
    return _bits_to_hex(pixels[:, 1:] > pixels[:, :-1])


def calculate_ahash(image: Image.Image, hash_size: int = HASH_SIZE) -> str:
    """Average hash: threshold against the mean intensity."""
    pixels = _load_grayscale(image, (hash_size, hash_size))
    return _bits_to_hex(pixels > pixels.mean())


def calculate_image_hashes(path: str | Path) -> dict[str, str]:
    """Compute all three perceptual hashes in one decode pass."""
    with Image.open(path) as image:
        image.load()
        return {
            "phash": calculate_phash(image),
            "dhash": calculate_dhash(image),
            "ahash": calculate_ahash(image),
        }


# --------------------------------------------------------------------------- #
# Distance
# --------------------------------------------------------------------------- #
def hamming_distance(hash_a: str | None, hash_b: str | None) -> int | None:
    """Exact Hamming distance between two hex hashes of equal width.

    Returns ``None`` when either hash is missing so callers can distinguish
    "no data" from "distance 0" -- conflating those would be a forensic error.
    """
    if not hash_a or not hash_b:
        return None
    if len(hash_a) != len(hash_b):
        raise ValueError(
            f"hash width mismatch: {len(hash_a) * 4} vs {len(hash_b) * 4} bits"
        )
    return (int(hash_a, 16) ^ int(hash_b, 16)).bit_count()


def similarity_from_distance(distance: int | None, bits: int = HASH_BITS) -> float | None:
    """Map a Hamming distance to a 0..1 similarity (1.0 = identical hashes)."""
    if distance is None:
        return None
    return round(max(0.0, 1.0 - distance / bits), 4)


def hash_to_bits(hex_hash: str, bits: int = HASH_BITS) -> np.ndarray:
    """Expand a hex hash into a uint8 bit vector for index storage."""
    value = int(hex_hash, 16)
    return np.array(
        [(value >> (bits - 1 - i)) & 1 for i in range(bits)], dtype=np.uint8
    )


def bits_to_hash(bits: np.ndarray) -> str:
    return _bits_to_hex(bits.astype(bool))
