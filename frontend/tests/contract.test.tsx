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
 * The deletion section additionally renders the real components with
 * react-dom/server, so what is asserted is the markup the operator would see
 * rather than a description of it. Effects do not run in a static render, which
 * costs nothing here: the delete dialog's effects are the focus trap and the
 * scroll lock, neither of which decides anything.
 *
 * Run with:
 *   npm run verify:contract
 */

import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
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
import {
  formatScore,
  formatTimestamp,
  formatTimestampShort,
  NOT_MEASURED,
  orPlaceholder,
} from '../src/lib/format'
import {
  askToDelete,
  beginDeletion,
  canConfirm,
  deletionSummary,
  dismissDeletion,
  IDLE_DELETION,
  isDeleting,
  removeCase,
  runCaseDeletion,
  typeConfirmation,
  type CaseDeletionState,
} from '../src/lib/casedelete'
import { CaseDeleteDialog, DeleteCaseButton } from '../src/components/CaseDelete'
import { ScreenCaseDetail } from '../src/screens/ScreenCaseDetail'
import type { Investigation, Slice } from '../src/state/useInvestigation'
import type {
  AnalysisResponse,
  CaseDeleteResult,
  CaseRecord,
  Signal,
  Verdict,
} from '../src/api/types'

// --- Recordings --------------------------------------------------------------

interface Recording {
  status: number
  headers: Record<string, string>
  json: unknown
  bytes_len: number | null
}

interface Recordings {
  /** Ids, digests and whole records the verifier captured while it ran. */
  context: Record<string, unknown>
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

// --- Deletion helpers --------------------------------------------------------

/**
 * Narrow a recorded payload to the type the client declares for it.
 *
 * Recordings are JSON, so they arrive as `unknown`. Rather than assert the shape
 * blindly, the fields the delete UI actually reads are checked first: if the
 * backend renames one, this throws here instead of quietly rendering
 * `undefined` somewhere in a confirmation dialog.
 */
function asCaseRecord(value: unknown, label: string): CaseRecord {
  const record = value as Partial<CaseRecord> | null
  if (
    !record ||
    typeof record.case_id !== 'string' ||
    typeof record.case_number !== 'string' ||
    typeof record.status !== 'string' ||
    typeof record.evidence_count !== 'number'
  ) {
    throw new Error(`${label} is not a CaseRecord: ${JSON.stringify(value).slice(0, 160)}`)
  }
  return record as CaseRecord
}

/** The same, for the delete response. Checks the nested blocks the notice reads. */
function asDeleteResult(value: unknown, label: string): CaseDeleteResult {
  const result = value as Partial<CaseDeleteResult> | null
  if (
    !result ||
    typeof result.case_id !== 'string' ||
    typeof result.deleted_at !== 'string' ||
    !result.deleted ||
    !result.storage ||
    !result.index ||
    !result.audit ||
    !Array.isArray(result.warnings)
  ) {
    throw new Error(`${label} is not a CaseDeleteResult: ${JSON.stringify(value).slice(0, 160)}`)
  }
  return result as CaseDeleteResult
}

/** The opening tag of the button carrying `label`, so attributes can be read. */
function buttonWith(html: string, label: string): string {
  const at = html.indexOf(label)
  if (at < 0) return ''
  const open = html.lastIndexOf('<button', at)
  if (open < 0) return ''
  return html.slice(open, html.indexOf('>', open) + 1)
}

/** The real dialog, rendered from one real state. Handlers are not exercised here. */
function renderDialog(state: CaseDeletionState): string {
  const noop = () => {}
  return renderToStaticMarkup(
    <CaseDeleteDialog state={state} onTyped={noop} onCancel={noop} onConfirm={noop} />,
  )
}

/**
 * An `Investigation` holding one real case and nothing else.
 *
 * ScreenCaseDetail is rendered for the redirect and delete-control checks, and it
 * takes the whole investigation state. Every slice here is the idle value the
 * real hook starts from; the only populated field is `caseRecord`, which is the
 * case the backend returned.
 */
function investigationFor(caseRecord: CaseRecord): Investigation {
  const idle = <T,>(): Slice<T> => ({ phase: 'idle', data: null, error: null })
  const noop = () => {}
  return {
    health: 'up',
    healthError: null,
    recheckHealth: noop,
    caseRecord,
    evidence: [],
    selectCase: noop,
    caseLoad: idle(),
    upload: idle(),
    uploadProgress: null,
    uploadFile: noop,
    reset: noop,
    analysis: idle(),
    runAnalysis: noop,
    metadata: idle(),
    loadMetadata: noop,
    propagation: idle(),
    loadPropagation: noop,
    auditVerification: idle(),
    verifyAudit: noop,
    report: idle(),
    generateReport: noop,
  }
}

// --- Tests -------------------------------------------------------------------

/**
 * Case deletion, end to end through the real components and the real client.
 *
 * The data is the verifier's own destructive run: the case record it created and
 * filled with two exhibits, the DELETE response it received, the queue as the
 * backend listed it either side of that delete, and the 404 it got when it tried
 * to delete the same case twice. Nothing here is written by hand.
 *
 * What is not covered: the three `useCallback`s in useCaseDeletion that bind this
 * flow to React's setState. Every rule it applies lives in lib/casedelete and is
 * driven directly below, with the same `deleteCase` the hook supplies.
 */
async function verifyCaseDeletion(): Promise<void> {
  const target = asCaseRecord(RECORDINGS.context.deleted_case, 'context.deleted_case')
  const path = `/api/cases/${target.case_id}`
  const recorded = RECORDINGS.responses[`DELETE ${path}`]
  const refused = RECORDINGS.responses[`DELETE ${path}#repeat`]
  if (!recorded || !refused) throw new Error(`no DELETE recordings for ${path}`)

  // The queue as the backend served it before and after that same delete.
  nextMarker = '#beforedelete'
  const queueBefore = await api.listCases()
  nextMarker = '#afterdelete'
  const queueAfter = await api.listCases()
  const other = queueAfter.cases.find((entry) => entry.case_id !== target.case_id) ?? null

  // D1. The control renders from the real case record, on both surfaces.
  const asked: CaseRecord[] = []
  const control = DeleteCaseButton({ target, onClick: (row) => asked.push(row) }) as ReactElement<{
    onClick: (event: { stopPropagation: () => void }) => void
  }>
  let stopped = false
  control.props.onClick({
    stopPropagation: () => {
      stopped = true
    },
  })
  check(
    asked.length === 1 && asked[0] === target && stopped,
    'the delete control asks about its own row, and does not open the case underneath',
    `asked=${asked.map((c) => c.case_number).join(',') || 'none'} stopPropagation=${stopped}`,
  )
  check(
    target.case_number === String(RECORDINGS.context.deleted_case_number) &&
      /^PRAMAAN-\d{8}-\d{4}$/.test(target.case_number),
    'the case number under test is the one the backend issued',
    target.case_number,
  )
  const controlHtml = renderToStaticMarkup(<DeleteCaseButton target={target} onClick={() => {}} />)
  check(
    controlHtml.includes(`aria-label="Delete case ${target.case_number}"`),
    "the control's accessible name carries that case number rather than a bare 'Delete'",
    controlHtml,
  )
  const dossier = renderToStaticMarkup(
    <ScreenCaseDetail
      caseId={target.case_id}
      investigation={investigationFor(target)}
      onNavigate={() => {}}
    />,
  )
  check(
    dossier.includes(`aria-label="Delete case ${target.case_number}"`) &&
      dossier.includes('>Delete Case<'),
    'the case dossier offers the delete for the case it is showing',
    `${dossier.length} chars of markup`,
  )

  // D2. Confirmation: the dialog states the consequence and stays locked until
  //     the operator types the case number the backend issued.
  const opened = askToDelete(target)
  const openedHtml = renderDialog(opened)
  check(
    openedHtml.includes(`Delete case #${target.case_number}?`) &&
      openedHtml.includes(String(target.title ?? '')) &&
      openedHtml.includes(`${target.evidence_count} item`) &&
      openedHtml.includes(target.status.replace(/_/g, ' ').toUpperCase()),
    'the dialog shows the case number, title, status and evidence count it was handed',
    `#${target.case_number} / ${target.title} / ${target.status} / ${target.evidence_count}`,
  )
  check(
    openedHtml.includes('permanent and cannot be undone') &&
      openedHtml.includes('no archive to restore from'),
    'the dialog states that the action is permanent',
  )
  check(
    buttonWith(openedHtml, 'Delete permanently').includes('disabled'),
    'the destructive button starts disabled',
    buttonWith(openedHtml, 'Delete permanently'),
  )
  // A real case number that is not this one: the case the backend still lists.
  const notThisCase = other ? other.case_number : target.case_number.slice(0, -1)
  const wrong = typeConfirmation(opened, notThisCase)
  const wrongHtml = renderDialog(wrong)
  check(
    !canConfirm(wrong) && buttonWith(wrongHtml, 'Delete permanently').includes('disabled'),
    "another case's number does not unlock this delete",
    notThisCase,
  )
  check(
    wrongHtml.includes('aria-invalid="true"') &&
      wrongHtml.includes(`Expected ${target.case_number}`),
    'a mismatch is marked invalid and names the number expected',
  )
  // As displayed on screen: with the leading '#', in any case.
  const confirmed = typeConfirmation(opened, `#${target.case_number.toLowerCase()}`)
  const confirmedHtml = renderDialog(confirmed)
  check(
    canConfirm(confirmed) && !buttonWith(confirmedHtml, 'Delete permanently').includes('disabled'),
    'typing the case number as displayed unlocks the delete',
    confirmed.typed,
  )

  // D3. The DELETE is really sent -- and only when confirmed.
  const emitted: CaseDeletionState[] = []
  const emit = (next: CaseDeletionState) => emitted.push(next)
  let returned: CaseDeleteResult | null = null
  // The transport the hook injects, unchanged.
  const deleteCase = async (caseId: string): Promise<CaseDeleteResult> => {
    returned = await api.deleteCase(caseId)
    return returned
  }

  const quiet = issued.length
  const ignored = await runCaseDeletion(opened, { deleteCase, emit })
  check(
    issued.length === quiet && ignored === opened && emitted.length === 0,
    'an unconfirmed dialog sends no request and changes no state',
    `${issued.length - quiet} requests, ${emitted.length} transitions`,
  )

  const navigated: string[] = []
  const final = await runCaseDeletion(confirmed, {
    deleteCase,
    emit,
    // Exactly what ScreenCaseDetail hands the hook: onNavigate('cases').
    onDeleted: () => navigated.push('cases'),
  })
  expectRequest('DELETE', path, 'confirming sends DELETE /api/cases/{case_id}')
  check(
    final.phase === 'deleted' && final.result !== null,
    'the confirmed flow ends in the deleted phase',
    final.phase,
  )
  check(
    final.result === returned,
    'the state holds the object the client parsed, not one the UI assembled',
  )
  check(
    JSON.stringify(final.result) === JSON.stringify(recorded.json),
    "every field the notice can show is the backend's, field for field",
    `${JSON.stringify(final.result).length} chars`,
  )

  // D4. The queue after a success: the row the UI drops is the row the backend
  //     dropped. Both lists here are real -- the verifier recorded the queue
  //     immediately before and immediately after this same delete.
  const ids = (cases: CaseRecord[]) =>
    cases
      .map((entry) => entry.case_id)
      .sort()
      .join(',')
  check(
    queueBefore.cases.some((entry) => entry.case_id === target.case_id),
    'the recorded queue held the case before the delete',
    `${queueBefore.count} cases`,
  )
  const pruned = removeCase(queueBefore.cases, target.case_id)
  check(
    ids(pruned) === ids(queueAfter.cases),
    "the queue's local update matches the list the backend now serves",
    `${pruned.length} kept locally vs ${queueAfter.cases.length} listed`,
  )
  check(
    pruned.length === queueBefore.cases.length - 1 &&
      queueBefore.cases.every(
        (entry) => entry.case_id === target.case_id || pruned.includes(entry),
      ),
    'exactly one row is removed and unrelated cases are left untouched',
    `${queueBefore.cases.length} -> ${pruned.length}`,
  )

  // D5. From the dossier, a confirmed delete leaves the screen. The screen has
  //     no payload channel back to the queue, so the queue re-lists from the
  //     backend -- the case's absence there is what confirms the delete.
  check(
    navigated.length === 1 && navigated[0] === 'cases',
    'a confirmed delete from the dossier navigates to the case queue, once',
    navigated.join(',') || 'no navigation',
  )

  // D6. A refusal is shown as the backend worded it. The recording is the real
  //     404 from deleting the same case a second time.
  const refusedStates: CaseDeletionState[] = []
  const refusedNav: string[] = []
  nextMarker = '#repeat'
  const failed = await runCaseDeletion(confirmed, {
    deleteCase: (caseId) => api.deleteCase(caseId),
    emit: (next) => refusedStates.push(next),
    onDeleted: () => refusedNav.push('cases'),
  })
  const error = failed.error
  const backendMessage =
    (refused.json as { error?: { message?: string } } | null)?.error?.message ?? ''
  check(
    failed.phase === 'failed' && failed.result === null,
    'a refused delete lands in the failed phase with no result',
    failed.phase,
  )
  check(
    error instanceof ApiError && error.status === 404 && error.kind === 'not_found',
    'the refusal arrives as the ApiError the client mapped from the real response',
    error instanceof ApiError ? `kind=${error.kind} status=${error.status}` : String(error),
  )
  check(
    error instanceof ApiError && error.message === backendMessage && backendMessage.length > 0,
    "the error carries the backend's own sentence, unrewritten",
    backendMessage,
  )
  const failedHtml = renderDialog(failed)
  check(
    failedHtml.includes(backendMessage) && failedHtml.includes('Case deletion failed'),
    'the dialog renders that sentence rather than a generic apology',
    failedHtml.includes(backendMessage) ? 'shown' : 'missing from markup',
  )
  check(
    failedHtml.includes('HTTP 404') &&
      failedHtml.includes(String(error instanceof ApiError ? error.requestId : '')),
    'the failure shows its status and request id, so it can be traced in the backend log',
    error instanceof ApiError ? `HTTP ${error.status} request ${error.requestId}` : 'not an ApiError',
  )
  check(
    failedHtml.includes('Nothing was deleted') && failedHtml.includes('still in the queue'),
    'the dialog says plainly that nothing was deleted',
  )
  check(
    refusedNav.length === 0 && !refusedStates.some((state) => state.phase === 'deleted'),
    'a refused delete neither navigates away nor passes through a success state',
    refusedStates.map((state) => state.phase).join(' -> '),
  )
  check(
    canConfirm(failed),
    'the failed dialog can be retried without retyping the case number',
    `phase=${failed.phase} typed=${failed.typed}`,
  )

  // D7. The loading state. The transport promise is held open so the in-flight
  //     dialog can be rendered as the operator would see it; a replayed response
  //     answers instantly, which is the one thing a real backend never does.
  let release: (result: CaseDeleteResult) => void = () => {}
  const held = new Promise<CaseDeleteResult>((resolve) => {
    release = resolve
  })
  const busyStates: CaseDeletionState[] = []
  const busyNav: string[] = []
  const inFlight = runCaseDeletion(confirmed, {
    deleteCase: () => held,
    emit: (next) => busyStates.push(next),
    onDeleted: () => busyNav.push('cases'),
  })
  const busy = busyStates[busyStates.length - 1] ?? IDLE_DELETION
  check(
    busy.phase === 'deleting' && isDeleting(busy),
    'the request in flight is published as the deleting phase',
    busy.phase,
  )
  const busyHtml = renderDialog(busy)
  check(
    buttonWith(busyHtml, 'Deleting…').includes('disabled') && busyHtml.includes('aria-busy="true"'),
    'the destructive button shows its spinner and cannot be pressed twice',
    buttonWith(busyHtml, 'Deleting…'),
  )
  check(
    buttonWith(busyHtml, 'Cancel').includes('disabled') &&
      /<input[^>]*disabled/.test(busyHtml),
    'cancel and the confirmation field are locked while the delete is with the backend',
  )
  check(
    dismissDeletion(busy) === busy && typeConfirmation(busy, 'x') === busy,
    'the dialog cannot be dismissed or retyped once the delete is away',
  )
  check(
    busyNav.length === 0 && !busyStates.some((state) => state.phase === 'deleted'),
    'nothing is reported while the backend has not answered',
    busyStates.map((state) => state.phase).join(' -> '),
  )
  release(asDeleteResult(recorded.json, `DELETE ${path}`))
  const settled = await inFlight
  check(
    settled.phase === 'deleted' &&
      busyStates.map((state) => state.phase).join(' -> ') === 'deleting -> deleted',
    'the busy state resolves into the outcome the backend returned',
    busyStates.map((state) => state.phase).join(' -> '),
  )

  // D8. No fabricated success, on any path.
  failTransport = true
  const deadStates: CaseDeletionState[] = []
  const deadNav: string[] = []
  const unreachable = await runCaseDeletion(confirmed, {
    deleteCase: (caseId) => api.deleteCase(caseId),
    emit: (next) => deadStates.push(next),
    onDeleted: () => deadNav.push('cases'),
  })
  failTransport = false
  check(
    unreachable.phase === 'failed' &&
      unreachable.result === null &&
      !deadStates.some((state) => state.phase === 'deleted'),
    'an unreachable backend never yields a deleted state',
    deadStates.map((state) => state.phase).join(' -> '),
  )
  check(
    unreachable.error instanceof ApiError &&
      unreachable.error.kind === 'network' &&
      unreachable.error.isBackendUnreachable,
    'the transport failure is reported as unreachable, not as a completed delete',
    unreachable.error instanceof ApiError ? unreachable.error.kind : String(unreachable.error),
  )
  check(
    deadNav.length === 0 && renderDialog(unreachable).includes('Nothing was deleted'),
    'the row is not dropped and the dialog says nothing was deleted',
  )

  // A stale click on a finished delete must not re-send, and must not produce a
  // second success the backend never saw.
  const settledRequests = issued.length
  const replayed = await runCaseDeletion(final, { deleteCase, emit })
  check(
    issued.length === settledRequests && replayed === final,
    'a completed delete cannot be replayed from its own state',
    `${issued.length - settledRequests} further requests`,
  )
  // Nor can the in-flight state be pressed again into a second request.
  const inFlightRequests = issued.length
  await runCaseDeletion(beginDeletion(confirmed), { deleteCase, emit })
  check(
    issued.length === inFlightRequests,
    'a second press while the delete is in flight sends nothing',
    `${issued.length - inFlightRequests} further requests`,
  )

  // And every figure the success notice shows traces to the response.
  const result = asDeleteResult(recorded.json, `DELETE ${path}`)
  const summary = deletionSummary(result)
  const head = summary[0] ?? ''
  check(
    head.includes(`${result.deleted.evidence} evidence record`) &&
      head.includes(`${result.deleted.analysis_results} analysis result`) &&
      head.includes(`${result.deleted.matches} match`) &&
      head.includes(`${result.deleted.reports} report`),
    'the counts in the notice are the counts the backend reported',
    head,
  )
  // The directory is nullable: a case whose upload never landed has none, and
  // the notice omits the line rather than naming an empty path.
  const caseDirectory = result.storage.case_directory
  check(
    summary.some((line) =>
      line.includes(`${result.storage.evidence_files_removed} stored file`),
    ) && (caseDirectory === null || summary.some((line) => line.includes(caseDirectory))),
    'the notice reports the files and directory the backend said it removed',
    `${result.storage.evidence_files_removed} files, dir=${orPlaceholder(caseDirectory)}`,
  )
  const auditLine = summary[summary.length - 1] ?? ''
  check(
    auditLine.includes(result.audit.event) &&
      auditLine.includes(`#${result.audit.seq}`) &&
      auditLine.includes(String(result.audit.case_rows_retained)),
    'the notice names the retained audit entry and its position in the chain',
    auditLine,
  )
  const payload = JSON.stringify(recorded.json)
  const invented = summary
    .flatMap((line) => [...line.matchAll(/\d+/g)].map((match) => match[0]))
    .filter((digits) => !payload.includes(digits))
  check(
    invented.length === 0,
    'no number in the notice is absent from the response it was built from',
    invented.join(',') || `${summary.length} lines checked`,
  )
  // "0 matchs" is what a missing plural form looks like, and an operator reading
  // the one irreversible confirmation in the console should not be shown that.
  // None of the nouns this notice counts pluralises to -chs/-shs/-ss/-xs, so a
  // word that ends that way came from the formatter, not from the backend. Path
  // segments are skipped: those are the backend's strings, not prose.
  const misspelt = summary
    .flatMap((line) => line.split(/\s+/))
    .filter((word) => !word.includes('/') && /[a-z](?:ch|sh|s|x)s[.,;]?$/i.test(word))
  check(
    misspelt.length === 0,
    'the notice pluralises every noun it counts',
    misspelt.join(',') || `${summary.length} lines checked`,
  )
  check(
    formatTimestampShort(result.deleted_at) !== NOT_MEASURED &&
      result.deleted_at === String((recorded.json as { deleted_at?: unknown }).deleted_at),
    "the notice is timestamped from the backend's deleted_at, not a local clock",
    formatTimestampShort(result.deleted_at),
  )
}

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
    uploaded.evidence.sha256 === String(RECORDINGS.context.sha256),
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

  // 12. Case deletion -- the destructive path, through the real components.
  await verifyCaseDeletion()

  // 13. Nothing reached a URL the backend does not serve.
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
