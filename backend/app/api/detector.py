"""Detector endpoints: capability status and direct multi-modal inference.

* ``GET /api/detector/status`` -- what is installed, per modality, and why not.
* ``POST /api/detect`` -- run the installed detector against one file or one
  existing evidence item. This is the route a plugged-in model is exercised
  through; it is deliberately thin, because everything that decides *what the
  answer means* lives in the detector adapter and the fusion engine.

The route never fabricates a result. With no model installed it returns the
adapter's abstention (``manipulation_score: null``, ``abstained: true``) with the
reason, which is a 200 because the question was answered truthfully: this
deployment cannot measure that file.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.api.deps import DbDep, SettingsDep
from app.models import Evidence
from app.schemas.api import DetectorResultResponse, DetectorStatusResponse
from app.services import audit
from app.services import detector as detector_service
from app.services.storage import absolute_path

logger = logging.getLogger("pramaan.api.detector")

router = APIRouter(prefix="", tags=["detector"])

#: Uploaded filenames are client-supplied, so only a conservative extension is
#: reused when staging the file. The suffix matters because the detector routes
#: on it when no media_type is declared.
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,12}$")

_READ_CHUNK = 1024 * 1024


@router.get(
    "/api/detector/status",
    response_model=DetectorStatusResponse,
    summary="AI detector availability and model identity",
)
def detector_status(settings: SettingsDep) -> DetectorStatusResponse:
    """Report the active detector adapters, models, versions and availability."""
    return DetectorStatusResponse(**detector_service.status(settings))


def _safe_suffix(filename: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if _SAFE_SUFFIX.match(suffix) else ""


async def _stage_upload(file: UploadFile, settings: SettingsDep) -> Path:
    """Stream an upload into the configured temp dir under the size cap.

    Staged inside ``settings.temp_dir`` rather than the system temp directory so
    the bytes stay on the deployment's own storage, and streamed rather than read
    whole so a large file cannot exhaust memory before the cap is checked.
    """
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    target = settings.temp_dir / f"detect-{uuid.uuid4().hex}{_safe_suffix(file.filename)}"
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await file.read(_READ_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            "File exceeds the maximum upload size of "
                            f"{settings.max_upload_bytes} bytes."
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        logger.error("Could not stage detection upload: %s", exc.__class__.__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not write the uploaded file to storage.",
        ) from exc

    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty."
        )
    return target


@router.post(
    "/api/detect",
    response_model=DetectorResultResponse,
    summary="Direct multi-modal AI manipulation inference (image, video, audio)",
)
async def detect_media(
    settings: SettingsDep,
    db: DbDep,
    file: Annotated[
        UploadFile | None, File(description="Media file to analyse (image, video or audio)")
    ] = None,
    evidence_id: Annotated[
        str | None, Form(description="Existing evidence id to run detection against")
    ] = None,
    media_type: Annotated[
        str | None, Form(description="Media type override: image | video | audio")
    ] = None,
) -> DetectorResultResponse:
    """Run the installed detector against an upload or an existing evidence item.

    ``media_type`` is optional: when omitted the detector routes on the stored
    media type (for evidence) or the file extension (for an upload). An
    unrecognised type is reported as ``UNSUPPORTED_MEDIA`` rather than being
    guessed at as an image -- routing an audio file to an image model would
    produce either a crash or a meaningless number.
    """
    detector = detector_service.get_detector(settings)

    if evidence_id:
        evidence = db.get(Evidence, evidence_id)
        if evidence is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Evidence {evidence_id} not found.",
            )
        path = absolute_path(evidence.stored_path, settings)
        if not path.is_file():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Evidence {evidence_id} is registered but its stored file is "
                    "missing from this host, so it cannot be analysed."
                ),
            )
        result = detector.analyse(path, media_type=media_type or evidence.media_type)

        # An ad-hoc examination of registered evidence belongs in the chain of
        # custody even though this route stores no analysis row: the audit trail
        # should show that the file was examined, and when.
        audit.record(
            db,
            event=audit.EVENT_DETECTOR_RUN,
            case_id=evidence.case_id,
            actor="api",
            details={
                "evidence_id": evidence.id,
                "route": "POST /api/detect",
                "adapter": detector.id,
                "model": result.model,
                "model_version": result.model_version,
                "weights_hash": result.weights_hash,
                "status": result.status,
                "score": result.score,
                "media_type": result.media_type,
                "latency_ms": result.latency_ms,
                "interface_version": result.interface_version,
                "persisted": False,
            },
        )
        return DetectorResultResponse(**result.to_dict())

    if file is not None:
        staged = await _stage_upload(file, settings)
        try:
            # No default of "image" for an unknown extension: the dispatcher
            # resolves the modality and abstains honestly when it is not one it
            # covers.
            declared = media_type or ""
            result = detector.analyse(staged, media_type=declared)
            return DetectorResultResponse(**result.to_dict())
        finally:
            staged.unlink(missing_ok=True)

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Either a 'file' upload or an 'evidence_id' form field is required.",
    )
