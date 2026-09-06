"""Case and evidence ingestion.

Coordinates the storage layer, perceptual hashing and the audit chain so every
ingested file is: validated, stored under a server-generated id, hashed from the
bytes on disk, fingerprinted, persisted, and recorded in the audit log.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ROLE_CASE_EVIDENCE, ROLE_CORPUS, AuditLog, Case, Evidence, Report
from app.services import audit
from app.services.hashing import (
    PERCEPTUAL_ALGORITHM,
    calculate_image_hashes,
)
from app.services.storage import (
    MEDIA_IMAGE,
    PayloadTooLargeError,
    StagedUpload,
    StorageError,
    absolute_path,
    commit_upload,
    stage_upload,
)
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.ingest")


@dataclass
class IngestResult:
    evidence: Evidence
    case: Case
    duplicate: bool
    warnings: list[str]


CASE_NUMBER_PREFIX = "PRAMAAN"
#: Compact, sequential human-facing numbers: PRAMAAN-1001, PRAMAAN-1002, ...
#: Deliberately a single numeric run with no date segment, so the visible case
#: number stays short and memorable. Older dated numbers of the form
#: ``PRAMAAN-YYYYMMDD-NNNN`` carry a second dash and never match this pattern, so
#: they are left untouched and simply ignored by the sequence scan below.
_CASE_NUMBER_RE = re.compile(rf"^{CASE_NUMBER_PREFIX}-(\d+)$")
#: First number ever issued is CASE_NUMBER_START + 1 (PRAMAAN-1001). Starting the
#: visible sequence at 1001 keeps early numbers uniform in width.
CASE_NUMBER_START = 1000


def _highest_issued_sequence(session: Session) -> int:
    """Highest compact sequence ever issued -- including deleted cases.

    Live rows alone are not enough. A case can be deleted, but its audit rows
    cannot be, and every ``CASE_CREATED`` event carries the number that was
    handed out. Reading both means a number is never re-issued after a deletion
    or a restart, so the retained trail cannot end up describing two different
    cases under one human-facing case number. The floor at ``CASE_NUMBER_START``
    makes the first issued number ``PRAMAAN-1001`` on a fresh database.
    """
    prefix = f"{CASE_NUMBER_PREFIX}-"
    issued: list[str | None] = list(
        session.execute(
            select(Case.case_number).where(Case.case_number.like(f"{prefix}%"))
        ).scalars()
    )
    recorded = AuditLog.details["case_number"].as_string()
    issued += list(
        session.execute(
            select(recorded).where(
                AuditLog.event == audit.EVENT_CASE_CREATED,
                recorded.like(f"{prefix}%"),
            )
        ).scalars()
    )
    highest = CASE_NUMBER_START
    for number in issued:
        match = _CASE_NUMBER_RE.match(number or "")
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


def generate_case_number(session: Session) -> str:
    """Human-facing case number: compact and sequential (PRAMAAN-1001, ...).

    The sequence is one past the highest ever *issued*, not the number of cases
    that currently exist. Counting live rows would re-issue a number the moment a
    case was deleted: the next insert would collide with a surviving case and be
    rejected by the unique constraint, and any number that did get through would
    appear against two different cases in the audit trail. Reading the audit
    chain as well as live rows also survives a full restart with no live cases.
    """
    return f"{CASE_NUMBER_PREFIX}-{_highest_issued_sequence(session) + 1}"


def create_case(
    session: Session,
    *,
    title: str | None = None,
    description: str | None = None,
    examiner: str | None = None,
    priority: str = "medium",
    complaint_reference: str | None = None,
    actor: str = "api",
) -> Case:
    case = Case(
        id=str(uuid.uuid4()),
        case_number=generate_case_number(session),
        title=title,
        description=description,
        examiner=examiner,
        priority=priority,
        complaint_reference=complaint_reference,
        status="open",
    )
    session.add(case)
    session.flush()
    audit.record(
        session,
        event=audit.EVENT_CASE_CREATED,
        case_id=case.id,
        actor=actor,
        details={
            "case_number": case.case_number,
            "title": title,
            "examiner": examiner,
            "priority": priority,
            "complaint_reference": complaint_reference,
        },
    )
    logger.info("Case created %s (%s)", case.id, case.case_number)
    return case


def get_case(session: Session, case_id: str) -> Case | None:
    return session.get(Case, case_id)


def find_duplicate(
    session: Session, *, case_id: str | None, sha256: str
) -> Evidence | None:
    """Exact-byte duplicate already ingested for this case."""
    stmt = select(Evidence).where(Evidence.sha256 == sha256)
    if case_id is not None:
        stmt = stmt.where(Evidence.case_id == case_id)
    return session.execute(stmt).scalars().first()


def report_count(session: Session, case_id: str) -> int:
    """Forensic reports on record for a case.

    Reported alongside the case so a client can distinguish "a report has been
    generated for this case" from "no report yet" without fetching the report
    list for every row -- the case workflow indicator needs exactly that, and
    needs it to survive a page reload.

    The case's ``status`` column cannot answer it: nothing in this service ever
    writes a report state into that column, so a client reading it would show
    every case as unreported no matter how many reports exist.
    """
    return int(
        session.execute(
            select(func.count()).select_from(Report).where(Report.case_id == case_id)
        ).scalar_one()
    )


def latest_fused_verdict_subquery() -> Any:
    """A correlated subquery for a case's newest *fused* verdict.

    Only fusion rows carry a verdict. ``metadata``, ``detector``, ``provenance``,
    ``forensics`` and ``propagation`` rows all store ``verdict = NULL``, because a
    single signal is not a verdict -- the verdict is what fusion produces after
    weighing them. So "the case's newest analysis row" is the wrong query: the
    pipeline writes propagation *after* fusion, so the newest row for an analysed
    case is normally a propagation row, and reading its NULL verdict reports a
    fully analysed case as never analysed.

    Correlates against ``Case.id``, so it can be used both to filter a case list
    and to sort one. Kept here, next to :func:`latest_fused_verdict`, so the
    filter and the value it filters on cannot drift apart -- which is exactly how
    they drifted before: four copies of this query in two modules, one of them
    with the ``kind`` predicate and three without.
    """
    from app.models import KIND_FUSION, AnalysisResult

    return (
        select(AnalysisResult.verdict)
        .where(AnalysisResult.case_id == Case.id, AnalysisResult.kind == KIND_FUSION)
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )


def latest_fused_verdict(session: Session, case_id: str) -> str | None:
    """The case's newest fused verdict, or ``None`` when it has no fusion on record.

    ``None`` means "not analysed yet" and is rendered as such. It is never
    softened into a verdict token, and a non-fusion row's NULL verdict never
    reaches a caller as if the case had never been examined -- see
    :func:`latest_fused_verdict_subquery` for why that distinction is not
    academic.
    """
    from app.models import KIND_FUSION, AnalysisResult

    return session.execute(
        select(AnalysisResult.verdict)
        .where(
            AnalysisResult.case_id == case_id,
            AnalysisResult.kind == KIND_FUSION,
        )
        .order_by(AnalysisResult.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def case_to_dict(
    case: Case,
    evidence_count: int | None = None,
    report_count: int | None = None,
) -> dict[str, Any]:
    """Serialise a case row for API responses.

    ``priority`` falls back to the model's default rather than to ``None``: an
    unset priority means "not triaged yet", which is what ``medium`` denotes
    here. Nothing else is defaulted.

    ``evidence_count`` and ``report_count`` stay ``None`` unless the caller
    counted them. A caller without a session cannot honestly report zero, and
    the UI reads zero as "none on record" -- so the absent case is left absent.
    """
    return {
        "case_id": case.id,
        "case_number": case.case_number,
        "title": case.title,
        "description": case.description,
        "examiner": case.examiner,
        "status": case.status,
        "priority": getattr(case, "priority", "medium") or "medium",
        "complaint_reference": getattr(case, "complaint_reference", None),
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "evidence_count": evidence_count,
        "report_count": report_count,
    }


def _attach_perceptual_hashes(
    evidence: Evidence, settings: Settings
) -> tuple[bool, str | None]:
    """Compute pHash/dHash/aHash for images. Returns (computed, error)."""
    if evidence.media_type != MEDIA_IMAGE:
        return False, "perceptual hashing applies to images only"
    try:
        hashes = calculate_image_hashes(absolute_path(evidence.stored_path, settings))
    except Exception as exc:  # noqa: BLE001 - never fail ingestion over a hash
        logger.warning(
            "Perceptual hashing failed for %s: %s", evidence.id, exc.__class__.__name__
        )
        return False, f"{exc.__class__.__name__}"
    evidence.phash = hashes["phash"]
    evidence.dhash = hashes["dhash"]
    evidence.ahash = hashes["ahash"]
    return True, None


def ingest_stream(
    session: Session,
    *,
    stream: BinaryIO,
    filename: str | None,
    settings: Settings,
    case: Case | None,
    declared_mime: str | None = None,
    role: str = ROLE_CASE_EVIDENCE,
    actor: str = "api",
    provenance: dict[str, Any] | None = None,
    is_synthetic: bool = False,
    evidence_id: str | None = None,
    acquisition_context: str | None = None,
) -> IngestResult:
    """Validate, store, hash, fingerprint, persist and audit one upload.

    ``evidence_id`` may be supplied so corpus items keep the stable identifiers
    recorded in the corpus manifest -- lineage fields (``parent_id``,
    ``source_id``) reference those ids, so regenerating them would break the
    graph. Uploads leave it unset and get a server-generated UUID.

    Raises ``StorageError`` (client-safe message) if the file is rejected.
    """
    staged: StagedUpload = stage_upload(
        stream, filename=filename, settings=settings, declared_mime=declared_mime
    )
    case_id = case.id if case else None

    existing = find_duplicate(session, case_id=case_id, sha256=staged.sha256)
    if existing is not None:
        staged.discard()
        audit.record(
            session,
            event=audit.EVENT_EVIDENCE_DUPLICATE,
            case_id=case_id,
            actor=actor,
            details={
                "sha256": staged.sha256,
                "existing_evidence_id": existing.id,
                "submitted_filename": staged.filename,
            },
        )
        logger.info(
            "Duplicate upload rejected for case %s (sha256=%s)", case_id, staged.sha256
        )
        return IngestResult(
            evidence=existing,
            case=case,  # type: ignore[arg-type]
            duplicate=True,
            warnings=[
                "Identical bytes (matching SHA-256) were already ingested for this "
                "case; the existing evidence record was returned."
            ],
        )

    evidence_id = evidence_id or str(uuid.uuid4())
    bucket = "cases" if role == ROLE_CASE_EVIDENCE else "corpus"
    bucket_key = case_id or "unassigned" if role == ROLE_CASE_EVIDENCE else "items"
    stored_path = commit_upload(
        staged,
        evidence_id=evidence_id,
        settings=settings,
        bucket=bucket,
        bucket_key=bucket_key,
    )

    prov = provenance or {}
    observed_at = prov.get("observed_at")
    if isinstance(observed_at, str):
        from app.utils.timeutil import parse_iso

        observed_at = parse_iso(observed_at)

    evidence = Evidence(
        id=evidence_id,
        case_id=case_id,
        role=role,
        filename=staged.filename,
        stored_path=stored_path,
        media_type=staged.media_type,
        mime_type=staged.mime_type,
        size_bytes=staged.size_bytes,
        sha256=staged.sha256,
        width=staged.width,
        height=staged.height,
        image_format=staged.image_format,
        source_id=prov.get("source_id"),
        parent_id=prov.get("parent_id"),
        generation=prov.get("generation"),
        platform=prov.get("platform"),
        observed_at=observed_at if isinstance(observed_at, datetime) else None,
        transformation=prov.get("transformation"),
        is_synthetic=is_synthetic,
        acquisition_context=(acquisition_context or None),
        indexed=False,
    )
    session.add(evidence)
    session.flush()

    audit.record(
        session,
        event=audit.EVENT_EVIDENCE_INGESTED,
        case_id=case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "filename": evidence.filename,
            "stored_path": evidence.stored_path,
            "mime_type": evidence.mime_type,
            "media_type": evidence.media_type,
            "size_bytes": evidence.size_bytes,
            "role": role,
            "acquisition_context": evidence.acquisition_context,
        },
    )
    audit.record(
        session,
        event=audit.EVENT_HASH_CALCULATED,
        case_id=case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "algorithm": "SHA-256",
            "sha256": evidence.sha256,
            "computed_from": "stored bytes on disk",
        },
    )

    computed, error = _attach_perceptual_hashes(evidence, settings)
    if computed:
        session.flush()
        audit.record(
            session,
            event=audit.EVENT_PERCEPTUAL_HASHED,
            case_id=case_id,
            actor=actor,
            details={
                "evidence_id": evidence.id,
                "algorithm": PERCEPTUAL_ALGORITHM,
                "phash": evidence.phash,
                "dhash": evidence.dhash,
                "ahash": evidence.ahash,
            },
        )

    warnings = list(staged.warnings)
    if not computed and error and evidence.media_type == MEDIA_IMAGE:
        warnings.append(f"Perceptual hashing unavailable for this file ({error}).")

    logger.info(
        "Ingested evidence %s (%s, %d bytes) into case %s",
        evidence.id,
        evidence.mime_type,
        evidence.size_bytes,
        case_id,
    )
    return IngestResult(
        evidence=evidence,
        case=case,  # type: ignore[arg-type]
        duplicate=False,
        warnings=warnings,
    )


def evidence_to_dict(evidence: Evidence) -> dict[str, Any]:
    """Serialise an evidence row for API responses."""
    return {
        "evidence_id": evidence.id,
        "case_id": evidence.case_id,
        "role": evidence.role,
        "filename": evidence.filename,
        "stored_path": evidence.stored_path,
        "media_type": evidence.media_type,
        "mime_type": evidence.mime_type,
        "size_bytes": evidence.size_bytes,
        "sha256": evidence.sha256,
        "ingested_at": iso(evidence.ingested_at),
        "width": evidence.width,
        "height": evidence.height,
        "format": evidence.image_format,
        "phash": evidence.phash,
        "dhash": evidence.dhash,
        "ahash": evidence.ahash,
        "source_id": evidence.source_id,
        "parent_id": evidence.parent_id,
        "generation": evidence.generation,
        "platform": evidence.platform,
        "observed_at": iso(evidence.observed_at),
        "transformation": evidence.transformation,
        "is_synthetic": evidence.is_synthetic,
        "acquisition_context": evidence.acquisition_context,
        "indexed": evidence.indexed,
    }


__all__ = [
    "IngestResult",
    "ROLE_CORPUS",
    "PayloadTooLargeError",
    "StorageError",
    "case_to_dict",
    "create_case",
    "evidence_to_dict",
    "find_duplicate",
    "generate_case_number",
    "get_case",
    "ingest_stream",
    "report_count",
]
