/**
 * "New Case" is a state transition, not a navigation.
 *
 * The investigation store is one long-lived object shared by every screen (see
 * state/useInvestigation). Navigating to the intake route does not empty it:
 * `upload`, `analysis`, `propagation`, `caseRecord` and the rest survive the
 * hash change, so Screen1Intake -- which shows its sealed dossier whenever
 * `upload.phase === 'ready'` -- would render the *previous* case's evidence,
 * hash and case number under a fresh "New Case" click. For a chain-of-custody
 * tool that is the worst possible confusion: the operator believes they are
 * starting Case B while looking at Case A's exhibit.
 *
 * So a new case is: discard all case-scoped state, then land on an intake route
 * that carries no stale identifier. `reset()` also bumps the store's generation
 * counter, which invalidates any upload/analysis still in flight from the case
 * being left -- a late response cannot repopulate the new, empty intake.
 *
 * This is deliberately a pure function of its two collaborators so it can be
 * driven directly in a test without React or a live store.
 */

import type { RoutePath } from './router'

export interface NewCaseDeps {
  /** Clears every case-scoped slice in the investigation store. */
  reset: () => void
  /** Hash navigation. Called with no params so the intake URL is clean. */
  navigate: (path: RoutePath) => void
}

/**
 * Start a brand-new case from a clean slate.
 *
 * Order matters: state is cleared first, then we navigate. If navigation ran
 * first, the intake screen could render one frame against the old, still-full
 * store before the reset landed.
 */
export function beginNewCase({ reset, navigate }: NewCaseDeps): void {
  reset()
  navigate('intake')
}

/**
 * The generation gate: what makes a reset safe while requests are in flight.
 *
 * Clearing the slices is not enough on its own. An upload or analysis issued for
 * Case A can still be on the wire when the operator clicks New Case, and its
 * `.then` closes over the setters for the very slices that were just emptied. A
 * late 201 would then write Case A's evidence -- filename, digest, case number --
 * straight into Case B's intake, some seconds after the screen had correctly
 * gone blank. That is the same chain-of-custody error as the original bug, only
 * harder to see, because it depends on network timing.
 *
 * So every case-scoped call takes a `snapshot()` before it is issued and asks
 * `accepts()` before it writes. `invalidate()`, which `reset()` calls, moves the
 * generation forward and every outstanding snapshot stops being accepted.
 *
 * This lives here rather than inline in the hook for two reasons: the rule was
 * previously repeated at eight call sites as a bare `gen !== generation.current`
 * comparison, and as a ref-and-closure idiom inside `useInvestigation` it could
 * not be driven by a test without a React renderer. It is a plain object with no
 * React in it, so the invariant can be asserted directly.
 */
export interface GenerationGate {
  /** Take the current generation, for a call about to be issued. */
  snapshot: () => number
  /** True when a response from `gen` may still be written to state. */
  accepts: (gen: number) => boolean
  /** Invalidate every outstanding snapshot. Called by `reset()`. */
  invalidate: () => void
}

export function createGenerationGate(): GenerationGate {
  let generation = 0
  return {
    snapshot: () => generation,
    accepts: (gen: number) => gen === generation,
    invalidate: () => {
      generation += 1
    },
  }
}

