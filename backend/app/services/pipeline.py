"""Analysis stage runners and the full-case pipeline.

Each ``run_*`` function performs one stage for one evidence item: it computes a
result, persists it as an ``AnalysisResult`` row, records the corresponding
audit event, and returns the payload. ``analyse_case`` chains them in the
documented order for ``POST /api/cases/{case_id}/analyse``.

Stages are individually callable so a single endpoint (metadata, matches,
detector) can run just what it needs, and so a stage that is unavailable
degrades to a reported status instead of failing the case.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    KIND_DETECTOR,
    KIND_FORENSICS,
    KIND_FUSION,
    KIND_METADATA,
    KIND_PROVENANCE,
    Case,
    Evidence,
)
from app.services import (
    analysis_store,
    audit,
    detector as detector_service,
    forensics as forensics_service,
    fusion as fusion_service,
    indexing,
    ingestion,
    matching,
    metadata as metadata_service,
    propagation as propagation_service,
    provenance as provenance_service,
    storage,
)
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.pipeline")

ANALYSIS_VERSION = "1.0"

# The documented order of the full-case pipeline. Reported in the response so a
# reader can see exactly which steps ran, in which order.
ANALYSIS_STAGES = (
    "metadata_extraction",
    "ai_detection",
    "provenance_inspection",
    "compression_forensics",
    "near_duplicate_retrieval",
    "signal_fusion",
    "case_wide_retrieval",
    "propagation_reconstruction",
    "audit_trail_read",
    "audit_chain_verification",
)

VERDICT_SELECTION = (
    "The case-level 'verdict' is the evidence item with the highest "
    "manipulation_score; items whose score could not be computed rank last. It "
    "is a pointer to the item that most warrants examiner attention, NOT a "
    "judgement about the case as a whole. Every item's own verdict is in "
    "'verdicts'."
)


def run_metadata(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
    refresh: bool = False,
) -> dict[str, Any]:
    """Extract (or reuse) metadata for one evidence item and persist it."""
    if not refresh:
        cached = analysis_store.latest_result(
            session, evidence_id=evidence.id, kind=KIND_METADATA
        )
        if cached is not None and isinstance(cached.payload, dict):
            payload = dict(cached.payload)
            payload["cached"] = True
            payload["extracted_at"] = analysis_store.result_to_dict(cached)["created_at"]
            return payload

    path = storage.absolute_path(evidence.stored_path, settings)
    payload = metadata_service.extract_metadata(path, evidence.media_type)
    payload["cached"] = False
    status = str(payload.get("status", "OK"))

    stored = analysis_store.store_result(
        session,
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        kind=KIND_METADATA,
        payload=payload,
        status=status,
    )
    payload["extracted_at"] = analysis_store.result_to_dict(stored)["created_at"]

    summary = payload.get("presence_summary", {})
    audit.record(
        session,
        event=audit.EVENT_METADATA_EXTRACTED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "media_type": evidence.media_type,
            "status": status,
            "exif_present": bool(payload.get("exif", {}).get("present")),
            "fields_present": summary.get("fields_present", []),
            "fields_missing": summary.get("fields_missing", []),
            "extractor": payload.get("extractor"),
        },
    )
    return payload


def run_detector(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
    refresh: bool = False,
) -> dict[str, Any]:
    """Run (or reuse) the AI-manipulation detector for one evidence item.

    An unavailable or failing detector produces a reported status, never a made-up
    score and never an exception that reaches the client.
    """
    if not refresh:
        cached = analysis_store.latest_result(
            session, evidence_id=evidence.id, kind=KIND_DETECTOR
        )
        if cached is not None and isinstance(cached.payload, dict):
            payload = dict(cached.payload)
            payload["cached"] = True
            payload["analysed_at"] = analysis_store.result_to_dict(cached)["created_at"]
            return payload

    adapter = detector_service.get_detector(settings)
    path = storage.absolute_path(evidence.stored_path, settings)
    result = adapter.analyse(path, media_type=evidence.media_type)
    payload = result.to_dict()
    payload["adapter"] = adapter.id
    payload["cached"] = False

    stored = analysis_store.store_result(
        session,
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        kind=KIND_DETECTOR,
        payload=payload,
        status=result.status,
        score=result.score,
        model=result.model,
        model_version=result.model_version,
    )
    payload["analysed_at"] = analysis_store.result_to_dict(stored)["created_at"]

    audit.record(
        session,
        event=audit.EVENT_DETECTOR_RUN,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "adapter": adapter.id,
            "model": result.model,
            "model_version": result.model_version,
            "status": result.status,
            "score": result.score,
            "detail": result.detail,
            "interface_version": result.interface_version,
        },
    )
    return payload


def _cached_payload(
    session: Session, *, evidence: Evidence, kind: str, stamp: str
) -> dict[str, Any] | None:
    """Return a stored payload for this (evidence, kind), or None."""
    cached = analysis_store.latest_result(session, evidence_id=evidence.id, kind=kind)
    if cached is None or not isinstance(cached.payload, dict):
        return None
    payload = dict(cached.payload)
    payload["cached"] = True
    payload[stamp] = analysis_store.result_to_dict(cached)["created_at"]
    return payload


def run_provenance(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
    refresh: bool = False,
) -> dict[str, Any]:
    """Inspect (or reuse) the C2PA provenance manifest for one evidence item."""
    if not refresh:
        cached = _cached_payload(
            session, evidence=evidence, kind=KIND_PROVENANCE, stamp="inspected_at"
        )
        if cached is not None:
            return cached

    path = storage.absolute_path(evidence.stored_path, settings)
    payload = provenance_service.inspect(path, evidence.media_type)
    payload["cached"] = False

    stored = analysis_store.store_result(
        session,
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        kind=KIND_PROVENANCE,
        payload=payload,
        status=str(payload.get("status", "OK")),
    )
    payload["inspected_at"] = analysis_store.result_to_dict(stored)["created_at"]

    audit.record(
        session,
        event=audit.EVENT_PROVENANCE_INSPECTED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "status": payload.get("status"),
            "state": payload.get("state"),
            "manifest_present": payload.get("manifest_present"),
            "signature_validated": payload.get("signature_validated"),
            "c2pa_library_available": payload.get("c2pa_library_available"),
            "inspector": payload.get("inspector"),
        },
    )
    return payload


def run_forensics(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
    refresh: bool = False,
) -> dict[str, Any]:
    """Run (or reuse) compression forensics for one evidence item."""
    if not refresh:
        cached = _cached_payload(
            session, evidence=evidence, kind=KIND_FORENSICS, stamp="analysed_at"
        )
        if cached is not None:
            return cached

    path = storage.absolute_path(evidence.stored_path, settings)
    payload = forensics_service.analyse(path, evidence.media_type)
    payload["cached"] = False

    stored = analysis_store.store_result(
        session,
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        kind=KIND_FORENSICS,
        payload=payload,
        status=str(payload.get("status", "OK")),
        score=payload.get("score"),
    )
    payload["analysed_at"] = analysis_store.result_to_dict(stored)["created_at"]

    audit.record(
        session,
        event=audit.EVENT_FORENSICS_ANALYSED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "status": payload.get("status"),
            "score": payload.get("score"),
            "analyser": payload.get("analyser"),
            "calibrated": False,
        },
    )
    return payload


def run_fusion(
    session: Session,
    *,
    evidence: Evidence,
    settings: Settings,
    actor: str = "system",
    refresh: bool = False,
) -> dict[str, Any]:
    """Run every signal stage for one evidence item and fuse them into a verdict.

    Stage failures do not propagate: each stage reports its own status and the
    fusion layer excludes it, renormalising the remaining weights.
    """
    if not refresh:
        cached = _cached_payload(
            session, evidence=evidence, kind=KIND_FUSION, stamp="fused_at"
        )
        if cached is not None:
            return cached

    metadata_payload = run_metadata(
        session, evidence=evidence, settings=settings, actor=actor, refresh=refresh
    )
    detector_payload = run_detector(
        session, evidence=evidence, settings=settings, actor=actor, refresh=refresh
    )
    provenance_payload = run_provenance(
        session, evidence=evidence, settings=settings, actor=actor, refresh=refresh
    )
    forensics_payload = run_forensics(
        session, evidence=evidence, settings=settings, actor=actor, refresh=refresh
    )
    match_payload = matching.search_evidence(
        session, evidence=evidence, settings=settings
    )
    # search_evidence is deliberately audit-free (search_case audits at case level),
    # so the search this stage performs is recorded here.
    audit.record(
        session,
        event=audit.EVENT_MATCH_SEARCHED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "scope": "single_evidence_item_for_fusion",
            "candidates": len(match_payload.get("candidates", [])),
            "indexed_count": match_payload.get("indexed_count"),
            "index_version": match_payload.get("index_version"),
            "max_distance": match_payload.get("max_distance"),
            "method": match_payload.get("method"),
        },
    )

    signals = fusion_service.build_signals(
        detector_payload=detector_payload,
        match_payload=match_payload,
        metadata_payload=metadata_payload,
        provenance_payload=provenance_payload,
        forensics_payload=forensics_payload,
        sha256=evidence.sha256,
    )
    payload = fusion_service.fuse(signals, settings, media_type=evidence.media_type)
    payload["evidence_id"] = evidence.id
    payload["filename"] = evidence.filename
    payload["sha256"] = evidence.sha256
    payload["cached"] = False

    stored = analysis_store.store_result(
        session,
        case_id=evidence.case_id,
        evidence_id=evidence.id,
        kind=KIND_FUSION,
        payload=payload,
        status="OK",
        score=payload.get("manipulation_score"),
        verdict=payload.get("verdict"),
        model=detector_payload.get("model"),
        model_version=detector_payload.get("model_version"),
    )
    payload["fused_at"] = analysis_store.result_to_dict(stored)["created_at"]

    audit.record(
        session,
        event=audit.EVENT_VERDICT_GENERATED,
        case_id=evidence.case_id,
        actor=actor,
        details={
            "evidence_id": evidence.id,
            "verdict": payload.get("verdict"),
            "manipulation_score": payload.get("manipulation_score"),
            "confidence": payload.get("confidence"),
            "signals_available": payload.get("signals_available"),
            "signals_total": payload.get("signals_total"),
            "signal_coverage": payload.get("signal_coverage"),
            "primary_signal_available": payload.get("primary_signal_available"),
            "fusion_version": payload.get("fusion_version"),
            "weights": payload.get("declared_weights"),
            "thresholds": payload.get("thresholds"),
            "included_signals": [
                {
                    "signal_id": s["signal_id"],
                    "score": s["score"],
                    "effective_weight": s["effective_weight"],
                    "contribution": s["contribution"],
                }
                for s in payload.get("signals", [])
                if s.get("included")
            ],
            "excluded_signals": [
                {"signal_id": s["signal_id"], "status": s["status"]}
                for s in payload.get("signals", [])
                if not s.get("included")
            ],
        },
    )
    return payload


# --------------------------------------------------------------------------- #
# Full-case pipeline
# --------------------------------------------------------------------------- #
def _leading_index(verdicts: list[dict[str, Any]]) -> int | None:
    """Index of the item with the highest manipulation score; None if empty.

    Items with no score (nothing could be measured) sort last rather than being
    treated as 0.0 -- absence of a measurement is not a low score.
    """
    if not verdicts:
        return None
    scored = [
        (i, v.get("manipulation_score"))
        for i, v in enumerate(verdicts)
    ]
    return max(
        scored,
        key=lambda pair: (pair[1] is not None, pair[1] if pair[1] is not None else -1.0),
    )[0]


def analyse_case(
    session: Session,
    *,
    case: Case,
    settings: Settings,
    actor: str = "api",
    refresh: bool = False,
    audit_limit: int | None = None,
) -> dict[str, Any]:
    """Run the complete pipeline for a case and return one consolidated result.

    Every figure in the response is computed here from this case's evidence --
    nothing is templated, defaulted or carried over from another case. A case
    with no evidence returns empty collections and a null verdict rather than a
    placeholder finding.
    """
    started = time.perf_counter()
    warnings: list[str] = []

    evidence_rows = analysis_store.case_evidence(session, case.id)
    if not evidence_rows:
        warnings.append(
            "No evidence has been ingested for this case, so no analysis could "
            "be performed."
        )

    index_status = indexing.status(settings)
    if index_status["indexed_count"] == 0:
        warnings.append(
            "The perceptual index is empty, so no near-duplicate candidates and "
            "no propagation history could be found. That absence is a limit of "
            "this deployment's coverage, not a finding about the evidence."
        )

    verdicts = [
        run_fusion(
            session, evidence=evidence, settings=settings, actor=actor, refresh=refresh
        )
        for evidence in evidence_rows
    ]

    matches = matching.search_case(
        session, case=case, settings=settings, actor=actor
    )
    propagation = propagation_service.reconstruct_case(
        session, case=case, settings=settings, actor=actor, refresh=False
    )

    detector_status = detector_service.status(settings)
    if not detector_status.get("available"):
        warnings.append(
            "No AI-manipulation detector is installed, so the ai_detection "
            "signal is UNAVAILABLE and was excluded from fusion. This is not a "
            "finding of authenticity and not a finding of manipulation."
        )

    leading = _leading_index(verdicts)
    verdict = verdicts[leading] if leading is not None else None
    signals = list(verdict["signals"]) if verdict else []

    # The trail is read before the completion entry is appended -- an entry
    # cannot contain the hash of a chain that already includes it.
    trail = audit.trail(session, case.id, limit=audit_limit)
    verification = audit.verify_chain(session, case.id)
    audit_block = {
        **trail,
        "chain_valid": verification["valid"],
        "first_invalid_seq": verification["first_invalid_seq"],
        "issues": verification["issues"],
        "note": (
            "Read before this analysis was recorded, so the ANALYSIS_COMPLETED "
            f"entry that follows head_hash {trail['head_hash']} is not listed "
            "here."
        ),
    }

    elapsed_ms = round((time.perf_counter() - started) * 1000, 3)

    result: dict[str, Any] = {
        "case": ingestion.case_to_dict(case, evidence_count=len(evidence_rows)),
        "evidence": [ingestion.evidence_to_dict(e) for e in evidence_rows],
        "verdict": verdict,
        "signals": signals,
        "matches": matches,
        "origin": propagation.get("origin"),
        "timeline": propagation.get("timeline", []),
        "audit": audit_block,
        "processing_time_ms": elapsed_ms,
        # --- context needed to interpret the above --------------------------- #
        "verdicts": verdicts,
        "verdict_selection": VERDICT_SELECTION,
        "verdict_evidence_id": verdict.get("evidence_id") if verdict else None,
        "propagation": {
            key: propagation[key]
            for key in (
                "method",
                "interpretation",
                "graph",
                "instance_count",
                "matched_candidate_count",
                "platforms",
                "generations",
                "truncated",
                "notes",
                "caveats",
            )
            if key in propagation
        },
        "detector": detector_status,
        "index": index_status,
        "stages": list(ANALYSIS_STAGES),
        "analysis_version": ANALYSIS_VERSION,
        "fusion_method": fusion_service.FUSION_METHOD,
        "score_semantics": fusion_service.SCORE_SEMANTICS,
        "caveat": fusion_service.CAVEAT,
        "warnings": warnings,
        "analysed_at": iso(utcnow()),
        "refreshed": refresh,
    }

    audit.record(
        session,
        event=audit.EVENT_ANALYSIS_COMPLETED,
        case_id=case.id,
        actor=actor,
        details={
            "analysis_version": ANALYSIS_VERSION,
            "stages": list(ANALYSIS_STAGES),
            "evidence_count": len(evidence_rows),
            "refresh": refresh,
            "verdicts": [
                {
                    "evidence_id": v.get("evidence_id"),
                    "verdict": v.get("verdict"),
                    "manipulation_score": v.get("manipulation_score"),
                    "confidence": v.get("confidence"),
                    "signal_coverage": v.get("signal_coverage"),
                }
                for v in verdicts
            ],
            "leading_evidence_id": verdict.get("evidence_id") if verdict else None,
            "total_candidates": matches.get("total_candidates"),
            "origin_evidence_id": (propagation.get("origin") or {}).get("evidence_id"),
            "timeline_events": len(propagation.get("timeline", [])),
            "index_version": index_status.get("index_version"),
            "indexed_count": index_status.get("indexed_count"),
            "detector_available": detector_status.get("available"),
            "chain_valid_before_this_entry": verification["valid"],
            "processing_time_ms": elapsed_ms,
            "warnings": warnings,
        },
    )
    return result
