"""System and settings status: what this deployment is configured to do.

Read-only, and deliberately so. Fusion weights, thresholds, model paths and
detector entrypoints are deployment configuration -- they belong in the
environment or the ``.env`` file, under whatever change control the deployment
has, not behind an HTTP endpoint where a client could silently re-weight a
verdict between two analyses of the same file. So this router reports the
configuration and never mutates it.

The point of the endpoint is that every limit bounding a conclusion is visible in
one place: which detector is installed for which modality, whether C2PA
signatures can actually be validated, how much of the corpus is indexed, whether
a real PDF renderer is present, and when the audit chain was last verified.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import DbDep, SettingsDep
from app.models import (
    KIND_FUSION,
    AnalysisResult,
    AuditLog,
    Case,
    Evidence,
    Match,
    Report,
)
from app.schemas.api import SignalApplicabilityResponse, SystemStatusResponse
from app.services import (
    assessment as assessment_service,
    audit as audit_service,
    detector as detector_service,
    fusion as fusion_service,
    indexing,
    matching,
    metadata as metadata_service,
    pipeline as pipeline_service,
    propagation as propagation_service,
    provenance as provenance_service,
    report as report_service,
    web_provenance as web_provenance_service,
)
from app.utils.timeutil import iso, utcnow

logger = logging.getLogger("pramaan.api.system")

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get(
    "/signals",
    response_model=SignalApplicabilityResponse,
    summary="Forensic signal applicability per media type",
)
def get_signal_applicability(settings: SettingsDep) -> SignalApplicabilityResponse:
    """Which forensic signals apply to which media type, from fusion truth.

    The applicability sets are defined by the fusion engine (``SIGNAL_APPLICABILITY``
    in ``app/services/fusion.py``) -- the same map that decides which stages run
    and which signals a verdict carries -- so this endpoint can never disagree
    with an analysis. A signal absent from a media type's list is NOT APPLICABLE:
    the UI hides it (it is not a failed or zero row), and it never enters the
    coverage denominator or the fused score.
    """
    return SignalApplicabilityResponse(
        signal_names=dict(fusion_service.SIGNAL_NAMES),
        applicability={
            media_type: fusion_service.applicable_signals(media_type)
            for media_type in fusion_service.SIGNAL_APPLICABILITY
        },
        note=fusion_service.SIGNAL_APPLICABILITY_NOTE,
        declared_weights=settings.fusion_weights,
    )

READ_ONLY_NOTE = (
    "This endpoint reports configuration; it does not change it. Fusion weights, "
    "thresholds and model paths are set in the environment or .env so that a "
    "verdict cannot be re-weighted between two analyses of the same file from a "
    "client."
)

PROTOTYPE_NOTE = (
    "The fusion weights and verdict thresholds shown here are configurable "
    "demonstration defaults. They have not been validated against a forensic "
    "reference dataset and no error rate is known for them."
)


def _dir_state(path: Any) -> dict[str, Any]:
    """Presence and writability of one working directory."""
    return {
        "path": str(path),
        "exists": path.exists(),
        "writable": os.access(path, os.W_OK) if path.exists() else False,
    }


def _count(db: Any, model: Any, *conditions: Any) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(model).where(*conditions)
        ).scalar_one()
    )


@router.get(
    "/status",
    response_model=SystemStatusResponse,
    summary="Configuration, capabilities and real row counts for this deployment",
)
def get_system_status(db: DbDep, settings: SettingsDep) -> SystemStatusResponse:
    """Everything the settings and system pages need, from real state.

    Counts are queries against the database. Capability blocks come from the
    services themselves -- so "detector available" means a detector that actually
    loaded, not a path that happens to be configured.
    """
    detector_status = detector_service.status(settings)
    validator = provenance_service.validator_status()
    index_status = indexing.status(settings)
    renderer = report_service.renderer_status()
    web_discovery_available, web_discovery_reason = (
        web_provenance_service.is_web_discovery_available(settings)
    )

    hashable = _count(db, Evidence, Evidence.phash.is_not(None))
    pending_index = _count(
        db, Evidence, Evidence.phash.is_not(None), Evidence.indexed.is_(False)
    )

    audit_total = _count(db, AuditLog)
    last_verification = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.event == audit_service.EVENT_AUDIT_VERIFIED)
            .order_by(AuditLog.seq.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )

    db_path = settings.db_path
    counts = {
        "cases": _count(db, Case),
        "evidence": _count(db, Evidence),
        "analysis_results": _count(db, AnalysisResult),
        "fused_evidence": int(
            db.execute(
                select(func.count(func.distinct(AnalysisResult.evidence_id))).where(
                    AnalysisResult.kind == KIND_FUSION
                )
            ).scalar_one()
        ),
        "matches": _count(db, Match),
        "reports": _count(db, Report),
        "audit_entries": audit_total,
    }

    notes = [READ_ONLY_NOTE, PROTOTYPE_NOTE]
    if not detector_status.get("available"):
        notes.append(detector_service.UNAVAILABLE_EXPLANATION)
    if not validator["signature_validation_available"]:
        notes.append(provenance_service.CONTAINER_SCAN_ONLY_DETAIL)
    if not renderer.get("reportlab_available", False):
        notes.append(
            "ReportLab is not installed, so reports are rendered by PRAMAAN's "
            "built-in minimal PDF writer. The document is a valid PDF with the "
            "same content and plainer typography -- reporting is not disabled."
        )
    if last_verification is None and audit_total:
        notes.append(
            "The audit chain has never been verified on this deployment. That is "
            "the check not having been run, not a sign of tampering."
        )

    return SystemStatusResponse(
        app={
            "name": settings.app_name,
            "version": settings.app_version,
            "description": settings.app_description,
            "environment": settings.environment,
            "debug": settings.debug,
            "docs_enabled": settings.enable_docs,
            # Reported, not asserted. This was a hardcoded ``True`` sitting
            # beside a detail line promising "no outbound network calls at
            # runtime" -- a claim the deployment stops honouring the moment
            # public web discovery is credentialed and enabled, which is the one
            # stage that leaves the machine. A settings page that prints
            # "OFFLINE: ENFORCED" over a deployment that can reach Google Cloud
            # Vision is telling the operator something false about the evidence
            # handling, so the flag now follows the capability.
            "offline": not web_discovery_available,
            "offline_detail": (
                "PRAMAAN performs no outbound network calls at runtime; every "
                "stage runs against local files and the local database."
                if not web_discovery_available
                else (
                    "Every stage runs against local files and the local "
                    "database except public web discovery, which is enabled and "
                    "credentialed in this deployment and contacts Google Cloud "
                    "Vision when an operator runs it."
                )
            ),
            "cors_allow_origins": settings.cors_origins,
        },
        storage={
            "data_dir": _dir_state(settings.data_dir),
            "evidence_dir": _dir_state(settings.evidence_dir),
            "index_dir": _dir_state(settings.index_dir),
            "reports_dir": _dir_state(settings.reports_dir),
            "corpus_dir": _dir_state(settings.corpus_dir),
            "temp_dir": _dir_state(settings.temp_dir),
        },
        database={
            "engine": "sqlite",
            "path": str(db_path),
            "exists": db_path.is_file(),
            "size_bytes": db_path.stat().st_size if db_path.is_file() else None,
            "detail": (
                "One portable SQLite file holds the whole case file: cases, "
                "evidence records, analysis results, matches, reports and the "
                "audit chain."
            ),
        },
        counts=counts,
        capabilities={
            "detector": detector_status,
            "c2pa_validator": validator,
            "perceptual_index": {
                **index_status,
                "hashable_evidence_count": hashable,
                "pending_evidence_count": pending_index,
                "covers": (
                    "Perceptual hashing covers images in this build. Video and "
                    "audio are ingested and analysed, but are not perceptually "
                    "indexed, so they are outside near-duplicate retrieval rather "
                    "than absent from it."
                ),
            },
            "report_renderer": renderer,
            # Not a switch and not a setting: this is the pipeline's own
            # declaration of a guarantee it enforces in code, reported here so
            # the settings page describes real behaviour instead of asserting a
            # control of its own. The constants live beside the check in
            # ``app/services/pipeline.py``; if the check is ever removed they go
            # with it and this block stops claiming it.
            "integrity_verification": {
                "algorithm": pipeline_service.INTEGRITY_ALGORITHM,
                "on_mismatch": pipeline_service.INTEGRITY_ON_MISMATCH,
                "scope": (
                    "every analysis stage that reads an evidence file: metadata, "
                    "AI detection, C2PA provenance and compression forensics"
                ),
                "detail": pipeline_service.INTEGRITY_DETAIL,
            },
            # The one stage that would leave the machine. Reported here for the
            # same reason as every other capability: the settings page must be
            # able to say whether public web discovery can actually run in this
            # deployment rather than describing it as permanently "optional".
            # `reason` is the operator-facing explanation of why it cannot --
            # a disabled flag and absent credentials are different problems with
            # different fixes, and it names the env var, never its value.
            "web_discovery": {
                "available": web_discovery_available,
                "enabled": settings.pramaan_web_discovery_enabled,
                "reason": web_discovery_reason,
                "provider": "Google Cloud Vision Web Detection",
                "detail": (
                    "The only stage that contacts an external service, and it "
                    "runs solely when an operator asks for it. Everything else "
                    "in PRAMAAN executes against local files and the local "
                    "database."
                ),
            },
            "metadata_extractor": {
                "extractor": metadata_service.EXTRACTOR,
                "interpretation": metadata_service.INTERPRETATION_NOTE,
            },
        },
        detector_contract={
            "interface_version": detector_service.INTERFACE_VERSION,
            "entrypoint": (
                "get_detector(settings).analyse(path, media_type='image|video|audio')"
            ),
            "modalities": list(detector_service.MODALITIES),
            "score_semantics": detector_service.SCORE_SEMANTICS,
            "result_fields": [
                "media_type",
                "label",
                "manipulation_score",
                "confidence",
                "abstained",
                "model",
                "model_version",
                "weights_hash",
                "latency_ms",
                "explanation",
                "heatmap_available",
                "regions",
                "timestamps",
            ],
            "sockets": {
                "configuration": {
                    name: {
                        "model_path_env": detector_service.ENV_HINTS[name]["model"],
                        "entrypoint_env": detector_service.ENV_HINTS[name][
                            "entrypoint"
                        ],
                        "model_path": getattr(settings, f"{name}_model_path", "")
                        or None,
                        "entrypoint": getattr(
                            settings, f"{name}_detector_entrypoint", ""
                        )
                        or None,
                    }
                    for name in detector_service.MODALITIES
                },
                "in_process": (
                    "detector.register_inference(modality, fn, model_name=..., "
                    "model_version=...)"
                ),
            },
            "abstention": detector_service.UNAVAILABLE_EXPLANATION,
            "guarantee": (
                "An unavailable model abstains: manipulation_score is null, "
                "abstained is true, and the ai_detection signal is excluded from "
                "fusion. No score is ever substituted for a missing one."
            ),
        },
        # The decision policy, published in full. This is the block a client
        # reads instead of hardcoding its own copy of the vocabulary, and the
        # block an examiner reads to see which checks were ever *entitled* to
        # decide -- the difference between a signal that scored low and a signal
        # that was never allowed to vote is not visible in a verdict alone.
        assessment={
            "policy_id": assessment_service.POLICY_ID,
            "policy_version": assessment_service.POLICY_VERSION,
            "policy_note": assessment_service.POLICY_NOTE,
            # Which checks can determine a state, and which are reported as
            # observations. Named explicitly because the reason a compression
            # anomaly cannot produce a finding is that it is not eligible, not
            # that it scored badly.
            "eligible_checks": list(assessment_service.ELIGIBLE_CHECKS),
            "eligible_checks_note": (
                "Only these checks can determine an assessment state. Every "
                "other signal is a forensic observation: reported for examiner "
                "review, excluded from the state by construction rather than by "
                "weight. Missing EXIF, an absent C2PA manifest, compression "
                "characteristics, perceptual distances and file hashes are not "
                "synthetic-media evidence."
            ),
            "scopes": {
                media_type: {
                    "scope": scope,
                    "detail": assessment_service.SCOPE_NOTES[scope],
                }
                for media_type, scope in assessment_service.SCOPE_BY_MEDIA.items()
            },
            "scopes_note": (
                "Each modality's detector answers a different question against a "
                "different training distribution, so each has its own scope and "
                "no score from one modality is combined with a score from "
                "another."
            ),
            "states": {
                state: assessment_service.STATE_NOTES[state]
                for state in assessment_service.ASSESSMENT_STATES
            },
            "conclusive_states": list(assessment_service.CONCLUSIVE_STATES),
            "execution_statuses": dict(assessment_service.EXECUTION_NOTES),
            "state_vs_execution_note": (
                "An examination can complete cleanly and still reach "
                "NOT_ASSESSED. 'conclusive' reports whether a finding was "
                "reached; 'execution_status' reports whether processing "
                "finished. Neither substitutes for the other."
            ),
            "check_statuses": dict(assessment_service.CHECK_STATUS_NOTES),
            "roles": dict(assessment_service.ROLE_NOTES),
            "reason_codes": dict(assessment_service.REASON_NOTES),
            "score_semantics": assessment_service.SCORE_SEMANTICS,
            "thresholds": {
                "manipulated_at_or_above": settings.verdict_manipulated_threshold,
                "authentic_at_or_below": settings.verdict_authentic_threshold,
                "minimum_eligible_coverage": settings.fusion_min_effective_weight,
            },
            "conflict_policy": (
                "None. Eligible checks pointing in opposite directions yield "
                "INCONCLUSIVE with CONFLICTING_RESULTS; the scores are not "
                "averaged into a middle number, because no conflict-resolution "
                "policy has been validated for this system."
            ),
            "examiner_conclusion": None,
            "examiner_conclusion_note": (
                assessment_service.EXAMINER_CONCLUSION_NOTE
            ),
            "limitations": list(assessment_service.BASE_LIMITATIONS),
            "legacy_projection": {
                "mapping": dict(assessment_service.LEGACY_VERDICT_BY_STATE),
                "detail": (
                    "The top-level verdict/manipulation_score/confidence fields "
                    "are a backward-compatible projection of the assessment, "
                    "never a second decision. The projection is lossy: both "
                    "NOT_ASSESSED and INCONCLUSIVE become INSUFFICIENT_EVIDENCE, "
                    "so a client that must tell them apart reads "
                    "assessment.state."
                ),
            },
            "authority": (
                "The backend is the single source of this assessment. Neither "
                "the frontend nor the PDF report re-derives a state, a score or "
                "a reason from raw signals."
            ),
        },
        fusion={
            "method": fusion_service.FUSION_METHOD,
            "version": fusion_service.FUSION_VERSION,
            "declared_weights": settings.fusion_weights,
            "signal_applicability": {
                media_type: [s["signal_id"] for s in fusion_service.applicable_signals(media_type)]
                for media_type in fusion_service.SIGNAL_APPLICABILITY
            },
            "signal_applicability_note": fusion_service.SIGNAL_APPLICABILITY_NOTE,
            "thresholds": {
                "manipulated_at_or_above": settings.verdict_manipulated_threshold,
                "authentic_at_or_below": settings.verdict_authentic_threshold,
                "min_effective_weight": settings.fusion_min_effective_weight,
            },
            "min_effective_weight_detail": (
                "Below this share of the ELIGIBLE checks' declared weight, the "
                "assessment is NOT_ASSESSED rather than a finding reached from "
                "too little of the evidence. Applied to eligible weight only: an "
                "eligible check that could not run lowers coverage instead of "
                "shrinking the denominator behind it."
            ),
            "decision_authority": (
                "This module measures and projects; it does not decide. The "
                "assessment state comes from the policy reported under "
                "'assessment', which fusion calls exactly once per item."
            ),
            "primary_signals": list(fusion_service.PRIMARY_SIGNALS),
            "score_semantics": fusion_service.SCORE_SEMANTICS,
            "caveat": fusion_service.CAVEAT,
        },
        ingestion={
            "max_upload_bytes": settings.max_upload_bytes,
            "allowed_extensions": {
                "image": sorted(settings.image_extensions),
                "video": sorted(settings.video_extensions),
                "audio": sorted(settings.audio_extensions),
            },
            "identification": (
                "The media type is decided by sniffing the file's own bytes; a "
                "declared MIME type never overrides it."
            ),
            "integrity": (
                "Every ingested file is hashed with SHA-256 at ingestion and "
                "stored unmodified, so the digest continues to describe the bytes "
                "on disk."
            ),
            "hash_bits": settings.hash_bits,
            "retrieval": {
                "top_k": settings.retrieval_top_k,
                "near_duplicate_max_distance": settings.near_duplicate_max_distance,
                "strong_duplicate_max_distance": settings.strong_duplicate_max_distance,
                "method": matching.METHOD,
                "interpretation": matching.INTERPRETATION,
            },
        },
        audit={
            "total_rows": audit_total,
            "head_hash": audit_service.head_hash(db),
            "genesis_hash": audit_service.GENESIS_HASH,
            "algorithm": audit_service.ALGORITHM,
            "interpretation": audit_service.INTERPRETATION,
            "verify_url": "/api/audit/verify",
            "trail_url": "/api/audit",
            "last_verified_at": (
                last_verification.timestamp if last_verification else None
            ),
            "last_verification": (
                None
                if last_verification is None
                else {
                    "audit_id": last_verification.audit_id,
                    "timestamp": last_verification.timestamp,
                    "actor": last_verification.actor,
                    **(last_verification.details or {}),
                }
            ),
            "last_verification_detail": (
                "The result of the last verification that was actually run. It is "
                "not re-verified here: reading a status page must not be able to "
                "pass for an integrity check."
            ),
        },
        vocabularies={
            # Legacy tokens, kept because stored rows, dashboard counters and
            # alert rules speak them. Each entry says what the token projects
            # from, so nothing here reads as a claim the assessment does not
            # make. New consumers read `assessment.states` above.
            "verdicts": {
                fusion_service.VERDICT_AUTHENTIC: (
                    "Legacy projection of NO_INDICATORS_DETECTED: an eligible "
                    "check reached its negative threshold. Despite the token's "
                    "name this is not a certification of authenticity."
                ),
                fusion_service.VERDICT_MANIPULATED: (
                    "Legacy projection of INDICATORS_DETECTED: an eligible check "
                    "reached its positive threshold. Indicators, not a "
                    "determination that the media is fake."
                ),
                fusion_service.VERDICT_INSUFFICIENT: (
                    "Legacy projection of BOTH INCONCLUSIVE and NOT_ASSESSED, "
                    "which it cannot distinguish. A statement about the "
                    "analysis, never about the media. Read assessment.state to "
                    "tell 'ran and could not decide' from 'was not assessed'."
                ),
            },
            "verdicts_note": (
                "Superseded by assessment.states. These three tokens are a lossy "
                "projection retained for backward compatibility."
            ),
            "signal_statuses": {
                fusion_service.SIGNAL_OK: (
                    "Produced a score. Contributes to the assessment only if the "
                    "signal is also eligible; a descriptive observation is OK and "
                    "measured, and still excluded from the state."
                ),
                fusion_service.SIGNAL_INCONCLUSIVE: (
                    "Ran but could not decide; no measurement. Not zero."
                ),
                fusion_service.SIGNAL_UNAVAILABLE: (
                    "Could not run at all in this deployment; excluded. Not zero."
                ),
                fusion_service.SIGNAL_ERROR: "Failed; excluded. Not zero.",
                fusion_service.SIGNAL_UNSUPPORTED: (
                    "Does not apply to this media type; excluded. Not applicable "
                    "is not failed."
                ),
            },
            "assessment_states": {
                state: assessment_service.STATE_NOTES[state]
                for state in assessment_service.ASSESSMENT_STATES
            },
            "assessment_reason_codes": dict(assessment_service.REASON_NOTES),
            "check_statuses": dict(assessment_service.CHECK_STATUS_NOTES),
            "provenance_states": {
                provenance_service.STATE_VERIFIED: (
                    "A manifest is present and its signature validated."
                ),
                provenance_service.STATE_INVALID: (
                    "A manifest is present and its signature failed validation."
                ),
                provenance_service.STATE_UNVERIFIED: (
                    "A manifest container is present but no signature validation "
                    "was performed; treat its contents as a self-assertion."
                ),
                provenance_service.STATE_ABSENT: (
                    "No manifest was found. This is normal for almost all media "
                    "and is not an indicator of manipulation."
                ),
            },
            "match_bands": {
                matching.BAND_STRONG: (
                    "Within the strong-candidate Hamming distance. A candidate for "
                    "comparison, not proof."
                ),
                matching.BAND_POSSIBLE: (
                    "Within the near-duplicate Hamming distance. A weaker "
                    "candidate for comparison, not proof."
                ),
            },
            "origin_label": propagation_service.ORIGIN_LABEL,
            "origin_caveats": propagation_service.CAVEATS,
            "audit_events": sorted(audit_service.KNOWN_EVENTS),
            "analysis_stages": [
                "metadata",
                "detector",
                "provenance",
                "forensics",
                "fusion",
                "propagation",
            ],
            "report": {
                "version": report_service.REPORT_VERSION,
                "document_status": report_service.DOCUMENT_STATUS,
                "limitations": report_service.LIMITATIONS,
            },
        },
        generated_at=iso(utcnow()),
        notes=notes,
    )
