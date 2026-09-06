/**
 * PRAMAAN API client.
 *
 * One function per backend endpoint. This is the only module that knows the
 * shape of the backend's URL space; components import from here and never
 * construct a path.
 *
 * Route reference (verified against the running FastAPI app):
 *   POST   /api/auth/login
 *   GET    /api/auth/me
 *   POST   /api/auth/logout
 *   POST   /api/cases/upload
 *   GET    /api/cases
 *   GET    /api/cases/{case_id}
 *   PATCH  /api/cases/{case_id}
 *   DELETE /api/cases/{case_id}
 *   GET    /api/cases/{case_id}/evidence
 *   GET    /api/cases/library/all
 *   POST   /api/cases/{case_id}/analyse
 *   POST   /api/cases/{case_id}/matches
 *   GET    /api/cases/{case_id}/matches
 *   GET    /api/cases/{case_id}/verdict
 *   GET    /api/cases/{case_id}/metadata
 *   GET    /api/cases/{case_id}/propagation
 *   GET    /api/cases/{case_id}/audit
 *   POST   /api/cases/{case_id}/audit/verify
 *   POST   /api/cases/{case_id}/report
 *   GET    /api/cases/{case_id}/reports
 *   GET    /api/cases/{case_id}/reports/{report_id}  (download URL only)
 *   POST   /api/cases/{case_id}/web-discovery
 *   GET    /api/cases/{case_id}/web-discovery
 *   POST   /api/detect
 *   GET    /api/index/status
 *   GET    /api/detector/status
 *   GET    /api/system/signals
 *   GET    /api/dashboard/summary
 *   GET    /api/evidence/{id}/file  (media src only)
 *   GET    /health
 *
 * `analyse` returns the whole pipeline in one response -- verdict, signals,
 * matches, propagation, origin, timeline, audit, detector and index status. The
 * per-stage endpoints exist for refreshing one panel without re-running
 * everything, and are wired to the refresh controls on each screen.
 */

import { request, requestBlob, upload, type UploadProgress } from './http'
import type {
  AnalysisResponse,
  AuditTrail,
  AuditVerification,
  AuthUser,
  CaseDeleteResult,
  CaseRecord,
  DashboardSummary,
  DetectorManifest,
  DetectorStatus,
  DetectResult,
  Evidence,
  IndexStatus,
  LoginResponse,
  LogoutResponse,
  MatchesResponse,
  MetadataResponse,
  PropagationResponse,
  ReportResponse,
  SignalApplicability,
  StoredMatchesResponse,
  StoredReport,
  StoredVerdictResponse,
  SystemStatus,
  UploadResponse,
  WebDiscoveryResponse,
} from './types'

// --- Authentication ----------------------------------------------------------

/**
 * Sign in and receive a bearer token.
 *
 * The token is returned once and only here -- the backend stores a hash of it.
 * This function does not install it; `useAuth` does that via `setAuthToken`, so
 * there is exactly one place that decides what the current session is.
 *
 * Rejects with a 401 `ApiError` for a wrong username *or* a wrong password: the
 * backend reports both identically on purpose, so nothing here can tell the
 * operator which half they got wrong.
 */
export function login(
  credentials: { username: string; password: string },
  signal?: AbortSignal,
): Promise<LoginResponse> {
  return request<LoginResponse>('/api/auth/login', {
    method: 'POST',
    json: credentials,
    signal,
  })
}

/**
 * Who the currently installed token belongs to.
 *
 * Used on page load to turn a persisted token back into an identity: a token
 * that no longer resolves rejects with 401 rather than returning a stale user,
 * which is how a restored session is proven live instead of assumed.
 */
export function currentUser(signal?: AbortSignal): Promise<AuthUser> {
  return request<AuthUser>('/api/auth/me', { signal })
}

/**
 * Revoke the current token server-side.
 *
 * Revokes exactly the presented token, so an operator signed in from another
 * browser stays signed in there.
 */
export function logout(signal?: AbortSignal): Promise<LogoutResponse> {
  return request<LogoutResponse>('/api/auth/logout', { method: 'POST', signal })
}

// --- System ------------------------------------------------------------------

/** Liveness probe. Fixed contract: { status: "ok" }. */
export function health(signal?: AbortSignal): Promise<{ status: string }> {
  // 30s timeout to tolerate Render free-tier cold starts gracefully.
  return request<{ status: string }>('/health', { signal, timeoutMs: 30_000 })
}

export function indexStatus(signal?: AbortSignal): Promise<IndexStatus> {
  return request<IndexStatus>('/api/index/status', { signal })
}

export function detectorStatus(signal?: AbortSignal): Promise<DetectorStatus> {
  return request<DetectorStatus>('/api/detector/status', { signal })
}

export function detectorManifest(signal?: AbortSignal): Promise<DetectorManifest> {
  return request<DetectorManifest>('/api/detector/manifest', { signal })
}

export function systemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  return request<SystemStatus>('/api/system/status', { signal })
}

/**
 * The applicable forensic signal set per media type, from fusion truth.
 *
 * The Analysis UI renders its signal matrix from this response; applicability
 * is never hardcoded in the frontend. A signal absent from a media type's list
 * is NOT APPLICABLE: hidden, not a failed or zero row, and outside every
 * coverage denominator.
 */
export function signalApplicability(signal?: AbortSignal): Promise<SignalApplicability> {
  return request<SignalApplicability>('/api/system/signals', { signal })
}

export function getDashboardSummary(signal?: AbortSignal): Promise<DashboardSummary> {
  return request<DashboardSummary>('/api/dashboard/summary', { signal })
}

export function detectMedia(
  file?: File | null,
  evidenceId?: string,
  mediaType?: string,
  signal?: AbortSignal,
): Promise<DetectResult> {
  const form = new FormData()
  if (file) form.append('file', file, file.name)
  if (evidenceId) form.append('evidence_id', evidenceId)
  if (mediaType) form.append('media_type', mediaType)
  return upload<DetectResult>('/api/detect', form, { signal })
}

// --- Cases and ingestion -----------------------------------------------------

/**
 * The intake form, as the backend accepts it.
 *
 * There is no `examiner` field. The name recorded against evidence is the display
 * name of the operator the bearer token identifies, resolved server-side -- a
 * client-supplied examiner is not merely ignored here, it is refused by the API.
 *
 * `title` and `description` are mandatory when opening a new case and are
 * validated again by the backend, which is the authority; sending blanks or
 * whitespace is a 422 naming whichever one is missing. They are omitted when
 * adding a second exhibit to an existing `caseId`, which already carries them.
 */
export interface UploadFields {
  /** Omit to have the backend create a new case for this file. */
  caseId?: string
  /** Required for a new case: the short identifier for the investigation. */
  title?: string
  /** Required for a new case: why this evidence is being examined. */
  description?: string
  /**
   * Optional custody note: how the evidence was acquired.
   *
   * A real column on the evidence row and part of the `EVIDENCE_INGESTED` audit
   * details. Blank is sent as nothing, so the record reads "not recorded" rather
   * than storing an empty string.
   */
  acquisitionContext?: string
}

/**
 * Ingest an evidence file, creating a case when `caseId` is omitted.
 *
 * Requires a signed-in operator: `upload()` attaches the bearer token, and the
 * backend refuses an unauthenticated intake with 401 before reading the file.
 *
 * Returns 201 for a new item and 200 with `duplicate: true` when identical
 * bytes were already ingested into the same case -- both resolve here; the
 * caller reads `duplicate` to decide what to tell the officer.
 */
export function uploadEvidence(
  file: File,
  fields: UploadFields = {},
  opts: { onProgress?: (p: UploadProgress) => void; signal?: AbortSignal } = {},
): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file, file.name)
  if (fields.caseId) form.append('case_id', fields.caseId)
  if (fields.title) form.append('title', fields.title)
  if (fields.description) form.append('description', fields.description)
  if (fields.acquisitionContext && fields.acquisitionContext.trim()) {
    form.append('acquisition_context', fields.acquisitionContext.trim())
  }
  return upload<UploadResponse>('/api/cases/upload', form, opts)
}

/**
 * Server-side filters and pagination for {@link listCases}.
 *
 * Every field maps to a query parameter the backend's `GET /api/cases` already
 * understands, so the Cases screen narrows the list against the database rather
 * than fetching everything and filtering in the browser. Omit a field (or leave
 * it empty) to not constrain on it; `status` and `priority` also accept the
 * literal `"all"`, which the backend treats as "no filter" -- so a dropdown that
 * defaults to "all" needs no special-casing here.
 *
 * `verdict` filters on the case's newest analysis verdict (the same
 * `latest_verdict` surfaced per row), evaluated in SQL. `created_after` /
 * `created_before` are ISO-8601 instants; a malformed value is rejected by the
 * backend with 422 rather than silently ignored.
 */
export interface CaseListParams {
  /** "all" | "active" | a status token (e.g. "open", "closed"). */
  status?: string
  /** "all" | a priority token (e.g. "low", "medium", "high"). */
  priority?: string
  /** Free-text search across case number, title, description and examiner. */
  q?: string
  /** A verdict token (AUTHENTIC / MANIPULATED / INSUFFICIENT_EVIDENCE) or "all". */
  verdict?: string
  /** Substring match against the examiner name. */
  examiner?: string
  /** ISO-8601 lower bound on created_at (inclusive). */
  created_after?: string
  /** ISO-8601 upper bound on created_at (inclusive). */
  created_before?: string
  /** Page size. Backend default is 100. */
  limit?: number
  /** Rows to skip, for paging. */
  offset?: number
}

/**
 * List cases, optionally filtered and paged by the server.
 *
 * The response `count` is the total number of rows matching the filters across
 * the whole table -- NOT the length of `cases`, which is the current page (up to
 * `limit`, starting at `offset`). "Showing X of Y" is therefore
 * `cases.length` of `count`.
 *
 * The collection key is `cases`, not `items` -- the backend names each list
 * after what it contains, and the names differ per endpoint (`cases`,
 * `evidence`, `reports`, `items`). They are not normalised here: renaming in the
 * transport layer would hide a contract change instead of surfacing it.
 */
export function listCases(
  params: CaseListParams = {},
  signal?: AbortSignal,
): Promise<{ count: number; cases: CaseRecord[] }> {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  if (params.priority) query.set('priority', params.priority)
  if (params.q && params.q.trim()) query.set('q', params.q.trim())
  if (params.verdict) query.set('verdict', params.verdict)
  if (params.examiner && params.examiner.trim()) query.set('examiner', params.examiner.trim())
  if (params.created_after) query.set('created_after', params.created_after)
  if (params.created_before) query.set('created_before', params.created_before)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const qs = query.toString() ? `?${query.toString()}` : ''
  return request(`/api/cases${qs}`, { signal })
}

export function getCase(caseId: string, signal?: AbortSignal): Promise<CaseRecord> {
  return request<CaseRecord>(`/api/cases/${encodeURIComponent(caseId)}`, { signal })
}

/**
 * The fields a case record exposes for editing.
 *
 * Every one is optional: the update endpoint writes only the fields it is sent,
 * so omitting a key leaves that column untouched. This is not the same as
 * sending a blank -- a blank string is a value, and the backend would store it.
 * `updateCase` therefore drops keys that are `undefined` and never invents an
 * empty string, so "left unchanged" and "cleared" stay distinct, and the caller
 * is responsible for not sending a blank into a field the backend requires.
 */
export interface CaseUpdateFields {
  title?: string
  description?: string
  examiner?: string
  status?: string
  priority?: string
  complaint_reference?: string
}

/**
 * Update the editable fields of a case.
 *
 * Sends multipart form data because that is what `PATCH /api/cases/{id}`
 * accepts, and sends only the keys present in `fields` -- an omitted field is
 * not touched, which is why partial edits are safe. The backend records a
 * `CASE_UPDATED` audit entry naming exactly the fields that changed, so the
 * custody trail reflects the edit rather than the whole record being rewritten.
 *
 * Returns the case as the backend now holds it, so the caller renders the
 * stored truth rather than echoing back what it just typed.
 */
export function updateCase(
  caseId: string,
  fields: CaseUpdateFields,
  signal?: AbortSignal,
): Promise<CaseRecord> {
  const form = new FormData()
  // The API's form field for status is `case_status`; the rest match one to one.
  const formKey: Record<keyof CaseUpdateFields, string> = {
    title: 'title',
    description: 'description',
    examiner: 'examiner',
    status: 'case_status',
    priority: 'priority',
    complaint_reference: 'complaint_reference',
  }
  for (const key of Object.keys(fields) as (keyof CaseUpdateFields)[]) {
    const value = fields[key]
    if (value !== undefined) form.append(formKey[key], value)
  }
  return request<CaseRecord>(`/api/cases/${encodeURIComponent(caseId)}`, {
    method: 'PATCH',
    body: form,
    signal,
  })
}

/**
 * Delete a case permanently, with everything it owns.
 *
 * This is a hard delete, not a tombstone: the case row, its evidence, analysis
 * results, matches, timeline events and reports go, the stored files and index
 * vectors go, and a `CASE_DELETED` entry is appended to the audit chain that
 * outlives the case. The resolved promise is the backend's measured account of
 * what it removed -- callers should show `warnings` rather than assume a clean
 * result, because a post-commit filesystem problem lands there while the delete
 * itself has already succeeded.
 *
 * A second delete of the same id rejects with a 404 `ApiError`. Nothing here
 * translates that into success.
 */
export function deleteCase(caseId: string, signal?: AbortSignal): Promise<CaseDeleteResult> {
  return request<CaseDeleteResult>(`/api/cases/${encodeURIComponent(caseId)}`, {
    method: 'DELETE',
    signal,
  })
}

export function listEvidence(
  caseId: string,
  signal?: AbortSignal,
): Promise<{ case_id: string; count: number; evidence: Evidence[] }> {
  return request(`/api/cases/${encodeURIComponent(caseId)}/evidence`, { signal })
}

/**
 * The evidence library, whole or scoped to one case.
 *
 * `case_id` is scope and `q` is search; they compose. Passing the case id as `q`
 * would not do: it matches filename, sha256 and evidence id too, so it answers
 * "mentions this text" rather than "belongs to this case", and it would occupy
 * the one parameter the operator's search box needs.
 */
export function listGlobalEvidence(
  params: { media_type?: string; q?: string; case_id?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<{ total: number; evidence: Evidence[] }> {
  const query = new URLSearchParams()
  if (params.media_type) query.set('media_type', params.media_type)
  if (params.q) query.set('q', params.q)
  if (params.case_id) query.set('case_id', params.case_id)
  if (params.limit) query.set('limit', String(params.limit))
  const qstr = query.toString() ? `?${query.toString()}` : ''
  return request<{ total: number; evidence: Evidence[] }>(`/api/cases/library/all${qstr}`, { signal })
}

/**
 * The stored bytes of one evidence item.
 *
 * Fetched rather than pointed at with an `<img src>`: the route requires the
 * operator's bearer token, and a browser-initiated image request cannot carry an
 * Authorization header. Callers should go through `components/EvidenceMedia`,
 * which owns the object-URL lifetime, rather than calling this directly.
 *
 * This is the only correct source for a preview -- never the JSON list endpoint.
 * The read writes no audit row.
 */
export function evidenceFile(evidenceId: string, signal?: AbortSignal): Promise<Blob> {
  return requestBlob(`/api/evidence/${encodeURIComponent(evidenceId)}/file`, { signal })
}

// --- Analysis ----------------------------------------------------------------

/**
 * Run the full forensic pipeline.
 *
 * This is the authoritative call: the verdict, signal states, fusion arithmetic
 * and gate rationale in the response are the backend's, and the frontend
 * displays them without recomputation.
 *
 * `refresh` re-runs every stage instead of replaying the stored result, and is
 * the difference between a button that says "Re-Run Analysis" and one that
 * means it. Without it the backend returns the verdict it already has, so an
 * examiner who has since added evidence or rebuilt the perceptual index sees
 * the old assessment with no indication that nothing was recomputed. Opening a
 * screen leaves it false: a page load must not silently rewrite the analysis of
 * record.
 */
export function analyse(
  caseId: string,
  options: { refresh?: boolean } = {},
  signal?: AbortSignal,
): Promise<AnalysisResponse> {
  const query = options.refresh ? '?refresh=true' : ''
  return request<AnalysisResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/analyse${query}`,
    {
      method: 'POST',
      signal,
    },
  )
}

/**
 * Run near-duplicate retrieval across the index for this case's evidence.
 *
 * A write: it replaces the case's stored match set and appends `MATCH_SEARCHED`
 * to the audit chain. Correct when the operator asks for a search, wrong on
 * mount -- see `storedMatches` for what a page load is entitled to.
 */
export function matches(caseId: string, signal?: AbortSignal): Promise<MatchesResponse> {
  return request<MatchesResponse>(`/api/cases/${encodeURIComponent(caseId)}/matches`, {
    method: 'POST',
    signal,
  })
}

/**
 * Read the candidates already stored for a case. Retrieval is not run.
 *
 * The read twin of `matches`, and what every screen should call on mount. The
 * Provenance screen used to POST here as it opened, so arriving at the page
 * appended a `MATCH_SEARCHED` row to the case's chain -- forensic history
 * written because somebody navigated.
 *
 * `searched` is the part that cannot be reconstructed from the candidate list:
 * it comes from the audit trail, so an empty result can be told apart from a
 * case nobody has searched.
 */
export function storedMatches(
  caseId: string,
  signal?: AbortSignal,
): Promise<StoredMatchesResponse> {
  return request<StoredMatchesResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/matches`,
    { signal },
  )
}

/**
 * Read the fused verdicts a case already carries. Fusion is not run.
 *
 * The counterpart to `analyse` for the case an examiner has merely *opened*.
 * `POST /analyse` is not safe to call on mount even with `refresh` false: it
 * still re-runs near-duplicate retrieval and appends `MATCH_SEARCHED` and
 * `ANALYSIS_COMPLETED` to the audit chain, so navigating to a screen would write
 * forensic history. This route computes nothing and writes nothing -- it returns
 * the fusion payloads already on record, which is what a page load is entitled
 * to see.
 *
 * `analysed_count` and `pending_evidence` are the honest distinction the count
 * alone cannot make: a case may hold five exhibits of which two were fused, and
 * the three that were not have no verdict rather than an inconclusive one.
 */
export function storedVerdict(
  caseId: string,
  signal?: AbortSignal,
): Promise<StoredVerdictResponse> {
  return request<StoredVerdictResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/verdict`,
    { signal },
  )
}

/**
 * A case's propagation reconstruction.
 *
 * `record` decides whether this call is allowed to write. A page load must pass
 * `false`: with `true` the backend runs near-duplicate retrieval for any case
 * that has none stored and appends `MATCH_SEARCHED` and
 * `PROPAGATION_RECONSTRUCTED` to the case's audit chain, so simply opening the
 * Provenance screen moved the chain's head hash. The read reconstructs from
 * retrieval already on record and reports `trace_status` so an empty graph is
 * never mistaken for a search that found nothing.
 *
 * `refresh` is the opposite intent -- recompute -- and the backend rejects it
 * with `record: false`, because a search that genuinely ran must be recorded.
 */
export function propagation(
  caseId: string,
  opts: { record?: boolean; refresh?: boolean } = {},
  signal?: AbortSignal,
): Promise<PropagationResponse> {
  const query = new URLSearchParams()
  if (opts.record === false) query.set('record', 'false')
  if (opts.refresh) query.set('refresh', 'true')
  const qstr = query.toString() ? `?${query.toString()}` : ''
  return request<PropagationResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/propagation${qstr}`,
    { signal },
  )
}

export function metadata(caseId: string, signal?: AbortSignal): Promise<MetadataResponse> {
  return request<MetadataResponse>(`/api/cases/${encodeURIComponent(caseId)}/metadata`, { signal })
}

// --- Audit -------------------------------------------------------------------

export function auditTrail(caseId: string, signal?: AbortSignal): Promise<AuditTrail> {
  return request<AuditTrail>(`/api/cases/${encodeURIComponent(caseId)}/audit`, { signal })
}

/** Recompute the hash chain and report whether it still verifies. */
export function verifyAudit(caseId: string, signal?: AbortSignal): Promise<AuditVerification> {
  return request<AuditVerification>(`/api/cases/${encodeURIComponent(caseId)}/audit/verify`, {
    method: 'POST',
    signal,
  })
}

// --- Reporting ---------------------------------------------------------------

/**
 * Generate the forensic PDF for a case.
 *
 * `examiner` goes in a JSON body -- the backend declares it as an embedded body
 * field, not a form field, because unlike the upload there is no file involved.
 * `refresh` is a query parameter and re-runs every analysis stage before
 * rendering; the UI leaves it false because REPORT is the last workflow step and
 * the analysis has already run.
 */
export function generateReport(
  caseId: string,
  fields: { examiner?: string; refresh?: boolean } = {},
  signal?: AbortSignal,
): Promise<ReportResponse> {
  const query = fields.refresh ? '?refresh=true' : ''
  return request<ReportResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/report${query}`,
    {
      method: 'POST',
      json: { examiner: fields.examiner ?? null },
      signal,
    },
  )
}

export function listReports(
  caseId: string,
  signal?: AbortSignal,
): Promise<{ case_id: string; count: number; reports: StoredReport[] }> {
  return request(`/api/cases/${encodeURIComponent(caseId)}/reports`, { signal })
}

/**
 * Fetch the report PDF as a Blob.
 *
 * There is deliberately no helper that turns `download_url` into a bare href.
 * The route is authenticated, so a link or `window.open` pointed at it is an
 * unauthenticated request that returns 401; the bytes have to come through this
 * transport, which attaches the operator's token. `components/ReportActions`
 * turns the blob into both a download and a new-tab view.
 */
export function downloadReport(downloadUrl: string, signal?: AbortSignal): Promise<Blob> {
  return requestBlob(downloadUrl, { signal })
}

/**
 * Run public web discovery via Google Cloud Vision Web Detection.
 */
export function runWebDiscovery(
  caseId: string,
  options: { evidenceId?: string; imageUrl?: string; refresh?: boolean } = {},
  signal?: AbortSignal,
): Promise<WebDiscoveryResponse> {
  const params = new URLSearchParams()
  if (options.evidenceId) params.set('evidence_id', options.evidenceId)
  if (options.imageUrl) params.set('image_url', options.imageUrl)
  if (options.refresh) params.set('refresh', 'true')
  const qs = params.toString() ? `?${params.toString()}` : ''
  return request<WebDiscoveryResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/web-discovery${qs}`,
    {
      method: 'POST',
      signal,
    },
  )
}

/**
 * Get cached public web discovery results for a case.
 */
export function getWebDiscovery(
  caseId: string,
  options: { evidenceId?: string } = {},
  signal?: AbortSignal,
): Promise<WebDiscoveryResponse> {
  const params = new URLSearchParams()
  if (options.evidenceId) params.set('evidence_id', options.evidenceId)
  const qs = params.toString() ? `?${params.toString()}` : ''
  return request<WebDiscoveryResponse>(
    `/api/cases/${encodeURIComponent(caseId)}/web-discovery${qs}`,
    { signal },
  )
}

export type { UploadProgress }

