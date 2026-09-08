"""PRAMAAN Local Inference Service.

A small standalone FastAPI app that exposes the existing multi-modal
detector (pramaan-detector) over HTTP on the local machine. It adds no
detection logic of its own: it routes an uploaded file to DetectorService,
which routes to ImageDetector / VideoDetector / AudioDetector, and returns
the DetectionResult those detectors produced, unchanged.

Design notes
------------
* Binds to 127.0.0.1 only (see uvicorn command in README / __main__). Not
  for public exposure.
* Optional shared-secret check via PRAMAAN_LOCAL_API_KEY: when set, requests
  must carry ``X-API-Key`` (or ``Authorization: Bearer <key>``). When unset,
  the service runs open on localhost -- convenient for local testing.
* Weights are loaded lazily by the detectors themselves from
  pramaan-detector/weights/ (no download, no substitution).
* Uploads are written to a per-request temp dir and deleted after
  inference, win or fail.
* No score is ever invented: an unavailable / abstaining / failed detector
  returns ``manipulation_score: null`` with a truthful explanation, exactly
  as the in-process contract does.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pramaan.local_service")

SERVICE_NAME = "pramaan-local-inference"
SERVICE_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]  # .../PRAMAAN/scripts/local-inference/service.py -> PRAMAAN
WEIGHTS_DIR = _REPO_ROOT / "pramaan-detector" / "weights"
DATA_DIR = _REPO_ROOT / "pramaan-detector" / "data"

_API_KEY = os.environ.get("PRAMAAN_LOCAL_API_KEY", "").strip()


def _api_key() -> str:
    """Current shared secret (env at start; tests may override)."""
    return _API_KEY
MAX_UPLOAD_BYTES = int(os.environ.get("PRAMAAN_LOCAL_MAX_UPLOAD_MB", "500")) * 1024 * 1024

# Mirrors DetectorService routing (pramaan/service.py): extension determines
# the modality; the declared media_type (form field) wins when given.
MEDIA_TYPES: dict[str, str] = {
    "image": "image",
    "img": "image",
    "photo": "image",
    "picture": "image",
    "video": "video",
    "vid": "video",
    "movie": "video",
    "clip": "video",
    "audio": "audio",
    "sound": "audio",
    "speech": "audio",
    "voice": "audio",
}


def _resolve_modality(declared: str | None, filename: str | None) -> tuple[str | None, str]:
    """Return (modality, route_note) or (None, reason) when unresolvable."""
    if declared:
        modality = MEDIA_TYPES.get(declared.lower().strip())
        if modality:
            return modality, "declared"
    suffix = Path(filename or "").suffix.lower()
    ext_modality: dict[str, str] = {}
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        ext_modality[ext] = "image"
    for ext in (".mp4", ".mov"):
        ext_modality[ext] = "video"
    for ext in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"):
        ext_modality[ext] = "audio"
    if suffix in ext_modality:
        return ext_modality[suffix], "file extension"
    return None, (
        f"Cannot determine modality for file {filename!r}. Supported: image "
        "(.jpg/.jpeg/.png/.webp), video (.mp4/.mov), audio (.wav/.mp3/.m4a/.aac/.flac/.ogg). "
        "Pass an explicit media_type for the explicit /detect/<modality> endpoints."
    )


# ---------------------------------------------------------------------------
# Detector singleton (loads existing weights, no duplication of model code)
# ---------------------------------------------------------------------------
_detector: Any = None
_detector_lock = threading.Lock()


def get_detector_service() -> Any:
    global _detector
    with _detector_lock:
        if _detector is None:
            from pramaan.service import DetectorService

            _detector = DetectorService(
                image_weights=str(WEIGHTS_DIR / "image_detector.safetensors"),
                video_weights=str(WEIGHTS_DIR / "video_detector.safetensors"),
                audio_weights=str(WEIGHTS_DIR / "audio_detector.pth"),
                device="cpu",
            )
            logger.info("DetectorService initialised with weights from %s", WEIGHTS_DIR)
        return _detector


async def check_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> None:
    secret = _api_key()
    if not secret:
        return
    if x_api_key == secret:
        return
    if authorization and authorization.lower().startswith("bearer "):
        if authorization.split(" ", 1)[1].strip() == secret:
            return
    raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not WEIGHTS_DIR.is_dir():
        logger.warning("Weights directory not found: %s", WEIGHTS_DIR)
    logger.info(
        "%s v%s starting (auth %s, weights dir %s)",
        SERVICE_NAME, SERVICE_VERSION,
        "enabled" if _API_KEY else "disabled (localhost only)",
        WEIGHTS_DIR,
    )
    yield


app = FastAPI(
    title=SERVICE_NAME,
    version=SERVICE_VERSION,
    description="Local-only HTTP wrapper around the PRAMAAN multi-modal detector.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def _checkpoint_info() -> dict[str, Any]:
    """Presence of each checkpoint file -- a fact, not a load attempt."""
    expected = {
        "image": "image_detector.safetensors",
        "video": "video_detector.safetensors",
        "audio": "audio_detector.pth",
    }
    out: dict[str, Any] = {}
    for modality, name in expected.items():
        path = WEIGHTS_DIR / name
        out[modality] = {
            "weights_file": name,
            "weights_present": path.is_file(),
            "weights_path": str(path),
        }
    return out


@app.get("/health")
async def health() -> dict[str, Any]:
    checkpoints = _checkpoint_info()
    modalities: dict[str, Any] = {}
    for modality, info in checkpoints.items():
        present = info["weights_present"]
        modalities[modality] = {
            "available": present,
            "weights_present": present,
            "weights_file": info["weights_file"],
        }
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "detector_service": "pramaan.detector.service.DetectorService",
        "weights_dir": str(WEIGHTS_DIR),
        "modalities": modalities,
        "api_key_required": bool(_API_KEY),
        "time": time.time(),
    }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
async def _run_detection(
    media_type: str | None, upload: UploadFile
) -> tuple[int, dict[str, Any]]:
    modality, note = _resolve_modality(media_type, upload.filename)
    if modality is None:
        # Unsupported/unroutable media is a 4xx on the transport layer, but the
        # body still uses the truthful-abstention shape the detectors use.
        body = {
            "media_type": "unknown",
            "label": "INSUFFICIENT_EVIDENCE",
            "manipulation_score": None,
            "confidence": None,
            "abstained": True,
            "model": SERVICE_NAME,
            "model_version": SERVICE_VERSION,
            "weights_hash": "",
            "latency_ms": 0.0,
            "explanation": note,
            "evidence": {},
            "heatmap_available": False,
            "regions": [],
            "timestamps": [],
        }
        return 415, body

    suffix = Path(upload.filename or "").suffix.lower() or f".{modality}"
    tmp_dir = tempfile.mkdtemp(prefix="pramaan_local_")
    tmp_path = Path(tmp_dir) / f"upload_{uuid.uuid4().hex}{suffix}"
    try:
        size = 0
        with tmp_path.open("wb") as out:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds limit of {MAX_UPLOAD_BYTES} bytes.",
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        detector = get_detector_service()
        started = time.perf_counter()
        try:
            result = detector.detect(str(tmp_path))
        except Exception as exc:  # noqa: BLE001 - truthful error, never a mock score
            logger.exception("Detector crashed on %s upload", modality)
            body = {
                "media_type": modality,
                "label": "INSUFFICIENT_EVIDENCE",
                "manipulation_score": None,
                "confidence": None,
                "abstained": True,
                "model": SERVICE_NAME,
                "model_version": SERVICE_VERSION,
                "weights_hash": "",
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "explanation": (
                    f"The detector raised {exc.__class__.__name__} while analysing this "
                    f"{modality}, so no score was produced. This is NOT a finding about the file."
                ),
                "evidence": {"routed_by": note, "error": str(exc)},
                "heatmap_available": False,
                "regions": [],
                "timestamps": [],
            }
            return 500, body
        payload = result.to_dict()
        payload.setdefault("service", SERVICE_NAME)
        payload.setdefault("service_version", SERVICE_VERSION)
        payload.setdefault("evidence", {})
        payload["evidence"].setdefault("routed_by", note)
        return 200, payload
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.post("/detect")
async def detect(
    file: UploadFile = File(..., description="Media file (image/video/audio)"),
    media_type: str | None = None,
    _auth: None = Depends(check_api_key),
) -> JSONResponse:
    status, body = await _run_detection(None, file)
    return JSONResponse(status_code=status, content=body)


@app.post("/detect/image")
async def detect_image(
    file: UploadFile = File(...),
    _auth: None = Depends(check_api_key),
) -> JSONResponse:
    status, body = await _run_detection("image", file)
    return JSONResponse(status_code=status, content=body)


@app.post("/detect/video")
async def detect_video(
    file: UploadFile = File(...),
    _auth: None = Depends(check_api_key),
) -> JSONResponse:
    status, body = await _run_detection("video", file)
    return JSONResponse(status_code=status, content=body)


@app.post("/detect/audio")
async def detect_audio(
    file: UploadFile = File(...),
    _auth: None = Depends(check_api_key),
) -> JSONResponse:
    status, body = await _run_detection("audio", file)
    return JSONResponse(status_code=status, content=body)


def main() -> None:  # pragma: no cover - manual entry point
    import uvicorn

    uvicorn.run(
        "local_inference_service.service:app",
        host="127.0.0.1",
        port=int(os.environ.get("PRAMAAN_LOCAL_PORT", 8432)),
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
