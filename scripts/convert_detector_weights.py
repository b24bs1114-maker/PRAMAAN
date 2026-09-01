"""Convert the Swin-B image detector checkpoint to a memory-mapped HF directory.

Why
---
``image_detector.pt`` is a ``torch.save``d ``{"config": ..., "state_dict": ...}``
pickle. Loading it costs, measured on this repository's checkpoint:

    torch.load(full checkpoint)      +331 MB
    SwinForImageClassification(cfg)  +336 MB   (random init, immediately overwritten)
    ------------------------------------------
    peak model-load RSS              1052 MB   for a 347 MB model

Written out as ``config.json`` + ``model.safetensors`` and loaded with
``from_pretrained``, the same weights cost:

    peak model-load RSS               396 MB

because safetensors is memory-mapped and transformers builds the module on the
meta device, so the parameters are adopted rather than copied. The resulting
model is numerically identical -- this script asserts that by comparing the
state dicts tensor by tensor.

Usage
-----
    python scripts/convert_detector_weights.py
    python scripts/convert_detector_weights.py --checkpoint path/to/image_detector.pt

Output goes to ``<checkpoint_dir>/<stem>_hf/``, which the detector picks up
automatically. Safe to re-run; skips work when the output is already current.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHECKPOINT = PROJECT_ROOT / "pramaan-detector" / "weights" / "image_detector.pt"

#: Bump when the stamp's meaning changes. v1 identified the source checkpoint by
#: (size, mtime_ns), which is not reproducible: a re-download of identical bytes
#: gets a new mtime, and a replaced checkpoint of the same length keeps the old
#: one if the filesystem timestamp is preserved. v2 identifies it by digest.
STAMP_VERSION = 2


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_stamp(checkpoint: Path) -> dict:
    """Identity of the checkpoint this HF directory was derived from."""
    return {
        "stamp_version": STAMP_VERSION,
        "checkpoint": checkpoint.name,
        "size_bytes": checkpoint.stat().st_size,
        "sha256": sha256_of(checkpoint),
    }


def convert(checkpoint: Path, force: bool = False) -> int:
    if not checkpoint.is_file():
        print(f"[SKIP] checkpoint not present: {checkpoint}")
        return 0

    out_dir = checkpoint.with_name(f"{checkpoint.stem}_hf")
    weights_out = out_dir / "model.safetensors"
    config_out = out_dir / "config.json"
    stamp = out_dir / "source.json"

    source = source_stamp(checkpoint)
    if not force and weights_out.is_file() and config_out.is_file() and stamp.is_file():
        try:
            existing = json.loads(stamp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        if existing == source:
            print(f"[OK] {out_dir.name} is already current; nothing to do.")
            return 0
        if isinstance(existing, dict) and existing.get("stamp_version") != STAMP_VERSION:
            print(
                f"[INFO] {stamp.name} uses stamp v{existing.get('stamp_version', 1)}; "
                f"reconverting to record a digest."
            )

    import torch
    from safetensors.torch import save_file
    from transformers import SwinConfig

    print(f"Reading {checkpoint} ({checkpoint.stat().st_size / 1e6:.1f} MB)")
    try:
        saved = torch.load(checkpoint, map_location="cpu", mmap=True, weights_only=False)
    except (TypeError, RuntimeError, ValueError):
        saved = torch.load(checkpoint, map_location="cpu", weights_only=False)

    if not (isinstance(saved, dict) and "state_dict" in saved and "config" in saved):
        print(
            "[ERROR] Expected a {'config': ..., 'state_dict': ...} checkpoint. "
            f"Got: {type(saved).__name__} "
            f"keys={list(saved)[:8] if isinstance(saved, dict) else 'n/a'}"
        )
        return 1

    config = SwinConfig(**saved["config"])
    state_dict = saved["state_dict"]

    out_dir.mkdir(parents=True, exist_ok=True)
    config.save_pretrained(out_dir)
    # safetensors requires contiguous, non-shared storages.
    save_file(
        {key: value.contiguous().clone() for key, value in state_dict.items()},
        str(weights_out),
        metadata={"format": "pt"},
    )

    params = sum(v.numel() for v in state_dict.values())
    print(
        f"[OK] wrote {out_dir} "
        f"({sum(p.stat().st_size for p in out_dir.iterdir()) / 1e6:.1f} MB, "
        f"{len(state_dict)} tensors, {params / 1e6:.1f}M params)"
    )

    # Verify: reload from the new directory and compare every tensor.
    from transformers import SwinForImageClassification

    reloaded = SwinForImageClassification.from_pretrained(out_dir, local_files_only=True)
    reloaded_sd = reloaded.state_dict()
    mismatched = [k for k in state_dict if k not in reloaded_sd]
    for key, original in state_dict.items():
        if key in reloaded_sd and not torch.equal(reloaded_sd[key], original):
            mismatched.append(key)
    if mismatched:
        print(f"[ERROR] {len(mismatched)} tensor(s) differ after round-trip: {mismatched[:5]}")
        return 1
    leftover_meta = [
        n
        for n, t in list(reloaded.named_parameters()) + list(reloaded.named_buffers())
        if t.is_meta
    ]
    if leftover_meta:
        print(f"[ERROR] {len(leftover_meta)} tensor(s) left on the meta device: {leftover_meta[:5]}")
        return 1
    print(f"[OK] verified: all {len(state_dict)} tensors round-trip bit-identically.")

    stamp.write_text(json.dumps(source, indent=2), encoding="utf-8")
    # The container runs as a non-root user; a 0600 artefact written during build
    # is unreadable at runtime. safetensors inherits the umask, so set it here.
    for artefact in (weights_out, config_out, stamp):
        if artefact.is_file():
            artefact.chmod(0o644)
    out_dir.chmod(0o755)
    print(f"[OK] {out_dir.name} stamped (v{STAMP_VERSION}, sha256={source['sha256'][:16]}...)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--force", action="store_true", help="Reconvert even if up to date.")
    args = parser.parse_args()
    return convert(args.checkpoint.expanduser(), force=args.force)


if __name__ == "__main__":
    sys.exit(main())
