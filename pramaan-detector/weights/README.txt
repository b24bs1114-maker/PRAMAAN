# PRAMAAN model weights

Nothing in this directory except the JSON files is tracked in Git.

`model_manifest.json` is the single source of truth: it declares each
modality's checkpoint filename, exact size in bytes, SHA-256 digest, and the
GitHub Release tag the asset is published under. The `*.pt.json` sidecars carry
the per-model inference spec (input size, which class index means "manipulated")
that the backend adapter reads.

## Provisioning

    python scripts/download_weights.py --all --strict   # fetch + verify
    python scripts/verify_model_assets.py               # verify + load

`download_weights.py` writes to `<name>.part`, verifies the size and SHA-256
against the manifest, and only then renames onto the final path -- so an
interrupted transfer can never leave something that looks like a usable model.
A digest mismatch is a hard failure.

## Expected contents after provisioning

    image_detector.pt          347,151,670 B  sha256 25edbe34...  (Swin-B, image + video)
    audio_detector.pt        1,262,977,339 B  sha256 79945f59...  (Wav2Vec2)
    image_detector_hf/         derived, optional -- see below

## image_detector_hf/ (derived, never a release asset)

    python scripts/convert_detector_weights.py

Rewrites `image_detector.pt` as `config.json` + `model.safetensors` so the model
can be memory-mapped at load (measured peak 396 MB vs 1052 MB). It is a pure
optimisation: the tensors are asserted bit-identical, so scores do not change.
`source.json` records the digest of the checkpoint it was derived from; the
loader ignores the directory if that does not match the checkpoint in use.

## Replacing a checkpoint

1. Drop the new file in and run `python scripts/verify_model_assets.py --no-load`.
2. Copy the printed size and SHA-256 into `model_manifest.json`.
3. Re-run `python scripts/convert_detector_weights.py` if the HF directory exists.
4. Publish the file as a release asset and bump `release.tag` in the manifest.
