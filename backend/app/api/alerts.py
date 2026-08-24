"""Alerts, derived at read time from rows that already exist.

There is no alerts table in this build, and adding one would create a second
place where a finding lives -- one that could disagree with the analysis it came
from, or outlive its correction. So every alert here is computed from the current
contents of the case file each time the endpoint is called: the stored fusion
verdicts, the stored provenance inspections, the stored near-duplicate matches,
the deployment's capability state, and the audit chain's last verification.

Consequences worth stating plainly:

* An alert disappears when the fact behind it does. Re-running analysis that
  changes a verdict changes the alerts about it, immediately.
* Nothing is acknowledged, snoozed or dismissed, because there is nowhere to
  record that. What the UI shows is the current state, not a worklist.
* No alert is a conclusion. A near-duplicate alert says candidates exist; a
  provenance alert says what a manifest asserts about itself. Each carries the
  ``basis`` figures it was derived from so the examiner can check it rather than
  trust it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbDep, SettingsDep
from app.models import (
    KIND_FUSION,
    KIND_PROVENANCE,
    AnalysisResult,
    AuditLog,
    Case,
    Evidence,
    Match,
)
from app.schemas.api import AlertOut, AlertsResponse
from app.services import (
    audit as audit_service,
    detector as detector_service,
    fusion as fusion_service,
    indexing,
    matching,
    provenance as provenance_service,
)
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.api.alerts")

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

#: Most serious first. This is the sort order of the response.
SEVERITIES: tuple[str, ...] = (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    SEVERITY_LOW,
    SEVERITY_INFO,
)

SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}

CATEGORY_VERDICT = "verdict"
CATEGORY_COVERAGE = "coverage"
CATEGORY_PROVENANCE = "provenance"
CATEGORY_NEAR_DUPLICATE = "near_duplicate"
CATEGORY_CAPABILITY = "capability"
CATEGORY_AUDIT = "audit"

CATEGORIES: tuple[str, ...] = (
    CATEGORY_VERDICT,
    CATEGORY_COVERAGE,
    CATEGORY_PROVENANCE,
    CATEGORY_NEAR_DUPLICATE,
    CATEGORY_CAPABILITY,
    CATEGORY_AUDIT,
)

SEVERITY_DEFINITIONS: dict[str, str] = {
    SEVERITY_CRITICAL: (
        "The integrity of the record itself is in question -- for example the "
        "audit chain no longer verifies. Nothing derived from the affected rows "
        "can be relied on until it is explained."
    ),
    SEVERITY_HIGH: (
        "A stored analysis result points at manipulation, an invalid provenance "
        "signature, or a self-declared generative origin. These are findings to "
        "act on, each traceable to the stage that produced it."
    ),
    SEVERITY_MEDIUM: (
        "Something needs an examiner's judgement: near-duplicate candidates to "
        "compare, or a capability gap that is limiting the analysis."
    ),
    SEVERITY_LOW: (
        "Worth knowing, nothing to do immediately -- typically incomplete "
        "coverage, or a deployment limitation that is expected in this build."
    ),
    SEVERITY_INFO: "Context only.",
}

CATEGORY_DEFINITIONS: dict[str, str] = {
    CATEGORY_VERDICT: (
        "Derived from the stored fused verdict. Fusion owns the verdict rule; "
        "alerts never re-threshold a score to reach their own conclusion."
    ),
    CATEGORY_COVERAGE: (
        "Fusion could not cover enough signal weight to conclude, so the verdict "
        "is INSUFFICIENT_EVIDENCE. That is a statement about the available "
        "analysis, not about the media."
    ),
    CATEGORY_PROVENANCE: (
        "Derived from the stored C2PA inspection. A manifest's own assertions are "
        "reported as assertions; only a validated signature is reported as "
        "verified."
    ),
    CATEGORY_NEAR_DUPLICATE: (
        "Perceptually similar items were retrieved from the index. These are "
        "candidates for comparison, not proof of a relationship."
    ),
    CATEGORY_CAPABILITY: (
        "A component of this deployment cannot do something, which bounds what "
        "any analysis here can conclude."
    ),
    CATEGORY_AUDIT: (
        "The state of the tamper-evident audit chain over the whole case file."
    ),
}

#: Because an unverified chain is not the same as a broken one, and neither is
#: the same as a verified one.
AUDIT_NEVER_VERIFIED = (
    "The audit chain has never been verified on this deployment. That is not a "
    "sign of tampering -- it means the check has not been run."
)

DERIVED_NOTE = (
    "Alerts are derived from stored rows at request time. There is no alerts "
    "table and no acknowledgement state in this build: an alert exists exactly as "
    "long as the fact behind it does."
)


def _case_lookup(db: Any, case_ids: set[str]) -> dict[str, Case]:
    if not case_ids:
        return {}
    return {
        case.id: case
        for case in db.execute(select(Case).where(Case.id.in_(case_ids))).scalars()
    }


def _verdict_alerts(db: Any) -> list[AlertOut]:
    """Alerts from stored fusion results.

    The verdict token is taken as fusion wrote it. MANIPULATED is a finding;
    INSUFFICIENT_EVIDENCE is a coverage problem and is deliberately *not* raised
    at the same severity, because "we could not tell" is not "we found something".
    """
    rows = db.execute(
        select(AnalysisResult, Evidence)
        .join(Evidence, Evidence.id == AnalysisResult.evidence_id)
        .where(AnalysisResult.kind == KIND_FUSION)
        .order_by(AnalysisResult.created_at.desc())
    ).all()
    cases = _case_lookup(db, {r.case_id for r, _ in rows if r.case_id})

    alerts: list[AlertOut] = []
    for result, evidence in rows:
        payload = result.payload if isinstance(result.payload, dict) else {}
        case = cases.get(result.case_id or "")
        common = {
            "case_id": result.case_id,
            "case_number": case.case_number if case else None,
            "evidence_id": evidence.id,
            "filename": evidence.filename,
            "observed_at": iso(result.created_at),
            "source": f"analysis_results/{result.id}",
        }
        basis = {
            "verdict": result.verdict,
            "manipulation_score": result.score,
            "confidence": payload.get("confidence"),
            "signal_coverage": payload.get("signal_coverage"),
            "signals_available": payload.get("signals_available"),
            "signals_total": payload.get("signals_total"),
            "fusion_version": payload.get("fusion_version"),
        }

        if result.verdict == fusion_service.VERDICT_MANIPULATED:
            alerts.append(
                AlertOut(
                    alert_id=f"verdict:{result.id}",
                    severity=SEVERITY_HIGH,
                    category=CATEGORY_VERDICT,
                    title=f"Manipulation indicated: {evidence.filename}",
                    detail=(
                        "The fused verdict for this item is MANIPULATED, from "
                        f"{payload.get('signals_available', '?')} of "
                        f"{payload.get('signals_total', '?')} signals. Open the "
                        "signal breakdown to see which signals drove it and by "
                        "how much."
                    ),
                    basis=basis,
                    action="Review the per-signal contributions and the evidence.",
                    **common,
                )
            )
        elif result.verdict == fusion_service.VERDICT_INSUFFICIENT:
            alerts.append(
                AlertOut(
                    alert_id=f"coverage:{result.id}",
                    severity=SEVERITY_LOW,
                    category=CATEGORY_COVERAGE,
                    title=f"Inconclusive analysis: {evidence.filename}",
                    detail=(
                        "Fusion returned INSUFFICIENT_EVIDENCE: the signals that "
                        "ran did not cover enough weight to reach a verdict. This "
                        "says nothing about whether the media is authentic."
                    ),
                    basis=basis,
                    action=(
                        "Install the missing signal sources, or record the item as "
                        "inconclusive."
                    ),
                    **common,
                )
            )
    return alerts


def _provenance_alerts(db: Any) -> list[AlertOut]:
    """Alerts from stored C2PA inspections.

    Only two states are raised. An invalid signature is a hard failure of a claim
    that was actually checkable. A *verified* manifest that declares generative AI
    is a first-party admission and worth surfacing -- but an absent manifest is
    normal for almost all media and is never raised as an alert.
    """
    rows = db.execute(
        select(AnalysisResult, Evidence)
        .join(Evidence, Evidence.id == AnalysisResult.evidence_id)
        .where(AnalysisResult.kind == KIND_PROVENANCE)
        .order_by(AnalysisResult.created_at.desc())
    ).all()
    cases = _case_lookup(db, {r.case_id for r, _ in rows if r.case_id})

    alerts: list[AlertOut] = []
    for result, evidence in rows:
        payload = result.payload if isinstance(result.payload, dict) else {}
        state = payload.get("state")
        declared = payload.get("declared") or {}
        case = cases.get(result.case_id or "")
        common = {
            "case_id": result.case_id,
            "case_number": case.case_number if case else None,
            "evidence_id": evidence.id,
            "filename": evidence.filename,
            "observed_at": iso(result.created_at),
            "source": f"analysis_results/{result.id}",
        }

        if state == provenance_service.STATE_INVALID:
            alerts.append(
                AlertOut(
                    alert_id=f"provenance-invalid:{result.id}",
                    severity=SEVERITY_HIGH,
                    category=CATEGORY_PROVENANCE,
                    title=f"C2PA signature did not validate: {evidence.filename}",
                    detail=(
                        "A C2PA manifest is present but its signature failed "
                        "validation. The manifest's claims about this file cannot "
                        "be trusted; the file may also have been altered after "
                        "signing."
                    ),
                    basis={
                        "state": state,
                        "signature_validated": payload.get("signature_validated"),
                        "validator": payload.get("validator"),
                        "claim_generator": declared.get("claim_generator"),
                    },
                    action="Inspect the manifest and the signing chain.",
                    **common,
                )
            )
        elif (
            state == provenance_service.STATE_VERIFIED
            and declared.get("declares_generative_ai") is True
        ):
            alerts.append(
                AlertOut(
                    alert_id=f"provenance-generative:{result.id}",
                    severity=SEVERITY_HIGH,
                    category=CATEGORY_PROVENANCE,
                    title=f"Manifest declares generative AI: {evidence.filename}",
                    detail=(
                        "A validated C2PA manifest declares a generative-AI source "
                        "for this file. This is the file's own signed provenance "
                        "record, not an inference from its pixels."
                    ),
                    basis={
                        "state": state,
                        "generative_source_types": declared.get(
                            "generative_source_types", []
                        ),
                        "claim_generator": declared.get("claim_generator"),
                        "actions": declared.get("actions", []),
                        "extraction": declared.get("extraction"),
                    },
                    action="Record the declared provenance in the case file.",
                    **common,
                )
            )
    return alerts


def _near_duplicate_alerts(db: Any) -> list[AlertOut]:
    """One alert per evidence item that has strong near-duplicate candidates.

    Aggregated per query item rather than one alert per pair: a single image with
    twelve candidates is one thing to look at, not twelve alerts. The wording is
    deliberately "candidates" throughout.
    """
    rows = db.execute(
        select(
            Match.query_evidence_id,
            func.count().label("candidates"),
            func.min(Match.distance).label("closest"),
            func.max(Match.similarity).label("best_similarity"),
            func.max(Match.created_at).label("searched_at"),
        )
        .where(Match.confidence_band == matching.BAND_STRONG)
        .group_by(Match.query_evidence_id)
    ).all()
    if not rows:
        return []

    evidence_ids = {row.query_evidence_id for row in rows}
    evidence_by_id = {
        ev.id: ev
        for ev in db.execute(
            select(Evidence).where(Evidence.id.in_(evidence_ids))
        ).scalars()
    }
    cases = _case_lookup(
        db, {ev.case_id for ev in evidence_by_id.values() if ev.case_id}
    )

    alerts: list[AlertOut] = []
    for row in rows:
        evidence = evidence_by_id.get(row.query_evidence_id)
        if evidence is None:
            continue
        case = cases.get(evidence.case_id or "")
        alerts.append(
            AlertOut(
                alert_id=f"near-duplicate:{evidence.id}",
                severity=SEVERITY_MEDIUM,
                category=CATEGORY_NEAR_DUPLICATE,
                title=(
                    f"{row.candidates} strong near-duplicate candidate(s): "
                    f"{evidence.filename}"
                ),
                detail=(
                    "Perceptual retrieval returned items within the strong-candidate "
                    "Hamming distance threshold. Near-duplicate matches are "
                    "candidates for comparison, not proof that the items are the "
                    "same image or that one came from the other."
                ),
                case_id=evidence.case_id,
                case_number=case.case_number if case else None,
                evidence_id=evidence.id,
                filename=evidence.filename,
                observed_at=iso(row.searched_at),
                source=f"matches?query_evidence_id={evidence.id}",
                basis={
                    "candidate_count": int(row.candidates),
                    "closest_hamming_distance": int(row.closest)
                    if row.closest is not None
                    else None,
                    "best_similarity": row.best_similarity,
                    "band": matching.BAND_STRONG,
                    "method": matching.METHOD,
                    "interpretation": matching.INTERPRETATION,
                },
                action="Compare the candidates against the query item.",
            )
        )
    return alerts


def _capability_alerts(db: Any, settings: Any) -> list[AlertOut]:
    """Alerts about what this deployment cannot do.

    These are the limits that bound every conclusion drawn on this host, so they
    belong in the same list as the findings rather than hidden on a settings page.
    """
    alerts: list[AlertOut] = []
    now = iso(utcnow())

    detector_status = detector_service.status(settings)
    if not detector_status.get("available"):
        alerts.append(
            AlertOut(
                alert_id="capability:detector-unavailable",
                severity=SEVERITY_MEDIUM,
                category=CATEGORY_CAPABILITY,
                title="No AI-manipulation detector installed",
                detail=(
                    "The ai_detection signal carries the largest declared weight "
                    "in fusion and cannot run, so it is excluded and the remaining "
                    "weights are renormalised. Verdicts on this deployment are "
                    "reached without it -- that is a limit of the installation, "
                    "not a finding about any file."
                ),
                observed_at=now,
                source="detector.status()",
                basis={
                    "adapter": detector_status.get("adapter"),
                    "reason": detector_status.get("reason"),
                    "unavailable_because": detector_status.get("unavailable_because"),
                    "interface_version": detector_status.get("interface_version"),
                    "modalities": detector_status.get("modalities", {}),
                },
                action="Configure a detector entrypoint or model path.",
            )
        )

    validator = provenance_service.validator_status()
    if not validator["signature_validation_available"]:
        alerts.append(
            AlertOut(
                alert_id="capability:c2pa-container-scan-only",
                severity=SEVERITY_LOW,
                category=CATEGORY_CAPABILITY,
                title="C2PA signatures cannot be validated",
                detail=provenance_service.CONTAINER_SCAN_ONLY_DETAIL,
                observed_at=now,
                source="provenance.validator_status()",
                basis=validator,
                action="Install the optional c2pa library for signature validation.",
            )
        )

    index_status = indexing.status(settings)
    pending = int(
        db.execute(
            select(func.count())
            .select_from(Evidence)
            .where(Evidence.phash.is_not(None), Evidence.indexed.is_(False))
        ).scalar_one()
    )
    if pending:
        alerts.append(
            AlertOut(
                alert_id="capability:index-pending",
                severity=SEVERITY_MEDIUM,
                category=CATEGORY_CAPABILITY,
                title=f"{pending} hashable item(s) not in the perceptual index",
                detail=(
                    "Near-duplicate retrieval and propagation only see indexed "
                    "items, so their coverage is incomplete until the index is "
                    "updated. An absent match here means 'not searched', not "
                    "'nothing similar exists'."
                ),
                observed_at=now,
                source="evidence.indexed",
                basis={
                    "pending_evidence_count": pending,
                    "indexed_count": index_status["indexed_count"],
                    "index_version": index_status["index_version"],
                },
                action="Rebuild the perceptual index.",
            )
        )
    elif index_status["indexed_count"] == 0:
        alerts.append(
            AlertOut(
                alert_id="capability:index-empty",
                severity=SEVERITY_LOW,
                category=CATEGORY_CAPABILITY,
                title="Perceptual index is empty",
                detail=(
                    "Nothing is indexed, so near-duplicate retrieval has nothing "
                    "to compare against and propagation cannot establish an "
                    "earliest known instance in the indexed evidence corpus."
                ),
                observed_at=now,
                source="index.status()",
                basis={
                    "indexed_count": 0,
                    "index_version": index_status["index_version"],
                    "backend": index_status["backend"],
                },
                action="Ingest a corpus and build the index.",
            )
        )
    return alerts


def _audit_alerts(db: Any) -> list[AlertOut]:
    """The chain's state, taken from the last recorded verification.

    This endpoint does not re-verify the chain: verification walks every row and
    recomputes every hash, and running that on a page load would make an
    expensive integrity check into background noise. What is reported instead is
    the result of the last verification that was actually performed, with its
    timestamp -- and if there has never been one, that is what it says.
    """
    latest = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.event == audit_service.EVENT_AUDIT_VERIFIED)
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    if latest is None:
        total = int(db.execute(select(func.count()).select_from(AuditLog)).scalar_one())
        if not total:
            return []
        return [
            AlertOut(
                alert_id="audit:never-verified",
                severity=SEVERITY_LOW,
                category=CATEGORY_AUDIT,
                title="Audit chain has never been verified",
                detail=AUDIT_NEVER_VERIFIED,
                observed_at=None,
                source="audit_log",
                basis={"total_rows": total, "algorithm": audit_service.ALGORITHM},
                action="POST /api/audit/verify",
            )
        ]

    details = latest.details or {}
    if details.get("valid") is False:
        return [
            AlertOut(
                alert_id=f"audit:invalid:{latest.audit_id}",
                severity=SEVERITY_CRITICAL,
                category=CATEGORY_AUDIT,
                title="Audit chain failed verification",
                detail=(
                    "The last recorded verification of the hash chain failed, so "
                    "the audit history has been altered, reordered or truncated. "
                    "Every record derived from it is in question until this is "
                    "explained."
                ),
                case_id=latest.case_id,
                observed_at=latest.timestamp,
                source=f"audit_log/{latest.audit_id}",
                basis={
                    "first_invalid_seq": details.get("first_invalid_seq"),
                    "issue_count": details.get("issue_count"),
                    "total_rows": details.get("total_rows"),
                    "verified_head_hash": details.get("verified_head_hash"),
                    "algorithm": details.get("algorithm"),
                },
                action="POST /api/audit/verify for the full issue list.",
            )
        ]
    return []


@router.get(
    "",
    response_model=AlertsResponse,
    summary="Alerts derived from the current contents of the case file",
)
def list_alerts(
    db: DbDep,
    settings: SettingsDep,
    severity: str | None = Query(None, description="Filter to one severity."),
    category: str | None = Query(None, description="Filter to one category."),
    case_id: str | None = Query(None, description="Filter to one case."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AlertsResponse:
    """Everything currently worth an examiner's attention, most serious first.

    Derived from stored analysis results, stored matches, capability state and the
    audit chain's last recorded verification -- never from a separate alerts
    table, and never by re-thresholding a score. An empty list means nothing in
    the case file currently warrants attention, which is itself a finding.

    ``severity_counts`` and ``category_counts`` are computed over all alerts
    before filtering or paging, so the totals do not shift as the client pages.
    """
    alerts = (
        _audit_alerts(db)
        + _verdict_alerts(db)
        + _provenance_alerts(db)
        + _near_duplicate_alerts(db)
        + _capability_alerts(db, settings)
    )
    alerts.sort(
        key=lambda a: (
            SEVERITY_RANK.get(a.severity, len(SEVERITIES)),
            a.observed_at or "",
        )
    )

    severity_counts = {s: 0 for s in SEVERITIES}
    category_counts = {c: 0 for c in CATEGORIES}
    for alert in alerts:
        severity_counts[alert.severity] = severity_counts.get(alert.severity, 0) + 1
        category_counts[alert.category] = category_counts.get(alert.category, 0) + 1

    selected = alerts
    if severity is not None:
        selected = [a for a in selected if a.severity == severity]
    if category is not None:
        selected = [a for a in selected if a.category == category]
    if case_id is not None:
        selected = [a for a in selected if a.case_id == case_id]

    total = len(selected)
    page = selected[offset : offset + limit]

    notes = [DERIVED_NOTE]
    if not alerts:
        notes.append(
            "No alerts. Nothing in the stored analysis, matches, capability state "
            "or audit chain currently warrants attention."
        )
    if severity_counts.get(SEVERITY_CRITICAL):
        notes.append(
            "A critical alert concerns the integrity of the record itself, not the "
            "media. Resolve it before relying on anything derived from the chain."
        )

    return AlertsResponse(
        total=total,
        count=len(page),
        offset=offset,
        truncated=offset + len(page) < total,
        alerts=page,
        severity_counts=severity_counts,
        category_counts=category_counts,
        severities=list(SEVERITIES),
        categories=list(CATEGORIES),
        severity_definitions=SEVERITY_DEFINITIONS,
        category_definitions=CATEGORY_DEFINITIONS,
        filters={
            "severity": severity,
            "category": category,
            "case_id": case_id,
            "limit": limit,
            "offset": offset,
        },
        generated_at=iso(utcnow()),
        notes=notes,
    )
