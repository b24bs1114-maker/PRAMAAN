"""Per-case analysis endpoints: metadata, matching, propagation, verdict, audit.

Grouped on the ``/api/cases/{case_id}`` prefix so the frontend addresses a case
and asks for one facet of it at a time. Every route here is read-mostly from the
client's point of view: results are computed if absent, cached in
``analysis_results``, and always audited.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CaseDep, DbDep, SettingsDep
from app.config import get_settings
from app.models import KIND_FUSION, AnalysisResult, AuditLog
from app.schemas.api import (
    AnalysisResponse,
    AuditTrailResponse,
    AuditVerifyResponse,
    DetectionResponse,
    MatchesResponse,
    MetadataResponse,
    PropagationResponse,
    ProvenanceResponse,
    StoredMatchesResponse,
    StoredVerdictResponse,
    VerdictResponse,
    WebDiscoveryResponse,
)
from app.services import (
    analysis_store,
    audit,
    detector as detector_service,
    fusion,
    matching,
    metadata as metadata_service,
    pipeline,
    propagation,
    provenance as provenance_service,
    storage,
)
from app.services.web_provenance import WebProvenanceService
from app.utils.timeutil import iso

logger = logging.getLogger("pramaan.api.analysis")

router = APIRouter(prefix="/api/cases", tags=["analysis"])

#: What each C2PA state means, spelled out because three of the four are commonly
#: misread -- above all ``ABSENT``, which is the normal condition for almost all
#: media and says nothing about authenticity.
PROVENANCE_STATE_DEFINITIONS: dict[str, str] = {
    provenance_service.STATE_VERIFIED: (
        "A C2PA manifest is present and its signature validated against the "
        "validator's trust list."
    ),
    provenance_service.STATE_INVALID: (
        "A C2PA manifest is present and its signature FAILED validation. Its "
        "claims cannot be relied on."
    ),
    provenance_service.STATE_UNVERIFIED: (
        "A C2PA manifest container is present but no signature validation was "
        "performed, so its contents are an unverified self-assertion."
    ),
    provenance_service.STATE_ABSENT: (
        "No C2PA manifest was found. This is the normal condition for almost all "
        "media and is NOT an indicator of manipulation."
    ),
}


@router.get(
    "/{case_id}/metadata",
    response_model=MetadataResponse,
    summary="Extract metadata for every evidence item in a case",
)
def get_case_metadata(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-extract instead of returning the stored result."
    ),
) -> MetadataResponse:
    """Local EXIF/container metadata for each item of evidence.

    Absence of metadata is reported plainly and is **not** treated as evidence of
    manipulation -- see the ``interpretation`` field on every item.
    """
    items = []
    for evidence in analysis_store.case_evidence(db, case.id):
        payload = pipeline.run_metadata(
            db, evidence=evidence, settings=settings, actor="api", refresh=refresh
        )
        items.append(
            {
                "evidence_id": evidence.id,
                "filename": evidence.filename,
                "media_type": evidence.media_type,
                "mime_type": evidence.mime_type,
                "size_bytes": evidence.size_bytes,
                "sha256": evidence.sha256,
                "ingested_at": iso(evidence.ingested_at),
                "metadata": payload,
            }
        )

    return MetadataResponse(
        case_id=case.id,
        count=len(items),
        items=items,
        interpretation=metadata_service.INTERPRETATION_NOTE,
        extractor=metadata_service.EXTRACTOR,
    )


@router.get(
    "/{case_id}/provenance",
    response_model=ProvenanceResponse,
    summary="Inspect the C2PA provenance manifest of every evidence item",
)
def get_case_provenance(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-inspect instead of returning the stored result."
    ),
) -> ProvenanceResponse:
    """C2PA manifest state for each item of evidence, with what it does and does
    not establish.

    Four states, and the difference between them matters: a *validated* signature
    is the only one that proves anything about who signed what. A present but
    unvalidated manifest is a self-assertion. An **absent** manifest is the normal
    condition for almost all media and is never an indicator of manipulation.

    ``validator`` reports what this deployment can actually do -- without the
    optional c2pa library, signatures cannot be validated at all, and every
    manifest found is reported as unverified rather than as verified.
    """
    items = []
    for evidence in analysis_store.case_evidence(db, case.id):
        payload = pipeline.run_provenance(
            db, evidence=evidence, settings=settings, actor="api", refresh=refresh
        )
        items.append(
            {
                "evidence_id": evidence.id,
                "filename": evidence.filename,
                "media_type": evidence.media_type,
                "sha256": evidence.sha256,
                "provenance": payload,
            }
        )

    validator = provenance_service.validator_status()
    notes: list[str] = []
    if not validator["signature_validation_available"]:
        notes.append(provenance_service.CONTAINER_SCAN_ONLY_DETAIL)
    absent = sum(
        1
        for item in items
        if item["provenance"].get("state") == provenance_service.STATE_ABSENT
    )
    if absent:
        notes.append(
            f"{absent} of {len(items)} item(s) carry no C2PA manifest. Almost no "
            "media in circulation does; this is not a finding about them."
        )

    return ProvenanceResponse(
        case_id=case.id,
        count=len(items),
        items=items,
        validator=validator,
        state_definitions=PROVENANCE_STATE_DEFINITIONS,
        interpretation=provenance_service.INTERPRETATION,
        notes=notes,
    )


@router.post(
    "/{case_id}/matches",
    response_model=MatchesResponse,
    summary="Find near-duplicate candidates for a case's evidence",
)
def find_case_matches(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    top_k: int | None = Query(
        None, ge=1, le=500, description="Maximum candidates per evidence item."
    ),
    max_distance: int | None = Query(
        None,
        ge=0,
        le=64,
        description="Hamming distance cut-off (0-64) for candidate retention.",
    ),
) -> MatchesResponse:
    """Retrieve, verify and rank **near-duplicate candidates** from the index.

    Retrieval is exhaustive over the local perceptual index; every candidate's
    pHash and dHash distances are then recomputed exactly from the stored hashes.
    Results are candidates only: perceptual similarity is not proof of identity,
    derivation, or a shared real-world origin.
    """
    result = matching.search_case(
        db,
        case=case,
        settings=settings,
        top_k=top_k,
        max_distance=max_distance,
        actor="api",
    )
    return MatchesResponse(**result)


@router.get(
    "/{case_id}/matches",
    response_model=StoredMatchesResponse,
    summary="Read the near-duplicate candidates already stored for a case",
)
def get_case_matches(
    case: CaseDep, db: DbDep, settings: SettingsDep
) -> StoredMatchesResponse:
    """Stored candidates only -- retrieval is not run.

    Opening a page must not silently change the case file, and retrieval writes:
    it replaces the stored match set and appends a ``MATCH_SEARCHED`` event. So
    this route reads back what the last search stored, with the distances and
    ranks exactly as that search computed them. Use ``POST`` on the same path to
    search.

    ``searched`` comes from the audit trail rather than from the match count,
    which is what separates "searched, nothing similar in the index" from "never
    searched" -- two very different states that an empty list alone would
    conflate.
    """
    last_search = (
        db.execute(
            select(AuditLog)
            .where(
                AuditLog.case_id == case.id,
                AuditLog.event == audit.EVENT_MATCH_SEARCHED,
            )
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    result = matching.stored_case_matches(db, case=case, settings=settings)

    notes: list[str] = []
    if last_search is None:
        notes.append(
            "Near-duplicate retrieval has never been run for this case, so there "
            "are no stored candidates. This is not a finding that nothing similar "
            "exists -- POST to this endpoint to search."
        )
    elif not result["total_candidates"]:
        notes.append(
            "Retrieval ran and returned no candidates within the distance "
            "cut-off, which means nothing perceptually similar was found in the "
            "indexed corpus."
        )

    return StoredMatchesResponse(
        **result,
        searched=last_search is not None,
        searched_at=last_search.timestamp if last_search else None,
        run_matches_url=f"/api/cases/{case.id}/matches",
        notes=notes,
    )


@router.get(
    "/{case_id}/propagation",
    response_model=PropagationResponse,
    summary="Reconstruct propagation and the earliest known instance",
)
def get_case_propagation(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-run near-duplicate matching before reconstructing."
    ),
    record: bool = Query(
        True,
        description=(
            "Append MATCH_SEARCHED / PROPAGATION_RECONSTRUCTED to the audit "
            "chain. Pass false for a page load, which must not write."
        ),
    ),
) -> PropagationResponse:
    """Timeline and graph of copies visible in the local indexed corpus.

    ``origin`` is the **earliest known instance in the indexed evidence corpus**.
    It is explicitly not an absolute real-world origin: the corpus is a partial
    view, and recorded timestamps can be wrong or altered.

    ``record=false`` is the read: it reconstructs from retrieval already on
    record, runs nothing, and writes nothing. It exists because merely opening
    the Provenance screen used to append two rows to this case's chain and move
    its head hash. ``trace_status`` then reports whether there was anything on
    record to reconstruct from, so an empty graph is never mistaken for a search
    that found nothing.

    The default stays ``true`` so that an explicit trace -- the operator asking
    for one -- remains an auditable act.
    """
    if refresh and not record:
        raise HTTPException(
            status_code=422,
            detail=(
                "refresh=true re-runs near-duplicate retrieval, which is an act "
                "on the evidence and must be recorded; it cannot be combined "
                "with record=false."
            ),
        )
    result = propagation.reconstruct_case(
        db, case=case, settings=settings, actor="api", refresh=refresh, record=record
    )
    return PropagationResponse(**result)


@router.post(
    "/{case_id}/detect",
    response_model=DetectionResponse,
    summary="Run the AI-manipulation detector over a case's evidence",
)
def run_case_detection(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-run the detector instead of reusing a stored result."
    ),
) -> DetectionResponse:
    """Per-evidence detector output.

    If no detector model is installed, every item reports ``status:
    "UNAVAILABLE"`` with ``score: null``. That is a missing signal -- not a
    finding of authenticity and not a finding of manipulation.
    """
    items = []
    for evidence in analysis_store.case_evidence(db, case.id):
        items.append(
            {
                "evidence_id": evidence.id,
                "filename": evidence.filename,
                "media_type": evidence.media_type,
                "detection": pipeline.run_detector(
                    db,
                    evidence=evidence,
                    settings=settings,
                    actor="api",
                    refresh=refresh,
                ),
            }
        )

    return DetectionResponse(
        case_id=case.id,
        count=len(items),
        items=items,
        detector=detector_service.status(settings),
        interpretation=detector_service.SCORE_SEMANTICS,
    )


@router.post(
    "/{case_id}/verdict",
    response_model=VerdictResponse,
    summary="Fuse all signals into a per-evidence verdict",
)
def generate_case_verdict(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-run every signal stage instead of reusing stored results."
    ),
) -> VerdictResponse:
    """Run every signal stage and assess the results, one assessment per item.

    Each signal reports its own score, declared weight, normalised weight,
    contribution, status and explanation, so the arithmetic can be recomputed by
    hand. Signals that could not produce a measurement are excluded and the
    remaining weights renormalised -- a missing signal is never scored as zero.

    Read ``assessment`` on each item. It is the authoritative finding and it is
    task-qualified per modality: ``INDICATORS_DETECTED``,
    ``NO_INDICATORS_DETECTED``, ``INCONCLUSIVE`` or ``NOT_ASSESSED``, with
    structured ``reason_codes``, the ``policy_version`` that decided it, and the
    coverage it was reached from. Only checks eligible for the assessed task can
    move the state; everything else the system measures is reported as a
    descriptive observation and cannot manufacture a finding.

    The top-level ``verdict`` / ``manipulation_score`` / ``confidence`` fields
    remain for existing clients but are a projection of ``assessment``, and a
    lossy one: both ``NOT_ASSESSED`` and ``INCONCLUSIVE`` become
    ``INSUFFICIENT_EVIDENCE``. Anything that must tell "not assessed" from
    "assessed and undecidable" reads ``assessment.state``.

    The weights and thresholds are configurable prototype defaults and have not
    been validated against a forensic reference dataset.
    """
    items = [
        pipeline.run_fusion(
            db, evidence=evidence, settings=settings, actor="api", refresh=refresh
        )
        for evidence in analysis_store.case_evidence(db, case.id)
    ]

    return VerdictResponse(
        case_id=case.id,
        count=len(items),
        items=items,
        method=fusion.FUSION_METHOD,
        interpretation=fusion.SCORE_SEMANTICS,
        caveat=fusion.CAVEAT,
    )


@router.get(
    "/{case_id}/verdict",
    response_model=StoredVerdictResponse,
    summary="Read the fused verdicts already stored for a case",
)
def get_case_verdict(case: CaseDep, db: DbDep) -> StoredVerdictResponse:
    """Stored verdicts only -- fusion is not run.

    Fusion is the only thing that decides a verdict, and it is not re-run here:
    the payloads returned are the ones it wrote, so this view can never disagree
    with the verdict of record.

    Evidence with no stored verdict is listed in ``pending_evidence`` rather than
    being given a placeholder. An item that has not been analysed has no verdict,
    which is not the same as an inconclusive one.
    """
    evidence_rows = analysis_store.case_evidence(db, case.id)
    stored = {
        row.evidence_id: row
        for row in db.execute(
            select(AnalysisResult)
            .where(
                AnalysisResult.case_id == case.id,
                AnalysisResult.kind == KIND_FUSION,
            )
            .order_by(AnalysisResult.created_at)
        ).scalars()
    }

    items: list[dict] = []
    pending: list[dict] = []
    for evidence in evidence_rows:
        row = stored.get(evidence.id)
        if row is None or not isinstance(row.payload, dict):
            pending.append(
                {
                    "evidence_id": evidence.id,
                    "filename": evidence.filename,
                    "media_type": evidence.media_type,
                    "sha256": evidence.sha256,
                    "reason": "No fused verdict is stored for this item.",
                }
            )
            continue
        payload = dict(row.payload)
        payload["cached"] = True
        payload.setdefault("fused_at", iso(row.created_at))
        items.append(payload)

    notes: list[str] = []
    if pending:
        notes.append(
            f"{len(pending)} of {len(evidence_rows)} evidence item(s) have no "
            "stored verdict. They have not been analysed; no verdict has been "
            "withheld or implied for them."
        )
    if not evidence_rows:
        notes.append("This case holds no evidence, so there is nothing to fuse.")

    return StoredVerdictResponse(
        case_id=case.id,
        count=len(items),
        items=items,
        method=fusion.FUSION_METHOD,
        interpretation=fusion.SCORE_SEMANTICS,
        caveat=fusion.CAVEAT,
        evidence_count=len(evidence_rows),
        analysed_count=len(items),
        pending_evidence=pending,
        run_verdict_url=f"/api/cases/{case.id}/verdict",
        notes=notes,
    )


@router.post(
    "/{case_id}/analyse",
    response_model=AnalysisResponse,
    summary="Run the complete analysis pipeline for a case",
)
def analyse_case(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    refresh: bool = Query(
        False, description="Re-run every stage instead of reusing stored results."
    ),
    audit_limit: int | None = Query(
        None,
        ge=1,
        le=5000,
        description="Keep only the most recent N audit entries in the response.",
    ),
) -> AnalysisResponse:
    """Run every stage for every evidence item in the case and return it all.

    This is the one-call endpoint: metadata extraction, AI detection, provenance
    inspection, compression forensics, near-duplicate retrieval, fusion,
    propagation reconstruction and audit verification, in that order. The stages
    that ran are listed in ``stages``.

    ``verdict`` and ``signals`` describe the single item with the highest
    manipulation score (see ``verdict_selection``); ``verdicts`` holds every item.
    ``origin`` is the earliest known instance **in the indexed evidence corpus**,
    not an absolute real-world origin. ``processing_time_ms`` is measured, not
    estimated.

    Nothing here is a fixed demo result: a case with no evidence returns a null
    verdict and empty collections, and an unavailable stage is reported as
    unavailable rather than scored.
    """
    logger.info("ANALYSE_REQUEST_RECEIVED case_id=%s refresh=%s", case.id, refresh)
    try:
        logger.info("ANALYSE_CASE_LOADED case_id=%s", case.id)
        result = pipeline.analyse_case(
            db,
            case=case,
            settings=settings,
            actor="api",
            refresh=refresh,
            audit_limit=audit_limit,
        )
        logger.info("ANALYSE_RESPONSE_RETURNING case_id=%s ms=%s", case.id, result.get("processing_time_ms"))
        return AnalysisResponse(**result)
    except Exception as exc:
        logger.exception("ANALYSE_REQUEST_FAILED case_id=%s exc=%s", case.id, exc)
        raise


@router.get(
    "/{case_id}/audit",
    response_model=AuditTrailResponse,
    summary="Read the hash-chained audit trail for a case",
)
def get_case_audit(
    case: CaseDep,
    db: DbDep,
    limit: int | None = Query(
        None, ge=1, le=5000, description="Keep only the most recent N entries."
    ),
) -> AuditTrailResponse:
    """Every recorded action for this case, in chain order (oldest first).

    Each entry carries its ``previous_hash`` and ``row_hash`` so a reader can
    recompute the chain independently. Reading the trail does not modify it.
    """
    return AuditTrailResponse(**audit.trail(db, case.id, limit=limit))


@router.post(
    "/{case_id}/audit/verify",
    response_model=AuditVerifyResponse,
    summary="Verify the integrity of the audit chain",
)
def verify_case_audit(
    case: CaseDep,
    db: DbDep,
    record: bool = Query(
        True, description="Append the verification result to the chain."
    ),
) -> AuditVerifyResponse:
    """Recompute the audit chain and report the first row that fails.

    The chain is global, so the whole database is verified and the rows belonging
    to this case are additionally returned. ``valid: false`` names the failing
    ``seq`` and whether the row's content changed or its link was broken.

    The verification is itself an auditable act, so by default it is appended to
    the chain (as ``AUDIT_CHAIN_VERIFIED``) after the check has been computed.
    """
    result = audit.verify_chain(db, case.id)
    result["interpretation"] = audit.INTERPRETATION

    if record:
        audit.record(
            db,
            event=audit.EVENT_AUDIT_VERIFIED,
            case_id=case.id,
            actor="api",
            details={
                "valid": result["valid"],
                "total_rows": result["total_rows"],
                "case_rows": result["case_rows"],
                "first_invalid_seq": result["first_invalid_seq"],
                "verified_head_hash": result["head_hash"],
                "issue_count": len(result["issues"]),
                "algorithm": result["algorithm"],
            },
        )

    return AuditVerifyResponse(**result)


# --- Web discovery cache -------------------------------------------------------
#
# Bounded, TTL'd, success-only. The three properties the old bare dict lacked:
#
#   * bounded: at most ``web_discovery_cache_max_entries`` keys, oldest-entry
#     evicted first, so a demo cannot grow the process without limit.
#   * TTL: an entry older than ``web_discovery_cache_ttl_seconds`` is treated
#     as absent, so a result does not masquerade as permanent truth.
#   * success-only: ERROR and UNAVAILABLE results are never cached. The Vision
#     API is allowed to be down once without its failure becoming the answer
#     every later request replays; a repeated bad image_url cannot poison the
#     cache either, because its ERROR is stored nowhere.

_web_discovery_cache: "OrderedDict[str, tuple[WebDiscoveryResponse, float]]" = OrderedDict()
_web_discovery_cache_lock = threading.Lock()

#: Statuses that represent a completed, reportable discovery run. Everything
#: else -- ERROR, PUBLIC_WEB_DISCOVERY_UNAVAILABLE -- is a condition of the
#: deployment or of the upstream call, not a finding about the evidence.
_CACHEABLE_WEB_STATUSES = frozenset({"SUCCESS", "NO_RESULTS"})


def _web_cache_get(key: str, now: float) -> WebDiscoveryResponse | None:
    with _web_discovery_cache_lock:
        entry = _web_discovery_cache.get(key)
        if entry is None:
            return None
        res, stored_at = entry
        ttl = get_settings().web_discovery_cache_ttl_seconds
        if ttl > 0 and (now - stored_at) > ttl:
            del _web_discovery_cache[key]
            return None
        _web_discovery_cache.move_to_end(key)
        return res


def _web_cache_put(key: str, res: WebDiscoveryResponse, now: float) -> None:
    settings = get_settings()
    if res.status not in _CACHEABLE_WEB_STATUSES:
        return
    max_entries = settings.web_discovery_cache_max_entries
    with _web_discovery_cache_lock:
        if max_entries <= 0:
            return
        _web_discovery_cache[key] = (res, now)
        _web_discovery_cache.move_to_end(key)
        while len(_web_discovery_cache) > max_entries:
            _web_discovery_cache.popitem(last=False)


def clear_web_discovery_cache() -> None:
    """Empty the web discovery cache (tests, and after a settings change)."""
    with _web_discovery_cache_lock:
        _web_discovery_cache.clear()


@router.post(
    "/{case_id}/web-discovery",
    response_model=WebDiscoveryResponse,
    summary="Run Google Cloud Vision public web discovery on case evidence",
)
def run_case_web_discovery(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    evidence_id: str | None = Query(None, description="Specific evidence ID to inspect."),
    image_url: str | None = Query(None, description="Optional public image URL to inspect directly."),
    refresh: bool = Query(False, description="Re-run web discovery instead of returning cached result."),
) -> WebDiscoveryResponse:
    """Discover and verify public web occurrences using Google Cloud Vision Web Detection.

    If credentials or configuration are absent, gracefully returns
    `PUBLIC_WEB_DISCOVERY_UNAVAILABLE` without impacting internal provenance.
    """
    cache_key = f"{case.id}:{evidence_id or 'default'}:{image_url or 'default'}"
    if not refresh:
        cached = _web_cache_get(cache_key, time.time())
        if cached is not None:
            return cached

    service = WebProvenanceService(settings)

    # Resolve evidence
    target_evidence = None
    evidences = list(analysis_store.case_evidence(db, case.id))
    if evidence_id:
        target_evidence = next((e for e in evidences if e.id == evidence_id), None)
    else:
        target_evidence = next((e for e in evidences if e.media_type == "image"), None)
        if not target_evidence and evidences:
            target_evidence = evidences[0]

    abs_path = None
    if target_evidence:
        abs_path = storage.absolute_path(target_evidence.stored_path, settings)

    res = service.run_discovery(
        case_id=case.id,
        evidence_id=target_evidence.id if target_evidence else None,
        image_path=abs_path,
        image_url=image_url,
    )

    _web_cache_put(cache_key, res, time.time())

    # Record in audit trail
    if res.available and res.status in ("SUCCESS", "NO_RESULTS"):
        audit.record(
            db,
            event=audit.EVENT_PUBLIC_WEB_DISCOVERY,
            case_id=case.id,
            actor="api",
            details={
                "status": res.status,
                "evidence_id": target_evidence.id if target_evidence else None,
                "occurrences_count": len(res.occurrences),
                "verified_matches_count": res.summary.verified_matches_count,
            },
        )

    return res


@router.get(
    "/{case_id}/web-discovery",
    response_model=WebDiscoveryResponse,
    summary="Get public web discovery occurrences for a case",
)
def get_case_web_discovery(
    case: CaseDep,
    db: DbDep,
    settings: SettingsDep,
    evidence_id: str | None = Query(None, description="Specific evidence ID to inspect."),
) -> WebDiscoveryResponse:
    """Read the latest public web discovery occurrences for a case."""
    cache_key = f"{case.id}:{evidence_id or 'default'}:default"
    cached = _web_cache_get(cache_key, time.time())
    if cached is not None:
        return cached

    return run_case_web_discovery(
        case=case,
        db=db,
        settings=settings,
        evidence_id=evidence_id,
        refresh=False,
    )

