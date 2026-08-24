/**
 * Backend response types.
 *
 * These mirror the FastAPI/Pydantic schemas in backend/app/schemas/api.py
 * exactly -- field names are the backend's, not renamed on the way in. Any
 * renaming happens in components, never in the transport layer, so that a
 * backend contract change surfaces here as a type error rather than as
 * silently-undefined data in the UI.
 *
 * Nullable fields are typed `| null` deliberately: the backend distinguishes
 * "not measured" (null) from "measured as zero" (0), and that distinction is
 * the core forensic guarantee. Never coalesce a null score to 0.
 */

// --- Signals -----------------------------------------------------------------

/**
 * Status of one forensic signal, as emitted by the backend's fusion engine.
 *
 * Only OK contributes to the fused score. The other four are excluded from both
 * the numerator and the denominator -- none of them is a finding of any kind.
 */
export type SignalStatus =
  | 'OK'
  | 'INCONCLUSIVE'
  | 'UNAVAILABLE'
  | 'ERROR'
  | 'UNSUPPORTED_MEDIA'
  | string // backend may add states; render unknown ones verbatim rather than crashing

export interface Signal {
  signal_id: string
  name: string
  /** null when the signal could not be measured. Never treat as 0. */
  score: number | null
  weight: number
  effective_weight: number
  contribution: number | null
  status: SignalStatus
  explanation: string
  included: boolean
  evidence_basis: Record<string, unknown>
}

export interface ExcludedSignal {
  signal_id: string
  status: SignalStatus
  reason: string
}

// --- Verdict -----------------------------------------------------------------

/** The backend's authoritative verdict enum. The frontend never computes this. */
export type VerdictBand =
  | 'AUTHENTIC'
  | 'MANIPULATED'
  | 'INSUFFICIENT_EVIDENCE'
  | string

export interface Verdict {
  evidence_id: string
  filename: string
  sha256: string
  verdict: VerdictBand
  /** 0..1, higher = more evidence consistent with manipulation. */
  manipulation_score: number | null
  confidence: string
  method: string
  fusion_version: string
  signals: Signal[]
  signals_available: number
  signals_total: number
  declared_weights: Record<string, number>
  signal_coverage: number
  primary_signal_available: boolean
  thresholds: {
    manipulated_at_or_above?: number
    authentic_at_or_below?: number
    minimum_signal_coverage?: number
    [k: string]: number | undefined
  }
  excluded_signals: ExcludedSignal[]
  /** Human-readable arithmetic, e.g. "0.1500x0.6667 + 0.4833x0.3333 = 0.2611". */
  arithmetic: string
  rationale: string
  score_semantics: string
  caveat: string
  fused_at: string | null
  cached: boolean
  media_type: string
  /** Always 1.0 -- the declared weights before renormalisation. */
  declared_weight_total: number
  /** Sum of declared weights that actually contributed. */
  available_weight: number
  /** Signals that can establish authenticity on their own; drives gate G-2. */
  primary_signals: string[]
}

// --- Case and evidence -------------------------------------------------------

export interface CaseRecord {
  case_id: string
  case_number: string
  title: string | null
  description: string | null
  examiner: string | null
  status: string
  created_at: string
  updated_at: string
  evidence_count: number
  priority?: string
  latest_verdict?: string
  complaint_reference?: string
}

export interface Evidence {
  evidence_id: string
  case_id: string
  role: string
  filename: string
  media_type: string
  mime_type: string
  size_bytes: number
  sha256: string
  ingested_at: string
  width: number | null
  height: number | null
  format: string | null
  phash: string | null
  dhash: string | null
  ahash: string | null
  source_id: string | null
  parent_id: string | null
  generation: number | null
  platform: string | null
  observed_at: string | null
  transformation: string | null
  is_synthetic: boolean
  indexed: boolean
}

export interface UploadResponse {
  case: CaseRecord
  evidence: Evidence
  /** true when identical bytes were already ingested (HTTP 200 rather than 201). */
  duplicate: boolean
  warnings: string[]
}

// --- Matches -----------------------------------------------------------------

export interface MatchCandidate {
  evidence_id: string
  distance: number
  similarity: number
  phash_distance: number
  dhash_distance: number | null
  source_id: string | null
  parent_id: string | null
  generation: number | null
  timestamp: string | null
  observed_at: string | null
  ingested_at: string | null
  platform: string | null
  transformation: string | null
  filename: string
  sha256: string
  role: string
  is_synthetic: boolean
  confidence_band: string
  rank: number
}

export interface MatchQuery {
  evidence_id: string
  filename: string
  media_type: string
  phash: string | null
  dhash: string | null
  top_k: number
  max_distance: number
  method: string
  algorithm: string
  index_backend: string
  indexed_count: number
  index_version: number
  candidates: MatchCandidate[]
  strong_candidates: number
  notes: string[]
}

export interface MatchesResponse {
  case_id: string
  interpretation: string
  queries: MatchQuery[]
  total_candidates: number
  thresholds: {
    strong_candidate_max_distance: number
    near_duplicate_max_distance: number
    hash_bits: number
    basis: string
  }
}

// --- Propagation -------------------------------------------------------------

export interface Origin {
  /** Backend wording: "earliest known instance in the indexed evidence corpus". */
  label: string
  evidence_id: string
  filename: string
  timestamp: string | null
  timestamp_source: string | null
  platform: string | null
  generation: number | null
  source_id: string | null
  is_synthetic: boolean
  /**
   * False whenever earlier copies could exist outside the corpus. The UI must
   * not present this instance as the real-world original when this is false.
   */
  is_absolute_origin: boolean
  caveat: string
  role: string
  discovered_by: string | null
  distance_to_case_evidence: number | null
}

export interface PropagationNode {
  evidence_id: string
  filename: string
  role: string
  is_case_evidence: boolean
  platform: string | null
  generation: number | null
  source_id: string | null
  parent_id: string | null
  transformation: string | null
  sha256: string
  is_synthetic: boolean
  timestamp: string | null
  timestamp_source: string | null
  discovered_by: string | null
  distance_to_case_evidence: number | null
  similarity_to_case_evidence: number | null
}

export interface PropagationEdge {
  source: string
  target: string
  relation: string
  basis: string
  transformation: string | null
  /** False when the link comes from recorded metadata rather than a hash match. */
  verified_by_pramaan: boolean
}

export interface TimelineEvent {
  evidence_id: string
  event_type: string
  occurred_at: string | null
  timestamp_source: string | null
  platform: string | null
  generation: number | null
  transformation: string | null
  distance_to_case_evidence: number | null
  discovered_by: string | null
  is_synthetic: boolean
  description: string
}

export interface PropagationGraph {
  nodes: PropagationNode[]
  edges: PropagationEdge[]
  node_count: number
  edge_count: number
  relations: Record<string, string>
}

/**
 * Propagation payload.
 *
 * `case_id`, `origin` and `timeline` are optional because the same object is
 * returned in two places: the standalone GET carries them, while inside the
 * analyse response `origin` and `timeline` are siblings of `propagation`
 * rather than nested within it.
 */
export interface PropagationResponse {
  case_id?: string
  method: string
  interpretation: string
  origin?: Origin | null
  timeline?: TimelineEvent[]
  graph: PropagationGraph
  instance_count: number
  matched_candidate_count: number
  platforms: string[]
  generations: number[]
  truncated: boolean
  notes: string[]
  caveats: string[]
  undated_instances?: unknown[]
}

// --- Audit -------------------------------------------------------------------

export interface AuditEvent {
  seq: number
  audit_id: string
  case_id: string | null
  event: string
  timestamp: string
  actor: string
  details: Record<string, unknown>
  previous_hash: string
  row_hash: string
}

/**
 * Audit trail.
 *
 * The chain-verification fields are optional: the trail embedded in the analyse
 * response carries them, the standalone GET leaves verification to
 * POST /audit/verify.
 */
export interface AuditTrail {
  case_id: string
  count: number
  total_rows: number
  truncated: boolean
  events: AuditEvent[]
  head_hash: string
  genesis_hash: string
  algorithm: string
  interpretation: string
  chain_valid?: boolean
  first_invalid_seq?: number | null
  issues?: string[]
  note?: string
}

export interface AuditVerification {
  valid: boolean
  scope: string
  case_id: string | null
  total_rows: number
  case_rows: number
  first_invalid_seq: number | null
  head_hash: string
  genesis_hash: string
  algorithm: string
  issues: string[]
  events: AuditEvent[]
  interpretation: string
}

// --- Metadata ----------------------------------------------------------------

export interface MetadataItem {
  evidence_id: string
  filename: string
  media_type: string
  mime_type: string
  size_bytes: number
  sha256: string
  ingested_at: string
  metadata: Record<string, unknown>
}

export interface MetadataResponse {
  case_id: string
  count: number
  items: MetadataItem[]
  /**
   * Backend copy: "Absence of metadata is NOT evidence of manipulation."
   * Displayed verbatim -- it is the sentence that stops an empty EXIF panel
   * being read as an incriminating finding.
   */
  interpretation: string
  extractor: string
}

// --- Report ------------------------------------------------------------------

export interface ReportResponse {
  case_id: string
  report_id: string
  filename: string
  path: string
  size_bytes: number
  sha256: string
  generated_at: string
  generator: string
  renderer: string
  pages: number | null
  audit_head_hash: string
  audit_chain_valid: boolean
  document_status: string
  renderer_status: Record<string, unknown>
  /** Relative path -- must be prefixed with the API base URL before use. */
  download_url: string
}

// --- Status ------------------------------------------------------------------

export interface IndexStatus {
  indexed_count: number
  last_updated: string | null
  index_version: number
  backend: string
  exact_search: boolean
  hash_bits: number
  dimensions: number
  persisted: boolean
  index_path: string
  faiss_available: boolean
  faiss_version?: string | null
  format_version?: number | string
  notes: string | null
}

export interface DetectorStatus {
  adapter: string
  model: string
  model_version: string
  /** false when no detector is installed -- the ai_detection signal is then UNAVAILABLE. */
  available: boolean
  reason: string | null
  interface_version: string
  score_semantics: string
  configured_backend: string
  configured_model_path: string | null
  candidate_adapters: unknown[]
  notes: string | null
}

// --- Analysis (the aggregate the whole workflow hangs off) -------------------

export interface AnalysisResponse {
  case: CaseRecord
  evidence: Evidence[]
  /** null when no evidence could be scored at all. */
  verdict: Verdict | null
  signals: Signal[]
  matches: MatchesResponse
  origin: Origin | null
  timeline: TimelineEvent[]
  audit: AuditTrail & {
    chain_valid: boolean
    first_invalid_seq: number | null
    issues: string[]
    note?: string
  }
  processing_time_ms: number
  verdicts: Verdict[]
  verdict_selection: string
  verdict_evidence_id: string | null
  propagation: PropagationResponse
  detector: DetectorStatus
  index: IndexStatus
  /** Pipeline stage ids that ran, in order. */
  stages: string[]
  analysis_version: string
  fusion_method: string
  score_semantics: string
  caveat: string
  warnings: string[]
  analysed_at: string | null
  refreshed: boolean
}

/** The backend's uniform error envelope, returned by every failure path. */
export interface ApiErrorEnvelope {
  error: {
    type: string
    message: string
    details?: Array<{ location: unknown[]; message: string; type: string }>
  }
  request_id: string
}

export interface DashboardSummary {
  active_investigations_count: number
  evidence_items_count: number
  flagged_media_count: number
  pending_review_count: number
  high_priority_count?: number
  evidence_breakdown?: { video: number; image: number; audio: number }
  avg_processing_time_ms: number | null
  recent_investigations: CaseRecord[]
  recent_evidence: Evidence[]
  flagged_media?: Evidence[]
  current_case_summary: CaseRecord | null
  system_status: string
  system_status_details?: Record<string, string>
}

export interface DetectResult {
  media_type: string
  label: string
  manipulation_score: number | null
  confidence: number | null
  abstained: boolean
  model: string
  model_version: string
  weights_hash?: string
  latency_ms?: number | null
  explanation: string
  heatmap_available?: boolean
  regions?: Array<Record<string, unknown>>
  timestamps?: Array<Record<string, unknown>>
  status: string
}
