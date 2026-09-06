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
import { api, ApiError, setAuthToken } from '../src/api'
import {
  barGeometry,
  coverageLine,
  exclusionSummary,
  isExcluded,
  mediaAwareSummary,
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
import { AnalystOfRecord, Screen1Intake } from '../src/screens/Screen1Intake'
import { beginNewCase, createGenerationGate } from '../src/lib/newcase'
import { AnalysisEntryState, Screen2Analysis } from '../src/screens/Screen2Analysis'
import {
  CASE_WORKFLOW_ROUTES,
  CASE_WORKFLOW_STEPS,
  CaseContextBar,
  CaseWorkflowStepper,
  caseWorkflowDone,
} from '../src/components/CaseWorkflowStepper'
import type { RoutePath } from '../src/lib/router'
import type { Investigation, Slice } from '../src/state/useInvestigation'
import type {
  AnalysisResponse,
  AuditVerification,
  AuthUser,
  CaseDeleteResult,
  CaseRecord,
  Evidence,
  PropagationResponse,
  ReportResponse,
  Signal,
  StoredVerdictResponse,
  UploadResponse,
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
const issued: Array<{ method: string; url: string; body: unknown; headers: Record<string, string> }> = []

/** Header lookup that does not care how the client cased the name. */
function headerOf(entry: { headers: Record<string, string> }, name: string): string | undefined {
  const wanted = name.toLowerCase()
  for (const [key, value] of Object.entries(entry.headers)) {
    if (key.toLowerCase() === wanted) return value
  }
  return undefined
}

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
  issued.push({
    method,
    url,
    body: init?.body ?? null,
    // Captured so the suite can assert the bearer token really is attached: the
    // evidence-bytes and report-PDF routes are authenticated, and the UI reaches
    // them through this transport precisely because a browser-issued <img src>
    // or <a href> cannot carry the header.
    headers: { ...((init?.headers as Record<string, string> | undefined) ?? {}) },
  })

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
    issued.push({ method: this.method, url: this.url, body, headers: { ...this.headers } })

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

/**
 * The canonical case-context row, rendered from one real store.
 *
 * The case number is stated here and nowhere else, so every "which case am I
 * looking at" assertion reads this rather than a screen's own markup.
 */
function contextBar(investigation: Investigation, routePath: RoutePath = 'analysis'): string {
  return renderToStaticMarkup(
    <CaseContextBar
      investigation={investigation}
      routePath={routePath}
      routeCaseId={investigation.caseRecord?.case_id ?? null}
      onNavigate={() => {}}
    />,
  )
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
    clearUpload: noop,
    reset: noop,
    analysis: idle(),
    runAnalysis: noop,
    metadata: idle(),
    loadMetadata: noop,
    propagation: idle(),
    loadPropagation: noop,
    traceProvenance: noop,
    auditVerification: idle(),
    verifyAudit: noop,
    report: idle(),
    generateReport: async () => null,
  }
}

// --- Tests -------------------------------------------------------------------

/**
 * "New Case" starts from a clean slate.
 *
 * Regression for the bug where clicking New Case after ingesting Case A left the
 * shared investigation store populated, so the intake screen rendered Case A's
 * sealed evidence -- its filename, SHA-256, preview and case number -- under a
 * fresh New Case click. A chain-of-custody tool must never show one case's
 * exhibit while the operator believes they are opening another.
 *
 * The store is real (state/useInvestigation) but is exercised here through its
 * two collaborators: `reset`, which clears every case-scoped slice, and
 * `navigate`. `beginNewCase` is what every genuine New Case button now calls,
 * and the intake screen's rendering is asserted from the real component via
 * react-dom/server -- the markup the operator would actually see.
 */
function verifyNewCaseReset(): void {
  // A stale store: Case A has been uploaded, analysed and traced. This is the
  // exact state that used to leak into the next case's intake.
  const uploaded = asCaseRecord(RECORDINGS.responses['POST /api/cases/upload']?.json &&
    (RECORDINGS.responses['POST /api/cases/upload']!.json as { case: unknown }).case, 'upload.case')
  const uploadJson = RECORDINGS.responses['POST /api/cases/upload']!.json as UploadResponse
  const staleFilename = uploadJson.evidence.filename
  const staleSha = uploadJson.evidence.sha256

  const stale: Investigation = {
    ...investigationFor(uploaded),
    upload: { phase: 'ready', data: uploadJson, error: null },
    evidence: [uploadJson.evidence],
    analysis: {
      phase: 'ready',
      data: RECORDINGS.responses[`POST /api/cases/${uploaded.case_id}/analyse`]!.json as AnalysisResponse,
      error: null,
    },
  }

  // 1. Before the fix, intake rendered Case A's dossier straight from this store.
  //    That is the observable symptom, so assert it is present in the stale view
  //    -- otherwise the "after" assertion below would prove nothing.
  const staleHtml = renderToStaticMarkup(
    <Screen1Intake investigation={stale} operator={recordedOperator()} onAnalyse={() => {}} />,
  )
  check(
    staleHtml.includes(staleFilename) && staleHtml.includes(staleSha),
    'a populated store makes intake show that case (this is the state New Case must clear)',
    `filename=${staleHtml.includes(staleFilename)} sha=${staleHtml.includes(staleSha)}`,
  )
  /*
   * The case number is asserted on the canonical context row, not on the intake
   * screen: the screen no longer prints it, because one case number stated in
   * one place is what makes a mid-flight case switch unambiguous. The guarantee
   * being checked is unchanged -- a populated store puts the previous case's
   * number in front of the operator, and that is what New Case must clear -- so
   * the positive control for the "after reset" check below still holds.
   */
  const staleBar = contextBar(stale, 'intake')
  check(
    staleBar.includes(uploaded.case_number),
    'the populated context row carries the active case number',
    uploaded.case_number,
  )
  check(
    !staleHtml.includes(uploaded.case_number),
    'the intake screen does not reprint the case number the context row already states',
    uploaded.case_number,
  )

  // 2. beginNewCase must reset the store *before* it navigates. Order matters:
  //    navigating first would render one frame of intake against the full store.
  const events: string[] = []
  const navPaths: RoutePath[] = []
  let cleared: Investigation = stale
  beginNewCase({
    reset: () => {
      events.push('reset')
      // The real reset() returns every case-scoped slice to idle and empties
      // evidence; model exactly that so the post-reset render is honest.
      cleared = {
        ...stale,
        caseRecord: null,
        evidence: [],
        upload: { phase: 'idle', data: null, error: null },
        analysis: { phase: 'idle', data: null, error: null },
        propagation: { phase: 'idle', data: null, error: null },
        auditVerification: { phase: 'idle', data: null, error: null },
        report: { phase: 'idle', data: null, error: null },
      }
    },
    navigate: (path: RoutePath) => {
      events.push(`navigate:${path}`)
      navPaths.push(path)
    },
  })
  check(
    events.join(' -> ') === 'reset -> navigate:intake',
    'New Case resets the store, then lands on a clean intake route',
    events.join(' -> '),
  )

  // 3. The intake screen rendered against the reset store shows nothing from the
  //    previous case: no filename, no hash, no case number, no verdict.
  const freshHtml = renderToStaticMarkup(
    <Screen1Intake investigation={cleared} operator={recordedOperator()} onAnalyse={() => {}} />,
  )
  check(
    !freshHtml.includes(staleFilename),
    'a reset intake does not show the previous filename',
    staleFilename,
  )
  check(
    !freshHtml.includes(staleSha),
    'a reset intake does not show the previous SHA-256 digest',
  )
  check(
    !freshHtml.includes(uploaded.case_number),
    'a reset intake does not show the previous case number',
    uploaded.case_number,
  )
  /*
   * And neither does the canonical row, which is where the case number lives.
   * This is the assertion the one above used to be: with the store cleared the
   * row must say "no case loaded" rather than keep the previous case's number
   * beside a blank intake form.
   */
  const freshBar = contextBar(cleared, 'intake')
  check(
    !freshBar.includes(uploaded.case_number) && freshBar.includes('No case loaded'),
    'the context row drops the previous case number and says no case is loaded',
    uploaded.case_number,
  )
  check(
    freshHtml.includes('Drop Digital Evidence Here'),
    'a reset intake shows the empty file selector, not a sealed dossier',
  )

  // 4. beginNewCase navigates with the bare route and no params, so the intake
  //    URL carries no stale caseId/evidenceId. A lingering caseId would let App
  //    re-select the old case on the next render, defeating the reset.
  check(
    navPaths.length === 1 && navPaths[0] === 'intake',
    'New Case navigates to intake exactly once, with no route parameters',
    navPaths.join(','),
  )
}

/**
 * The async race: New Case clicked while Case A's upload is still on the wire.
 *
 * Clearing the slices is necessary but not sufficient. The upload's `.then` holds
 * the setters for the slices that were just emptied, so a late 201 would write
 * Case A's evidence into Case B's intake seconds after the screen had correctly
 * gone blank. This drives the real gate `useInvestigation` uses at all eleven of
 * its guard sites, with a genuinely deferred response rather than a replayed one.
 */
async function verifyStaleResponseCannotLand(): Promise<void> {
  const uploadJson = RECORDINGS.responses['POST /api/cases/upload']!.json as UploadResponse
  const gate = createGenerationGate()

  // Case A's upload leaves, and is deliberately held open.
  let deliver: (data: UploadResponse) => void = () => {}
  const inFlight = new Promise<UploadResponse>((resolve) => {
    deliver = resolve
  })
  const genA = gate.snapshot()

  // Whatever the store would have written, recorded rather than applied.
  const writes: string[] = []
  const settle = inFlight.then((data) => {
    if (!gate.accepts(genA)) return
    writes.push(`upload:${data.evidence.filename}`)
  })

  // The operator clicks New Case while that request is outstanding.
  beginNewCase({ reset: () => gate.invalidate(), navigate: () => {} })
  check(
    !gate.accepts(genA),
    "New Case invalidates the generation of the case being left, mid-flight",
  )

  // Case A's response arrives late. It must be dropped, not applied.
  deliver(uploadJson)
  await settle
  check(
    writes.length === 0,
    "a late upload response from the previous case writes nothing into the new intake",
    writes.join(',') || 'no writes',
  )

  // The new case's own upload, issued after the reset, must still land -- the
  // gate has to discriminate, not simply block everything after a reset.
  const genB = gate.snapshot()
  check(
    gate.accepts(genB) && genB !== genA,
    'a request issued after New Case is accepted, under a new generation',
    `genA=${genA} genB=${genB}`,
  )
  const laterWrites: string[] = []
  await Promise.resolve(uploadJson).then((data) => {
    if (!gate.accepts(genB)) return
    laterWrites.push(`upload:${data.evidence.filename}`)
  })
  check(
    laterWrites.length === 1,
    "the new case's own upload response is applied normally",
    laterWrites.join(','),
  )

  // Three consecutive New Case clicks: every generation is distinct, and only
  // the newest is accepted. This is the A -> B -> C walk in the brief.
  const seen = [gate.snapshot()]
  gate.invalidate()
  seen.push(gate.snapshot())
  gate.invalidate()
  seen.push(gate.snapshot())
  check(
    new Set(seen).size === seen.length,
    'three consecutive New Case clicks yield three distinct generations',
    seen.join(','),
  )
  check(
    seen.slice(0, -1).every((g) => !gate.accepts(g)) && gate.accepts(seen[seen.length - 1]!),
    'only the newest generation is accepted; every superseded one is refused',
    seen.join(','),
  )
}

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
      // The compact sequential form the console prints. Historical rows may still
      // carry the older PRAMAAN-YYYYMMDD-NNNN identifier, which is why the
      // backend's sequence only recognises this shape -- but a number issued by
      // the running backend, as this one was, is always the short one.
      /^PRAMAAN-\d{4,}$/.test(target.case_number),
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

/**
 * Case switch regression (A -> B): the shared store must not leak Case A's
 * analysis, provenance, audit or report into Case B.
 *
 * `selectCase` (state/useInvestigation) invalidates the generation gate and
 * clears every derived slice BEFORE fetching Case B, so this drives exactly that
 * sequence against the real gate, with the real recordings as Case A's data and
 * the delete-verification case as Case B. The recordings are real backend
 * payloads: Case A's analysis/audit/report and the record of the case that was
 * created, analysed, reported on and then deleted.
 *
 * What is asserted, in the order the brief requires:
 *   1. Case A loaded: analysis, provenance, audit, report all present.
 *   2. Switch to Case B: selectCase's exact transition sequence runs.
 *   3. Every Case A slice is empty for Case B; a late Case A response cannot
 *      land (the same gate the real hook uses).
 *   4. The Analysis screen, rendered from the post-switch store, shows none of
 *      Case A's forensic content.
 */
async function verifyCaseSwitch(): Promise<void> {
  const caseA = asCaseRecord(RECORDINGS.responses['POST /api/cases/upload']?.json &&
    (RECORDINGS.responses['POST /api/cases/upload']!.json as { case: unknown }).case, 'switch.caseA')
  const caseB = asCaseRecord(RECORDINGS.context.deleted_case, 'switch.caseB')

  // The full Case A payload set, from the recordings the backend produced.
  const analysisA = RECORDINGS.responses[`POST /api/cases/${caseA.case_id}/analyse`]!.json as AnalysisResponse
  const propagationA = RECORDINGS.responses[`GET /api/cases/${caseA.case_id}/propagation`]!.json as PropagationResponse
  const auditA = RECORDINGS.responses[`GET /api/cases/${caseA.case_id}/audit`]!.json as { case_id: string; events: unknown[]; head_hash: string }
  const reportA = RECORDINGS.responses[`POST /api/cases/${caseA.case_id}/report`]!.json as ReportResponse
  const evidenceA = RECORDINGS.responses[`GET /api/cases/${caseA.case_id}/evidence`]!.json as { evidence: Evidence[] }

  // 1. Case A is fully loaded: analysis, provenance, audit, report.
  const loadedA: Investigation = {
    ...investigationFor(caseA),
    evidence: evidenceA.evidence,
    analysis: { phase: 'ready', data: analysisA, error: null },
    propagation: { phase: 'ready', data: propagationA, error: null },
    auditVerification: { phase: 'ready', data: auditA as unknown as AuditVerification, error: null },
    report: { phase: 'ready', data: reportA, error: null },
  }
  const analysisHtmlA = renderToStaticMarkup(
    <Screen2Analysis
      caseId={caseA.case_id}
      investigation={loadedA}
      onNavigate={() => {}}
      onPropagation={() => {}}
    />,
  )
  /*
   * Which case is on screen is stated by the canonical context row, so that is
   * where the switch is observed. The analysis screen is checked too, for the
   * forensic content it does own (digest, verdict, filename) -- but a screen that
   * never prints a case number could not tell a successful switch from a failed
   * one, and asserting the absence of something never rendered would be vacuous.
   */
  const barA = contextBar(loadedA)
  check(
    barA.includes(caseA.case_number),
    'the context row names Case A while Case A is loaded',
    caseA.case_number,
  )
  /*
   * Positive controls for section 4's "disappears after the switch" checks. Each
   * of those asserts an absence, which is only evidence of a working switch if
   * the thing was present beforehand -- otherwise a screen that quietly stopped
   * rendering the digest would make the whole section pass while leaking.
   */
  const shaA = String(RECORDINGS.context.sha256)
  const filenameA = (RECORDINGS.responses['POST /api/cases/upload']!.json as UploadResponse).evidence.filename
  check(
    analysisHtmlA.includes(shaA) || analysisHtmlA.includes(shaA.slice(0, 16)),
    "Case A's digest is on its own analysis screen (control for the switch check)",
    shaA.slice(0, 16),
  )
  check(
    analysisHtmlA.includes(filenameA),
    "Case A's evidence filename is on its own analysis screen (control)",
    filenameA,
  )
  const verdictA: Verdict | null = analysisA.verdict
  check(
    verdictA !== null,
    'Case A analysis arrived with a verdict before the switch',
    verdictA ? verdictA.verdict : 'none',
  )

  // 2. The switch: the exact sequence selectCase performs.
  const gate = createGenerationGate()
  // Case A's derived slices were written under genA.
  const genA = gate.snapshot()
  check(gate.accepts(genA), 'Case A data was accepted under its generation', String(genA))

  // A late response for Case A is still on the wire when the operator switches.
  let deliverPropagationA: () => void = () => {}
  const inFlightPropagation = new Promise<PropagationResponse>((resolve) => {
    deliverPropagationA = () => resolve(propagationA)
  })

  // selectCase: invalidate first, then clear the derived slices. Modelled here
  // exactly as useInvestigation.selectCase does, using the same gate.
  gate.invalidate()
  const cleared: Investigation = {
    ...loadedA,
    caseRecord: caseB,
    evidence: [],
    caseLoad: { phase: 'loading', data: null, error: null },
    upload: { phase: 'idle', data: null, error: null },
    uploadProgress: null,
    analysis: { phase: 'idle', data: null, error: null },
    metadata: { phase: 'idle', data: null, error: null },
    propagation: { phase: 'idle', data: null, error: null },
    auditVerification: { phase: 'idle', data: null, error: null },
    report: { phase: 'idle', data: null, error: null },
  }

  check(
    !gate.accepts(genA),
    'switching to Case B invalidates the generation Case A wrote under',
  )

  // 3. The late Case A propagation response arrives after the switch. The real
  //    hook's `live(gen)` check is what must refuse it.
  const lateWrites: string[] = []
  const settle = inFlightPropagation.then((data) => {
    if (!gate.accepts(genA)) return
    lateWrites.push(`propagation:${data.case_id ?? 'A'}`)
  })
  deliverPropagationA()
  await settle
  check(
    lateWrites.length === 0,
    'a late Case A propagation response writes nothing after the switch',
    lateWrites.join(',') || 'no writes',
  )

  // 4. No Case A content survives in the post-switch store's rendered screen.
  const analysisHtmlB = renderToStaticMarkup(
    <Screen2Analysis
      caseId={caseB.case_id}
      investigation={cleared}
      onNavigate={() => {}}
      onPropagation={() => {}}
    />,
  )
  check(
    !contextBar(cleared).includes(caseA.case_number) &&
      contextBar(cleared).includes(caseB.case_number),
    'the context row names Case B and no longer names Case A after the switch',
    `${caseA.case_number} -> ${caseB.case_number}`,
  )
  check(
    !analysisHtmlB.includes(caseA.case_number),
    'Case A case number appears nowhere on the analysis screen after the switch',
    caseA.case_number,
  )
  check(
    !analysisHtmlB.includes(shaA) && !analysisHtmlB.includes(shaA.slice(0, 16)),
    'Case A SHA-256 disappears from the analysis screen after the switch',
  )
  check(
    !analysisHtmlB.includes(filenameA),
    'Case A evidence filename disappears after the switch',
    filenameA,
  )
  // The verdict Case A received must not render for Case B. The screen renders
  // the band label; the raw token travels in the payload, so check both the
  // token and its label, plus Case A's fused score.
  const bandA = verdictA ? verdictBandLabel(verdictA.verdict) : 'LIKELY MANIPULATED'
  check(
    analysisHtmlA.includes(bandA),
    `Case A's verdict band (${bandA}) is on its own analysis screen (control)`,
  )
  check(
    !analysisHtmlB.includes(bandA),
    `Case A's verdict band (${bandA}) does not render for Case B`,
  )
  const fusedA = verdictA ? verdictA.manipulation_score : null
  if (fusedA !== null) {
    check(
      !analysisHtmlB.includes(formatScore(fusedA, 4)) && !analysisHtmlB.includes(fusedA.toFixed(4)),
      "Case A's fused score does not render for Case B",
      formatScore(fusedA, 4),
    )
  }
  /*
   * What Case B's screen may say before the case file has answered.
   *
   * The analysis slice is idle after a switch -- it only ever holds a pipeline run
   * from this session -- so the screen's entry state is driven by a read-only
   * `GET /verdict`. This render has no effects (static markup), which is exactly
   * the moment before that read resolves, and the guarantee under test is that the
   * screen makes NO claim about Case B in that moment.
   *
   * "NOT YET ANALYSED" is the specific thing it must not say. Case B may well carry
   * a stored verdict; announcing it as unanalysed because this client has not
   * looked yet would report the absence of a fetch as a forensic fact. Nor may any
   * verdict band appear: there is nothing yet to base one on.
   */
  check(
    !analysisHtmlB.includes('NOT YET ANALYSED') && !analysisHtmlB.includes('ANALYSIS ON RECORD'),
    'before the case file answers, Case B is called neither analysed nor unanalysed',
    'no premature claim',
  )
  for (const band of ['LIKELY MANIPULATED', 'LIKELY AUTHENTIC', 'INCONCLUSIVE'] as const) {
    check(
      !analysisHtmlB.includes(band),
      `no verdict band ("${band}") is asserted for Case B before its verdict is read`,
    )
  }

  // Case B's own data lands normally under the new generation.
  const genB = gate.snapshot()
  check(gate.accepts(genB) && genB !== genA, 'Case B operates under a new generation', `${genA}->${genB}`)
}

/**
 * Case switch regression, part 2: delete Case A -> store reset -> queue
 * refreshed -> Case A cannot reappear from stale client state.
 *
 * Uses the verifier's destructive run: the deleted case's record, the real
 * before/after queues and the real 404 its id now returns.
 */
async function verifyDeleteResetsState(): Promise<void> {
  const target = asCaseRecord(RECORDINGS.context.deleted_case, 'delete-reset.target')

  nextMarker = '#beforedelete'
  const queueBefore = await api.listCases()
  nextMarker = '#afterdelete'
  const queueAfter = await api.listCases()
  check(
    queueBefore.cases.some((entry) => entry.case_id === target.case_id) &&
      !queueAfter.cases.some((entry) => entry.case_id === target.case_id),
    'the deleted case is in the recorded queue before the delete and gone after',
    `${queueBefore.count} -> ${queueAfter.count}`,
  )

  // The store held the deleted case as the active case. ScreenCases' onDeleted
  // resets the shared investigation store when the deleted case is the loaded
  // one; ScreenCaseDetail does the same from the dossier. Model the dossier's
  // transition: reset -> navigate('cases').
  const events: string[] = []
  const gate = createGenerationGate()
  const genA = gate.snapshot()
  gate.invalidate()

  const loadedCaseBefore = target
  const resetStore: Investigation = {
    ...investigationFor(loadedCaseBefore),
    caseRecord: null,
    evidence: [],
    caseLoad: { phase: 'idle', data: null, error: null },
    upload: { phase: 'idle', data: null, error: null },
    uploadProgress: null,
    analysis: { phase: 'idle', data: null, error: null },
    metadata: { phase: 'idle', data: null, error: null },
    propagation: { phase: 'idle', data: null, error: null },
    auditVerification: { phase: 'idle', data: null, error: null },
    report: { phase: 'idle', data: null, error: null },
  }
  events.push('reset')

  // A stale in-flight response for the deleted case must not repopulate.
  const lateWrites: string[] = []
  await Promise.resolve(target).then((data) => {
    if (!gate.accepts(genA)) return
    lateWrites.push(`caseRecord:${data.case_id}`)
  })
  check(
    lateWrites.length === 0 && !gate.accepts(genA),
    'a response issued for the deleted case cannot repopulate the reset store',
    lateWrites.join(',') || 'no writes',
  )

  events.push('navigate:cases')
  check(
    events.join(' -> ') === 'reset -> navigate:cases',
    'delete-then-reset-then-navigate happens in that order',
    events.join(' -> '),
  )

  // The queue the UI shows after the delete is the backend's own list: the
  // deleted case cannot reappear from client state because the row removal is
  // checked against the real served list.
  const pruned = removeCase(queueBefore.cases, target.case_id)
  check(
    !pruned.some((entry) => entry.case_id === target.case_id) &&
      pruned.length === queueAfter.cases.length,
    'the local queue update drops the deleted case and matches the served list',
    `${pruned.length} local vs ${queueAfter.cases.length} served`,
  )

  // Navigating back to the deleted case: the backend's real 404 is what the
  // client receives, and the store did not resurrect the record.
  nextMarker = '#deleted'
  const err = await expectApiError(
    'navigating to the deleted case id returns the backend 404',
    () => api.getCase(target.case_id),
    { kind: 'not_found', status: 404 },
  )
  check(
    err instanceof ApiError && err.message.length > 0,
    'the 404 carries the backend sentence, not a silent client-side resurrection',
    err?.message.slice(0, 50),
  )
  // And the reset store renders no dossier for the deleted case.
  const dossierHtml = renderToStaticMarkup(
    <ScreenCaseDetail caseId={target.case_id} investigation={resetStore} onNavigate={() => {}} />,
  )
  check(
    !dossierHtml.includes(target.case_number) || dossierHtml.includes('Case not found') || dossierHtml.includes('not found'),
    'the reset store does not render the deleted case dossier from stale state',
    `${dossierHtml.includes(target.case_number) ? 'number still present' : 'number gone'}`,
  )
}

/**
 * The signed-in operator, as the backend reported them.
 *
 * Not a fixture: this is the `GET /api/auth/me` payload the verifier captured
 * while signed in as the seeded account. The intake screen takes it as a prop
 * and displays it read-only, so asserting against the recorded value is what
 * proves the analyst shown is the analyst the backend will stamp -- a
 * hand-written object here would prove only that the component renders a string.
 */
function recordedOperator(): AuthUser {
  const operator = RECORDINGS.context.operator as Partial<AuthUser> | undefined
  if (!operator || typeof operator.display_name !== 'string' || typeof operator.username !== 'string') {
    throw new Error(
      'recordings carry no operator: re-run scripts/verify_integration.py, which signs in',
    )
  }
  return operator as AuthUser
}

/**
 * Evidence intake: the analyst is displayed, never entered, and the seal is only
 * ever reported after the backend confirms it.
 *
 * Rendered with react-dom/server against the real recordings, so what is
 * asserted is the markup an operator would see. Effects do not run in a static
 * render, which costs nothing here: intake's effects compute the pre-flight
 * digest of a selected file, and no file can be selected in a static render --
 * so this covers the empty form and the sealed dossier, the two states that are
 * reachable from store data alone.
 */
async function verifyIntakeContract(): Promise<void> {
  const operator = recordedOperator()
  const uploadJson = RECORDINGS.responses['POST /api/cases/upload']!.json as UploadResponse
  const empty: Investigation = investigationFor(null as unknown as CaseRecord)

  // A1. The analyst block names the signed-in operator, from the session. It is
  //     rendered directly because the form around it only exists once a file has
  //     been chosen, which a static render cannot do.
  const analyst = renderToStaticMarkup(
    <AnalystOfRecord operator={operator} note="Recorded as the examiner on this case." />,
  )
  check(
    analyst.includes(operator.display_name) && analyst.includes(operator.role),
    'intake shows the signed-in operator as the analyst',
    `${operator.display_name} / ${operator.role}`,
  )
  check(
    analyst.includes(`signed in as ${operator.username}`),
    'the analyst block says which account it came from',
    operator.username,
  )
  check(
    !/<input|<textarea|<select|contenteditable/i.test(analyst),
    'the analyst block contains no editable control',
  )

  // A2. The empty form offers no way to type an identity either. This is the
  //     whole point of the field: an examiner name that can be typed is a name
  //     that can be wrong, and the backend would ignore it regardless.
  const form = renderToStaticMarkup(
    <Screen1Intake
      investigation={{ ...empty, caseRecord: null }}
      operator={operator}
      onAnalyse={() => {}}
    />,
  )
  check(
    !/<input[^>]*id="intake-examiner"/.test(form) && !/name="examiner"/.test(form),
    'intake has no examiner input to type an identity into',
  )
  check(
    !/Forensic Team|>Analyst<|Admin/.test(form),
    'no placeholder analyst name is rendered anywhere on the form',
  )

  // A3. Nothing claims a seal before one exists. "Sealed" and "Ready for
  //     Analysis" are stepper labels; neither may read as done on an empty form.
  check(
    !form.includes('step--done'),
    'no workflow step is marked done on an empty intake form',
  )
  check(
    !form.includes(uploadJson.evidence.sha256),
    'the empty form shows no stored digest',
  )

  // B. The sealed dossier, from the real upload response.
  const sealed: Investigation = {
    ...investigationFor(uploadJson.case),
    upload: { phase: 'ready', data: uploadJson, error: null },
    evidence: [uploadJson.evidence],
  }
  const dossier = renderToStaticMarkup(
    <Screen1Intake investigation={sealed} operator={operator} onAnalyse={() => {}} />,
  )
  /*
   * The case number the sealed exhibit belongs to is stated by the canonical
   * context row above the screen, in the compact PRAMAAN-#### form the backend
   * issued -- not reprinted by the dossier from its own copy of the case row.
   * Both halves are asserted: the operator must be able to read the case number
   * while looking at the seal, and must not be shown two of them.
   */
  const sealedBar = contextBar(sealed, 'intake')
  check(
    sealedBar.includes(uploadJson.case.case_number) &&
      /^PRAMAAN-\d{4,}$/.test(uploadJson.case.case_number),
    'the context row above the sealed dossier prints the compact case number the backend issued',
    uploadJson.case.case_number,
  )
  check(
    !dossier.includes(uploadJson.case.case_number),
    'the sealed dossier does not restate the case number the context row already carries',
    uploadJson.case.case_number,
  )
  check(
    dossier.includes(uploadJson.evidence.sha256),
    'the sealed dossier prints the digest the backend stored',
    uploadJson.evidence.sha256.slice(0, 16),
  )
  check(
    dossier.includes(String(uploadJson.case.examiner)),
    'the sealed dossier names the analyst of record from the case row',
    String(uploadJson.case.examiner),
  )
  check(
    uploadJson.case.examiner === operator.display_name,
    'the analyst the form displayed is the examiner the backend recorded',
    `${operator.display_name} -> ${uploadJson.case.examiner}`,
  )
  const acquisition = uploadJson.evidence.acquisition_context
  check(
    typeof acquisition === 'string' && acquisition.length > 0 && dossier.includes(acquisition),
    'the acquisition context is read back from the evidence row, not the form',
    String(acquisition),
  )

  // C. Refusals carry the backend's own sentence (spec: do not swallow errors,
  //    do not display fake success). Each ApiError below is produced by the real
  //    client from the real recorded response, not constructed here.
  const probe = new File([new Uint8Array([0xff, 0xd8, 0xff, 0x00])], 'probe.jpg', {
    type: 'image/jpeg',
  })
  const refusals: Array<{ marker: string; kind: string; status: number; needle: string }> = [
    { marker: '#notitle', kind: 'validation', status: 422, needle: 'title' },
    { marker: '#nodescription', kind: 'validation', status: 422, needle: 'description' },
    { marker: '#unauthenticated', kind: 'unknown', status: 401, needle: 'sign in' },
  ]
  for (const refusal of refusals) {
    nextMarker = refusal.marker
    const error = await expectApiError(
      `intake ${refusal.marker} maps to ${refusal.status}`,
      () => api.uploadEvidence(probe, { title: 'x', description: 'y' }),
      { kind: refusal.kind, status: refusal.status },
    )
    check(
      (error?.userMessage.toLowerCase() ?? '').includes(refusal.needle),
      `a ${refusal.status} refusal is shown in the backend's words (${refusal.needle})`,
      error?.userMessage ?? 'no error',
    )
    if (!error) continue
    const failed = renderToStaticMarkup(
      <Screen1Intake
        investigation={{ ...empty, upload: { phase: 'error', data: null, error } }}
        operator={operator}
        onAnalyse={() => {}}
      />,
    )
    check(
      failed.includes('Evidence was not sealed') &&
        failed.includes(error.userMessage) &&
        !failed.includes('step--done'),
      `a ${refusal.status} refusal renders as a failure, not a seal`,
      refusal.marker,
    )
  }
}

/**
 * The case workflow stepper: one row, and every tick earned.
 *
 * Two properties are checked here, both of which have been broken before.
 *
 * 1. Nothing is pre-ticked. The stepper's done-states come from one pure
 *    derivation over the shared store (`caseWorkflowDone`), so each flag is
 *    driven from the idle store to a real recorded payload and asserted to
 *    change only then -- and only for the case the payload belongs to. A
 *    workflow indicator that ticks a step the examiner has not performed is a
 *    false statement about the case file.
 *
 * 2. The row exists once. There used to be four screen-local copies of it in
 *    addition to the shell's, which disagreed with each other because each
 *    derived its own completion state. `CASE_WORKFLOW_ROUTES` is the single list
 *    of routes the shell renders it on, and the six case screens are asserted to
 *    emit no workflow nav of their own.
 */
/**
 * The Analysis screen's entry state, over the real `GET /verdict` payload.
 *
 * This is what an examiner sees on every arrival that is not a pipeline run in
 * this session -- the queue, a deep link, a browser reload. The five branches are
 * asserted directly because each encodes a forensic-honesty rule that a screen
 * test would only reach incidentally:
 *
 *   - While the case file has not answered, no claim is made either way. The
 *     screen must not print "NOT YET ANALYSED" merely because this client has not
 *     looked yet; that would report an unfinished fetch as a finding.
 *   - A failed read says the read failed. Falling through to "not analysed" would
 *     turn an unreachable backend into a statement about the evidence.
 *   - An analysed case names its stored verdict rather than offering to compute
 *     one, and says so is `cached`.
 *   - An unfused exhibit is counted as unfused, never given a band.
 *   - A null score prints as NOT MEASURED. Never 0.
 */
function verifyAnalysisEntryState(): void {
  const caseId = String(RECORDINGS.context.case_id ?? '')
  const storedJson = RECORDINGS.responses[`GET /api/cases/${caseId}/verdict`]?.json as
    | StoredVerdictResponse
    | undefined
  check(
    storedJson !== undefined && storedJson.source === 'stored',
    'the read-only verdict route was recorded, and declares itself stored',
    storedJson ? `source=${storedJson.source} analysed=${storedJson.analysed_count}` : 'missing',
  )
  if (!storedJson) return

  const noop = () => {}
  const render = (stored: Parameters<typeof AnalysisEntryState>[0]['stored'], evidenceCount: number) =>
    renderToStaticMarkup(
      <AnalysisEntryState
        stored={stored}
        evidenceCount={evidenceCount}
        onRun={noop}
        onReRun={noop}
        onIngest={noop}
      />,
    )

  // 1. Nothing read yet: no claim about the case, in either direction.
  for (const phase of ['idle', 'loading'] as const) {
    const html = render({ status: phase, data: null, error: null }, 0)
    check(
      !html.includes('NOT YET ANALYSED') &&
        !html.includes('ANALYSIS ON RECORD') &&
        !html.includes('NO EVIDENCE IN THIS CASE'),
      `entry state (${phase}) makes no claim before the case file answers`,
    )
    check(
      html.includes('verdict on record'),
      `entry state (${phase}) says what it is waiting for`,
    )
  }

  // 2. A failed read is reported as a failed read.
  const failed = render(
    {
      status: 'error',
      data: null,
      error: new ApiError({
        kind: 'unavailable',
        status: 503,
        message: 'Service Unavailable',
        url: `${BASE}/api/cases/${caseId}/verdict`,
      }),
    },
    1,
  )
  check(
    !failed.includes('NOT YET ANALYSED'),
    'a failed verdict read is never rendered as "not yet analysed"',
  )
  check(
    failed.includes('Stored verdict'),
    'a failed verdict read names the operation that failed',
  )

  // 3. The recorded case, which really was fused. Its band is the stored one.
  const ready = render({ status: 'ready', data: storedJson, error: null }, 0)
  const first = storedJson.items[0]
  check(
    storedJson.analysed_count > 0 && ready.includes('ANALYSIS ON RECORD'),
    'a case with a stored verdict is announced as already analysed',
    `analysed_count=${storedJson.analysed_count}`,
  )
  check(
    !ready.includes('NOT YET ANALYSED'),
    'an analysed case is never offered as unanalysed',
  )
  if (first) {
    check(
      ready.includes(first.filename),
      'the stored verdict is attributed to the exhibit it belongs to',
      first.filename,
    )
    check(
      ready.includes(verdictBandLabel(first.verdict)),
      'the band on screen is the band in the case file',
      `${first.verdict} -> ${verdictBandLabel(first.verdict)}`,
    )
    check(
      first.manipulation_score === null
        ? ready.includes(NOT_MEASURED)
        : ready.includes(formatScore(first.manipulation_score, 4)),
      'the stored score is printed as recorded, and a null score as NOT MEASURED',
      String(first.manipulation_score),
    )
  }
  check(
    ready.includes('Load Full Analysis') && ready.includes('Re-Run Analysis'),
    'both onward actions are offered, and named for what they do',
  )
  check(
    ready.includes('recorded in the audit chain'),
    'the examiner is told both actions are audited before taking one',
  )
  // Counted from the case file, not from a store slice that is empty on a deep
  // link. `evidenceCount` is passed as 0 above precisely to catch a regression
  // that counts exhibits from the wrong source.
  check(
    ready.includes(`${storedJson.analysed_count} of ${storedJson.evidence_count} exhibit(s) fused`),
    'the exhibit count comes from the case file, not from the in-session store',
    `${storedJson.analysed_count} of ${storedJson.evidence_count}`,
  )

  // 4. A real payload with the verdicts removed: evidence present, none fused.
  const unfused: StoredVerdictResponse = {
    ...storedJson,
    count: 0,
    items: [],
    analysed_count: 0,
    evidence_count: 2,
    pending_evidence: storedJson.items.map((v) => ({
      evidence_id: v.evidence_id,
      filename: v.filename,
      media_type: v.media_type,
      sha256: '',
      reason: 'No fused verdict is stored for this item.',
    })),
  }
  const none = render({ status: 'ready', data: unfused, error: null }, 0)
  check(
    none.includes('NOT YET ANALYSED') && none.includes('Run Analysis'),
    'a case with evidence but no fusion offers the run, and says none is stored',
  )
  check(
    !none.includes('ANALYSIS ON RECORD'),
    'a case with no fusion is not announced as analysed',
  )
  check(
    none.includes('2 exhibits'),
    'the unanalysed case states how many exhibits are waiting',
  )
  for (const band of ['LIKELY MANIPULATED', 'LIKELY AUTHENTIC', 'INCONCLUSIVE'] as const) {
    check(
      !none.includes(band),
      `an unfused exhibit is given no band ("${band}")`,
    )
  }

  // 5. No evidence at all: intake is the next step, not analysis.
  const empty = render(
    { status: 'ready', data: { ...unfused, evidence_count: 0, pending_evidence: [] }, error: null },
    0,
  )
  check(
    empty.includes('NO EVIDENCE IN THIS CASE') && empty.includes('Ingest Evidence'),
    'an empty case is sent to intake rather than offered an analysis run',
  )
  check(
    !empty.includes('Run Analysis'),
    'an empty case is not offered a run that could only fuse nothing',
  )

  // 6. A partially analysed case says so, so the band cannot be read as covering
  //    the whole case file.
  const partial = render(
    {
      status: 'ready',
      data: {
        ...storedJson,
        evidence_count: storedJson.analysed_count + 1,
        pending_evidence: [
          {
            evidence_id: 'pending-1',
            filename: 'not-yet-fused.jpg',
            media_type: 'image',
            sha256: '',
            reason: 'No fused verdict is stored for this item.',
          },
        ],
      },
      error: null,
    },
    0,
  )
  check(
    partial.includes('have no stored verdict'),
    'a partially analysed case discloses the exhibits its verdict does not cover',
  )
}

function verifyCaseWorkflowStepper(): void {
  const caseA = asCaseRecord(RECORDINGS.responses['POST /api/cases/upload']?.json &&
    (RECORDINGS.responses['POST /api/cases/upload']!.json as { case: unknown }).case, 'stepper.caseA')
  const caseB = asCaseRecord(RECORDINGS.context.deleted_case, 'stepper.caseB')
  const analysisA = RECORDINGS.responses[`POST /api/cases/${caseA.case_id}/analyse`]!.json as AnalysisResponse
  const propagationA = RECORDINGS.responses[`GET /api/cases/${caseA.case_id}/propagation`]!.json as PropagationResponse
  const verifyA = RECORDINGS.responses[`POST /api/cases/${caseA.case_id}/audit/verify`]!.json as AuditVerification
  const reportA = RECORDINGS.responses[`POST /api/cases/${caseA.case_id}/report`]!.json as ReportResponse
  const evidenceA = (RECORDINGS.responses[`GET /api/cases/${caseA.case_id}/evidence`]!.json as { evidence: Evidence[] }).evidence

  // The store before anything has been done to the case: a case row and nothing
  // else. This is what a freshly opened case looks like.
  const opened: Investigation = investigationFor(caseA)

  const fresh = caseWorkflowDone(opened, caseA.case_id)
  check(
    fresh['case-detail'] === true,
    'opening a case completes the Case step and only that step',
    JSON.stringify(fresh),
  )
  check(
    CASE_WORKFLOW_STEPS.filter((s) => s.path !== 'case-detail').every((s) => fresh[s.path] === false),
    'every later step is unticked on a case that has only been opened',
    CASE_WORKFLOW_STEPS.filter((s) => fresh[s.path]).map((s) => s.label).join(',') || 'none but Case',
  )
  const empty = caseWorkflowDone({ ...opened, caseRecord: null }, null)
  check(
    CASE_WORKFLOW_STEPS.every((s) => empty[s.path] === false),
    'with no case loaded no step is complete, including Case',
    CASE_WORKFLOW_STEPS.filter((s) => empty[s.path]).map((s) => s.label).join(',') || 'none',
  )

  // Each flag, driven by exactly the state that earns it -- and by nothing else.
  const withEvidence = caseWorkflowDone({ ...opened, evidence: evidenceA }, caseA.case_id)
  check(
    withEvidence.evidence === true && withEvidence.analysis === false,
    'sealing evidence completes Evidence without completing Analysis',
    `evidence=${withEvidence.evidence} analysis=${withEvidence.analysis}`,
  )

  const analysed = caseWorkflowDone(
    { ...opened, evidence: evidenceA, analysis: { phase: 'ready', data: analysisA, error: null } },
    caseA.case_id,
  )
  check(
    analysed.analysis === true,
    'a real analysis result completes Analysis',
    `verdict=${analysisA.verdict?.verdict ?? 'none'}`,
  )
  check(
    analysed.provenance === false && analysed.audit === false && analysed.reports === false,
    'analysing does not complete Provenance, Audit or Report',
    `provenance=${analysed.provenance} audit=${analysed.audit} report=${analysed.reports}`,
  )

  // Case A's analysis while Case B is the active case: the slice outlives the
  // switch, so the id it carries decides, not its mere presence.
  const analysisOfAnotherCase = caseWorkflowDone(
    {
      ...investigationFor(caseB),
      analysis: { phase: 'ready', data: analysisA, error: null },
    },
    caseB.case_id,
  )
  check(
    analysisOfAnotherCase.analysis === false,
    "another case's analysis never completes this case's Analysis step",
    `analysis_case=${analysisA.case.case_id.slice(0, 8)} active=${caseB.case_id.slice(0, 8)}`,
  )

  // A backend-issued verdict on the case row also completes Analysis: after a
  // reload the analysis slice is empty, but the case file still records one.
  const reloadedWithVerdict = caseWorkflowDone(
    { ...investigationFor({ ...caseA, latest_verdict: 'MANIPULATED' }) },
    caseA.case_id,
  )
  check(
    reloadedWithVerdict.analysis === true,
    "the case row's own latest_verdict completes Analysis across a reload",
    'latest_verdict=MANIPULATED',
  )

  /*
   * The Provenance step is the act of tracing, and `trace_status` is the only
   * field that reports whether it happened.
   *
   * These two checks used to assert the opposite -- that the step completes from
   * `instance_count > 0 || matched_candidate_count > 0` -- and so encoded a
   * false claim rather than catching it. `instance_count` counts the instances
   * in the reconstructed timeline, and a case's own exhibits are instances: a
   * real case carrying three exhibits reported `instance_count: 3` next to
   * `trace_status: "NOT_RUN"`, and the canonical workflow bar ticked "Provenance
   * — completed" for work nobody had done.
   */
  const tracedNotRun = caseWorkflowDone(
    {
      ...opened,
      propagation: {
        phase: 'ready',
        data: { ...propagationA, trace_status: 'NOT_RUN', instance_count: 3 },
        error: null,
      },
    },
    caseA.case_id,
  )
  check(
    tracedNotRun.provenance === false,
    'a case whose own exhibits are the only instances does not count as traced',
    `instances=3 trace_status=NOT_RUN done=${tracedNotRun.provenance}`,
  )

  for (const status of ['COMPUTED', 'STORED'] as const) {
    const ran = caseWorkflowDone(
      {
        ...opened,
        propagation: {
          phase: 'ready',
          // Zero matches on purpose: finding nothing is a result, not a failure
          // to perform the step.
          data: { ...propagationA, trace_status: status, instance_count: 0, matched_candidate_count: 0 },
          error: null,
        },
      },
      caseA.case_id,
    )
    check(
      ran.provenance === true,
      `a trace that ran and matched nothing still completes Provenance (${status})`,
      `done=${ran.provenance}`,
    )
  }

  const traceUnknown = caseWorkflowDone(
    {
      ...opened,
      propagation: {
        phase: 'ready',
        // What arrives when this object is nested in an analyse response.
        data: { ...propagationA, trace_status: undefined },
        error: null,
      },
    },
    caseA.case_id,
  )
  check(
    traceUnknown.provenance === false,
    'not knowing whether a trace ran is not grounds for claiming one did',
    `done=${traceUnknown.provenance}`,
  )

  const verified = caseWorkflowDone(
    { ...opened, auditVerification: { phase: 'ready', data: verifyA, error: null } },
    caseA.case_id,
  )
  check(
    verified.audit === true && verifyA.valid === true,
    'a passing chain verification completes Audit',
    `valid=${verifyA.valid}`,
  )
  const failedChain = caseWorkflowDone(
    {
      ...opened,
      auditVerification: { phase: 'ready', data: { ...verifyA, valid: false }, error: null },
    },
    caseA.case_id,
  )
  check(
    failedChain.audit === false,
    'a chain that did not verify leaves Audit unticked',
    `done=${failedChain.audit}`,
  )

  // Report. The generated report in this session, and -- across a reload, where
  // the slice is empty -- the backend's own count on the case row.
  const reported = caseWorkflowDone(
    { ...opened, report: { phase: 'ready', data: reportA, error: null } },
    caseA.case_id,
  )
  check(
    reported.reports === true,
    'generating a report completes Report',
    reportA.report_id.slice(0, 8),
  )
  check(
    caseWorkflowDone(
      { ...opened, report: { phase: 'ready', data: reportA, error: null } },
      caseB.case_id,
    ).reports === false,
    "another case's report never completes this case's Report step",
    `report_case=${reportA.case_id.slice(0, 8)} active=${caseB.case_id.slice(0, 8)}`,
  )
  check(
    caseWorkflowDone(investigationFor({ ...caseA, report_count: 1 }), caseA.case_id).reports === true,
    'a report counted by the backend completes Report across a reload',
    'report_count=1',
  )
  check(
    caseWorkflowDone(investigationFor({ ...caseA, report_count: 0 }), caseA.case_id).reports === false,
    'a counted zero leaves Report unticked',
    'report_count=0',
  )
  check(
    caseWorkflowDone(investigationFor({ ...caseA, report_count: null }), caseA.case_id).reports === false,
    'an uncounted report_count is not read as a report (null is not a tick, and not a zero either)',
    'report_count=null',
  )
  // The regression this field exists for: the flag used to be read out of the
  // case's status string, which no backend path ever writes a report state into.
  check(
    caseWorkflowDone(
      investigationFor({ ...caseA, status: 'open', report_count: 1 }),
      caseA.case_id,
    ).reports === true,
    "Report completion no longer depends on the word 'report' appearing in the case status",
    `status=${caseA.status}`,
  )

  // --- The rendered row ------------------------------------------------------
  const render = (routePath: RoutePath, investigation: Investigation, caseId: string | null) =>
    renderToStaticMarkup(
      <CaseWorkflowStepper
        investigation={investigation}
        routePath={routePath}
        caseId={caseId}
        onNavigate={() => {}}
      />,
    )

  const navCount = (html: string) => html.split('aria-label="Investigation Workflow"').length - 1

  const analysisRow = render('analysis', analysedStore(opened, evidenceA, analysisA), caseA.case_id)
  check(
    navCount(analysisRow) === 1,
    'the stepper renders exactly one workflow nav',
    `${navCount(analysisRow)} found`,
  )
  check(
    (analysisRow.match(/aria-current="step"/g) ?? []).length === 1,
    'exactly one step is announced as current',
    String((analysisRow.match(/aria-current="step"/g) ?? []).length),
  )
  check(
    buttonWith(analysisRow, 'Analysis').includes('aria-current="step"'),
    'on the Analysis route it is the Analysis step that is current',
    buttonWith(analysisRow, 'Analysis').slice(0, 120),
  )
  check(
    analysisRow.includes('(completed)'),
    'completion is stated in text for a screen reader, not only in colour and a tick',
    'sr-only (completed)',
  )

  // Intake is the Evidence step's own form. Before this mapping the whole row
  // rendered with no active step on the one screen where the operator is most
  // likely to be lost.
  const intakeRow = render('intake', opened, caseA.case_id)
  check(
    buttonWith(intakeRow, 'Evidence').includes('aria-current="step"'),
    'the intake route marks Evidence as the current step',
    buttonWith(intakeRow, 'Evidence').slice(0, 120),
  )

  // With no case there is nowhere for the later steps to go, and they say so
  // rather than navigating to an empty screen.
  const caseless = render('intake', { ...opened, caseRecord: null }, null)
  check(
    (caseless.match(/disabled=""/g) ?? []).length === CASE_WORKFLOW_STEPS.length - 1 &&
      !buttonWith(caseless, 'Case').includes('disabled'),
    'with no case loaded every step but Case is disabled',
    `${(caseless.match(/disabled=""/g) ?? []).length} disabled`,
  )

  // The route list, and the screens themselves.
  check(
    (['dashboard', 'cases', 'settings'] as RoutePath[]).every((p) => !CASE_WORKFLOW_ROUTES.includes(p)),
    'the workflow row is not claimed by the global destinations (Dashboard, Cases, Settings)',
    CASE_WORKFLOW_ROUTES.join(','),
  )
  check(
    CASE_WORKFLOW_STEPS.every((s) => CASE_WORKFLOW_ROUTES.includes(s.path)) &&
      CASE_WORKFLOW_ROUTES.includes('intake'),
    'every case-scoped route does carry it, intake included',
    CASE_WORKFLOW_ROUTES.join(','),
  )

  /*
   * The gate is the component's own, so a future caller cannot reintroduce the
   * bug by rendering the row on a global destination: on those routes it emits
   * nothing at all, rather than an empty bar reserving vertical space.
   */
  const loaded = analysedStore(opened, evidenceA, analysisA)
  for (const global of ['dashboard', 'cases', 'settings'] as RoutePath[]) {
    check(
      contextBar(loaded, global) === '',
      `the context row renders nothing on ${global}, even with a case loaded`,
      contextBar(loaded, global).slice(0, 60) || 'empty',
    )
  }
  const bar = contextBar(loaded, 'analysis')
  /*
   * Counted in visible text only: the chip's tooltip ("Open case dossier
   * #PRAMAAN-1001") legitimately repeats the number in a title attribute, which
   * is a hover affordance rather than a second statement on the page.
   */
  const visibleNumbers = bar.split(`>#${caseA.case_number}<`).length - 1
  check(
    navCount(bar) === 1 && visibleNumbers === 1,
    'on a case route the row states the case number exactly once, above exactly one workflow nav',
    `navs=${navCount(bar)} visible=${visibleNumbers} total=${bar.split(caseA.case_number).length - 1}`,
  )
  check(
    bar.includes(`title="Open case dossier #${caseA.case_number}"`),
    'the case chip says where it goes, naming the case it would open',
    caseA.case_number,
  )
  check(
    bar.includes(caseA.title ?? '') && bar.includes('Open case dossier'),
    'the case chip carries the case title beside the number',
    caseA.title ?? '(untitled)',
  )

  // No case screen renders a second copy. The shell owns this row; a screen-local
  // one is the duplication that put two disagreeing workflow rows on screen.
  const screens: Array<[string, string]> = [
    ['Case Detail', renderToStaticMarkup(
      <ScreenCaseDetail caseId={caseA.case_id} investigation={loaded} onNavigate={() => {}} />,
    )],
    ['Analysis', renderToStaticMarkup(
      <Screen2Analysis
        caseId={caseA.case_id}
        investigation={loaded}
        onNavigate={() => {}}
        onPropagation={() => {}}
      />,
    )],
    ['Intake', renderToStaticMarkup(
      <Screen1Intake investigation={loaded} operator={recordedOperator()} onAnalyse={() => {}} />,
    )],
  ]
  for (const [name, html] of screens) {
    check(
      navCount(html) === 0,
      `${name} renders no workflow stepper of its own`,
      `${navCount(html)} found`,
    )
  }
}

/** Case A, evidence sealed and analysed -- the store the loaded screens get. */
function analysedStore(
  opened: Investigation,
  evidence: Evidence[],
  analysis: AnalysisResponse,
): Investigation {
  return {
    ...opened,
    evidence,
    analysis: { phase: 'ready', data: analysis, error: null },
  }
}

async function main(): Promise<void> {
  const caseId = String(RECORDINGS.context.case_id)
  const downloadUrl = String(RECORDINGS.context.download_url)

  /*
   * A signed-in operator, because that is the only state this console runs in.
   *
   * Every API route but `POST /api/auth/login` and `GET /health` requires a
   * bearer token; the recordings were captured with one. Installing it here also
   * makes the "is the header actually attached" assertions below mean something,
   * which is the property that decides whether an authenticated route can be
   * reached from a `<img src>` or `<a href>` at all -- it cannot.
   */
  setAuthToken('contract-test-token')

  // 1. System probes.
  const health = await api.health()
  expectRequest('GET', '/health', 'health() calls GET /health')
  check(health.status === 'ok', 'health() parses the real payload', JSON.stringify(health))
  check(
    (headerOf(issued[issued.length - 1], 'authorization') ?? '') === 'Bearer contract-test-token',
    'the transport attaches the operator bearer token to every call',
    JSON.stringify(issued[issued.length - 1].headers),
  )

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

  // 1b. Signal applicability -- backend truth for which signals apply to which
  //     media type. The Analysis UI renders from this response; applicability is
  //     never hardcoded in the frontend.
  const applicability = await api.signalApplicability()
  expectRequest('GET', '/api/system/signals', 'signalApplicability() calls GET /api/system/signals')
  check(
    Boolean(applicability.applicability.image) &&
      Boolean(applicability.applicability.video) &&
      Boolean(applicability.applicability.audio),
    'signalApplicability() parses the per-media-type map',
    Object.keys(applicability.applicability).join(','),
  )
  const imageIds = applicability.applicability.image!.map((s) => s.signal_id)
  const videoIds = applicability.applicability.video!.map((s) => s.signal_id)
  const audioIds = applicability.applicability.audio!.map((s) => s.signal_id)
  check(
    imageIds.length === 5 &&
      imageIds.includes('perceptual_duplication') &&
      imageIds.includes('compression_forensics'),
    'image carries all five forensic signals, including the image-only pair',
    imageIds.join(','),
  )
  check(
    !videoIds.includes('perceptual_duplication') &&
      !videoIds.includes('compression_forensics') &&
      videoIds.length === 3,
    'video carries no image-only perceptual or compression signals',
    videoIds.join(','),
  )
  check(
    !audioIds.includes('perceptual_duplication') &&
      !audioIds.includes('compression_forensics') &&
      audioIds.length === 1,
    'audio carries no image perceptual matching or compression forensics',
    audioIds.join(','),
  )

  // 2. UPLOAD -- via XMLHttpRequest, with progress.
  const progress: number[] = []
  const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0x00])], 'complaint-photo.jpg', {
    type: 'image/jpeg',
  })
  const uploaded = await api.uploadEvidence(
    file,
    {
      title: 'Verification case',
      description: 'Contract verification run',
      acquisitionContext: 'Captured by the integration verifier',
    },
    { onProgress: (p) => progress.push(p.fraction ?? -1) },
  )
  expectRequest('POST', '/api/cases/upload', 'uploadEvidence() posts to /api/cases/upload')
  check(
    progress.length === 2 && progress[1] === 1,
    'upload progress is reported from real XHR events',
    `fractions=${progress.join(',')}`,
  )
  const uploadForm = issued[issued.length - 1].body
  check(uploadForm instanceof FormData, 'upload body is FormData (so the backend sees multipart)')
  if (uploadForm instanceof FormData) {
    check(
      uploadForm.get('title') === 'Verification case' &&
        uploadForm.get('description') === 'Contract verification run' &&
        uploadForm.get('acquisition_context') === 'Captured by the integration verifier',
      'the form carries the field names the backend reads',
      [...uploadForm.keys()].join(','),
    )
    // The examiner is resolved server-side from the bearer token. A client that
    // sent one would be asserting an identity, and the backend would ignore it --
    // so the client must not send one at all.
    check(
      !uploadForm.has('examiner'),
      'intake sends no examiner field: identity comes from the session, not the form',
      [...uploadForm.keys()].join(','),
    )
  }
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
    statusLabel('UNSUPPORTED_MEDIA') === 'NOT APPLICABLE' &&
      statusLabel('OK') === 'CONTRIBUTED' &&
      statusLabel('INCONCLUSIVE') === 'RAN — ABSTAINED',
    'the statuses that need rewording get it',
    `${statusLabel('UNSUPPORTED_MEDIA')} / ${statusLabel('OK')} / ${statusLabel('INCONCLUSIVE')}`,
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

  // Media-aware summary: Applicable / Evaluated / Contributing, all values the
  // backend's own, scoped to the applicable set for this media type.
  const summary = mediaAwareSummary(verdict)
  check(
    summary.applicable === verdict.signals_total &&
      summary.evaluated === verdict.signals_evaluated &&
      summary.contributing === verdict.signals_available,
    'media-aware summary states Applicable/Evaluated/Contributing from backend truth',
    summary.line,
  )
  check(
    verdict.signals_total === verdict.signals.length &&
      verdict.applicable_signals.length === verdict.signals.length,
    'the image verdict carries exactly its applicable signal set',
    `${verdict.signals_total} of ${verdict.applicable_signals.length}`,
  )

  // The same truth, per modality, from the video and audio cases the verifier
  // analysed. The recordings are real backend responses, so these assert what
  // the Analysis UI will actually render for each media type.
  const videoVerdict = RECORDINGS.context.video_verdict as Verdict | undefined
  const audioVerdict = RECORDINGS.context.audio_verdict as Verdict | undefined
  if (videoVerdict) {
    const videoListed = videoVerdict.signals.map((s: Signal) => s.signal_id)
    check(
      videoListed.length === 3 &&
        !videoListed.includes('perceptual_duplication') &&
        !videoListed.includes('compression_forensics'),
      'video verdict hides the image-only perceptual and compression signals',
      videoListed.join(','),
    )
    check(
      videoVerdict.signals_total === 3 &&
        videoVerdict.media_type === 'video' &&
        videoVerdict.signals_evaluated <= 3,
      'video coverage counts are scoped to the applicable set',
      `total=${videoVerdict.signals_total} evaluated=${videoVerdict.signals_evaluated}`,
    )
  }
  if (audioVerdict) {
    const audioListed = audioVerdict.signals.map((s: Signal) => s.signal_id)
    check(
      audioListed.length === 1 && audioListed[0] === 'ai_detection',
      'audio verdict shows only the applicable detector signal',
      audioListed.join(','),
    )
    check(
      audioVerdict.signals_total === 1 && audioVerdict.media_type === 'audio',
      'audio coverage counts are scoped to the applicable set',
      `total=${audioVerdict.signals_total}`,
    )
  }
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

  // 5b. The read the Provenance screen performs on mount.
  //
  // Without `record=false` the backend runs near-duplicate retrieval for any
  // case that has none stored and appends MATCH_SEARCHED and
  // PROPAGATION_RECONSTRUCTED, so merely opening the screen moved the case's
  // head hash. The parameter must actually be on the wire, which is what this
  // asserts -- the screen's intention is not enough if the client drops it.
  const read = await api.propagation(caseId, { record: false })
  expectRequest(
    'GET',
    `/api/cases/${caseId}/propagation?record=false`,
    'a propagation read sends record=false, so a page load writes nothing',
  )
  check(
    read.recorded === false,
    'the backend confirms the read wrote nothing',
    `recorded=${read.recorded}`,
  )
  check(
    read.trace_status === 'COMPUTED' ||
      read.trace_status === 'STORED' ||
      read.trace_status === 'NOT_RUN',
    'the read reports whether the retrieval behind it ever ran',
    `trace_status=${read.trace_status}`,
  )
  check(
    Boolean(read.trace_status_meaning),
    'trace_status arrives with wording the screen can render verbatim',
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

  // 6b. The candidate list a page load is entitled to.
  //
  // The POST above replaces the case's stored match set and appends
  // MATCH_SEARCHED. The Provenance screen used to call it on mount, so arriving
  // at the page wrote an audit row; it reads now, and `searched` is the field
  // that keeps an empty table honest -- "nothing similar is indexed" and
  // "nobody has looked" are different claims and only the first is a finding.
  const stored = await api.storedMatches(caseId)
  expectRequest(
    'GET',
    `/api/cases/${caseId}/matches`,
    'storedMatches() reads with GET, never posting a search',
  )
  check(
    stored.source === 'stored',
    'the stored candidate list declares itself stored, not freshly retrieved',
    `source=${stored.source}`,
  )
  check(
    typeof stored.searched === 'boolean',
    'whether the case has ever been searched arrives as its own fact',
    `searched=${stored.searched}`,
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

  const listed = await api.listReports(caseId)
  check(
    Array.isArray(listed.reports),
    "listReports() reads the 'reports' key",
    `${listed.count} reports`,
  )

  const blob = await api.downloadReport(downloadUrl)
  check(blob.size > 0, 'downloadReport() returns a non-empty Blob', `${blob.size} bytes`)
  /*
   * The PDF is fetched with the operator's token, not linked to.
   *
   * The report route is authenticated, so the "Open PDF" affordance cannot be an
   * <a href> -- a browser navigation carries no Authorization header, and the
   * three anchors that used to sit beside these buttons would return 401 the
   * moment the route was protected. `components/ReportActions` is the one place
   * both actions live, and both go through this call.
   */
  const pdfRequest = issued[issued.length - 1]
  check(
    pdfRequest.url === `${BASE}${downloadUrl}` &&
      (headerOf(pdfRequest, 'authorization') ?? '').startsWith('Bearer '),
    'the report PDF is fetched with the bearer token attached',
    `${pdfRequest.url} auth=${headerOf(pdfRequest, 'authorization') ? 'yes' : 'MISSING'}`,
  )

  const evidenceId = String(RECORDINGS.context.evidence_id)
  const bytes = await api.evidenceFile(evidenceId)
  check(bytes.size > 0, 'evidenceFile() returns the stored bytes as a Blob', `${bytes.size} bytes`)
  const bytesRequest = issued[issued.length - 1]
  check(
    bytesRequest.url === `${BASE}/api/evidence/${evidenceId}/file` &&
      (headerOf(bytesRequest, 'authorization') ?? '').startsWith('Bearer '),
    'evidence bytes are fetched with the bearer token attached, not pointed at with an <img src>',
    `${bytesRequest.url} auth=${headerOf(bytesRequest, 'authorization') ? 'yes' : 'MISSING'}`,
  )

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

  // 12b. Case switch regression -- Case A's data never renders for Case B.
  await verifyCaseSwitch()

  // 12c. Delete -> reset -> refresh: the deleted case cannot reappear.
  await verifyDeleteResetsState()

  // 13. New Case starts clean -- no previous case leaks into a fresh intake.
  verifyNewCaseReset()
  await verifyStaleResponseCannotLand()

  // 13b. Evidence intake: analyst from the session, seal only after the backend.
  await verifyIntakeContract()

  // 13c. The case workflow row: one copy, and no step ticked before it is earned.
  verifyCaseWorkflowStepper()

  // 13d. The Analysis screen's entry state, over the read-only verdict payload.
  verifyAnalysisEntryState()

  // 14. Nothing reached a URL the backend does not serve.
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
