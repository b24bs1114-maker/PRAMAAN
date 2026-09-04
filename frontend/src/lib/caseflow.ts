/**
 * Workflow derivation: UPLOAD -> EXAMINE -> EXPLAIN -> TRACE -> VERIFY -> REPORT.
 *
 * The rule this module exists to enforce: a stage is complete because the
 * backend produced the artefact for it, never because the operator navigated
 * past it. The previous build tracked stage completion in session state, so a
 * reload lost it, and several call sites passed `done` as a literal -- the
 * Analysis screen claimed "3 of 6" while the Audit screen simultaneously
 * claimed Analysis was not done.
 *
 * Every stage here answers one question with one piece of real state:
 *   UPLOAD   did evidence rows arrive?
 *   EXAMINE  did the fusion engine return signals?
 *   EXPLAIN  is there a verdict band with its rationale?
 *   TRACE    did propagation return a graph?
 *   VERIFY   did POST /audit/verify return valid: true?
 *   REPORT   did the backend render and hash a PDF?
 *
 * `blocked` is a real state and is rendered as one. It means the prerequisite
 * artefact does not exist yet, which is different from "not started".
 */

import type { RoutePath } from './router'
import type {
  AnalysisResponse,
  AuditVerification,
  Evidence,
  PropagationResponse,
  ReportResponse,
} from '../api/types'
import type { Slice } from '../state/useInvestigation'

export type StageId = 'upload' | 'examine' | 'explain' | 'trace' | 'verify' | 'report'

/**
 * done    the artefact exists
 * running a call for it is in flight
 * failed  the call returned an error
 * ready   prerequisites met, not run yet
 * blocked prerequisites not met
 */
export type StageState = 'done' | 'running' | 'failed' | 'ready' | 'blocked'

export interface Stage {
  id: StageId
  /** 1-based, for the numbered rail. */
  index: number
  /** Short workflow verb, uppercase in the rail. */
  label: string
  /** What the stage actually does, in an investigator's words. */
  name: string
  state: StageState
  /** The specific state this verdict was read from. Never a generic string. */
  detail: string
  route: RoutePath
}

const LABELS: Record<StageId, { label: string; name: string; route: RoutePath }> = {
  upload: { label: 'Upload', name: 'Evidence intake', route: 'intake' },
  examine: { label: 'Examine', name: 'Forensic signals', route: 'analysis' },
  explain: { label: 'Explain', name: 'Verdict & rationale', route: 'analysis' },
  trace: { label: 'Trace', name: 'Provenance & spread', route: 'provenance' },
  verify: { label: 'Verify', name: 'Audit chain integrity', route: 'audit' },
  report: { label: 'Report', name: 'Signed forensic PDF', route: 'reports' },
}

export interface CaseFlowInput {
  evidence: Evidence[]
  analysis: Slice<AnalysisResponse>
  propagation: Slice<PropagationResponse>
  auditVerification: Slice<AuditVerification>
  report: Slice<ReportResponse>
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

export function deriveStages(input: CaseFlowInput): Stage[] {
  const { evidence, analysis, propagation, auditVerification, report } = input

  const stages: Array<{ id: StageId; state: StageState; detail: string }> = []

  // --- UPLOAD ---------------------------------------------------------------
  const evidenceCount = evidence.length
  stages.push({
    id: 'upload',
    state: evidenceCount > 0 ? 'done' : 'ready',
    detail:
      evidenceCount > 0
        ? `${plural(evidenceCount, 'item', 'items')} ingested and hashed`
        : 'No evidence ingested yet',
  })

  // --- EXAMINE --------------------------------------------------------------
  // Signals are what EXAMINE produces. A verdict is not required: a run that
  // measured signals but could not fuse them still examined the evidence.
  const signalCount = analysis.data?.signals.length ?? 0
  let examine: StageState
  let examineDetail: string
  if (evidenceCount === 0) {
    examine = 'blocked'
    examineDetail = 'Waiting on evidence intake'
  } else if (analysis.phase === 'loading') {
    examine = 'running'
    examineDetail = 'Analysis in progress'
  } else if (analysis.phase === 'error') {
    examine = 'failed'
    examineDetail = 'Analysis request failed'
  } else if (signalCount > 0) {
    const available = analysis.data?.verdict?.signals_available
    const total = analysis.data?.verdict?.signals_total
    examine = 'done'
    examineDetail =
      typeof available === 'number' && typeof total === 'number'
        ? `${available} of ${total} signals measured`
        : `${plural(signalCount, 'signal', 'signals')} returned`
  } else {
    examine = 'ready'
    examineDetail = 'Analysis not run'
  }
  stages.push({ id: 'examine', state: examine, detail: examineDetail })

  // --- EXPLAIN --------------------------------------------------------------
  // A verdict band plus the backend's own rationale. INSUFFICIENT_EVIDENCE
  // counts as done: it is a forensic result, not a failure to produce one.
  const verdict = analysis.data?.verdict ?? null
  let explain: StageState
  let explainDetail: string
  if (examine === 'running') {
    explain = 'running'
    explainDetail = 'Fusing measured signals'
  } else if (examine !== 'done') {
    explain = 'blocked'
    explainDetail = 'Waiting on forensic signals'
  } else if (verdict) {
    explain = 'done'
    explainDetail = `Verdict: ${String(verdict.verdict).replace(/_/g, ' ')}`
  } else {
    explain = 'failed'
    explainDetail = 'No evidence item could be scored'
  }
  stages.push({ id: 'explain', state: explain, detail: explainDetail })

  // --- TRACE ---------------------------------------------------------------
  // The graph is the artefact. A single-node graph is a real result: it says
  // the corpus holds no other instance, which is not the same as no answer.
  const graph = propagation.data?.graph
  let trace: StageState
  let traceDetail: string
  if (evidenceCount === 0) {
    trace = 'blocked'
    traceDetail = 'Waiting on evidence intake'
  } else if (propagation.phase === 'loading') {
    trace = 'running'
    traceDetail = 'Querying the perceptual-hash index'
  } else if (propagation.phase === 'error') {
    trace = 'failed'
    traceDetail = 'Propagation request failed'
  } else if (graph) {
    trace = 'done'
    traceDetail =
      graph.node_count > 1
        ? `${plural(graph.node_count, 'instance', 'instances')}, ${plural(graph.edge_count, 'link', 'links')}`
        : 'No further instance in the indexed corpus'
  } else {
    trace = 'ready'
    traceDetail = 'Trace not run'
  }
  stages.push({ id: 'trace', state: trace, detail: traceDetail })

  // --- VERIFY -------------------------------------------------------------
  // Only a returned valid:true is done. Nothing about navigating to the Audit
  // screen, or reading the trail, verifies a hash chain.
  const verification = auditVerification.data
  let verify: StageState
  let verifyDetail: string
  if (evidenceCount === 0) {
    verify = 'blocked'
    verifyDetail = 'Waiting on evidence intake'
  } else if (auditVerification.phase === 'loading') {
    verify = 'running'
    verifyDetail = 'Recomputing the hash chain'
  } else if (auditVerification.phase === 'error') {
    verify = 'failed'
    verifyDetail = 'Verification request failed'
  } else if (verification?.valid === true) {
    verify = 'done'
    verifyDetail = `${plural(verification.case_rows, 'row', 'rows')} recomputed, chain intact`
  } else if (verification) {
    verify = 'failed'
    verifyDetail =
      verification.first_invalid_seq !== null
        ? `Chain broken at row ${verification.first_invalid_seq}`
        : 'Chain verification failed'
  } else {
    verify = 'ready'
    verifyDetail = 'Not yet verified'
  }
  stages.push({ id: 'verify', state: verify, detail: verifyDetail })

  // --- REPORT -------------------------------------------------------------
  let reportState: StageState
  let reportDetail: string
  if (explain !== 'done') {
    reportState = 'blocked'
    reportDetail = 'Waiting on a verdict'
  } else if (report.phase === 'loading') {
    reportState = 'running'
    reportDetail = 'Rendering the document'
  } else if (report.phase === 'error') {
    reportState = 'failed'
    reportDetail = 'Report generation failed'
  } else if (report.data) {
    reportState = 'done'
    reportDetail = `${report.data.filename} rendered and hashed`
  } else {
    reportState = 'ready'
    reportDetail = 'Not generated'
  }
  stages.push({ id: 'report', state: reportState, detail: reportDetail })

  return stages.map((s, i) => ({
    id: s.id,
    index: i + 1,
    label: LABELS[s.id].label,
    name: LABELS[s.id].name,
    state: s.state,
    detail: s.detail,
    route: LABELS[s.id].route,
  }))
}

/** How many stages the backend has actually completed. */
export function completedCount(stages: Stage[]): number {
  return stages.filter((s) => s.state === 'done').length
}

/** "3 of 6 stages complete" -- a count of artefacts, not of screens visited. */
export function progressLabel(stages: Stage[]): string {
  return `${completedCount(stages)} of ${stages.length} stages complete`
}

/**
 * Which stage a route belongs to, so the rail can mark position without
 * implying completion. Routes outside the case flow return null.
 */
export function stageIdForRoute(path: RoutePath): StageId | null {
  switch (path) {
    case 'intake':
      return 'upload'
    case 'analysis':
      return 'examine'
    case 'provenance':
      return 'trace'
    case 'audit':
      return 'verify'
    case 'reports':
      return 'report'
    default:
      return null
  }
}

/**
 * Progress pips for a case row in a list, where only the case record is known.
 * `evidence_count` and `latest_verdict` are the two flow facts the list
 * endpoint returns, so exactly two pips can be filled honestly and the
 * remaining four are drawn empty rather than guessed.
 */
export function rowPips(evidenceCount: number, latestVerdict: string | null | undefined): boolean[] {
  const hasEvidence = evidenceCount > 0
  const hasVerdict = Boolean(latestVerdict)
  return [hasEvidence, hasVerdict, hasVerdict, false, false, false]
}

/** What the pips can and cannot say, for the row's title attribute. */
export const ROW_PIPS_BASIS =
  'Filled from the case list fields only: evidence count and latest verdict. Open the case for trace, verification and report state.'
