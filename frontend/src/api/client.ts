/**
 * PRAMAAN API client.
 *
 * One function per backend endpoint. This is the only module that knows the
 * shape of the backend's URL space; components import from here and never
 * construct a path.
 *
 * Route reference (verified against the running FastAPI app):
 *   POST   /api/cases/upload
 *   GET    /api/cases
 *   GET    /api/cases/{case_id}
 *   PATCH  /api/cases/{case_id}
 *   DELETE /api/cases/{case_id}
 *   GET    /api/cases/{case_id}/evidence
 *   POST   /api/cases/{case_id}/analyse
 *   POST   /api/cases/{case_id}/verdict
 *   POST   /api/cases/{case_id}/matches
 *   POST   /api/cases/{case_id}/detect
 *   GET    /api/cases/{case_id}/metadata
 *   GET    /api/cases/{case_id}/propagation
 *   GET    /api/cases/{case_id}/audit
 *   POST   /api/cases/{case_id}/audit/verify
 *   POST   /api/cases/{case_id}/report
 *   GET    /api/cases/{case_id}/reports
 *   GET    /api/cases/{case_id}/reports/{report_id}
 *   GET    /api/index/status
 *   GET    /api/detector/status
 *   GET    /health
 *
 * `analyse` returns the whole pipeline in one response -- verdict, signals,
 * matches, propagation, origin, timeline, audit, detector and index status. The
 * per-stage endpoints exist for refreshing one panel without re-running
 * everything, and are wired to the refresh controls on each screen.
 */

import { apiUrl } from './config'
import { request, requestBlob, upload, type UploadProgress } from './http'
import type {
  AnalysisResponse,
  AuditTrail,
  AuditVerification,
  CaseRecord,
  DashboardSummary,
  DetectorStatus,
  DetectResult,
  Evidence,
  IndexStatus,
  MatchesResponse,
  MetadataResponse,
  PropagationResponse,
  ReportResponse,
  UploadResponse,
  Verdict,
} from './types'

// --- System ------------------------------------------------------------------

/** Liveness probe. Fixed contract: { status: "ok" }. */
export function health(signal?: AbortSignal): Promise<{ status: string }> {
  // Short timeout: this drives the "backend unavailable" banner, so it must
  // fail fast rather than leaving the UI in a pending state.
  return request<{ status: string }>('/health', { signal, timeoutMs: 5_000 })
}

export function indexStatus(signal?: AbortSignal): Promise<IndexStatus> {
  return request<IndexStatus>('/api/index/status', { signal })
}

export function detectorStatus(signal?: AbortSignal): Promise<DetectorStatus> {
  return request<DetectorStatus>('/api/detector/status', { signal })
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

export interface UploadFields {
  /** Omit to have the backend create a new case for this file. */
  caseId?: string
  title?: string
  description?: string
  examiner?: string
}

/**
 * Ingest an evidence file, creating a case when `caseId` is omitted.
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
  if (fields.examiner) form.append('examiner', fields.examiner)
  return upload<UploadResponse>('/api/cases/upload', form, opts)
}

/**
 * List cases.
 *
 * The collection key is `cases`, not `items` -- the backend names each list
 * after what it contains, and the names differ per endpoint (`cases`,
 * `evidence`, `reports`, `items`). They are not normalised here: renaming in the
 * transport layer would hide a contract change instead of surfacing it.
 */
export function listCases(signal?: AbortSignal): Promise<{ count: number; cases: CaseRecord[] }> {
  return request('/api/cases', { signal })
}

export function getCase(caseId: string, signal?: AbortSignal): Promise<CaseRecord> {
  return request<CaseRecord>(`/api/cases/${encodeURIComponent(caseId)}`, { signal })
}

export function listEvidence(
  caseId: string,
  signal?: AbortSignal,
): Promise<{ case_id: string; count: number; evidence: Evidence[] }> {
  return request(`/api/cases/${encodeURIComponent(caseId)}/evidence`, { signal })
}

export function listGlobalEvidence(
  params: { media_type?: string; q?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<{ total: number; evidence: Evidence[] }> {
  const query = new URLSearchParams()
  if (params.media_type) query.set('media_type', params.media_type)
  if (params.q) query.set('q', params.q)
  if (params.limit) query.set('limit', String(params.limit))
  const qstr = query.toString() ? `?${query.toString()}` : ''
  return request<{ total: number; evidence: Evidence[] }>(`/api/cases/library/all${qstr}`, { signal })
}

// --- Analysis ----------------------------------------------------------------

/**
 * Run the full forensic pipeline.
 *
 * This is the authoritative call: the verdict, signal states, fusion arithmetic
 * and gate rationale in the response are the backend's, and the frontend
 * displays them without recomputation.
 */
export function analyse(caseId: string, signal?: AbortSignal): Promise<AnalysisResponse> {
  return request<AnalysisResponse>(`/api/cases/${encodeURIComponent(caseId)}/analyse`, {
    method: 'POST',
    signal,
  })
}

export function verdicts(
  caseId: string,
  signal?: AbortSignal,
): Promise<{
  case_id: string
  count: number
  items: Verdict[]
  method: string
  interpretation: string
  caveat: string
}> {
  return request(`/api/cases/${encodeURIComponent(caseId)}/verdict`, { method: 'POST', signal })
}

export function matches(caseId: string, signal?: AbortSignal): Promise<MatchesResponse> {
  return request<MatchesResponse>(`/api/cases/${encodeURIComponent(caseId)}/matches`, {
    method: 'POST',
    signal,
  })
}

export function propagation(caseId: string, signal?: AbortSignal): Promise<PropagationResponse> {
  return request<PropagationResponse>(`/api/cases/${encodeURIComponent(caseId)}/propagation`, {
    signal,
  })
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
): Promise<{ case_id: string; count: number; reports: ReportResponse[] }> {
  return request(`/api/cases/${encodeURIComponent(caseId)}/reports`, { signal })
}

/**
 * Absolute URL for a generated report.
 *
 * The backend returns `download_url` as a path; this prefixes the configured
 * base so it can be used in an <a href> or window.open directly.
 */
export function reportDownloadUrl(downloadUrl: string): string {
  return apiUrl(downloadUrl)
}

/** Fetch the report PDF as a Blob, for download without leaving the app. */
export function downloadReport(downloadUrl: string, signal?: AbortSignal): Promise<Blob> {
  return requestBlob(downloadUrl, { signal })
}

export type { UploadProgress }
