"""Dashboard summary endpoint.

Every figure returned here is an aggregate over rows that already exist in the
case file. Nothing is estimated, sampled, extrapolated or defaulted to make a
tile look populated.

Two rules govern the numbers:

* ``null`` and ``0`` are not interchangeable. ``avg_processing_time_ms`` is
  ``null`` when no analysis has ever been timed -- never ``0``, which would claim
  an instantaneous pipeline. A count is ``0`` only when zero matching rows exist,
  which is itself a fact.
* Verdicts come from fusion and nowhere else. The dashboard never re-thresholds a
  score to decide what is "flagged"; doing so would put a second, competing
  verdict rule in the codebase.

Component status is reported per capability rather than as one green light, so an
unavailable detector or a stale index is visible instead of being averaged away.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbDep, SettingsDep
from app.models import KIND_FUSION, AnalysisResult, AuditLog, Case, Evidence
from app.schemas.api import CaseOut, DashboardSummaryResponse, EvidenceOut
from app.services import (
    audit as audit_service,
    detector as detector_service,
    fusion as fusion_service,
    indexing,
    ingestion,
    provenance as provenance_service,
)

logger = logging.getLogger("pramaan.api.dashboard")

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

FLAGGED_PREVIEW_LIMIT = 10
RECENT_LIMIT = 5

#: What each metric actually counts, so the UI can label it truthfully instead
#: of inventing a caption for it.
METRIC_DEFINITIONS: dict[str, str] = {
    "active_investigations_count": "Cases whose status is not 'closed'.",
    "evidence_items_count": (
        "All ingested evidence rows, including indexed corpus items as well as "
        "case evidence."
    ),
    "flagged_media_count": (
        "Distinct evidence items whose current fused verdict is MANIPULATED. "
        "Fusion is the only source of that verdict -- the dashboard does not "
        "re-threshold scores."
    ),
    "pending_review_count": "Cases whose status is 'pending_review'.",
    "unanalysed_case_count": (
        "Cases that hold evidence but have no fused verdict for any of it yet."
    ),
    "high_priority_count": "Non-closed cases whose priority is 'high'.",
    "evidence_breakdown": "Ingested evidence counted by media_type.",
    "analysed_evidence_count": "Distinct evidence items that have a fused verdict.",
    "verdict_breakdown": (
        "Current fused verdicts, counted by verdict token. INSUFFICIENT_EVIDENCE "
        "means the available signals did not cover enough weight to conclude -- "
        "it is not a finding of authenticity or of manipulation."
    ),
    "avg_processing_time_ms": (
        "Mean end-to-end pipeline time over the ANALYSIS_COMPLETED audit "
        "entries. null when no analysis run has been timed on this deployment."
    ),
    "system_status": (
        "The API and case database are reachable. Per-capability state is in "
        "system_status_details."
    ),
}


def _count(db: Any, stmt: Any) -> int:
    return int(db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one())


def _evidence_breakdown(db: Any) -> dict[str, int]:
    """Real counts per media_type, with the three canonical keys always present.

    A zero here is a genuine count of rows, not a stand-in for "unknown": if no
    audio has been ingested, ``audio`` is 0 because that is how many audio rows
    exist.
    """
    breakdown: dict[str, int] = {"image": 0, "video": 0, "audio": 0}
    for media_type, count in db.execute(
        select(Evidence.media_type, func.count()).group_by(Evidence.media_type)
    ).all():
        breakdown[str(media_type)] = int(count)
    return breakdown


def _verdict_breakdown(db: Any) -> dict[str, int]:
    """Current fused verdicts, counted by token.

    ``analysis_results`` holds one current row per (evidence, stage), so counting
    fusion rows counts evidence items, not analysis history.
    """
    rows = db.execute(
        select(AnalysisResult.verdict, func.count(func.distinct(AnalysisResult.evidence_id)))
        .where(AnalysisResult.kind == KIND_FUSION)
        .group_by(AnalysisResult.verdict)
    ).all()
    return {str(verdict): int(count) for verdict, count in rows if verdict}


def _measured_analysis_time(db: Any) -> tuple[float | None, int]:
    """Mean and sample size of the recorded end-to-end pipeline time.

    The source is the ``processing_time_ms`` written into every
    ``ANALYSIS_COMPLETED`` audit entry -- a real measurement taken at the time of
    the run. Entries written before that field existed simply do not contribute
    (``count`` ignores nulls), and with no measurements at all the answer is
    ``None``: an untimed pipeline is unknown, not fast.
    """
    measurement = func.json_extract(AuditLog.details, "$.processing_time_ms")
    try:
        average, samples = db.execute(
            select(func.avg(measurement), func.count(measurement)).where(
                AuditLog.event == audit_service.EVENT_ANALYSIS_COMPLETED
            )
        ).one()
    except Exception:  # noqa: BLE001 - a metric must never break the dashboard
        logger.warning("Could not read recorded analysis times", exc_info=True)
        try:
            db.rollback()
        except Exception:
            pass
        return None, 0
    if average is None or not samples:
        return None, 0
    return round(float(average), 3), int(samples)


def _index_status_label(db: Any, settings: Any) -> tuple[str, dict[str, Any]]:
    """Honest label for the perceptual index, including anything not yet indexed.

    The database is authoritative and the index is derived from it, so "up to
    date" is only true when every hashable evidence item is actually in the
    index. Items without a perceptual hash (video, audio) are not pending -- they
    are outside what this index covers in this build.
    """
    status = indexing.status(settings)
    hashable = _count(db, select(Evidence.id).where(Evidence.phash.is_not(None)))
    pending = _count(
        db,
        select(Evidence.id).where(
            Evidence.phash.is_not(None), Evidence.indexed.is_(False)
        ),
    )
    if status["indexed_count"] == 0:
        label = "EMPTY"
    elif pending:
        label = f"{pending} NOT INDEXED"
    else:
        label = "UP-TO-DATE"
    detail = {
        **status,
        "hashable_evidence_count": hashable,
        "pending_evidence_count": pending,
        "label": label,
    }
    return label, detail


def _case_out(db: Any, case: Case) -> CaseOut:
    evidence_count = db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.case_id == case.id)
    ).scalar_one()
    latest = (
        db.execute(
            select(AnalysisResult)
            .where(AnalysisResult.case_id == case.id, AnalysisResult.kind == KIND_FUSION)
            .order_by(AnalysisResult.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    payload = ingestion.case_to_dict(case, evidence_count=evidence_count)
    if latest is not None:
        payload["latest_verdict"] = latest.verdict
    return CaseOut(**payload)


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Dashboard aggregate metrics and recent activity",
)
def get_dashboard_summary(db: DbDep, settings: SettingsDep) -> DashboardSummaryResponse:
    """Aggregate the case file for the investigator dashboard.

    An empty deployment returns zeros and empty lists -- an honest empty state --
    rather than sample figures.
    """
    logger.info("DASHBOARD_REQUEST_RECEIVED")
    logger.info("DASHBOARD_DB_QUERY_START")
    evidence_count = db.execute(select(func.count()).select_from(Evidence)).scalar_one()
    active_cases_count = _count(db, select(Case.id).where(Case.status != "closed"))
    high_priority_count = _count(
        db, select(Case.id).where(Case.status != "closed", Case.priority == "high")
    )
    pending_review_count = _count(db, select(Case.id).where(Case.status == "pending_review"))

    # Cases holding evidence that has never reached fusion. Real work queue, not
    # a status label somebody has to remember to set.
    fused_case_ids = select(AnalysisResult.case_id).where(
        AnalysisResult.kind == KIND_FUSION, AnalysisResult.case_id.is_not(None)
    )
    unanalysed_case_count = _count(
        db,
        select(Case.id).where(
            Case.id.in_(select(Evidence.case_id).where(Evidence.case_id.is_not(None))),
            Case.id.not_in(fused_case_ids),
        ),
    )

    # Flagged = fusion said MANIPULATED. No score re-thresholding here: fusion
    # owns the verdict rule and duplicating it would let the dashboard disagree
    # with the case verdict.
    flagged_stmt = select(AnalysisResult.evidence_id).where(
        AnalysisResult.kind == KIND_FUSION,
        AnalysisResult.verdict == fusion_service.VERDICT_MANIPULATED,
    )
    flagged_count = _count(db, flagged_stmt.distinct())
    flagged_preview_ids = [
        row
        for row in db.execute(
            select(AnalysisResult.evidence_id)
            .where(
                AnalysisResult.kind == KIND_FUSION,
                AnalysisResult.verdict == fusion_service.VERDICT_MANIPULATED,
            )
            .order_by(AnalysisResult.created_at.desc())
            .limit(FLAGGED_PREVIEW_LIMIT)
        ).scalars()
    ]
    flagged_media: list[EvidenceOut] = []
    if flagged_preview_ids:
        by_id = {
            row.id: row
            for row in db.execute(
                select(Evidence).where(Evidence.id.in_(flagged_preview_ids))
            ).scalars()
        }
        flagged_media = [
            EvidenceOut(**ingestion.evidence_to_dict(by_id[eid]))
            for eid in flagged_preview_ids
            if eid in by_id
        ]

    analysed_evidence_count = _count(
        db, select(AnalysisResult.evidence_id).where(AnalysisResult.kind == KIND_FUSION).distinct()
    )
    avg_processing_time_ms, timed_runs = _measured_analysis_time(db)

    recent_cases = [
        _case_out(db, case)
        for case in db.execute(
            select(Case).order_by(Case.updated_at.desc()).limit(RECENT_LIMIT)
        ).scalars()
    ]
    recent_evidence = [
        EvidenceOut(**ingestion.evidence_to_dict(ev))
        for ev in db.execute(
            select(Evidence).order_by(Evidence.ingested_at.desc()).limit(RECENT_LIMIT)
        ).scalars()
    ]

    detector_status = detector_service.status(settings)
    validator = provenance_service.validator_status()
    index_label, index_detail = _index_status_label(db, settings)

    notes: list[str] = []
    if not detector_status.get("available"):
        notes.append(
            "No AI-manipulation detector is installed, so the ai_detection signal "
            "is UNAVAILABLE and excluded from fusion. That is why fused verdicts "
            "may read INSUFFICIENT_EVIDENCE; it is not a finding about the media."
        )
    if not validator["signature_validation_available"]:
        notes.append(provenance_service.CONTAINER_SCAN_ONLY_DETAIL)
    if index_detail["pending_evidence_count"]:
        notes.append(
            f"{index_detail['pending_evidence_count']} hashable evidence item(s) "
            "are not in the perceptual index, so near-duplicate and propagation "
            "coverage is incomplete until the index is updated."
        )
    if avg_processing_time_ms is None:
        notes.append(
            "No analysis run has been timed on this deployment, so the average "
            "processing time is unknown rather than zero."
        )

    logger.info("DASHBOARD_RESPONSE_BUILD_START")
    res = DashboardSummaryResponse(
        active_investigations_count=active_cases_count,
        evidence_items_count=evidence_count,
        flagged_media_count=flagged_count,
        pending_review_count=pending_review_count,
        unanalysed_case_count=unanalysed_case_count,
        high_priority_count=high_priority_count,
        evidence_breakdown=_evidence_breakdown(db),
        analysed_evidence_count=analysed_evidence_count,
        verdict_breakdown=_verdict_breakdown(db),
        avg_processing_time_ms=avg_processing_time_ms,
        avg_processing_time_basis=(
            f"mean end-to-end pipeline time over {timed_runs} recorded analysis "
            "run(s)"
            if timed_runs
            else "no analysis run has been timed on this deployment"
        ),
        timed_analysis_runs=timed_runs,
        recent_investigations=recent_cases,
        recent_evidence=recent_evidence,
        flagged_media=flagged_media,
        flagged_media_truncated=flagged_count > len(flagged_media),
        current_case_summary=recent_cases[0] if recent_cases else None,
        system_status="online",
        system_status_details={
            "ai_detectors": "ONLINE" if detector_status.get("available") else "UNAVAILABLE",
            "c2pa_validator": (
                "ONLINE"
                if validator["signature_validation_available"]
                else "CONTAINER-SCAN ONLY"
            ),
            "propagate_index": index_label,
        },
        components={
            "detector": detector_status,
            "c2pa_validator": validator,
            "perceptual_index": index_detail,
        },
        metric_definitions=METRIC_DEFINITIONS,
        notes=notes,
    )
    logger.info("DASHBOARD_RESPONSE_RETURNING")
    return res
