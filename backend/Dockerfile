# PRAMAAN backend -- offline / on-premise container image.
#
# Design constraints this file honours:
#   * No network at RUNTIME. Nothing is fetched, phoned home, or checked for
#     updates after the image is built. Network access is only needed once, at
#     build time, to install the pinned dependencies (see PIP_EXTRA_ARGS below
#     for the air-gapped route).
#   * SQLite only. No PostgreSQL, no Redis, no message broker, no orchestrator.
#     One process, one database file.
#   * All mutable state lives outside the image, under /data and /reports, so a
#     container can be destroyed and recreated without losing evidence, the
#     database, the perceptual index or generated reports.
#   * Runs as a non-root user.

FROM python:3.12-slim AS runtime

# Python behaviour: no .pyc clutter in the layer, unbuffered logs so `docker
# logs` shows them immediately, no pip version nag.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Air-gapped build: populate a wheelhouse next to this Dockerfile and build with
#   docker build --build-arg PIP_EXTRA_ARGS="--no-index --find-links=/wheels" .
# after adding `COPY wheels /wheels` above the install step. Left as a build arg
# rather than baked in, because an empty COPY source would fail the build.
ARG PIP_EXTRA_ARGS=""

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
# No apt packages are needed -- SQLite is in the Python standard library and
# Pillow ships manylinux wheels.
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir ${PIP_EXTRA_ARGS} -r requirements.txt

# Copy production application code (runtime files only, no test files or test config)
COPY app ./app

# Non-root user, uid/gid 1000 so bind-mounted host directories line up with the
# usual first Linux desktop account. Override with `user:` in compose if yours
# differs.
RUN groupadd --gid 1000 pramaan \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin pramaan \
    && mkdir -p /data /reports /corpus \
    && chown -R pramaan:pramaan /app /data /reports /corpus

# Storage lives on volumes, not in the image. Paths are absolute so they do not
# depend on where the code happens to be mounted.
#   /data    SQLite database + evidence store + perceptual index
#   /reports generated forensic PDFs
#   /corpus  reference corpus searched for near-duplicates
ENV PRAMAAN_DATA_DIR=/data \
    PRAMAAN_REPORTS_DIR=/reports \
    PRAMAAN_CORPUS_DIR=/corpus \
    PRAMAAN_HOST=0.0.0.0 \
    PRAMAAN_PORT=8000 \
    PRAMAAN_ENVIRONMENT=production \
    PRAMAAN_DEBUG=false \
    PRAMAAN_LOG_LEVEL=INFO

VOLUME ["/data", "/reports"]

USER pramaan
EXPOSE 8000

# Health check with the standard library only -- no curl/wget in the slim image,
# and adding one would mean an apt layer for nothing. Fails on any status other
# than 200 with {"status": "ok"}.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json,sys,urllib.request;\
r=urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4);\
sys.exit(0 if r.status==200 and json.load(r).get('status')=='ok' else 1)"]

# One uvicorn worker: SQLite is single-writer and the audit chain is sequential,
# so concurrent writers would contend on both. Scale by running more cases, not
# more workers.
CMD ["python", "-m", "uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--no-server-header"]
