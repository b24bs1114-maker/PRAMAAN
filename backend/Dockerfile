# PRAMAAN backend -- Production Dockerfile (Repository Root Context).
# Compatible with Render (Root Directory: blank, Dockerfile Path: backend/Dockerfile)

FROM python:3.12-slim AS runtime

# Python environment: unbuffered logs, no .pyc clutter, no pip nag
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

ARG PIP_EXTRA_ARGS=""

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

# 3. Copy production backend application code (no test files or frontend)
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
    PRAMAAN_IMAGE_MODEL_PATH=/app/pramaan-detector/weights/image_detector.pt \
    PRAMAAN_VIDEO_MODEL_PATH=/app/pramaan-detector/weights/image_detector.pt \
    PRAMAAN_IMAGE_DETECTOR_ENTRYPOINT=pramaan.detectors.image_detector:detect_image \
    PRAMAAN_VIDEO_DETECTOR_ENTRYPOINT=pramaan.detectors.video_detector:detect_video \
    PYTHONPATH=/app/backend

VOLUME ["/data", "/reports"]

USER pramaan
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json,sys,urllib.request;\
r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4);\
sys.exit(0 if r.status==200 and json.load(r).get('status')=='ok' else 1)"]

CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-server-header"]
