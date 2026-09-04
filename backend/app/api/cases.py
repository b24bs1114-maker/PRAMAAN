"""Case and evidence endpoints."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import ColumnElement, and_, func, or_, select

from app.api.deps import CaseDep, DbDep, SettingsDep
from app.config import Settings
from app.models import (
    AnalysisResult,
    AuditLog,
    Base,
    Evidence,
    Match,
    Report,
    TimelineEvent,
)
from app.schemas import (
    CaseDeleteAudit,
    CaseDeleteCounts,
    CaseDeleteIndex,
    CaseDeleteResponse,
    CaseDeleteStorage,
    CaseListResponse,
    CaseOut,
    EvidenceListGlobalResponse,
    EvidenceListResponse,
    EvidenceOut,
    UploadResponse,
)
from app.services import audit, indexing, ingestion
from app.services.dinov2_index import get_dinov2_index
from app.services.index import get_index
from app.services.storage import (
    PayloadTooLargeError,
    StorageError,
    absolute_path,
    resolve_within,
)
from app.utils.timeutil import iso, utcnow

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
    observed_at: Annotated[str | None, Form()] = None,
    platform: Annotated[str | None, Form()] = None,
    transformation: Annotated[str | None, Form()] = None,
    parent_id: Annotated[str | None, Form()] = None,
    source_id: Annotated[str | None, Form()] = None,
    generation: Annotated[int | None, Form()] = None,
    is_synthetic: Annotated[bool, Form()] = False,
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
        prov = {
            "source_id": source_id,
            "parent_id": parent_id,
            "generation": generation,
            "platform": platform,
            "observed_at": observed_at,
            "transformation": transformation,
        }
        result = ingestion.ingest_stream(
            db,
            stream=file.file,
            filename=file.filename,
            settings=settings,
            case=case,
            declared_mime=file.content_type,
            actor="api",
            provenance=prov,
            is_synthetic=is_synthetic,
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
    else:
        # Add to the perceptual index so this evidence is immediately
        # searchable for near-duplicate retrieval and provenance tracing.
        indexing.add_evidence(
            db, evidence=result.evidence, settings=settings, actor="api"
        )

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


def _resolve_case_owned_files(
    stored_paths: Iterable[str],
    *,
    settings: Settings,
    warnings: list[str],
) -> list[Path]:
    """Resolve evidence ``stored_path`` values to files this case may delete.

    Two independent guards. ``absolute_path`` refuses anything that escapes
    ``data_dir``, which is the path-traversal defence. The result must then sit
    inside ``evidence_dir`` and *outside* the shared ``corpus`` bucket: corpus
    items carry no case id so they cannot be reached through the database, but a
    stored path is still stored data, and shared reference material must not
    become deletable because one row is malformed. A refusal is reported, never
    silently skipped.
    """
    evidence_root = settings.evidence_dir.resolve()
    corpus_root = (settings.evidence_dir / "corpus").resolve()
    resolved: list[Path] = []
    seen: set[Path] = set()
    for stored_path in stored_paths:
        if not stored_path:
            continue
        try:
            path = absolute_path(stored_path, settings)
        except StorageError:
            warnings.append(
                f"Refused to delete {stored_path!r}: path escapes the storage root."
            )
            continue
        if evidence_root not in path.parents or corpus_root in path.parents:
            warnings.append(
                f"Refused to delete {stored_path!r}: not inside this deployment's "
                "case evidence storage."
            )
            continue
        if path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def _resolve_report_files(
    stored_paths: Iterable[str],
    *,
    settings: Settings,
    warnings: list[str],
) -> list[Path]:
    """Resolve report ``stored_path`` values, which are flat names in reports_dir.

    Reports are written directly into ``reports_dir`` rather than a per-case
    subdirectory, so each PDF has to be removed by name. ``resolve_within`` keeps
    a stored name from reaching outside that directory, and the directory itself
    is never a deletion target.
    """
    reports_root = settings.reports_dir.resolve()
    resolved: list[Path] = []
    seen: set[Path] = set()
    for stored_path in stored_paths:
        if not stored_path:
            continue
        try:
            path = resolve_within(settings.reports_dir, stored_path)
        except StorageError:
            warnings.append(
                f"Refused to delete report {stored_path!r}: path escapes the report root."
            )
            continue
        if path == reports_root or path in seen:
            continue
        seen.add(path)
        resolved.append(path)
    return resolved


def _unlink_all(paths: Iterable[Path], *, label: str, warnings: list[str]) -> tuple[int, int]:
    """Unlink files, counting removed against already-missing. Never raises.

    A file the database knows about but the filesystem has already lost is not an
    error: the row is being removed either way, and the count says which happened.
    """
    removed = missing = 0
    for path in paths:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed += 1
            else:
                missing += 1
        except OSError as exc:
            warnings.append(f"Could not remove {label} file {path.name}: {exc}")
    return removed, missing


@router.delete(
    "/{case_id}",
    response_model=CaseDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a case, its evidence, its files and its index entries",
)
def delete_case(case: CaseDep, db: DbDep, settings: SettingsDep) -> CaseDeleteResponse:
    """Permanently delete one case: database rows, stored files and index entries.

    A missing or unknown ``case_id`` is a 404 from ``require_case`` before any of
    this runs, so a delete never succeeds against a case that does not exist.

    The ordering is deliberate:

    1. Everything about to be destroyed is **counted first**, while it still
       exists, so the response and the audit event report measured numbers rather
       than assumptions.
    2. The case row is deleted and flushed. ``PRAGMA foreign_keys=ON`` plus
       ``ON DELETE CASCADE`` removes the evidence, analyses, matches, timeline
       events and report rows that belong to it.
    3. The ``CASE_DELETED`` audit event is appended **inside the same
       transaction**: a case cannot be deleted without its audit entry, and an
       audit failure cannot leave a half-deleted case.
    4. The transaction is committed explicitly.
    5. Only then are files unlinked and the perceptual index pruned.

    Steps 4 and 5 cannot be one atomic unit -- a filesystem is not part of the
    SQLite transaction -- so the order is chosen to fail safe. A crash between
    them leaves orphaned *files* whose rows and audit entry agree with each
    other, which is recoverable. The reverse order could destroy evidence files
    for a case whose deletion then rolled back. For the same reason a problem
    after the commit is reported in ``warnings`` with exact removed/missing
    counts instead of a 5xx, which would claim the deletion had not happened.

    This is a hard delete, not a soft delete, because the audit chain already
    provides the durable record: ``audit_log.case_id`` carries no foreign key to
    ``cases``, so every event for this case -- including this deletion -- stays
    in the chain and stays verifiable through ``GET /api/audit/verify`` after the
    case is gone. A tombstoned case row would add nothing and would require every
    read path to learn to hide it.
    """
    case_id = case.id
    case_number = case.case_number
    case_title = case.title
    case_examiner = case.examiner
    warnings: list[str] = []

    # 1. Count and snapshot while the rows still exist. -----------------------
    evidence_rows = list(
        db.execute(
            select(Evidence.id, Evidence.stored_path).where(Evidence.case_id == case_id)
        ).all()
    )
    evidence_ids = [row.id for row in evidence_rows]
    report_paths = list(
        db.execute(select(Report.stored_path).where(Report.case_id == case_id)).scalars()
    )

    def count_where(model: type[Base], condition: ColumnElement[bool]) -> int:
        return db.execute(
            select(func.count()).select_from(model).where(condition)
        ).scalar_one()

    counts = CaseDeleteCounts(
        evidence=len(evidence_ids),
        analysis_results=count_where(
            AnalysisResult,
            or_(
                AnalysisResult.case_id == case_id,
                AnalysisResult.evidence_id.in_(evidence_ids),
            ),
        ),
        matches=count_where(
            Match,
            or_(
                Match.case_id == case_id,
                Match.query_evidence_id.in_(evidence_ids),
                Match.candidate_evidence_id.in_(evidence_ids),
            ),
        ),
        matches_owned_by_other_cases=count_where(
            Match,
            and_(
                or_(Match.case_id != case_id, Match.case_id.is_(None)),
                or_(
                    Match.query_evidence_id.in_(evidence_ids),
                    Match.candidate_evidence_id.in_(evidence_ids),
                ),
            ),
        ),
        timeline_events=count_where(TimelineEvent, TimelineEvent.case_id == case_id),
        timeline_events_detached=count_where(
            TimelineEvent,
            and_(
                TimelineEvent.evidence_id.in_(evidence_ids),
                or_(TimelineEvent.case_id != case_id, TimelineEvent.case_id.is_(None)),
            ),
        ),
        reports=len(report_paths),
    )
    evidence_files = _resolve_case_owned_files(
        (row.stored_path for row in evidence_rows), settings=settings, warnings=warnings
    )

    # 2. Delete the case; the cascade takes its children with it. -------------
    deleted_at = iso(utcnow()) or ""
    db.delete(case)
    db.flush()

    # 3. Audit inside the same transaction as the delete. ---------------------
    entry = audit.record(
        db,
        event=audit.EVENT_CASE_DELETED,
        case_id=case_id,
        actor="api",
        details={
            "case_number": case_number,
            "title": case_title,
            "examiner": case_examiner,
            "deleted_at": deleted_at,
            "deleted_evidence_count": counts.evidence,
            "deleted_evidence_ids": evidence_ids,
            "deleted_rows": counts.model_dump(),
            "evidence_files_targeted": len(evidence_files),
            "report_files_targeted": len(report_paths),
            "audit_history_retained": True,
            "deletion_type": "hard_delete",
        },
    )
    audit_entry = CaseDeleteAudit(
        audit_id=entry.audit_id,
        seq=entry.seq,
        event=entry.event,
        timestamp=entry.timestamp,
        actor=entry.actor,
        previous_hash=entry.previous_hash,
        row_hash=entry.row_hash,
        retained=True,
        case_rows_retained=db.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.case_id == case_id)
        ).scalar_one(),
    )

    # 4. Commit before touching the filesystem. ------------------------------
    db.commit()

    # 5. Storage, then the derived index. Neither can undo the commit. --------
    files_removed, files_missing = _unlink_all(
        evidence_files, label="evidence", warnings=warnings
    )
    case_directory: str | None = None
    case_directory_removed = False
    try:
        case_dir = resolve_within(settings.evidence_dir, "cases", case_id)
        case_directory = str(case_dir.relative_to(settings.data_dir.resolve()))
        if case_dir.is_dir():
            shutil.rmtree(case_dir)
            case_directory_removed = True
    except (StorageError, OSError, ValueError) as exc:
        warnings.append(f"Could not remove the case evidence directory: {exc}")
        logger.warning("Could not remove evidence directory for %s: %s", case_id, exc)

    report_files_removed, report_files_missing = _unlink_all(
        _resolve_report_files(report_paths, settings=settings, warnings=warnings),
        label="report",
        warnings=warnings,
    )

    index_removed = 0
    index_version: int | None = None
    index_backend: str | None = None
    rebuild_required = False
    try:
        index = get_index(settings)
        index_removed = index.remove(evidence_ids)
        index_status = index.status()
        index_version = index_status["index_version"]
        index_backend = index_status["backend"]
        if getattr(settings, "enable_dinov2_retrieval", True):
            try:
                get_dinov2_index(settings).remove(evidence_ids)
            except Exception as d_exc:
                logger.debug("DINOv2 index prune failed: %s", d_exc)
    except Exception as exc:  # noqa: BLE001 - the index is derived, never load-bearing
        rebuild_required = True
        warnings.append(
            f"Could not prune the perceptual index ({exc.__class__.__name__}: {exc}); "
            "run POST /api/index/rebuild to resynchronise it with the database."
        )
        logger.warning("Perceptual index prune failed for case %s: %s", case_id, exc)

    return CaseDeleteResponse(
        status="deleted",
        case_id=case_id,
        case_number=case_number,
        title=case_title,
        examiner=case_examiner,
        deleted_at=deleted_at,
        deleted_evidence_count=counts.evidence,
        deleted=counts,
        storage=CaseDeleteStorage(
            evidence_files_removed=files_removed,
            evidence_files_missing=files_missing,
            report_files_removed=report_files_removed,
            report_files_missing=report_files_missing,
            case_directory=case_directory,
            case_directory_removed=case_directory_removed,
        ),
        index=CaseDeleteIndex(
            vectors_removed=index_removed,
            index_version=index_version,
            backend=index_backend,
            rebuild_required=rebuild_required,
        ),
        audit=audit_entry,
        warnings=warnings,
    )


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
