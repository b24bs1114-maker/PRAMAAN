/**
 * Case deletion flow, as data.
 *
 * Deleting a case is the one irreversible action in this console, so the rules
 * governing it live here as pure functions rather than inside a component: the
 * screens render this state, they do not invent it.
 *
 * Two properties this module exists to guarantee:
 *
 *  - The UI never claims a deletion happened. `deleted` is only reachable by
 *    handing `deletionSucceeded` a real `CaseDeleteResult` from the backend, and
 *    every figure the success message shows is read off that result. A failure
 *    lands in `failed` with the error itself, which the dialog renders.
 *  - Confirmation is explicit. `canConfirm` is false until the operator has
 *    typed the case number the backend issued, so a permanent delete cannot be
 *    reached by one mis-aimed click.
 *
 * The phases: `idle` (no dialog) -> `confirming` (dialog open, waiting on the
 * typed case number) -> `deleting` (request in flight) -> `deleted` or `failed`.
 * `failed` is re-confirmable, because the usual cause is a backend that was
 * briefly unreachable and the case is still there.
 */

import type { CaseDeleteResult, CaseRecord } from '../api/types'

export type CaseDeletionPhase = 'idle' | 'confirming' | 'deleting' | 'deleted' | 'failed'

export interface CaseDeletionState {
  phase: CaseDeletionPhase
  /** The case the operator asked to delete. Null only while `idle`. */
  target: CaseRecord | null
  /** Exactly what the operator has typed into the confirmation field. */
  typed: string
  /** The backend's account of what it removed. Non-null only when `deleted`. */
  result: CaseDeleteResult | null
  /** The failure, unchanged, for the error banner. Non-null only when `failed`. */
  error: unknown
}

export const IDLE_DELETION: CaseDeletionState = {
  phase: 'idle',
  target: null,
  typed: '',
  result: null,
  error: null,
}

/** Open the confirmation dialog for one real case row. */
export function askToDelete(target: CaseRecord): CaseDeletionState {
  return { phase: 'confirming', target, typed: '', result: null, error: null }
}

/** Record a keystroke in the confirmation field. Ignored once the delete is away. */
export function typeConfirmation(state: CaseDeletionState, typed: string): CaseDeletionState {
  if (state.phase === 'deleting' || state.phase === 'deleted') return state
  return { ...state, typed }
}

/**
 * Close the dialog.
 *
 * Refused while the request is in flight: the backend may already have committed
 * the delete, and closing the dialog at that moment would leave the operator
 * unable to tell whether it happened.
 */
export function dismissDeletion(state: CaseDeletionState): CaseDeletionState {
  if (state.phase === 'deleting') return state
  return IDLE_DELETION
}

/**
 * Compare a typed case number with an issued one.
 *
 * The leading `#` is stripped because that is how the case number is displayed
 * on every screen, so an operator copying what they can see types it. Case and
 * surrounding whitespace are ignored; nothing else is.
 */
export function normaliseCaseNumber(value: string): string {
  return value.trim().replace(/^#+/, '').toUpperCase()
}

/** True when the typed text is the target's own case number. */
export function isConfirmed(state: CaseDeletionState): boolean {
  const issued = state.target?.case_number
  if (!issued || !issued.trim()) return false
  return normaliseCaseNumber(state.typed) === normaliseCaseNumber(issued)
}

/** True when the destructive button may be pressed. */
export function canConfirm(state: CaseDeletionState): boolean {
  if (state.phase !== 'confirming' && state.phase !== 'failed') return false
  return isConfirmed(state)
}

/** True while the request is with the backend -- drives the button's spinner. */
export function isDeleting(state: CaseDeletionState): boolean {
  return state.phase === 'deleting'
}

/** The request has left. Only `runCaseDeletion` should reach this. */
export function beginDeletion(state: CaseDeletionState): CaseDeletionState {
  return { ...state, phase: 'deleting', result: null, error: null }
}

/**
 * The backend confirmed the delete.
 *
 * `result` is required: there is no way to reach `deleted` without the backend's
 * own response, which is what stops the UI from reporting a success it has not
 * been told about.
 */
export function deletionSucceeded(
  state: CaseDeletionState,
  result: CaseDeleteResult,
): CaseDeletionState {
  return { ...state, phase: 'deleted', result, error: null }
}

/** The delete failed. The error is kept as thrown so the banner can read it. */
export function deletionFailed(state: CaseDeletionState, error: unknown): CaseDeletionState {
  return { ...state, phase: 'failed', result: null, error }
}

export interface CaseDeletionDeps {
  /** The real API call. Injected so this module never imports the transport. */
  deleteCase(caseId: string): Promise<CaseDeleteResult>
  /** Publish each state transition (the screens pass their setState). */
  emit(next: CaseDeletionState): void
  /**
   * Called once, only after the backend has confirmed. This is where a screen
   * drops the row from its list or navigates away -- both of which would be lies
   * if they ran on the failure path, so neither is wired to anything else.
   */
  onDeleted?(result: CaseDeleteResult, target: CaseRecord): void
}

/**
 * Send the delete and report what happened.
 *
 * Returns the final state and emits every transition on the way, so a caller
 * that only renders `emit` output still shows the in-flight state. The error is
 * not rethrown -- it is carried in the returned state and rendered by the dialog;
 * rethrowing from a click handler would produce an unhandled rejection and no
 * message on screen, which is the opposite of surfacing it.
 *
 * A state that is not confirmed is returned untouched: no request, no
 * transitions. That makes a double-press of the destructive button harmless,
 * since the first press moves the phase to `deleting`.
 */
export async function runCaseDeletion(
  state: CaseDeletionState,
  deps: CaseDeletionDeps,
): Promise<CaseDeletionState> {
  const target = state.target
  if (!target || !canConfirm(state)) return state

  const inFlight = beginDeletion(state)
  deps.emit(inFlight)

  try {
    const result = await deps.deleteCase(target.case_id)
    const done = deletionSucceeded(inFlight, result)
    deps.emit(done)
    deps.onDeleted?.(result, target)
    return done
  } catch (error) {
    const failed = deletionFailed(inFlight, error)
    deps.emit(failed)
    return failed
  }
}

/** The case list without one case. Used to update the queue after a real delete. */
export function removeCase(cases: CaseRecord[], caseId: string): CaseRecord[] {
  return cases.filter((entry) => entry.case_id !== caseId)
}

/**
 * `"1 report"` / `"4 reports"`.
 *
 * The default plural is a naive `+s`, so any noun that does not take it (match,
 * analysis) has to pass `many` explicitly. The contract test rejects a notice
 * containing a `-chs`/`-shs`/`-ss`/`-xs` ending, which is what a missed one
 * looks like.
 */
function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * What the backend reported it removed, as lines for the confirmation notice.
 *
 * Every line is read off the response. Nothing is inferred and nothing is
 * omitted for being awkward: the cross-case disclosures are included when the
 * backend reports them, because an examiner working a different case is entitled
 * to know a comparison disappeared.
 */
export function deletionSummary(result: CaseDeleteResult): string[] {
  const lines: string[] = []
  const { deleted, storage, index, audit } = result

  lines.push(
    `${plural(deleted.evidence, 'evidence record')}, ` +
      `${plural(deleted.analysis_results, 'analysis result')}, ` +
      `${plural(deleted.matches, 'match', 'matches')} and ` +
      `${plural(deleted.reports, 'report')} removed.`,
  )

  const files = `${plural(storage.evidence_files_removed, 'stored file')} deleted from disk`
  lines.push(
    storage.evidence_files_missing > 0
      ? `${files}; ${storage.evidence_files_missing} were already absent.`
      : `${files}.`,
  )
  if (storage.report_files_removed > 0 || storage.report_files_missing > 0) {
    lines.push(
      `${plural(storage.report_files_removed, 'report PDF')} deleted` +
        (storage.report_files_missing > 0
          ? `; ${storage.report_files_missing} were already absent.`
          : '.'),
    )
  }
  if (storage.case_directory) {
    lines.push(
      storage.case_directory_removed
        ? `Case directory ${storage.case_directory} removed.`
        : `Case directory ${storage.case_directory} could not be removed.`,
    )
  }

  lines.push(
    `${plural(index.vectors_removed, 'perceptual index vector')} dropped from the search index` +
      (index.rebuild_required ? ' -- a rebuild is required.' : '.'),
  )

  if (deleted.matches_owned_by_other_cases > 0) {
    const owned = deleted.matches_owned_by_other_cases
    lines.push(
      `${plural(owned, 'match', 'matches')} filed under other cases ` +
        `referenced this evidence and ${owned === 1 ? 'was' : 'were'} removed with it. ` +
        'Those cases keep their own evidence.',
    )
  }
  if (deleted.timeline_events_detached > 0) {
    const detached = deleted.timeline_events_detached
    lines.push(
      `${plural(detached, 'timeline event')} belonging to other cases ` +
        `lost ${detached === 1 ? 'its' : 'their'} link to this evidence but ` +
        `${detached === 1 ? 'was' : 'were'} not deleted.`,
    )
  }

  lines.push(
    `${audit.event} recorded at chain position #${audit.seq}. ` +
      `${plural(audit.case_rows_retained, 'audit row')} for this case ${
        audit.case_rows_retained === 1 ? 'remains' : 'remain'
      } verifiable.`,
  )

  return lines
}
