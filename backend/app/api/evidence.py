"""Per-evidence endpoints: the record, the bytes, and the stored analysis.

Evidence is addressed globally (``/api/evidence/{evidence_id}``) rather than
under its case, because not all evidence has a case: indexed corpus items do not,
and a near-duplicate candidate surfaced for one case usually belongs to another.

Two rules shape every response here:

* **Nothing is computed.** These routes read what the case file already holds. A
  stage that has never run is reported as ``null`` with the endpoint that would
  run it, never as a zero score or an empty-but-successful result.
* **The bytes are served unchanged.** No transcoding, no re-encoding, no
  thumbnailing: the file a client renders is byte-for-byte the file whose SHA-256
  is on record, so what the examiner sees is what was hashed.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import DbDep, EvidenceDep, SettingsDep
from app.models import (
    KIND_DETECTOR,
    KIND_FORENSICS,
    KIND_FUSION,
    KIND_METADATA,
    KIND_PROPAGATION,
    KIND_PROVENANCE,
    Case,
    Evidence,
    Match,
    Report,
)
from app.schemas.api import (
    EvidenceAnalysisResponse,
    EvidenceDetailResponse,
    EvidenceFileOut,
    EvidenceOut,
)
from app.services import (
    analysis_store,
    audit,
    detector as detector_service,
    fusion as fusion_service,
    ingestion,
    matching,
    storage,
)
from app.services.hashing import sha256_file
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.api.evidence")

router = APIRouter(prefix="/api/evidence", tags=["evidence"])

#: Every stage that can hold a stored result, in pipeline order. The order is the
#: order the analysis pages present them in, so it lives here rather than being
#: re-decided by each caller.
STAGES: tuple[str, ...] = (
    KIND_METADATA,
    KIND_DETECTOR,
    KIND_PROVENANCE,
    KIND_FORENSICS,
    KIND_FUSION,
    KIND_PROPAGATION,
)

FILE_MISSING_DETAIL = (
    "The evidence row exists but its stored file is not present in evidence "
    "storage, so the bytes cannot be served and the recorded SHA-256 cannot be "
    "re-checked. This is a storage integrity problem to investigate, not a "
    "finding about the media."
)


def _case_of(db: Any, evidence: Evidence) -> Case | None:
    if evidence.case_id is None:
        return None
    return db.get(Case, evidence.case_id)


def _stage_rows(db: Any, evidence_id: str) -> dict[str, Any]:
    """Latest stored row per stage, with ``None`` for stages that never ran."""
    return {
        kind: analysis_store.latest_result(db, evidence_id=evidence_id, kind=kind)
        for kind in STAGES
    }


def _file_state(
    evidence: Evidence, settings: Any, *, verify: bool
) -> tuple[EvidenceFileOut, str | None]:
    """Describe the stored file, optionally re-hashing it.

    Returns the description and, when a digest was recomputed, the ISO timestamp
    of that check so the caller can audit it.
    """
    url = f"/api/evidence/{evidence.id}/file"
    try:
        path = storage.absolute_path(evidence.stored_path, settings)
    except storage.StorageError:
        # A stored path that no longer resolves inside the evidence root is a
        # storage fault, and it is reported as one rather than being served.
        logger.error("Evidence %s has a stored path outside the root", evidence.id)
        path = None

    if path is None or not path.is_file():
        return (
            EvidenceFileOut(
                available=False,
                url=None,
                mime_type=evidence.mime_type,
                size_bytes=evidence.size_bytes,
                stored_sha256=evidence.sha256,
                detail=FILE_MISSING_DETAIL,
            ),
            None,
        )

    if not verify:
        return (
            EvidenceFileOut(
                available=True,
                url=url,
                mime_type=evidence.mime_type,
                size_bytes=evidence.size_bytes,
                stored_sha256=evidence.sha256,
                integrity_verified=None,
                detail=(
                    "The stored digest was not recomputed for this response. Pass "
                    "verify=true to re-hash the bytes on disk and compare them "
                    "against the recorded SHA-256."
                ),
            ),
            None,
        )

    recomputed = sha256_file(path)
    matches = recomputed == evidence.sha256
    checked_at = iso(utcnow())
    return (
        EvidenceFileOut(
            available=True,
            url=url,
            mime_type=evidence.mime_type,
            size_bytes=evidence.size_bytes,
            stored_sha256=evidence.sha256,
            recomputed_sha256=recomputed,
            integrity_verified=matches,
            verified_at=checked_at,
            detail=(
                "The bytes on disk hash to the SHA-256 recorded at ingestion."
                if matches
                else (
                    "The bytes on disk do NOT hash to the SHA-256 recorded at "
                    "ingestion. The stored file has changed since it was ingested; "
                    "treat this item as compromised until explained."
                )
            ),
        ),
        checked_at,
    )


@router.get(
    "/{evidence_id}",
    response_model=EvidenceDetailResponse,
    summary="Get one evidence item with its stored analysis state",
)
def get_evidence(
    evidence: EvidenceDep,
    db: DbDep,
    settings: SettingsDep,
    verify: bool = Query(
        False,
        description=(
            "Re-hash the stored bytes and compare them against the recorded "
            "SHA-256. Recorded in the audit chain when requested."
        ),
    ),
) -> EvidenceDetailResponse:
    """The evidence record, its file state, and which analysis stages exist.

    Nothing is analysed here. ``stages`` reports one entry per analysis kind:
    a stored row, or ``null`` when that stage has never run for this item -- an
    absent stage is a gap in the record, not a finding about the file.

    With ``verify=true`` the stored bytes are re-hashed and the comparison is
    appended to the audit chain, because re-verifying an exhibit's integrity is
    itself an act worth recording.
    """
    file_state, verified_at = _file_state(evidence, settings, verify=verify)

    if verify and verified_at is not None:
        audit.record(
            db,
            event=audit.EVENT_HASH_CALCULATED,
            case_id=evidence.case_id,
            actor="api",
            details={
                "evidence_id": evidence.id,
                "route": "GET /api/evidence/{evidence_id}",
                "purpose": "integrity re-verification",
                "algorithm": "SHA-256",
                "recorded_sha256": evidence.sha256,
                "recomputed_sha256": file_state.recomputed_sha256,
                "matches": file_state.integrity_verified,
            },
        )

    rows = _stage_rows(db, evidence.id)
    stages = {
        kind: (
            None
            if row is None
            else {
                "kind": kind,
                "status": row.status,
                "score": row.score,
                "verdict": row.verdict,
                "model": row.model,
                "model_version": row.model_version,
                "created_at": iso(row.created_at),
            }
        )
        for kind, row in rows.items()
    }

    fusion_row = rows[KIND_FUSION]
    verdict = (
        fusion_row.payload
        if fusion_row is not None and isinstance(fusion_row.payload, dict)
        else None
    )

    candidate_count = int(
        db.execute(
            select(func.count())
            .select_from(Match)
            .where(Match.query_evidence_id == evidence.id)
        ).scalar_one()
    )
    report_count = 0
    if evidence.case_id is not None:
        report_count = int(
            db.execute(
                select(func.count())
                .select_from(Report)
                .where(Report.case_id == evidence.case_id)
            ).scalar_one()
        )

    stored = [kind for kind, row in rows.items() if row is not None]
    not_run = [kind for kind, row in rows.items() if row is None]

    notes: list[str] = []
    if not_run:
        notes.append(
            f"{len(not_run)} analysis stage(s) have no stored result for this item "
            f"({', '.join(not_run)}). They have not run; that is not a result."
        )
    if file_state.integrity_verified is False:
        notes.append(
            "The stored bytes no longer match the SHA-256 recorded at ingestion."
        )
    if evidence.case_id is None:
        notes.append(
            "This item belongs to the indexed corpus rather than to a case, so no "
            "case-scoped analysis or report applies to it."
        )
    if evidence.phash is None:
        notes.append(
            "No perceptual hash was computed for this item, so it is not covered "
            "by near-duplicate retrieval. Perceptual hashing covers images in this "
            "build."
        )

    case = _case_of(db, evidence)
    return EvidenceDetailResponse(
        evidence=EvidenceOut(**ingestion.evidence_to_dict(evidence)),
        case=None
        if case is None
        else ingestion.case_to_dict(
            case,
            evidence_count=int(
                db.execute(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.case_id == case.id)
                ).scalar_one()
            ),
        ),
        file=file_state,
        stages=stages,
        stages_stored=stored,
        stages_not_run=not_run,
        verdict=verdict,
        near_duplicate_candidate_count=candidate_count,
        near_duplicate_interpretation=matching.INTERPRETATION,
        report_count=report_count,
        analysis_url=f"/api/evidence/{evidence.id}/analysis",
        file_url=f"/api/evidence/{evidence.id}/file",
        run_analysis_url=(
            None
            if evidence.case_id is None
            else f"/api/cases/{evidence.case_id}/analyse"
        ),
        notes=notes,
    )


@router.get(
    "/{evidence_id}/file",
    response_class=FileResponse,
    summary="Stream the stored evidence bytes for preview",
)
def get_evidence_file(
    evidence: EvidenceDep, settings: SettingsDep, download: bool = Query(False)
) -> FileResponse:
    """Return the stored bytes unchanged, inline by default.

    The response is the original file: nothing is resized, re-encoded or stripped,
    so the recorded SHA-256 still describes what the client received (it is sent in
    ``X-PRAMAAN-Evidence-SHA256`` for exactly that comparison).

    Reading evidence for display is deliberately **not** written to the audit
    chain: a single page render issues one request per thumbnail, and burying the
    examination record under view events would damage the trail that matters.
    Deliberate acts -- analysis, integrity re-verification, report generation --
    are what the chain records.
    """
    try:
        path = storage.absolute_path(evidence.stored_path, settings)
    except storage.StorageError as exc:
        logger.error("Refusing to serve %s: %s", evidence.id, exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=FILE_MISSING_DETAIL
        ) from exc

    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=FILE_MISSING_DETAIL
        )

    return FileResponse(
        path,
        media_type=evidence.mime_type,
        filename=evidence.filename,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "X-PRAMAAN-Evidence-SHA256": evidence.sha256,
            "X-PRAMAAN-Evidence-ID": evidence.id,
            # The bytes for an evidence id never change, but they are case
            # material: cache in the browser only, never in a shared proxy.
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get(
    "/{evidence_id}/analysis",
    response_model=EvidenceAnalysisResponse,
    summary="Read the stored analysis payloads for one evidence item",
)
def get_evidence_analysis(
    evidence: EvidenceDep, db: DbDep, settings: SettingsDep
) -> EvidenceAnalysisResponse:
    """Every stored stage payload for one item, exactly as it was computed.

    This route never runs the pipeline, so opening an analysis page cannot change
    the case file. Stages with no stored result are listed in ``missing_stages``
    and ``run_analysis_url`` names the endpoint that produces them.

    ``verdict`` and ``signals`` come from the stored fusion result and are not
    recomputed here -- fusion is the only place a verdict is decided.
    """
    rows = _stage_rows(db, evidence.id)
    stages = {
        kind: (None if row is None else analysis_store.result_to_dict(row))
        for kind, row in rows.items()
    }

    fusion_row = rows[KIND_FUSION]
    verdict = (
        fusion_row.payload
        if fusion_row is not None and isinstance(fusion_row.payload, dict)
        else None
    )
    signals = list(verdict.get("signals", [])) if verdict else []

    stored = [kind for kind, row in rows.items() if row is not None]
    missing = [kind for kind, row in rows.items() if row is None]

    notes: list[str] = []
    if missing:
        notes.append(
            "Stored results only: this endpoint does not run analysis. Missing "
            f"stage(s): {', '.join(missing)}."
        )
    if evidence.case_id is None:
        notes.append(
            "This item belongs to the indexed corpus, so there is no case pipeline "
            "to run for it."
        )
    if verdict is None:
        notes.append(
            "No fused verdict is stored for this item, so there is no verdict to "
            "show. That is an absence of analysis, not an assessment of the media."
        )

    return EvidenceAnalysisResponse(
        evidence=EvidenceOut(**ingestion.evidence_to_dict(evidence)),
        case_id=evidence.case_id,
        stored_stages=stored,
        missing_stages=missing,
        stages=stages,
        verdict=verdict,
        signals=signals,
        detector_capability=detector_service.status(settings),
        run_analysis_url=(
            None
            if evidence.case_id is None
            else f"/api/cases/{evidence.case_id}/analyse"
        ),
        score_semantics=fusion_service.SCORE_SEMANTICS,
        interpretation=fusion_service.FUSION_METHOD,
        caveat=fusion_service.CAVEAT,
        notes=notes,
    )
