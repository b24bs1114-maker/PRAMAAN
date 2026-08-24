"""Response schemas for the PRAMAAN API.

These models are the stable contract the frontend codes against. Deeply nested
analysis payloads (metadata, propagation, full analysis) are typed as open
objects: their internal structure is documented in the README and the OpenAPI
examples, and they must stay additive.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="allow")


# --------------------------------------------------------------------------- #
# Cases and evidence
# --------------------------------------------------------------------------- #
class CaseOut(ApiModel):
    case_id: str
    case_number: str
    title: str | None = None
    description: str | None = None
    examiner: str | None = None
    status: str
    priority: str = "medium"
    complaint_reference: str | None = None
    latest_verdict: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    evidence_count: int | None = None


class EvidenceOut(ApiModel):
    evidence_id: str
    case_id: str | None = None
    role: str
    filename: str
    media_type: str
    mime_type: str
    size_bytes: int
    sha256: str
    ingested_at: str | None = None
    width: int | None = None
    height: int | None = None
    format: str | None = None
    phash: str | None = None
    dhash: str | None = None
    ahash: str | None = None
    source_id: str | None = None
    parent_id: str | None = None
    generation: int | None = None
    platform: str | None = None
    observed_at: str | None = None
    transformation: str | None = None
    is_synthetic: bool = False
    indexed: bool = False


class UploadResponse(ApiModel):
    case: CaseOut
    evidence: EvidenceOut
    duplicate: bool = Field(
        description="True when identical bytes were already ingested for this case."
    )
    warnings: list[str] = []


class CaseListResponse(ApiModel):
    count: int
    cases: list[CaseOut]


class EvidenceListResponse(ApiModel):
    case_id: str
    count: int
    evidence: list[EvidenceOut]


class EvidenceListGlobalResponse(ApiModel):
    total: int
    evidence: list[EvidenceOut]


class DashboardSummaryResponse(ApiModel):
    """Aggregates over the case file, plus the per-capability system state.

    ``avg_processing_time_ms`` is ``null`` when no analysis run has been timed --
    an unmeasured pipeline is unknown, not instantaneous. Counts are ``0`` only
    when zero rows match.
    """

    active_investigations_count: int
    evidence_items_count: int
    flagged_media_count: int
    pending_review_count: int
    unanalysed_case_count: int = 0
    high_priority_count: int = 0
    evidence_breakdown: dict[str, int] = Field(
        default_factory=lambda: {"video": 0, "image": 0, "audio": 0}
    )
    analysed_evidence_count: int = 0
    verdict_breakdown: dict[str, int] = {}
    avg_processing_time_ms: float | None = None
    avg_processing_time_basis: str | None = None
    timed_analysis_runs: int = 0
    recent_investigations: list[CaseOut] = []
    recent_evidence: list[EvidenceOut] = []
    flagged_media: list[EvidenceOut] = []
    flagged_media_truncated: bool = False
    current_case_summary: CaseOut | None = None
    system_status: str = "online"
    system_status_details: dict[str, str] = Field(
        default_factory=lambda: {
            "ai_detectors": "UNAVAILABLE",
            "c2pa_validator": "CONTAINER-SCAN ONLY",
            "propagate_index": "EMPTY",
        }
    )
    components: dict[str, Any] = {}
    metric_definitions: dict[str, str] = {}
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #
class MetadataResponse(ApiModel):
    case_id: str
    count: int
    items: list[dict[str, Any]]


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
class MatchOut(ApiModel):
    evidence_id: str
    distance: int
    similarity: float
    phash_distance: int | None = None
    dhash_distance: int | None = None
    source_id: str | None = None
    parent_id: str | None = None
    generation: int | None = None
    timestamp: str | None = None
    platform: str | None = None
    filename: str | None = None
    sha256: str | None = None
    is_synthetic: bool = False
    confidence_band: str
    rank: int


class MatchesResponse(ApiModel):
    case_id: str
    interpretation: str
    queries: list[dict[str, Any]]
    total_candidates: int
    thresholds: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Propagation
# --------------------------------------------------------------------------- #
class OriginOut(ApiModel):
    """The earliest instance visible in the local index -- never an absolute origin."""

    label: str
    evidence_id: str
    filename: str | None = None
    timestamp: str | None = None
    timestamp_source: str | None = None
    platform: str | None = None
    generation: int | None = None
    source_id: str | None = None
    is_synthetic: bool = False
    is_absolute_origin: bool = False
    caveat: str


class PropagationResponse(ApiModel):
    case_id: str
    method: str
    interpretation: str
    origin: OriginOut | None = None
    timeline: list[dict[str, Any]] = []
    graph: dict[str, Any] = {}
    instance_count: int = 0
    matched_candidate_count: int = 0
    platforms: list[str] = []
    generations: list[int] = []
    truncated: bool = False
    notes: list[str] = []
    caveats: list[str] = []


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
class IndexStatusResponse(ApiModel):
    indexed_count: int
    last_updated: str | None = None
    index_version: int
    backend: str
    exact_search: bool
    hash_bits: int
    dimensions: int
    persisted: bool
    index_path: str
    faiss_available: bool
    notes: str | None = None


class IndexOperationResponse(ApiModel):
    status: str
    indexed_count: int
    index_version: int
    added: int = 0
    skipped: int = 0
    detail: str | None = None


# --------------------------------------------------------------------------- #
# Detector, signals, verdict
# --------------------------------------------------------------------------- #
class DetectorResponse(ApiModel):
    score: float | None
    model: str
    model_version: str
    status: str
    detail: str | None = None


class DetectorStatusResponse(ApiModel):
    adapter: str
    available: bool
    reason: str | None = None
    configured_backend: str
    image_model_path: str | None = None
    video_model_path: str | None = None
    audio_model_path: str | None = None
    modalities: dict[str, Any] = {}
    notes: str | None = None


class DetectorResultResponse(ApiModel):
    media_type: str = "image"
    label: str = "INSUFFICIENT_EVIDENCE"
    manipulation_score: float | None = None
    confidence: float | None = None
    abstained: bool = True
    model: str = "none"
    model_version: str = "0"
    weights_hash: str = ""
    latency_ms: float | None = None
    explanation: str = ""
    heatmap_available: bool = False
    regions: list[dict[str, Any]] = []
    timestamps: list[dict[str, Any]] = []


class DetectionResponse(ApiModel):
    case_id: str
    count: int
    items: list[dict[str, Any]] = []
    detector: dict[str, Any] = {}
    interpretation: str


class SignalOut(ApiModel):
    signal_id: str
    name: str
    score: float | None
    weight: float
    effective_weight: float
    contribution: float | None
    status: str
    explanation: str
    included: bool = False
    evidence_basis: dict[str, Any] | None = None


class VerdictOut(ApiModel):
    """One fused verdict for one evidence item, with the full signal breakdown."""

    evidence_id: str
    filename: str | None = None
    sha256: str | None = None
    verdict: str
    manipulation_score: float | None
    confidence: str
    method: str
    fusion_version: str
    signals: list[SignalOut] = []
    signals_available: int
    signals_total: int
    declared_weights: dict[str, float] = {}
    signal_coverage: float
    primary_signal_available: bool = False
    thresholds: dict[str, float] = {}
    excluded_signals: list[dict[str, Any]] = []
    arithmetic: str | None = None
    rationale: str
    score_semantics: str
    caveat: str
    fused_at: str | None = None
    cached: bool = False


class VerdictResponse(ApiModel):
    case_id: str
    count: int
    items: list[VerdictOut] = []
    method: str
    interpretation: str
    caveat: str


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class AuditTrailResponse(ApiModel):
    case_id: str | None = None
    count: int
    total_rows: int
    truncated: bool = False
    events: list[dict[str, Any]] = []
    head_hash: str
    genesis_hash: str
    algorithm: str
    interpretation: str


class AuditVerifyResponse(ApiModel):
    valid: bool
    scope: str
    case_id: str | None = None
    total_rows: int
    case_rows: int
    first_invalid_seq: int | None = None
    head_hash: str
    genesis_hash: str
    algorithm: str
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    interpretation: str | None = None


# --------------------------------------------------------------------------- #
# Full analysis
# --------------------------------------------------------------------------- #
class AnalysisResponse(ApiModel):
    """The consolidated result of the full pipeline for one case.

    The nine documented keys come first; everything after them is the context
    needed to interpret them (per-item verdicts, propagation detail, detector and
    index status, warnings). All of it is additive.
    """

    case: CaseOut
    evidence: list[EvidenceOut] = []
    verdict: VerdictOut | None = None
    signals: list[SignalOut] = []
    matches: dict[str, Any] = {}
    origin: OriginOut | None = None
    timeline: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}
    processing_time_ms: float

    verdicts: list[VerdictOut] = []
    verdict_selection: str
    verdict_evidence_id: str | None = None
    propagation: dict[str, Any] = {}
    detector: dict[str, Any] = {}
    index: dict[str, Any] = {}
    stages: list[str] = []
    analysis_version: str
    fusion_method: str
    score_semantics: str
    caveat: str
    warnings: list[str] = []
    analysed_at: str | None = None
    refreshed: bool = False


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
class ReportResponse(ApiModel):
    case_id: str
    report_id: str
    filename: str
    path: str
    size_bytes: int
    sha256: str
    generated_at: str
    generator: str
    renderer: str
    pages: int | None = None
    audit_head_hash: str
    audit_chain_valid: bool
    document_status: str
    renderer_status: dict[str, Any] = {}
    download_url: str


class ReportListResponse(ApiModel):
    case_id: str
    count: int
    reports: list[dict[str, Any]] = []


class ReportLibraryResponse(ApiModel):
    """Reports across every case, for the report library view."""

    total: int
    count: int
    offset: int = 0
    truncated: bool = False
    reports: list[dict[str, Any]] = []
    renderer: dict[str, Any] = {}
    document_status: str
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# One evidence item
# --------------------------------------------------------------------------- #
class EvidenceFileOut(ApiModel):
    """Where the stored bytes are and whether they still hash as recorded.

    ``integrity_verified`` is ``null`` until the digest is actually recomputed --
    unverified is not the same claim as verified, and neither is it a failure.
    """

    available: bool
    url: str | None = None
    mime_type: str
    size_bytes: int
    stored_sha256: str
    recomputed_sha256: str | None = None
    integrity_verified: bool | None = None
    verified_at: str | None = None
    detail: str


class EvidenceStageOut(ApiModel):
    """A stored analysis stage. Absence is represented by ``null``, not by this."""

    kind: str
    status: str
    score: float | None = None
    verdict: str | None = None
    model: str | None = None
    model_version: str | None = None
    created_at: str | None = None


class EvidenceDetailResponse(ApiModel):
    """Everything known about one evidence item, without running anything.

    ``stages`` maps each analysis kind to its stored row or to ``null`` when that
    stage has never run for this item. A ``null`` stage is a gap in the record, not
    a finding about the file.
    """

    evidence: EvidenceOut
    case: CaseOut | None = None
    file: EvidenceFileOut
    stages: dict[str, EvidenceStageOut | None] = {}
    stages_stored: list[str] = []
    stages_not_run: list[str] = []
    verdict: VerdictOut | None = None
    near_duplicate_candidate_count: int = 0
    near_duplicate_interpretation: str | None = None
    report_count: int = 0
    analysis_url: str
    file_url: str
    run_analysis_url: str | None = None
    notes: list[str] = []


class EvidenceAnalysisResponse(ApiModel):
    """Stored analysis payloads for one evidence item. Reads only -- never runs.

    A stage listed in ``missing_stages`` has no stored result; the client is told
    which endpoint runs the pipeline rather than being handed a placeholder.
    """

    evidence: EvidenceOut
    case_id: str | None = None
    source: str = "stored"
    stored_stages: list[str] = []
    missing_stages: list[str] = []
    stages: dict[str, Any] = {}
    verdict: VerdictOut | None = None
    signals: list[SignalOut] = []
    detector_capability: dict[str, Any] = {}
    run_analysis_url: str | None = None
    score_semantics: str
    interpretation: str
    caveat: str
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
class ProvenanceResponse(ApiModel):
    """C2PA manifest inspection for every evidence item in a case."""

    case_id: str
    count: int
    items: list[dict[str, Any]] = []
    validator: dict[str, Any] = {}
    state_definitions: dict[str, str] = {}
    interpretation: str
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# Stored (read-only) views of results that a POST produces
# --------------------------------------------------------------------------- #
class StoredVerdictResponse(VerdictResponse):
    """Fused verdicts already in the case file. Fusion is not re-run.

    Evidence with no stored verdict appears in ``pending_evidence`` -- it is never
    given a placeholder verdict to fill the list out.
    """

    source: str = "stored"
    evidence_count: int = 0
    analysed_count: int = 0
    pending_evidence: list[dict[str, Any]] = []
    run_verdict_url: str | None = None
    notes: list[str] = []


class StoredMatchesResponse(MatchesResponse):
    """Near-duplicate candidates already stored. Retrieval is not re-run.

    ``searched`` comes from the audit trail, so "no candidates" can be told apart
    from "never searched".
    """

    source: str = "stored"
    searched: bool = False
    searched_at: str | None = None
    run_matches_url: str | None = None
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# Alerts (derived at read time -- no alert table)
# --------------------------------------------------------------------------- #
class AlertOut(ApiModel):
    """One thing that warrants an examiner's attention, derived from stored rows.

    Every alert points at the row it came from (``source``) and carries the figures
    it was derived from (``basis``), so none of it has to be taken on trust.
    """

    alert_id: str
    severity: str
    category: str
    title: str
    detail: str
    case_id: str | None = None
    case_number: str | None = None
    evidence_id: str | None = None
    filename: str | None = None
    observed_at: str | None = None
    source: str
    basis: dict[str, Any] = {}
    action: str | None = None


class AlertsResponse(ApiModel):
    total: int
    count: int
    offset: int = 0
    truncated: bool = False
    alerts: list[AlertOut] = []
    severity_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    severities: list[str] = []
    categories: list[str] = []
    severity_definitions: dict[str, str] = {}
    category_definitions: dict[str, str] = {}
    filters: dict[str, Any] = {}
    generated_at: str | None = None
    notes: list[str] = []


# --------------------------------------------------------------------------- #
# Global audit trail and system status
# --------------------------------------------------------------------------- #
class AuditTrailGlobalResponse(AuditTrailResponse):
    """The chain across every case, with the filters that produced this page."""

    scope: str = "all_cases"
    offset: int = 0
    returned_from: int | None = None
    filters: dict[str, Any] = {}
    known_events: list[str] = []


class SystemStatusResponse(ApiModel):
    """Configuration and capability state of this deployment, read-only."""

    app: dict[str, Any] = {}
    storage: dict[str, Any] = {}
    database: dict[str, Any] = {}
    counts: dict[str, int] = {}
    capabilities: dict[str, Any] = {}
    detector_contract: dict[str, Any] = {}
    fusion: dict[str, Any] = {}
    ingestion: dict[str, Any] = {}
    audit: dict[str, Any] = {}
    vocabularies: dict[str, Any] = {}
    generated_at: str | None = None
    notes: list[str] = []


__all__ = [
    "AlertOut",
    "AlertsResponse",
    "AnalysisResponse",
    "ApiModel",
    "AuditTrailGlobalResponse",
    "AuditTrailResponse",
    "AuditVerifyResponse",
    "CaseListResponse",
    "CaseOut",
    "DashboardSummaryResponse",
    "DetectionResponse",
    "DetectorResponse",
    "DetectorResultResponse",
    "DetectorStatusResponse",
    "EvidenceAnalysisResponse",
    "EvidenceDetailResponse",
    "EvidenceFileOut",
    "EvidenceListGlobalResponse",
    "EvidenceListResponse",
    "EvidenceOut",
    "EvidenceStageOut",
    "IndexOperationResponse",
    "IndexStatusResponse",
    "MatchOut",
    "MatchesResponse",
    "MetadataResponse",
    "OriginOut",
    "PropagationResponse",
    "ProvenanceResponse",
    "ReportLibraryResponse",
    "ReportListResponse",
    "ReportResponse",
    "SignalOut",
    "StoredMatchesResponse",
    "StoredVerdictResponse",
    "SystemStatusResponse",
    "UploadResponse",
    "VerdictOut",
    "VerdictResponse",
]
