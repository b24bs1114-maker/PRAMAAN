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

// --- Authentication ----------------------------------------------------------

/** Credentials posted to `POST /api/auth/login`. The backend forbids extra fields. */
export interface LoginRequest {
  username: string
  password: string
}

/**
 * The signed-in operator, as the backend reports them.
 *
 * This is the only source of the examiner's name anywhere in the UI. `display_name`
 * is what the backend stamps on evidence at ingest, so the intake screen shows this
 * value rather than asking anyone to type one.
 */
export interface AuthUser {
  user_id: string
  username: string
  display_name: string
  role: string
  /** ISO-8601, or null for an account that has never signed in before now. */
  last_login_at: string | null
  /**
   * True when the backend supplied this identity through its local development
   * auth bypass instead of a real login. Server-decided: the browser cannot set
   * it, and it is absent (false) for every authenticated operator. The UI shows it
   * so a bypass session is never mistaken for an examiner's attested work.
   */
  dev_bypass?: boolean
}

/**
 * A successful login.
 *
 * `token` is shown exactly once, in this response -- the server keeps only its
 * hash, so a lost token cannot be recovered, only replaced by signing in again.
 */
export interface LoginResponse {
  token: string
  /** Always `"bearer"`; sent back verbatim as the `Authorization` scheme. */
  token_type: string
  /** ISO-8601 expiry of this session. */
  expires_at: string
  user: AuthUser
}

/** Result of `POST /api/auth/logout`. `revoked` is false if the token was already gone. */
export interface LogoutResponse {
  status: string
  revoked: boolean
}

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
  /** Contributing signals, from the backend's own `included` set. */
  signals_available: number
  /** Signals APPLICABLE to this media type that were considered (== signals.length). */
  signals_total: number
  /**
   * Applicable signals that actually ran (were attempted), whether or not they
   * could decide. Inapplicable signals are in none of these counts.
   */
  signals_evaluated: number
  /** The applicable signal set this verdict was fused over, from backend truth. */
  applicable_signals: Array<{ signal_id: string; name: string }>
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
  /** Declared weights of the APPLICABLE signals before renormalisation. */
  declared_weight_total: number
  /** Sum of applicable declared weights that actually contributed. */
  available_weight: number
  /** Signals that can establish authenticity on their own; drives gate G-2. */
  primary_signals: string[]
}

/**
 * The fused verdicts already in a case file, from `GET /api/cases/{id}/verdict`.
 *
 * This is the read-only twin of `POST /analyse`: fusion is not re-run, so opening
 * a screen with it cannot rewrite the analysis of record or append to the audit
 * chain. That is the whole reason it exists here -- the Analysis screen has to be
 * able to show the verdict a case already carries when an examiner arrives by
 * deep link, without the page load itself becoming a forensic act.
 *
 * `items` are the stored fusion payloads verbatim, so each carries its own
 * signals, arithmetic and thresholds. Evidence that has never been fused is in
 * `pending_evidence` rather than being given a placeholder verdict: an item with
 * no verdict has no verdict, which is not the same as an inconclusive one.
 */
export interface StoredVerdictResponse {
  case_id: string
  count: number
  items: Verdict[]
  method: string
  interpretation: string
  caveat: string
  /** Always "stored" -- the backend says so explicitly rather than implying it. */
  source: string
  /** Evidence items in the case, fused or not. */
  evidence_count: number
  /** How many of them have a stored fused verdict. */
  analysed_count: number
  pending_evidence: Array<{
    evidence_id: string
    filename: string
    media_type: string
    sha256: string
    reason: string
  }>
  run_verdict_url: string | null
  notes: string[]
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
  /**
   * Forensic reports on record for this case.
   *
   * `null`/absent means the endpoint that produced this record did not count
   * them -- NOT that there are none. Read a positive number as "a report
   * exists"; never read the absence of one as "no report".
   */
  report_count?: number | null
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
  /**
   * How the evidence came into the examiner's hands, as typed at intake.
   *
   * Optional and free text: a real column on the evidence row, written once at
   * ingest and carried into the `EVIDENCE_INGESTED` audit entry. `null` means the
   * operator recorded nothing -- never an empty string, so a report cannot print
   * a blank custody note as though something had been said.
   */
  acquisition_context: string | null
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

// --- Case deletion -----------------------------------------------------------

/**
 * Rows removed from each table, counted by the backend before the delete ran.
 *
 * Two fields describe collateral in *other* cases and exist so the UI can say so
 * out loud. A match row is filed under one case but references two evidence
 * rows, so deleting this case's evidence removes match rows owned by other cases
 * (`matches_owned_by_other_cases`); those cases keep all their own evidence and
 * only lose the cross-case comparison. `timeline_events.evidence_id` is
 * ON DELETE SET NULL, so a surviving case's timeline row is detached rather than
 * deleted (`timeline_events_detached`).
 */
export interface CaseDeleteCounts {
  evidence: number
  analysis_results: number
  matches: number
  matches_owned_by_other_cases: number
  timeline_events: number
  timeline_events_detached: number
  reports: number
}

/**
 * What the delete did on disk.
 *
 * A file recorded in the database but already gone is counted in `*_missing`
 * rather than failing the operation, so these two pairs need not sum to the row
 * counts above.
 */
export interface CaseDeleteStorage {
  evidence_files_removed: number
  evidence_files_missing: number
  report_files_removed: number
  report_files_missing: number
  case_directory: string | null
  case_directory_removed: boolean
}

/** Perceptual-index cleanup. `rebuild_required` means POST /api/index/rebuild. */
export interface CaseDeleteIndex {
  vectors_removed: number
  index_version: number | null
  backend: string | null
  rebuild_required: boolean
}

/**
 * The CASE_DELETED chain entry, echoed back so the UI can show it was recorded.
 *
 * `retained` is always true and `case_rows_retained` is the number of audit rows
 * that still reference the deleted case id: audit history survives the case.
 */
export interface CaseDeleteAudit {
  audit_id: string
  seq: number
  event: string
  timestamp: string
  actor: string
  previous_hash: string
  row_hash: string
  retained: boolean
  case_rows_retained: number
}

/**
 * Result of DELETE /api/cases/{case_id}.
 *
 * Every number is measured by the backend. `deleted_evidence_count` duplicates
 * `deleted.evidence` at the top level for clients that predate the breakdown.
 * `warnings` is where a post-commit problem surfaces -- the rows and the audit
 * entry are already committed at that point, so a file that could not be removed
 * is reported here instead of turning a completed delete into an error.
 */
export interface CaseDeleteResult {
  status: string
  case_id: string
  case_number: string
  title: string | null
  examiner: string | null
  deleted_at: string
  deleted_evidence_count: number
  deleted: CaseDeleteCounts
  storage: CaseDeleteStorage
  index: CaseDeleteIndex
  audit: CaseDeleteAudit
  warnings: string[]
}

// --- Matches -----------------------------------------------------------------

export interface MatchCandidate {
  evidence_id: string
  distance: number
  similarity: number
  phash_distance: number
  dhash_distance: number | null
  ahash_distance?: number | null
  dinov2_similarity?: number | null
  match_basis?: string | null
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

/**
 * The candidates a case already carries, as the last search stored them.
 *
 * The response of `GET /api/cases/{id}/matches`, which runs no retrieval and
 * writes nothing -- unlike the `POST` on the same path, which replaces the
 * stored set and appends `MATCH_SEARCHED` to the case's audit chain.
 */
export interface StoredMatchesResponse extends MatchesResponse {
  source: string
  /**
   * Whether near-duplicate retrieval has ever run for this case.
   *
   * Taken from the audit trail, not from the candidate count, because an empty
   * list means two different things: nothing similar is indexed, or nobody has
   * looked. Only the first is a finding.
   */
  searched: boolean
  searched_at: string | null
  run_matches_url: string | null
  notes: string[]
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
  /**
   * Whether the near-duplicate retrieval this reconstruction rests on ran during
   * this request, ran earlier, or has never run.
   *
   * All three yield an identical empty graph when nothing matched, and they mean
   * different things: `NOT_RUN` is an absence of measurement, not a finding that
   * no other copies exist.
   */
  trace_status?: 'COMPUTED' | 'STORED' | 'NOT_RUN'
  trace_status_meaning?: string
  /** Whether this call appended to the case's audit chain. False for a read. */
  recorded?: boolean
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

/**
 * A report as it comes back from a listing.
 *
 * These are the facts recorded about a document that already exists: the
 * renderer that produced it, the digest of the bytes on disk, the audit head
 * that was current when it was sealed, and `document_status` -- the standing
 * printed onto the document itself. Nothing here is recomputed at read time, so
 * a listing cannot disagree with the document it lists.
 *
 * `path` and `renderer_status` are deliberately not part of this shape. The
 * listing endpoints do not send them, and the reasons are not incidental:
 * `renderer_status` describes the renderer importable *now*, which is a fact
 * about the reader's environment rather than about a document written in a
 * previous one, and it lives on the library envelope instead; `path` is the
 * file's location on the host, which a client never needs when it has an
 * authenticated `download_url`. Declaring them here made every listed report
 * claim two fields the server had never sent -- which is how the prototype
 * caveat came to render blank for every stored report.
 */
export interface StoredReport {
  case_id: string
  report_id: string
  filename: string
  size_bytes: number
  sha256: string
  generated_at: string
  generator: string
  renderer: string
  pages: number | null
  audit_head_hash: string
  audit_chain_valid: boolean
  /**
   * The caveat printed into the PDF -- prototype output, demonstration
   * thresholds, requires qualified examiner review. Shown verbatim wherever a
   * report is presented, because it is the document's own standing.
   */
  document_status: string
  /** Relative path -- must be prefixed with the API base URL before use. */
  download_url: string
  /** Present on listings, which join the case; absent from the generate response. */
  case_number?: string | null
  case_title?: string | null
}

/**
 * The response to generating a report.
 *
 * Everything a listing carries, plus the two things only the caller who caused
 * the write can be told: where the file was written, and which renderer was
 * available at the moment it was written.
 */
export interface ReportResponse extends StoredReport {
  path: string
  renderer_status: Record<string, unknown>
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

/**
 * `GET /api/dashboard/summary`.
 *
 * Mirrors `backend/app/schemas/api.py::DashboardSummaryResponse`. Several fields
 * the backend has always returned were missing from this interface, which is why
 * the dashboard invented substitutes for them: `verdict_breakdown` (real counts
 * per verdict token) was replaced by `evidence_items_count * 0.17`, and
 * `analysed_evidence_count` by nothing at all.
 *
 * `avg_processing_time_ms` is `null` when no run has been timed. An unmeasured
 * pipeline is unknown, not instantaneous.
 */
export interface DashboardSummary {
  active_investigations_count: number
  evidence_items_count: number
  flagged_media_count: number
  pending_review_count: number
  unanalysed_case_count?: number
  high_priority_count?: number
  evidence_breakdown?: { video: number; image: number; audio: number }
  /** Count of evidence rows that have a fused verdict on record. */
  analysed_evidence_count?: number
  /** Real per-verdict counts, keyed by the backend's own verdict token. */
  verdict_breakdown?: Record<string, number>
  avg_processing_time_ms: number | null
  avg_processing_time_basis?: string | null
  timed_analysis_runs?: number
  recent_investigations: CaseRecord[]
  recent_evidence: Evidence[]
  flagged_media?: Evidence[]
  flagged_media_truncated?: boolean
  current_case_summary: CaseRecord | null
  system_status: string
  system_status_details?: Record<string, string>
  metric_definitions?: Record<string, string>
  notes?: string[]
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

// --- Public Web Discovery (Google Cloud Vision Web Detection) ----------------

/**
 * `GET /api/system/signals`.
 *
 * The applicable forensic signal set per media type, from the fusion engine's
 * own applicability map. The single source the Analysis UI renders its signal
 * matrix from: a signal absent from a media type's list is NOT APPLICABLE and
 * is hidden -- never rendered as a failed or zero row, never counted in any
 * coverage denominator. The frontend never hardcodes applicability itself.
 */
export interface SignalApplicability {
  signal_names: Record<string, string>
  applicability: Record<string, Array<{ signal_id: string; name: string }>>
  note: string
  declared_weights: Record<string, number>
}

export type WebOccurrenceType = 'EXACT_MATCH' | 'NEAR_DUPLICATE' | 'VISUALLY_SIMILAR' | 'PAGE_ONLY'

export interface WebOccurrence {
  occurrence_id: string
  url: string
  domain: string
  page_title: string | null
  match_type: WebOccurrenceType
  raw_google_category: string
  similarity: number | null
  perceptual_distance: number | null
  dinov2_similarity?: number | null
  match_basis: string
  published_at: string | null
  discovered_at: string
  verified: boolean
  verification_error?: string | null
}

export interface WebEntity {
  entity_id: string | null
  description: string
  score: number | null
}

export interface WebTimelineNode {
  node_id: string
  url: string
  domain: string
  label: string
  timestamp: string
  timestamp_type: 'published' | 'discovered'
  match_type: string
  similarity: number | null
  is_earliest: boolean
}

export interface WebDiscoverySummary {
  total_occurrences: number
  pages_count: number
  full_matches_count: number
  partial_matches_count: number
  visually_similar_count: number
  verified_matches_count: number
}

export interface WebDiscoveryResponse {
  case_id: string
  evidence_id: string | null
  available: boolean
  status: 'SUCCESS' | 'PUBLIC_WEB_DISCOVERY_UNAVAILABLE' | 'NO_RESULTS' | 'ERROR'
  earliest_discovered_occurrence: WebOccurrence | null
  occurrences: WebOccurrence[]
  web_entities: WebEntity[]
  best_guess_labels: string[]
  timeline: WebTimelineNode[]
  summary: WebDiscoverySummary
  source_image_url: string | null
  caveats: string[]
  unavailable_reason: string | null
}

export interface ModelManifestSpec {
  status: string
  model_name: string
  model_version: string
  source_repo?: string
  hf_hub_model?: string
  checkpoint_filename: string
  release_asset?: string
  weights_size_bytes?: number
  weights_sha256?: string
  parameters?: number
  license?: string
  dataset?: string
  dataset_license?: string
  architecture?: string
  input_preprocessing?: Record<string, unknown>
  label_mapping?: Record<string, string>
  score_direction?: string
  validation_status?: string
  known_limitations?: string
}

export interface DetectorManifest {
  manifest_version: string
  project?: string
  updated_at?: string
  models: {
    image?: ModelManifestSpec
    video?: ModelManifestSpec
    audio?: ModelManifestSpec
    [key: string]: ModelManifestSpec | undefined
  }
}

export interface SystemStatusDirectory {
  path: string
  exists: boolean
  writable: boolean
}

/**
 * `GET /api/system/status`.
 *
 * The shape below is the response the backend actually sends. The previous
 * declaration was not: it put `detector`, `validator`, `index`, `renderer` and
 * `last_verification` at the top level, where none of them exist, and omitted
 * `database` and `capabilities` entirely. Nothing caught it because TypeScript
 * only checks the annotation against its uses, and `api.systemStatus()` casts
 * an untyped JSON body -- so `systemStatus.validator.c2pa_installed` typechecked
 * cleanly and threw at runtime, taking the whole Settings screen into the error
 * boundary the moment the Integrations tab was opened.
 *
 * Every capability block is optional-free but individually nullable-aware: the
 * backend always sends the keys, and their *contents* say whether the thing
 * works. `c2pa_library_available: false` with `container_scan_available: true`
 * is a real and distinct state (a manifest can be found but its signature not
 * validated) and must not be flattened into "no C2PA".
 */
export interface SystemStatus {
  app: {
    name: string
    version: string
    description: string
    environment: string
    debug: boolean
    docs_enabled: boolean
    /** True only while no stage can make an outbound call -- follows `capabilities.web_discovery`. */
    offline: boolean
    offline_detail: string
    cors_allow_origins: string[]
  }
  storage: {
    data_dir: SystemStatusDirectory
    evidence_dir: SystemStatusDirectory
    index_dir: SystemStatusDirectory
    reports_dir: SystemStatusDirectory
    corpus_dir: SystemStatusDirectory
    temp_dir: SystemStatusDirectory
  }
  database: {
    engine: string
    path: string
    exists: boolean
    /** Null when the file is not on disk -- absent, not zero bytes. */
    size_bytes: number | null
    detail: string
  }
  counts: {
    cases: number
    evidence: number
    analysis_results: number
    fused_evidence: number
    matches: number
    reports: number
    audit_entries: number
  }
  capabilities: {
    detector: DetectorStatus & {
      unavailable_because?: string | null
    }
    c2pa_validator: {
      c2pa_library_available: boolean
      c2pa_library_version: string | null
      container_scan_available: boolean
      signature_validation_available: boolean
      state: string
      inspector: string
      detail: string
    }
    perceptual_index: IndexStatus & {
      hashable_evidence_count: number
      pending_evidence_count: number
      covers: string
    }
    report_renderer: {
      renderer: string
      reportlab_available: boolean
      reason: string | null
      writer: string
      note: string | null
    }
    web_discovery: {
      available: boolean
      enabled: boolean
      /** Why it cannot run. Names the environment variable, never its value. */
      reason: string | null
      provider: string
      detail: string
    }
    /**
     * The pipeline's own declaration of a guarantee it enforces in code: every
     * stage that reads an evidence file re-hashes it first and refuses to run
     * when the bytes no longer match the digest recorded at intake.
     *
     * Read rather than restated. The Settings screen used to print
     * "Pre-Inference Digest Verification — ACTIVE" as a hardcoded string over a
     * pipeline that had no such check, which is the worst kind of thing a
     * forensic console can get wrong: it told an examiner that tampering on
     * disk between intake and analysis would be caught, and it would not have
     * been.
     */
    integrity_verification: {
      algorithm: string
      /** What happens when the digests differ. Currently `REFUSE`. */
      on_mismatch: string
      scope: string
      detail: string
    }
    metadata_extractor: {
      extractor: string
      interpretation: string
    }
  }
  audit: {
    total_rows: number
    head_hash: string | null
    genesis_hash: string
    algorithm: string
    interpretation: string
    /** Null when the chain has never been verified on this deployment. */
    last_verified_at: string | null
    last_verification: Record<string, unknown> | null
    last_verification_detail: string
  }
  generated_at: string | null
  /** Backend-authored caveats about this deployment. Rendered verbatim. */
  notes: string[]
}



