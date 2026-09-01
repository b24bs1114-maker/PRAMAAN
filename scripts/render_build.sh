#!/usr/bin/env bash
set -euo pipefail

echo "=== PRAMAAN Render Build Step ==="

# 1. Upgrade pip & install requirements
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install -e ./pramaan-detector[all]

# 2. Provision model checkpoints from the pinned GitHub Release and verify each
#    one's SHA-256 against pramaan-detector/weights/model_manifest.json.
#    PRAMAAN_WEIGHTS_MODALITIES / *_WEIGHTS_URL / PRAMAAN_WEIGHTS_RELEASE_TAG
#    come from render.yaml.
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

# 4. Cache the torchvision EfficientNet-B0 weights the video frame model falls
#    back to, so the first video analysis does not download 20 MB mid-request.
#    Best-effort: no egress at build time is not a reason to fail the deploy.
python3 - <<'PY' || echo "[WARN] could not pre-cache EfficientNet-B0; it will be fetched on first video analysis"
from torchvision.models import EfficientNet_B0_Weights as W
print("cached EfficientNet-B0:", len(W.DEFAULT.get_state_dict(progress=False)), "tensors")
PY

echo "=== Render Build Step Complete ==="
