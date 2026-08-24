#!/usr/bin/env bash
set -euo pipefail

echo "=== PRAMAAN Render Build Step ==="

# 1. Upgrade pip & install requirements
pip install --upgrade pip
pip install -r backend/requirements.txt
pip install -e ./pramaan-detector[all]

# 2. Download weights if required
python3 scripts/download_weights.py

echo "=== Render Build Step Complete ==="
