/**
 * The one canonical case workflow stepper.
 *
 * Case-specific workflow navigation (Case → Evidence → Analysis → Provenance
 * → Audit → Report) lives here and is rendered ONCE, by the app shell, above
 * the active screen. Individual case screens must not render their own copy:
 * the previous build carried a second, screen-local stepper on Analysis,
 * Provenance, Audit and Case Detail, so the operator saw two rows describing
 * the same workflow.
 *
 * Each step's done-state is derived from what has actually happened in the
 * shared investigation store -- never from the route, and never hardcoded:
 *   - Case:      a case row exists in the store
 *   - Evidence:  the store's evidence list for the case is non-empty
 *   - Analysis:  an analysis result is loaded, or the case row itself carries
 *                a backend-issued latest_verdict
 *   - Provenance:a provenance trace has actually been run for the case
 *   - Audit:     a chain verification has been run and passed
 *   - Report:    the case row's status records a report
 *
 * The active step is the route, and clicking a step performs a real navigation
 * through the router with the active case id -- no hidden state, no synthetic
 * transitions.
 */

import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

export interface CaseWorkflowStep {
  path: RoutePath
  label: string
}

/** The workflow steps in order, with the route each one navigates to. */
export const CASE_WORKFLOW_STEPS: CaseWorkflowStep[] = [
  { path: 'case-detail', label: 'Case' },
  { path: 'evidence', label: 'Evidence' },
  { path: 'analysis', label: 'Analysis' },
  { path: 'provenance', label: 'Provenance' },
  { path: 'audit', label: 'Audit' },
  { path: 'reports', label: 'Report' },
]

/**
 * The routes this bar belongs on.
 *
 * The six step routes plus `intake`, which is the Evidence step's own form and
 * so is part of the workflow without being a separate stop in it. Dashboard,
 * Cases and Settings are deliberately absent: they are global destinations, and
 * showing a case's workflow position above them claims the operator is
 * somewhere in a case when they are not.
 */
export const CASE_WORKFLOW_ROUTES: RoutePath[] = [
  ...CASE_WORKFLOW_STEPS.map((s) => s.path),
  'intake',
]

/**
 * Which step a route lights up.
 *
 * `intake` maps to Evidence: ingesting an exhibit *is* step 2, and leaving it
 * unmapped left the whole bar with no active step on the one screen where the
 * operator is most likely to be lost.
 */
function activeStepFor(routePath: RoutePath): RoutePath {
  return routePath === 'intake' ? 'evidence' : routePath
}

/**
 * Derive the workflow position from real state. Nothing is pre-ticked.
 *
 * `analysis`, `propagation`, `auditVerification` and `report` are shared slices
 * that survive a case switch until the new case's own result arrives, so each is
 * additionally scoped to the active case id where the response carries one.
 */
export function caseWorkflowDone(
  investigation: Pick<
    Investigation,
    'caseRecord' | 'evidence' | 'analysis' | 'propagation' | 'auditVerification' | 'report'
  >,
  caseId: string | null,
): Record<RoutePath, boolean> {
  const { caseRecord, evidence, analysis, propagation, auditVerification, report } = investigation

  const analysisDone =
    (isReady(analysis) && (!caseId || analysis.data.case.case_id === caseId)) ||
    Boolean(caseRecord?.latest_verdict)

  const propagationReady =
    isReady(propagation) &&
    (propagation.data.case_id === undefined || !caseId || propagation.data.case_id === caseId)
  /*
   * The Provenance step is done when the trace has actually been run.
   *
   * This tested `instance_count > 0 || matched_candidate_count > 0`, which does
   * not mean what it reads like: `instance_count` counts the instances in the
   * reconstructed timeline, and a case's own exhibits are instances. A case with
   * three exhibits and no trace ever run reported `instance_count: 3` alongside
   * `trace_status: "NOT_RUN"`, so the canonical workflow bar ticked "Provenance
   * — completed" for work nobody had done.
   *
   * `trace_status` is the field that answers the actual question. Anything other
   * than a positive COMPUTED/STORED leaves the step untitled, including the
   * `undefined` returned when this object arrives nested in an analyse response:
   * not knowing whether a trace ran is not grounds for claiming one did.
   *
   * A trace that ran and matched nothing still completes the step -- the step is
   * the act of tracing, and "no other instance in the indexed corpus" is a real
   * result, not a failure to perform one.
   */
  const provenanceDone =
    propagationReady &&
    (propagation.data.trace_status === 'COMPUTED' || propagation.data.trace_status === 'STORED')

  const auditDone =
    isReady(auditVerification) &&
    auditVerification.data.valid &&
    (auditVerification.data.case_id === null || !caseId || auditVerification.data.case_id === caseId)

  /*
   * A report exists for this case -- either one was generated in this session,
   * or the backend counted reports already on record.
   *
   * `report_count` is what makes this survive a reload: the store's `report`
   * slice is empty after one, so a case reported yesterday would otherwise show
   * its last step as never done. It is deliberately read as "> 0" rather than
   * "!== 0": the field is null when the endpoint did not count, and absence of
   * a count is not evidence of absence of a report.
   *
   * This used to test `caseRecord.status.includes('report')`. No code path in
   * the backend ever writes a report state into that column, so the Report step
   * could never tick -- the indicator was wired to nothing.
   */
  const reportDone =
    (isReady(report) && (!caseId || report.data.case_id === caseId)) ||
    (caseRecord?.report_count ?? 0) > 0

  return {
    'case-detail': Boolean(caseRecord),
    evidence: evidence.length > 0,
    analysis: analysisDone,
    provenance: provenanceDone,
    audit: auditDone,
    reports: reportDone,
    // Route paths that are not workflow steps are never marked done.
    dashboard: false,
    cases: false,
    intake: false,
    settings: false,
  }
}

export function CaseWorkflowStepper({
  investigation,
  routePath,
  caseId,
  onNavigate,
}: {
  investigation: Investigation
  /** The route currently rendered, which selects the active step. */
  routePath: RoutePath
  /** The case the workflow is about, if one is loaded. */
  caseId: string | null
  onNavigate: (path: RoutePath, params?: { caseId?: string }) => void
}) {
  const done = caseWorkflowDone(investigation, caseId)
  const activeStep = activeStepFor(routePath)

  return (
    <nav
      className="case-workflow-breadcrumb-bar__steps"
      aria-label="Investigation Workflow"
    >
      {CASE_WORKFLOW_STEPS.map((step, idx, arr) => {
        const isActive = activeStep === step.path
        const stepDone = done[step.path]
        return (
          <span key={step.path} className="case-workflow-breadcrumb-bar__step-wrap">
            <button
              type="button"
              className={`case-workflow-breadcrumb-bar__step${
                isActive ? ' case-workflow-breadcrumb-bar__step--active' : ''
              }${stepDone && !isActive ? ' case-workflow-breadcrumb-bar__step--done' : ''}`}
              onClick={() => {
                if (caseId) {
                  onNavigate(step.path, { caseId })
                } else if (step.path === 'case-detail') {
                  onNavigate('cases')
                }
              }}
              disabled={!caseId && step.path !== 'case-detail'}
              /* The active step is announced, not merely coloured: the
                 active/done distinction is otherwise carried by colour and a
                 tick glyph alone. */
              aria-current={isActive ? 'step' : undefined}
              title={
                stepDone
                  ? `${step.label} — completed`
                  : isActive
                    ? `${step.label} — current step`
                    : step.label
              }
            >
              <span className="case-workflow-breadcrumb-bar__step-idx" aria-hidden>
                {stepDone && !isActive ? '✓' : `${idx + 1}.`}
              </span>
              <span>{step.label}</span>
              {stepDone && !isActive ? <span className="sr-only"> (completed)</span> : null}
            </button>
            {idx < arr.length - 1 ? (
              <span className="case-workflow-breadcrumb-bar__sep" aria-hidden>
                →
              </span>
            ) : null}
          </span>
        )
      })}
    </nav>
  )
}

/**
 * The one canonical case-context row: which case, and where in its workflow.
 *
 * This is the single place the console states "you are working on case
 * #PRAMAAN-1001, at step 3 of 6". Screens deliberately do not restate it. Before
 * this was consolidated, the case number was reprinted by the intake screen, the
 * analysis screen and the case dossier, each from its own copy of the case row,
 * and the workflow row was rendered four more times below this one -- so a
 * mid-flight case switch could leave two different case numbers on screen at
 * once, and the operator had no way to know which one the next click would act
 * on.
 *
 * The route gate lives here rather than at the call site so there is exactly one
 * decision about where this row belongs. On a route that is not part of a case
 * workflow (Dashboard, the Cases queue, Settings) it renders nothing at all:
 * claiming a workflow position above a global destination is a false statement
 * about where the operator is.
 */
export function CaseContextBar({
  investigation,
  routePath,
  routeCaseId,
  onNavigate,
}: {
  investigation: Investigation
  routePath: RoutePath
  /** The case id in the URL, used when the store has not loaded the row yet. */
  routeCaseId: string | null
  onNavigate: (path: RoutePath, params?: { caseId?: string }) => void
}) {
  if (!CASE_WORKFLOW_ROUTES.includes(routePath)) return null

  const { caseRecord } = investigation

  /*
   * No case, no workflow position.
   *
   * Every route on this list can be reached with no case open, and there the bar
   * had nothing true to say: it printed "No case loaded (Intake / New Case)"
   * beside six numbered steps, five of them disabled, and the screen underneath
   * then said "No case is selected" in its own words. Two claims about the same
   * absence, the first of them dressed as a position in a workflow the operator
   * has not entered.
   *
   * `#evidence` was the worst of it -- the full stepper above 269 exhibits
   * belonging to other investigations -- but Analysis, Provenance, Audit, Reports
   * and the case dossier all did it, and each of those screens already renders
   * the canonical `NoCaseSelected` with the one action that applies.
   *
   * `intake` is the exception, and deliberately: with no case it opens the
   * new-case form, which genuinely *is* the start of a workflow, so the bar
   * belongs there saying exactly that.
   */
  if (routePath !== 'intake' && !caseRecord && !routeCaseId) return null

  return (
    <div className="case-workflow-breadcrumb-bar">
      <div className="case-workflow-breadcrumb-bar__case">
        <span className="case-workflow-breadcrumb-bar__tag">CASE</span>
        {caseRecord ? (
          <button
            type="button"
            className="case-workflow-breadcrumb-bar__case-btn"
            onClick={() => onNavigate('case-detail', { caseId: caseRecord.case_id })}
            title={`Open case dossier #${caseRecord.case_number}`}
          >
            <span className="case-workflow-breadcrumb-bar__case-num">#{caseRecord.case_number}</span>
            {caseRecord.title ? (
              <span className="case-workflow-breadcrumb-bar__case-title">· {caseRecord.title}</span>
            ) : null}
          </button>
        ) : (
          /* No case number is invented while one is being opened, and no blank
             is left where one would go: the row says which state it is in. */
          <span className="case-workflow-breadcrumb-bar__no-case">No case loaded (Intake / New Case)</span>
        )}
      </div>

      <CaseWorkflowStepper
        investigation={investigation}
        routePath={routePath}
        caseId={caseRecord?.case_id || routeCaseId || null}
        onNavigate={onNavigate}
      />
    </div>
  )
}
