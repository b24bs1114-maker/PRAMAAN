"""Case and evidence endpoints."""

from __future__ import annotations

import logging
import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import CaseDep, DbDep, SettingsDep
from app.models import Evidence
from app.schemas import (
    CaseListResponse,
    CaseOut,
    EvidenceListGlobalResponse,
    EvidenceListResponse,
    EvidenceOut,
    UploadResponse,
)
from app.services import audit, ingestion
from app.services.storage import PayloadTooLargeError, StorageError, resolve_within

logger = logging.getLogger("pramaan.api.cases")

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest evidence (creates a case when no case_id is given)",
)
def upload_evidence(
    db: DbDep,
    settings: SettingsDep,
    response: Response,
    file: Annotated[UploadFile, File(description="Image, video or audio file to ingest")],
    case_id: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    examiner: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    complaint_reference: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Validate, store and fingerprint an uploaded file.

    The file is stored under a server-generated evidence id -- the client
    filename is never used as a path. SHA-256 is computed from the bytes as
    written to disk. Re-submitting identical bytes to the same case returns the
    existing record with ``duplicate: true`` (HTTP 200) rather than storing a
    second copy.
    """
    if case_id:
        case = ingestion.get_case(db, case_id)
        if case is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Case {case_id} not found.",
            )
    else:
        case = ingestion.create_case(
            db,
            title=title,
            description=description,
            examiner=examiner,
            priority=priority or "medium",
            complaint_reference=complaint_reference,
            actor="api",
        )

    try:
        result = ingestion.ingest_stream(
            db,
            stream=file.file,
            filename=file.filename,
            settings=settings,
            case=case,
            declared_mime=file.content_type,
            actor="api",
        )
    except StorageError as exc:
        # Rejections are audited: an attempt to submit an invalid file is itself
        # a fact about the case.
        audit.record(
            db,
            event=audit.EVENT_EVIDENCE_REJECTED,
            case_id=case.id,
            actor="api",
            details={"filename": file.filename, "reason": str(exc)},
        )
        db.commit()
        # An oversized upload is 413; every other rejection is a 400. Both are
        # audited identically -- only the status the client sees differs.
        code = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if isinstance(exc, PayloadTooLargeError)
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    if result.duplicate:
        response.status_code = status.HTTP_200_OK

    evidence_count = db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.case_id == case.id)
    ).scalar_one()

    return UploadResponse(
        case=CaseOut(**ingestion.case_to_dict(case, evidence_count=evidence_count)),
        evidence=ingestion.evidence_to_dict(result.evidence),  # type: ignore[arg-type]
        duplicate=result.duplicate,
        warnings=result.warnings,
    )


@router.get("", response_model=CaseListResponse, summary="List cases")
def list_cases(
    db: DbDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority_filter: Annotated[str | None, Query(alias="priority")] = None,
    query: Annotated[str | None, Query(alias="q")] = None,
    limit: int = 100,
    offset: int = 0,
) -> CaseListResponse:
    from app.models import AnalysisResult, Case

    stmt = select(Case)
    if status_filter and status_filter.lower() != "all":
        if status_filter.lower() == "active":
            stmt = stmt.where(Case.status != "closed")
        else:
            stmt = stmt.where(Case.status == status_filter.lower())
    if priority_filter and priority_filter.lower() != "all":
        stmt = stmt.where(Case.priority == priority_filter.lower())
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            (Case.case_number.like(pattern))
            | (Case.title.like(pattern))
            | (Case.description.like(pattern))
            | (Case.examiner.like(pattern))
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(Case.created_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    cases = []
    for case in rows:
        count = db.execute(
            select(func.count()).select_from(Evidence).where(Evidence.case_id == case.id)
        ).scalar_one()
        latest_res = db.execute(
            select(AnalysisResult)
            .where(AnalysisResult.case_id == case.id)
            .order_by(AnalysisResult.created_at.desc())
            .limit(1)
        ).scalars().first()

        cdict = ingestion.case_to_dict(case, evidence_count=count)
        if latest_res:
            cdict["latest_verdict"] = latest_res.verdict
        cases.append(CaseOut(**cdict))
    return CaseListResponse(count=total, cases=cases)


@router.get("/{case_id}", response_model=CaseOut, summary="Get one case")
def get_case(case: CaseDep, db: DbDep) -> CaseOut:
    from app.models import AnalysisResult

    count = db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.case_id == case.id)
    ).scalar_one()
    latest_res = db.execute(
        select(AnalysisResult)
        .where(AnalysisResult.case_id == case.id)
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    ).scalars().first()

    cdict = ingestion.case_to_dict(case, evidence_count=count)
    if latest_res:
        cdict["latest_verdict"] = latest_res.verdict
    return CaseOut(**cdict)


@router.patch("/{case_id}", response_model=CaseOut, summary="Update case fields")
def update_case(
    case: CaseDep,
    db: DbDep,
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    examiner: Annotated[str | None, Form()] = None,
    case_status: Annotated[str | None, Form()] = None,
    priority: Annotated[str | None, Form()] = None,
    complaint_reference: Annotated[str | None, Form()] = None,
) -> CaseOut:
    changed: dict[str, str] = {}
    for field, value in (
        ("title", title),
        ("description", description),
        ("examiner", examiner),
        ("status", case_status),
        ("priority", priority),
        ("complaint_reference", complaint_reference),
    ):
        if value is not None:
            setattr(case, field, value)
            changed[field] = value
    db.flush()
    if changed:
        audit.record(
            db,
            event=audit.EVENT_CASE_UPDATED,
            case_id=case.id,
            actor="api",
            details={"changed_fields": sorted(changed)},
        )
    count = db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.case_id == case.id)
    ).scalar_one()
    return CaseOut(**ingestion.case_to_dict(case, evidence_count=count))


@router.delete(
    "/{case_id}", status_code=status.HTTP_200_OK, summary="Delete a case and its evidence"
)
def delete_case(case: CaseDep, db: DbDep, settings: SettingsDep) -> dict[str, object]:
    """Delete a case, its evidence rows and its stored files.

    The audit chain is intentionally *not* pruned: audit rows are append-only and
    survive deletion of the case they describe.
    """
    case_id = case.id
    evidence_ids = [
        row.id
        for row in db.execute(
            select(Evidence).where(Evidence.case_id == case_id)
        ).scalars()
    ]
    db.delete(case)
    db.flush()

    try:
        case_dir = resolve_within(settings.evidence_dir, "cases", case_id)
        if case_dir.exists():
            shutil.rmtree(case_dir)
    except (StorageError, OSError) as exc:
        logger.warning("Could not remove evidence directory for %s: %s", case_id, exc)

    audit.record(
        db,
        event=audit.EVENT_CASE_DELETED,
        case_id=case_id,
        actor="api",
        details={"deleted_evidence_count": len(evidence_ids)},
    )
    return {
        "status": "deleted",
        "case_id": case_id,
        "deleted_evidence_count": len(evidence_ids),
    }


@router.get(
    "/{case_id}/evidence",
    response_model=EvidenceListResponse,
    summary="List evidence for a case",
)
def list_evidence(case: CaseDep, db: DbDep) -> EvidenceListResponse:
    rows = list(
        db.execute(
            select(Evidence)
            .where(Evidence.case_id == case.id)
            .order_by(Evidence.ingested_at.desc())
        ).scalars()
    )
    return EvidenceListResponse(
        case_id=case.id,
        count=len(rows),
        evidence=[ingestion.evidence_to_dict(ev) for ev in rows],  # type: ignore[arg-type]
    )


@router.get(
    "/library/all",
    response_model=EvidenceListGlobalResponse,
    summary="List evidence globally across all cases",
)
def list_global_evidence(
    db: DbDep,
    media_type: Annotated[str | None, Query()] = None,
    query: Annotated[str | None, Query(alias="q")] = None,
    limit: int = 100,
    offset: int = 0,
) -> EvidenceListGlobalResponse:
    stmt = select(Evidence)
    if media_type and media_type.lower() != "all":
        stmt = stmt.where(Evidence.media_type == media_type.lower())
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(
            (Evidence.filename.like(pattern))
            | (Evidence.sha256.like(pattern))
            | (Evidence.id.like(pattern))
            | (Evidence.case_id.like(pattern))
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = list(
        db.execute(
            stmt.order_by(Evidence.ingested_at.desc()).limit(limit).offset(offset)
        ).scalars()
    )
    return EvidenceListGlobalResponse(
        total=total,
        evidence=[EvidenceOut(**ingestion.evidence_to_dict(ev)) for ev in rows],
    )
