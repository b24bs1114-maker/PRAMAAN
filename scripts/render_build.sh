#!/usr/bin/env bash
# PRAMAAN -- build step for a NATIVE PYTHON Render service (the secondary path).
#
# render.yaml deploys backend/Dockerfile, and that is the supported deployment.
# This script exists for a service that was created with Render's Python runtime
# instead, and it has one limitation that no build step can fix: Render's native
# runtime cannot install system packages, so ffmpeg and libsndfile1 are absent.
# Audio and video files therefore cannot be decoded on such a service -- those
# modalities report an honest failure to read the media, not a forensic verdict.
# Image analysis, hashing, near-duplicate retrieval, metadata, C2PA provenance and
# the JPEG/ELA forensics all work.
#
# Storage is the other difference: a Python service still needs a persistent disk
# (and therefore a paid instance) for the case database, evidence store, hash
# index, reports and audit chain to survive a restart. Point PRAMAAN_DATA_DIR and
# PRAMAAN_REPORTS_DIR at the mount, exactly as render.yaml does.
set -euo pipefail

echo "=== PRAMAAN Render Build Step ==="

# 1. Upgrade pip & install requirements
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install -e ./pramaan-detector[all]

# 2. Provision model checkpoints from the pinned GitHub Release and verify each
#    one's SHA-256 against pramaan-detector/weights/model_manifest.json.
#    PRAMAAN_WEIGHTS_MODALITIES / *_WEIGHTS_URL / PRAMAAN_WEIGHTS_RELEASE_TAG come
#    from the service's environment. Set PRAMAAN_WEIGHTS_MODALITIES=none to
#    provision nothing on an instance too small to load a model; leaving it unset
#    provisions the image checkpoint.
python3 scripts/download_weights.py

# 3. Report exactly which assets landed. --no-load keeps this cheap: it checks
#    the bytes, it does not build a model. Non-fatal unless the deploy asked for
#    strictness, so a missing asset degrades to an honest UNAVAILABLE verdict
#    rather than a red deploy.
if [ "${PRAMAAN_FAIL_ON_MISSING_WEIGHTS:-0}" = "1" ]; then
  python3 scripts/verify_model_assets.py --no-load
else
  python3 scripts/verify_model_assets.py --no-load \
    || echo "[WARN] some model assets are absent or unverified; affected modalities will report UNAVAILABLE"
fi

# 4. When the audio checkpoint was provisioned, cache its architecture config --
#    config.json only, never weights; the tensors come from the verified
#    checkpoint on disk. With PRAMAAN_DETECTOR_OFFLINE=true the runtime may not
#    reach the hub, and without this cached config the audio model cannot be
#    constructed at all.
#
#    This step used to pre-cache torchvision's EfficientNet-B0 "the video frame
#    model falls back to". That fallback no longer exists: with no trained video
#    checkpoint the detector builds no model and abstains, so the download only
#    made a deploy slower and implied a video capability that is not there.
case ",${PRAMAAN_WEIGHTS_MODALITIES:-image}," in
  *,audio,*)
    python3 - <<'PY' || echo "[WARN] could not cache the audio architecture config; audio will report INSUFFICIENT_EVIDENCE"
from transformers import AutoConfig
c = AutoConfig.from_pretrained("garystafford/wav2vec2-deepfake-voice-detector")
print("cached audio architecture config:", c.architectures, c.id2label)
PY
    ;;
  *) echo "[NOTE] audio not provisioned; architecture config not cached" ;;
esac

echo "=== Render Build Step Complete ==="
