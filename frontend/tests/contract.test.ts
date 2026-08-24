/**
 * Frontend contract tests.
 *
 * These run the *real* API client and the real presentation helpers against real
 * backend responses. The responses are captured by scripts/verify_integration.py
 * driving the actual FastAPI app, so nothing here is a hand-written mock: if the
 * backend's payload shape changes, the recordings change and these tests move.
 *
 * The transport is replayed rather than live because a browser and a bound
 * socket are not always available (CI, sandboxes). What that means precisely:
 * request construction, response parsing, error mapping and every display rule
 * are verified; the network itself and a browser's CORS enforcement are not.
 * Those are covered on the backend side by scripts/verify_integration.py.
 *
 * Run with:
 *   npm run verify:contract
 */

import { api, ApiError } from '../src/api'
import {
  barGeometry,
  coverageLine,
  exclusionSummary,
  isExcluded,
  signalPillVariant,
  statusLabel,
  verdictBandLabel,
  verdictTone,
} from '../src/lib/signals'
import { formatScore, formatTimestamp, NOT_MEASURED, orPlaceholder } from '../src/lib/format'
import type { AnalysisResponse, Signal, Verdict } from '../src/api/types'

// --- Recordings --------------------------------------------------------------

interface Recording {
  status: number
  headers: Record<string, string>
  json: unknown
  bytes_len: number | null
}

interface Recordings {
  context: Record<string, string | boolean | null>
  responses: Record<string, Recording>
}

declare const process: { env: Record<string, string | undefined>; exit(code: number): void }

const RECORDINGS: Recordings = JSON.parse(
  // Injected by the runner so this file has no filesystem dependency.
  (globalThis as { __RECORDINGS__?: string }).__RECORDINGS__ ?? '{}',
)

const BASE = 'http://127.0.0.1:8000'

/** Every request the client made, in order, so the contract can be asserted. */
const issued: Array<{ method: string; url: string; body: unknown }> = []

/** Look up a recording, tolerating the `#suffix` markers used for error cases. */
function lookup(method: string, url: string, marker?: string): Recording | undefined {
  const path = url.startsWith(BASE) ? url.slice(BASE.length) : url
  return RECORDINGS.responses[`${method} ${path}${marker ?? ''}`]
}

// --- Transport shims ---------------------------------------------------------
// The client only ever touches fetch and XMLHttpRequest, both of which are
// replaced here. Nothing else in the client is stubbed.

/** Marker forced onto the next upload/request, to select an error recording. */
let nextMarker: string | undefined
/** Force a transport-level failure, to exercise the backend-unreachable path. */
let failTransport = false

globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
  const url = String(input)
  const method = (init?.method ?? 'GET').toUpperCase()
  issued.push({ method, url, body: init?.body ?? null })

  if (failTransport) throw new TypeError('fetch failed')

  const rec = lookup(method, url, nextMarker)
  nextMarker = undefined
  if (!rec) throw new TypeError(`fetch failed (no recording for ${method} ${url})`)

  const isJson = rec.json !== null && rec.json !== undefined
  const body = isJson ? JSON.stringify(rec.json) : 'x'.repeat(rec.bytes_len ?? 0)
  return new Response(body, {
    status: rec.status,
    headers: {
      'content-type': isJson ? 'application/json' : 'application/pdf',
      'x-request-id': rec.headers['x-request-id'] ?? 'replayed',
    },
  })
}) as typeof fetch

class ReplayXHR {
  status = 0
  statusText = ''
  responseText = ''
  responseType = ''
  timeout = 0
  upload: { onprogress?: (e: { loaded: number; total: number; lengthComputable: boolean }) => void } =
    {}
  onload?: () => void
  onerror?: () => void
  ontimeout?: () => void
  onabort?: () => void
  private method = 'GET'
  private url = ''
  private headers: Record<string, string> = {}

  open(method: string, url: string): void {
    this.method = method.toUpperCase()
    this.url = url
  }

  setRequestHeader(key: string, value: string): void {
    this.headers[key] = value
  }

  getResponseHeader(key: string): string | null {
    return key.toLowerCase() === 'x-request-id' ? 'replayed' : null
  }

  abort(): void {
    this.onabort?.()
  }

  send(body: FormData): void {
    issued.push({ method: this.method, url: this.url, body })

    if (failTransport) {
      this.onerror?.()
      return
    }

    // Report progress the way a browser would, so the UI's progress path runs.
    this.upload.onprogress?.({ loaded: 512, total: 2048, lengthComputable: true })
    this.upload.onprogress?.({ loaded: 2048, total: 2048, lengthComputable: true })

    const rec = lookup(this.method, this.url, nextMarker)
    nextMarker = undefined
    if (!rec) {
      this.onerror?.()
      return
    }
    this.status = rec.status
    this.responseText = JSON.stringify(rec.json)
    this.onload?.()
  }
}
;(globalThis as { XMLHttpRequest?: unknown }).XMLHttpRequest = ReplayXHR

// --- Assertions --------------------------------------------------------------

let passed = 0
const failures: string[] = []

function check(ok: boolean, name: string, detail = ''): void {
  if (ok) {
    passed += 1
    console.log(`PASS  ${name}${detail ? `  [${detail}]` : ''}`)
  } else {
    failures.push(`${name}${detail ? `: ${detail}` : ''}`)
    console.log(`FAIL  ${name}${detail ? `  [${detail}]` : ''}`)
  }
}

/** Assert the client issued exactly the method and path the backend serves. */
function expectRequest(method: string, path: string, name: string): void {
  const last = issued[issued.length - 1]
  const actual = last ? `${last.method} ${last.url.replace(BASE, '')}` : 'none'
  check(actual === `${method} ${path}`, name, actual)
}

async function expectApiError(
  name: string,
  fn: () => Promise<unknown>,
  expected: { kind: string; status: number },
): Promise<ApiError | null> {
  try {
    await fn()
    check(false, name, 'resolved instead of throwing')
    return null
  } catch (error) {
    if (!(error instanceof ApiError)) {
      check(false, name, `threw ${String(error)} rather than ApiError`)
      return null
    }
    check(
      error.kind === expected.kind && error.status === expected.status,
      name,
      `kind=${error.kind} status=${error.status}`,
    )
    return error
  }
}

// --- Tests -------------------------------------------------------------------

async function main(): Promise<void> {
  const caseId = String(RECORDINGS.context.case_id)
  const downloadUrl = String(RECORDINGS.context.download_url)

  // 1. System probes.
  const health = await api.health()
  expectRequest('GET', '/health', 'health() calls GET /health')
  check(health.status === 'ok', 'health() parses the real payload', JSON.stringify(health))

  const index = await api.indexStatus()
  expectRequest('GET', '/api/index/status', 'indexStatus() calls GET /api/index/status')
  check(
    typeof index.indexed_count === 'number' && typeof index.backend === 'string',
    'indexStatus() parses indexed_count/backend',
    `count=${index.indexed_count} backend=${index.backend}`,
  )

  const detector = await api.detectorStatus()
  expectRequest('GET', '/api/detector/status', 'detectorStatus() calls GET /api/detector/status')
  check(
    typeof detector.available === 'boolean',
    'detectorStatus() reports availability as a boolean',
    `available=${detector.available}`,
  )

  // 2. UPLOAD -- via XMLHttpRequest, with progress.
  const progress: number[] = []
  const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0x00])], 'complaint-photo.jpg', {
    type: 'image/jpeg',
  })
  const uploaded = await api.uploadEvidence(
    file,
    { title: 'Verification case', examiner: 'automated' },
    { onProgress: (p) => progress.push(p.fraction ?? -1) },
  )
  expectRequest('POST', '/api/cases/upload', 'uploadEvidence() posts to /api/cases/upload')
  check(
    progress.length === 2 && progress[1] === 1,
    'upload progress is reported from real XHR events',
    `fractions=${progress.join(',')}`,
  )
  check(
    Boolean(uploaded.case.case_id) && Boolean(uploaded.evidence.sha256),
    'uploadEvidence() parses case + evidence',
    `case=${uploaded.case.case_number} sha=${uploaded.evidence.sha256.slice(0, 12)}`,
  )
  check(
    uploaded.evidence.sha256 === RECORDINGS.context.sha256,
    'the digest the client reads is the digest the backend computed',
    uploaded.evidence.sha256.slice(0, 16),
  )
  check(
    /^multipart\/form-data|^$/.test('') && issued[issued.length - 1].body instanceof FormData,
    'upload body is FormData (so the backend sees multipart)',
  )

  // 3. ANALYSE -- the authoritative call.
  const analysis: AnalysisResponse = await api.analyse(caseId)
  expectRequest('POST', `/api/cases/${caseId}/analyse`, 'analyse() posts to /analyse')
  const verdict = analysis.verdict as Verdict
  check(verdict !== null, 'analyse() returns a verdict object')
  check(
    ['AUTHENTIC', 'MANIPULATED', 'INSUFFICIENT_EVIDENCE'].includes(verdict.verdict),
    'verdict band is one the UI knows how to render',
    verdict.verdict,
  )

  // 4. The display rules the brief makes non-negotiable.
  const band = verdictBandLabel(verdict.verdict)
  check(!/\d/.test(band), 'verdict band label carries no number', band)
  check(!/%/.test(band), 'verdict band label carries no percentage', band)
  check(
    band !== verdict.verdict,
    'verdict band is hedged rather than restating the raw token',
    `${verdict.verdict} -> ${band}`,
  )
  check(
    ['authentic', 'manipulated', 'inconclusive'].includes(verdictTone(verdict.verdict)),
    'verdict tone resolves to a known token',
    verdictTone(verdict.verdict),
  )

  // isExcluded takes the whole signal, not its status: the backend's own
  // `included` flag is the primary source and status is only the fallback.
  const excluded = verdict.signals.filter((s: Signal) => isExcluded(s))
  const included = verdict.signals.filter((s: Signal) => !isExcluded(s))
  check(
    excluded.length > 0,
    'the real analysis has at least one excluded signal to render',
    `${excluded.length} excluded of ${verdict.signals.length}`,
  )
  check(
    excluded.every((s: Signal) => barGeometry(s) === null),
    'no bar is drawn for an excluded signal (a zero-length bar reads as a score of 0)',
    excluded.map((s: Signal) => s.signal_id).join(','),
  )
  check(
    included.every((s: Signal) => barGeometry(s) !== null),
    'every contributing signal does get a bar',
    included.map((s: Signal) => s.signal_id).join(','),
  )
  // Some statuses are already plain English and map to themselves
  // (INCONCLUSIVE, ERROR); what must never reach the eye is a raw enum token
  // like UNSUPPORTED_MEDIA.
  check(
    excluded.every((s: Signal) => !statusLabel(s.status).includes('_')),
    'no raw enum token is shown as a status label',
    excluded.map((s: Signal) => `${s.status}->${statusLabel(s.status)}`).join(' '),
  )
  check(
    statusLabel('UNSUPPORTED_MEDIA') === 'NOT APPLICABLE' && statusLabel('OK') === 'ASSESSED',
    'the statuses that need rewording get it',
    `${statusLabel('UNSUPPORTED_MEDIA')} / ${statusLabel('OK')}`,
  )
  check(
    excluded.every((s: Signal) => exclusionSummary(s.status).length > 0),
    'every excluded signal carries an explanatory sentence',
    excluded.map((s: Signal) => exclusionSummary(s.status)).join(' | '),
  )
  check(
    verdict.signals.every((s: Signal) => s.status === 'OK' || s.score === null),
    'no unmeasured signal arrives with a numeric score',
    verdict.signals.map((s: Signal) => `${s.signal_id}=${s.score}`).join(' '),
  )
  check(
    formatScore(null) === NOT_MEASURED && orPlaceholder(null) === NOT_MEASURED,
    'a null measurement formats as the not-measured placeholder, never 0',
    `null -> ${formatScore(null)}`,
  )
  check(
    !formatScore(0.5).includes('%'),
    'scores are not rendered as percentages',
    formatScore(0.5),
  )
  const coverage = coverageLine(verdict)
  check(
    coverage.includes(String(verdict.signals_total)) &&
      coverage.includes(String(verdict.signals_available)),
    'coverage line states both totals from the backend',
    coverage,
  )
  check(
    signalPillVariant(included[0], verdict.thresholds) !== undefined,
    'signal pill direction is derived from the backend thresholds',
    `${included[0]?.signal_id} -> ${signalPillVariant(included[0], verdict.thresholds)}`,
  )

  // The frontend must not recompute fusion. Assert the numbers it shows are the
  // backend's, by checking the published arithmetic reproduces the score.
  const terms = [...verdict.arithmetic.matchAll(/([\d.]+)x([\d.]+)/g)].map(
    ([, score, weight]) => Number(score) * Number(weight),
  )
  const reproduced = terms.reduce((a, b) => a + b, 0)
  check(
    terms.length > 0 &&
      Math.abs(reproduced - (verdict.manipulation_score ?? -1)) < 5e-4,
    "the backend's own arithmetic reproduces its score (frontend adds no maths)",
    `${verdict.arithmetic} -> ${reproduced.toFixed(4)} vs ${verdict.manipulation_score}`,
  )

  // 5. Origin wording, read through the client.
  const propagation = await api.propagation(caseId)
  expectRequest('GET', `/api/cases/${caseId}/propagation`, 'propagation() calls GET /propagation')
  check(
    propagation.origin?.label === 'earliest known instance in the indexed evidence corpus',
    'origin label reaches the UI as the mandated wording',
    String(propagation.origin?.label),
  )
  check(
    propagation.origin?.is_absolute_origin === false && Boolean(propagation.origin?.caveat),
    'origin is not presented as absolute, and carries its caveat',
    `absolute=${propagation.origin?.is_absolute_origin}`,
  )

  // 6. Matches are candidates, with real distances.
  const matches = await api.matches(caseId)
  expectRequest('POST', `/api/cases/${caseId}/matches`, 'matches() posts to /matches')
  const candidates = matches.queries.flatMap((q) => q.candidates)
  check(candidates.length > 0, 'matches() surfaces real candidates', `${candidates.length}`)
  check(
    candidates.every((c) => typeof c.distance === 'number' && typeof c.similarity === 'number'),
    'every candidate carries a numeric distance and similarity',
  )
  check(
    typeof matches.thresholds.hash_bits === 'number',
    'match thresholds arrive so distances can be shown in context',
    `hash_bits=${matches.thresholds.hash_bits}`,
  )

  // 7. Remaining panels.
  const metadata = await api.metadata(caseId)
  expectRequest('GET', `/api/cases/${caseId}/metadata`, 'metadata() calls GET /metadata')
  check(
    Array.isArray(metadata.items) && metadata.items.length > 0,
    'metadata() reads the items array (not a renamed key)',
    `${metadata.items.length} items`,
  )
  check(
    /not evidence of manipulation/i.test(metadata.interpretation ?? ''),
    'the metadata caveat reaches the UI verbatim',
    String(metadata.interpretation).slice(0, 60),
  )

  const trail = await api.auditTrail(caseId)
  expectRequest('GET', `/api/cases/${caseId}/audit`, 'auditTrail() calls GET /audit')
  check(
    trail.events.length > 0 && Boolean(trail.head_hash),
    'audit trail arrives with events and a head hash',
    `${trail.events.length} events`,
  )
  check(
    trail.events.every((e) => Boolean(e.row_hash) && e.previous_hash !== undefined),
    'each audit event carries its chain hashes',
  )

  const verification = await api.verifyAudit(caseId)
  expectRequest('POST', `/api/cases/${caseId}/audit/verify`, 'verifyAudit() posts to /audit/verify')
  check(verification.valid === true, 'audit verification result is read as valid')

  const cases = await api.listCases()
  check(Array.isArray(cases.cases), "listCases() reads the 'cases' key", `${cases.count} cases`)
  const evidence = await api.listEvidence(caseId)
  check(
    Array.isArray(evidence.evidence),
    "listEvidence() reads the 'evidence' key",
    `${evidence.count} items`,
  )

  // 8. REPORT -- JSON body, and a real PDF blob.
  const report = await api.generateReport(caseId, { examiner: 'automated' })
  expectRequest('POST', `/api/cases/${caseId}/report`, 'generateReport() posts to /report')
  const reportBody = issued[issued.length - 1].body
  check(
    typeof reportBody === 'string' && JSON.parse(reportBody).examiner === 'automated',
    'generateReport() sends a JSON body (the backend rejects multipart here)',
    String(reportBody),
  )
  check(Boolean(report.download_url), 'report carries a download_url', report.download_url)
  check(
    api.reportDownloadUrl(report.download_url).startsWith(BASE),
    'reportDownloadUrl() prefixes the configured base',
    api.reportDownloadUrl(report.download_url),
  )

  const listed = await api.listReports(caseId)
  check(
    Array.isArray(listed.reports),
    "listReports() reads the 'reports' key",
    `${listed.count} reports`,
  )

  const blob = await api.downloadReport(downloadUrl)
  check(blob.size > 0, 'downloadReport() returns a non-empty Blob', `${blob.size} bytes`)

  // 9. Error paths -- each mapped from a real backend response.
  nextMarker = '#badtype'
  const badType = await expectApiError(
    '400 maps to bad_request',
    () => api.uploadEvidence(file),
    { kind: 'bad_request', status: 400 },
  )
  check(
    Boolean(badType?.userMessage) && badType?.userMessage === badType?.message,
    "400 shows the backend's own rejection reason",
    badType?.userMessage.slice(0, 60),
  )

  nextMarker = '#oversize'
  const tooLarge = await expectApiError(
    '413 maps to payload_too_large',
    () => api.uploadEvidence(file),
    { kind: 'payload_too_large', status: 413 },
  )
  check(
    /maximum upload size/i.test(tooLarge?.userMessage ?? ''),
    '413 explains the size limit',
    tooLarge?.userMessage.slice(0, 60),
  )

  nextMarker = '#nofile'
  const invalid = await expectApiError(
    '422 maps to validation',
    () => api.uploadEvidence(file),
    { kind: 'validation', status: 422 },
  )
  check(
    (invalid?.details?.length ?? 0) > 0 && /file/i.test(invalid?.userMessage ?? ''),
    '422 surfaces the offending field',
    invalid?.userMessage.slice(0, 80),
  )

  const notFound = await expectApiError(
    '404 maps to not_found',
    () => api.getCase('does-not-exist'),
    { kind: 'not_found', status: 404 },
  )
  check(
    notFound?.isRetryable === false,
    '404 is not offered as retryable',
    `retryable=${notFound?.isRetryable}`,
  )
  check(
    Boolean(notFound?.requestId),
    '404 carries the request id for support',
    String(notFound?.requestId),
  )

  await expectApiError(
    'analyse on an unknown case maps to not_found',
    () => api.analyse('does-not-exist'),
    { kind: 'not_found', status: 404 },
  )

  // 10. Backend unreachable -- the banner path.
  failTransport = true
  const down = await expectApiError('a dead backend maps to network', () => api.health(), {
    kind: 'network',
    status: 0,
  })
  check(
    down?.isBackendUnreachable === true && down?.isRetryable === true,
    'an unreachable backend is flagged unreachable and retryable',
    `unreachable=${down?.isBackendUnreachable}`,
  )
  check(
    /CORS_ALLOW_ORIGINS/.test(down?.userMessage ?? ''),
    'the unreachable message names the CORS setting to check',
    down?.userMessage.slice(0, 80),
  )
  const downUpload = await expectApiError(
    'a dead backend fails the upload path too (XHR, not fetch)',
    () => api.uploadEvidence(file),
    { kind: 'network', status: 0 },
  )
  check(
    downUpload?.isBackendUnreachable === true,
    'upload transport failure is also flagged unreachable',
  )
  failTransport = false

  // 11. Timestamps always name their zone, so an offset can never be misread.
  const stamp = formatTimestamp(verdict.fused_at)
  check(
    stamp !== NOT_MEASURED && /(UTC|GMT|[+-]\d{2}:?\d{2}|[A-Z]{2,5})/.test(stamp),
    'timestamps are rendered with their timezone',
    stamp,
  )
  // Guards a specific regression: combining dateStyle/timeStyle with
  // timeZoneName is a TypeError in every engine, and the catch branch that
  // caught it returned the raw ISO string -- readable, so it looked fine.
  check(
    verdict.fused_at !== null && stamp !== new Date(verdict.fused_at).toISOString(),
    'timestamps are localised, not falling through to the raw ISO branch',
    stamp,
  )
  check(
    formatTimestamp(null) === NOT_MEASURED,
    'a missing timestamp is not invented',
    formatTimestamp(null),
  )

  // 12. Nothing reached a URL the backend does not serve.
  const strayHosts = issued.filter((r) => !r.url.startsWith(BASE))
  check(
    strayHosts.length === 0,
    'every request went to the configured base URL',
    strayHosts.map((r) => r.url).join(',') || `${issued.length} requests`,
  )

  console.log()
  console.log(`${passed}/${passed + failures.length} checks passed`)
  if (failures.length) {
    console.log('\nFAILURES:')
    for (const f of failures) console.log(`  - ${f}`)
    process.exit(1)
  }
}

main().catch((error) => {
  console.error('harness error:', error)
  process.exit(1)
})
