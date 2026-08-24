"""Index endpoints: status, rebuild, add, and direct corpus ingestion.

The index holds pHash vectors for every image in the database (case evidence and
corpus items alike) and is what near-duplicate retrieval searches. It is derived
state: ``POST /api/index/rebuild`` regenerates it from the database at any time.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from app.api.deps import DbDep, SettingsDep
from app.models import ROLE_CORPUS, Evidence
from app.schemas.api import IndexOperationResponse, IndexStatusResponse
from app.services import indexing, ingestion
from app.services.storage import PayloadTooLargeError, StorageError

logger = logging.getLogger("pramaan.api.index")

router = APIRouter(prefix="/api/index", tags=["index"])


@router.get("/status", response_model=IndexStatusResponse, summary="Index status")
def index_status(settings: SettingsDep) -> IndexStatusResponse:
    """Size, version, freshness and active backend of the perceptual index."""
    return IndexStatusResponse(**indexing.status(settings))


@router.post(
    "/rebuild",
    response_model=IndexOperationResponse,
    summary="Rebuild the index from the database",
)
def rebuild_index(db: DbDep, settings: SettingsDep) -> IndexOperationResponse:
    """Discard the index and rebuild it from every hashed evidence row."""
    return IndexOperationResponse(**indexing.rebuild(db, settings=settings, actor="api"))


@router.post(
    "/add/{evidence_id}",
    response_model=IndexOperationResponse,
    summary="Add one existing evidence item to the index",
)
def add_to_index(
    evidence_id: str, db: DbDep, settings: SettingsDep
) -> IndexOperationResponse:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Evidence {evidence_id} not found.",
        )
    return IndexOperationResponse(
        **indexing.add_evidence(db, evidence=evidence, settings=settings, actor="api")
    )


@router.post(
    "/ingest",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an image straight into the searchable corpus",
)
def ingest_into_index(
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    file: UploadFile = File(..., description="Image to add to the corpus"),
    source_id: str | None = Form(None, description="Lineage group identifier"),
    parent_id: str | None = Form(None, description="Immediate predecessor id"),
    generation: int | None = Form(None, description="Distance from the lineage root"),
    platform: str | None = Form(None, description="Where this copy was observed"),
    observed_at: str | None = Form(None, description="ISO-8601 observation time"),
    transformation: str | None = Form(None, description="Applied transformation"),
    is_synthetic: bool = Form(
        False, description="Mark as synthetic/demo data rather than real evidence"
    ),
) -> dict:
    """Store, hash and index one image so it is immediately searchable.

    Corpus items carry no case: they are reference material for near-duplicate
    retrieval, not evidence under examination.
    """
    try:
        result = ingestion.ingest_stream(
            db,
            stream=file.file,
            filename=file.filename,
            settings=settings,
            case=None,
            declared_mime=file.content_type,
            role=ROLE_CORPUS,
            actor="api",
            provenance={
                "source_id": source_id,
                "parent_id": parent_id,
                "generation": generation,
                "platform": platform,
                "observed_at": observed_at,
                "transformation": transformation,
            },
            is_synthetic=is_synthetic,
        )
    except StorageError as exc:
        # Same split as the case upload: oversized is 413, anything else 400.
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if isinstance(exc, PayloadTooLargeError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    index_result = indexing.add_evidence(
        db, evidence=result.evidence, settings=settings, actor="api"
    )
    if result.duplicate:
        # Nothing new was stored, so 201 would be a lie.
        response.status_code = status.HTTP_200_OK
    return {
        "evidence": ingestion.evidence_to_dict(result.evidence),
        "duplicate": result.duplicate,
        "warnings": result.warnings,
        "index": index_result,
        "searchable": index_result["indexed_count"] > 0
        and index_result["status"] in {"added", "already_indexed"},
    }
