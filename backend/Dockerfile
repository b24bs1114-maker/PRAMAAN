# PRAMAAN backend -- Production Dockerfile (Repository Root Context).
# Compatible with Render (Root Directory: blank, Dockerfile Path: backend/Dockerfile)

FROM python:3.12-slim AS runtime

# Python environment: unbuffered logs, no .pyc clutter, no pip nag
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG PIP_EXTRA_ARGS=""

# --- Model asset provisioning (build-time) ----------------------------------
# Weights are NOT in the build context (see .dockerignore) and NOT in Git. They
# are fetched here from a pinned GitHub Release asset and verified against
# pramaan-detector/weights/model_manifest.json, so the image contents do not
# depend on which checkpoints happened to be on the build machine.
#
# Override the tag to move to a new set of checkpoints; override a *_WEIGHTS_URL
# to pull from a mirror or a signed URL instead.
ARG PRAMAAN_WEIGHTS_RELEASE_REPO=""
ARG PRAMAAN_WEIGHTS_RELEASE_TAG=""
ARG PRAMAAN_WEIGHTS_MODALITIES="image,video"
ARG PRAMAAN_IMAGE_WEIGHTS_URL=""
ARG PRAMAAN_VIDEO_WEIGHTS_URL=""
ARG PRAMAAN_AUDIO_WEIGHTS_URL=""
# "1" makes a build fail rather than produce an image with no detector weights.
ARG PRAMAAN_FAIL_ON_MISSING_WEIGHTS="0"
# "1" also writes the memory-mapped safetensors directory the image loader
# prefers. Off by default: it is a pure optimisation, not required for correctness.
ARG PRAMAAN_CONVERT_SAFETENSORS="0"

# Install system runtime dependencies for OpenCV and audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libsndfile1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Install backend requirements from repository root context (backend/requirements.txt)
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir ${PIP_EXTRA_ARGS} -r requirements.txt

# 2. Copy pramaan-detector runtime package and install it
COPY pramaan-detector ./pramaan-detector
RUN python -m pip install --no-cache-dir ${PIP_EXTRA_ARGS} -e ./pramaan-detector[all]

# 3. Provision and verify model weights into the deterministic image path
#    /app/pramaan-detector/weights/. download_weights.py rejects any file whose
#    SHA-256 does not match the manifest, so a truncated or wrong-revision
#    checkpoint fails the build instead of shipping.
COPY scripts/download_weights.py scripts/verify_model_assets.py scripts/convert_detector_weights.py ./scripts/
RUN set -eu; \
    export PRAMAAN_WEIGHTS_RELEASE_REPO="${PRAMAAN_WEIGHTS_RELEASE_REPO}" \
           PRAMAAN_WEIGHTS_RELEASE_TAG="${PRAMAAN_WEIGHTS_RELEASE_TAG}" \
           PRAMAAN_WEIGHTS_MODALITIES="${PRAMAAN_WEIGHTS_MODALITIES}" \
           PRAMAAN_IMAGE_WEIGHTS_URL="${PRAMAAN_IMAGE_WEIGHTS_URL}" \
           PRAMAAN_VIDEO_WEIGHTS_URL="${PRAMAAN_VIDEO_WEIGHTS_URL}" \
           PRAMAAN_AUDIO_WEIGHTS_URL="${PRAMAAN_AUDIO_WEIGHTS_URL}" \
           PRAMAAN_FAIL_ON_MISSING_WEIGHTS="${PRAMAAN_FAIL_ON_MISSING_WEIGHTS}"; \
    python scripts/download_weights.py; \
    if [ "${PRAMAAN_CONVERT_SAFETENSORS}" = "1" ]; then python scripts/convert_detector_weights.py; fi; \
    if [ "${PRAMAAN_FAIL_ON_MISSING_WEIGHTS}" = "1" ]; then \
        python scripts/verify_model_assets.py --no-load; \
    else \
        python scripts/verify_model_assets.py --no-load \
          || echo "[WARN] model assets absent/unverified; those modalities will report UNAVAILABLE"; \
    fi

# 3b. Bake in the torchvision EfficientNet-B0 weights the video frame model falls
#     back to. Without this the *first video analysis* downloads them from
#     download.pytorch.org at runtime -- a 20 MB fetch that makes the image
#     non-reproducible, stalls the request, and fails outright on an instance
#     with no egress. TORCH_HOME must match the runtime user's, so it is set to a
#     fixed path rather than the build user's $HOME.
ENV TORCH_HOME=/app/.torch
RUN python -c "from torchvision.models import EfficientNet_B0_Weights as W; \
p=W.DEFAULT.get_state_dict(progress=False); print('cached EfficientNet-B0:', len(p), 'tensors')"

# 4. Copy production backend application code (no test files or frontend)
COPY backend/app ./backend/app

# Create non-root runtime user with volume storage directories
RUN groupadd --gid 1000 pramaan \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin pramaan \
    && mkdir -p /data /reports /corpus \
    && chown -R pramaan:pramaan /app /data /reports /corpus

ENV PRAMAAN_DATA_DIR=/data \
    PRAMAAN_REPORTS_DIR=/reports \
    PRAMAAN_CORPUS_DIR=/corpus \
    PRAMAAN_HOST=0.0.0.0 \
    PRAMAAN_PORT=8000 \
    PRAMAAN_ENVIRONMENT=production \
    PRAMAAN_DEBUG=false \
    PRAMAAN_LOG_LEVEL=INFO \
    PRAMAAN_WEIGHTS_DIR=/app/pramaan-detector/weights \
    PRAMAAN_IMAGE_MODEL_PATH=/app/pramaan-detector/weights/image_detector.pt \
    PRAMAAN_VIDEO_MODEL_PATH=/app/pramaan-detector/weights/image_detector.pt \
    PRAMAAN_AUDIO_MODEL_PATH=/app/pramaan-detector/weights/audio_detector.pt \
    PRAMAAN_IMAGE_MODEL_HF_DIR=/app/pramaan-detector/weights/image_detector_hf \
    PRAMAAN_DETECTOR_OFFLINE=true \
    PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT=pramaan.detectors.image_detector:detect_image \
    PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT=pramaan.detectors.video_detector:detect_video \
    PRAMAAN_AUDIO_DETECTOR_ENTRYPOINT=pramaan.detectors.audio_detector:detect_audio \
    PYTHONPATH=/app/backend

VOLUME ["/data", "/reports"]

USER pramaan
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json,sys,urllib.request;\
r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4);\
sys.exit(0 if r.status==200 and json.load(r).get('status')=='ok' else 1)"]

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-server-header"]
