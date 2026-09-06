# PRAMAAN model weights

Nothing in this directory except the JSON/YAML files is tracked in Git.

`model_manifest.json` is the single source of truth: it declares each
modality's checkpoint filename, exact size in bytes, SHA-256 digest, and the
GitHub Release tag the asset is published under. The `*.pt.json` /
`*.pth.json` / `*.safetensors.json` sidecars carry the per-model inference
spec (input size, which class index means "manipulated") that the backend
adapter reads.

## Provisioning

    python scripts/download_weights.py --all --strict   # fetch + verify
    python scripts/verify_model_assets.py --no-load     # verify without loading

`download_weights.py` writes to `<name>.part`, verifies the size and SHA-256
against the manifest, and only then renames onto the final path -- so an
interrupted transfer can never leave something that looks like a usable model.
A digest mismatch is a hard failure.

## Expected contents after provisioning

    image_detector.safetensors   87,262,324 B  (ViT-S/16, OwensLab/commfor-model-384)
    audio_detector.pth             1,281,532 B  (AASIST, SpeechAntiSpoofingBenchmarks/AASIST)
    video_detector.safetensors   344,937,328 B  (VideoMAE, Vansh180/VideoMae-ffc23-deepfake-detector)

Each detector loads its primary format above and falls back to the sibling
`.pt` copy if the primary file is missing; only one of the two is needed.

## Replacing a checkpoint

1. Drop the new file in and run `python scripts/verify_model_assets.py --no-load`.
2. Copy the printed size and SHA-256 into `model_manifest.json`.
3. Publish the file as a release asset and bump `release.tag` in the manifest.
