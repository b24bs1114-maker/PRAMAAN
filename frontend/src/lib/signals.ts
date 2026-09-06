/**
 * Signal and verdict presentation.
 *
 * This module decides how a backend value *looks*. It never decides what a
 * value *is*: no score is recomputed, no threshold is invented, and no signal
 * is promoted or demoted here. Direction cues come from the thresholds the
 * backend publishes in its own verdict payload.
 *
 * Two rules from the workflow specification are enforced structurally rather
 * than left to each screen:
 *
 *   1. An excluded signal is greyed AND carries its explanation sentence.
 *      Never one without the other -- a greyed bar alone reads as a score of
 *      zero, which is the precise misreading this product exists to prevent.
 *
 *   2. The verdict band carries no percentage. `verdictBandLabel` returns words
 *      only; the numeric score is shown separately, labelled as what it is.
 */

import type { Signal, SignalStatus, Verdict, VerdictBand } from '../api/types'

/** The statuses the backend's fusion engine can emit. */
const EXCLUDED_STATUSES: ReadonlySet<string> = new Set([
  'UNAVAILABLE',
  'INCONCLUSIVE',
  'ERROR',
  'UNSUPPORTED_MEDIA',
  'NO_MATCH',
  'NOT_PRESENT',
  'NOT_FOUND',
])

/** True when the backend excluded this signal from the fused score. */
export function isExcluded(signal: Signal): boolean {
  // `included` is the backend's own determination; status is the fallback for
  // any future status value this build does not know about.
  return signal.included === false || EXCLUDED_STATUSES.has(signal.status)
}

/**
 * Short, honest label for a status.
 *
 * Each phrasing states what happened, never a conclusion. "Not available" is
 * not "clean"; "inconclusive" is not "suspicious".
 */
export function statusLabel(status: SignalStatus | string, signal?: Partial<Signal>): string {
  if (signal) {
    if (signal.status === 'UNSUPPORTED_MEDIA') return 'NOT APPLICABLE'
    const basis = signal.evidence_basis
    if (
      signal.status === 'INCONCLUSIVE' ||
      basis?.availability === 'ran_and_declined' ||
      basis?.abstained === true ||
      String(signal.explanation || '').toLowerCase().includes('abstained')
    ) {
      return 'RAN — ABSTAINED'
    }
    if (signal.included && signal.score !== null) {
      return 'CONTRIBUTED'
    }
    if (signal.included === false) {
      if (signal.status === 'UNAVAILABLE') return 'UNAVAILABLE'
      if (signal.status === 'ERROR') return 'ERROR'
      return 'EXCLUDED FROM FUSION'
    }
  }

  switch (status) {
    case 'OK':
      return 'CONTRIBUTED'
    case 'NO_MATCH':
      return 'NO MATCH'
    case 'NOT_PRESENT':
    case 'NOT_FOUND':
      return 'NOT PRESENT'
    case 'UNAVAILABLE':
      return 'UNAVAILABLE'
    case 'INCONCLUSIVE':
      return 'RAN — ABSTAINED'
    case 'ERROR':
      return 'ERROR'
    case 'UNSUPPORTED_MEDIA':
      return 'NOT APPLICABLE'
    default:
      return String(status).replace(/_/g, ' ')
  }
}

/**
 * One-line reason a signal did not contribute, for the availability column.
 *
 * The full backend `explanation` is always rendered too; this is the summary
 * that fits in a table cell.
 */
export function exclusionSummary(status: SignalStatus | string): string {
  switch (status) {
    case 'NO_MATCH':
      return 'No similar item found in index - excluded from the score.'
    case 'NOT_PRESENT':
    case 'NOT_FOUND':
      return 'No metadata or C2PA manifest present - excluded from the score.'
    case 'UNAVAILABLE':
      return 'Could not run in this deployment - excluded from the score.'
    case 'INCONCLUSIVE':
      return 'Ran but could not decide - excluded from the score.'
    case 'ERROR':
      return 'Failed during analysis - excluded from the score.'
    case 'UNSUPPORTED_MEDIA':
      return 'Does not apply to this media type - excluded from the score.'
    default:
      return 'Excluded from the score.'
  }
}

export type PillVariant =
  | 'strong-authentic'
  | 'weak-authentic'
  | 'neutral'
  | 'weak-manipulated'
  | 'strong-manipulated'
  | 'unavailable'
  | 'warn'

/**
 * Pill styling for a signal.
 *
 * For an excluded signal the answer is always `unavailable` -- the dashed,
 * unfilled treatment. For an included signal the direction is read off the
 * backend's own published thresholds, so this build has no opinion of its own
 * about where "manipulated" begins.
 */
export function signalPillVariant(
  signal: Signal,
  thresholds?: Verdict['thresholds'] | null,
): PillVariant {
  const lbl = statusLabel(signal.status, signal)
  if (lbl === 'RAN — ABSTAINED') return 'warn'
  if (isExcluded(signal) || signal.score === null) return 'unavailable'

  const manipulatedAt = thresholds?.manipulated_at_or_above
  const authenticAt = thresholds?.authentic_at_or_below

  // Without backend thresholds there is nothing to compare against, so the
  // signal is shown as assessed-but-undirected rather than guessed at.
  if (typeof manipulatedAt !== 'number' || typeof authenticAt !== 'number') return 'neutral'

  const score = signal.score
  if (score >= manipulatedAt) {
    // Midway between the manipulated threshold and the 1.0 ceiling.
    return score >= manipulatedAt + (1 - manipulatedAt) / 2 ? 'strong-manipulated' : 'weak-manipulated'
  }
  if (score <= authenticAt) {
    return score <= authenticAt / 2 ? 'strong-authentic' : 'weak-authentic'
  }
  return 'neutral'
}

/**
 * Fill geometry for a signal bar.
 *
 * The track represents 0.0 to 1.0 of the backend's manipulation score, which is
 * one-directional, so the fill starts at the left edge. Returns null for an
 * excluded signal: there is no length to draw, and drawing a zero-length bar
 * would imply a measurement of zero.
 */
export function barGeometry(signal: Signal): { widthPercent: number } | null {
  if (isExcluded(signal) || signal.score === null) return null
  const clamped = Math.max(0, Math.min(1, signal.score))
  return { widthPercent: clamped * 100 }
}

// --- Verdict -----------------------------------------------------------------

export type VerdictTone = 'authentic' | 'manipulated' | 'inconclusive'

export function verdictTone(band: VerdictBand | string | null | undefined): VerdictTone {
  switch (band) {
    case 'AUTHENTIC':
      return 'authentic'
    case 'MANIPULATED':
      return 'manipulated'
    default:
      // INSUFFICIENT_EVIDENCE, and anything this build does not recognise, are
      // shown as inconclusive rather than being forced into a decision.
      return 'inconclusive'
  }
}

/**
 * Chip tone for a verdict band, for the `Pill` component.
 *
 * Distinct from `verdictTone`, which names the band semantically for the hero
 * treatment. This one answers a narrower question -- which of the chip colours a
 * status pill should take -- and the two are deliberately not merged: the hero
 * has three states and the chip has four, because a chip also has to render "no
 * verdict at all".
 *
 * That fourth state is the reason this lives here rather than in three screens.
 * `neutral` for absent is the whole point: a case with no fused verdict is not
 * inconclusive, and colouring it like an inconclusive one would turn a case that
 * has never been examined into a finding. The Cases queue, the case record and
 * the Analysis screen all show this chip, and they each had their own copy of the
 * mapping -- three places for that distinction to drift.
 *
 * The return type is spelled out rather than imported as `PillTone` to keep this
 * module free of a dependency on the component that consumes it; every member is
 * a valid `PillTone`.
 */
export function verdictPillTone(
  band: VerdictBand | string | null | undefined,
): PillVariant | 'ok' | 'error' {
  if (!band) return 'neutral'
  if (band.includes('MANIPULATED')) return 'error'
  if (band.includes('AUTHENTIC')) return 'ok'
  return 'warn'
}

/**
 * Hedged display wording for the band.
 *
 * The specification requires the hedge: a forensic prototype may report that
 * evidence leans one way, never that a file *is* fake. The backend's raw token
 * is displayed alongside this label, so nothing is concealed by the rephrasing.
 */
export function verdictBandLabel(band: VerdictBand | string | null | undefined): string {
  switch (band) {
    case 'AUTHENTIC':
      return 'LIKELY AUTHENTIC'
    case 'MANIPULATED':
      return 'LIKELY MANIPULATED'
    case 'INSUFFICIENT_EVIDENCE':
      return 'INCONCLUSIVE'
    case null:
    case undefined:
      return 'NO VERDICT'
    default:
      return String(band)
  }
}

/**
 * Authoritative task-qualified assessment state label.
 * Rendered from backend Assessment.state.
 */
export function assessmentStateLabel(state: string | null | undefined): string {
  switch (state) {
    case 'INDICATORS_DETECTED':
      return 'INDICATORS DETECTED'
    case 'NO_INDICATORS_DETECTED':
      return 'NO INDICATORS DETECTED'
    case 'INCONCLUSIVE':
      return 'INCONCLUSIVE'
    case 'NOT_ASSESSED':
      return 'NOT ASSESSED'
    case null:
    case undefined:
      return 'NOT ASSESSED'
    default:
      return String(state).replace(/_/g, ' ')
  }
}

export function assessmentStateTone(state: string | null | undefined): VerdictTone {
  switch (state) {
    case 'INDICATORS_DETECTED':
      return 'manipulated'
    case 'NO_INDICATORS_DETECTED':
      return 'authentic'
    case 'INCONCLUSIVE':
    case 'NOT_ASSESSED':
    default:
      return 'inconclusive'
  }
}

export function assessmentPillTone(
  state: string | null | undefined,
): PillVariant | 'ok' | 'error' {
  if (!state) return 'neutral'
  switch (state) {
    case 'INDICATORS_DETECTED':
      return 'error'
    case 'NO_INDICATORS_DETECTED':
      return 'ok'
    case 'INCONCLUSIVE':
      return 'warn'
    case 'NOT_ASSESSED':
      return 'neutral'
    default:
      return 'neutral'
  }
}

export function executionStatusLabel(status: string | null | undefined): string {
  switch (status) {
    case 'COMPLETED':
      return 'COMPLETED'
    case 'PARTIAL':
      return 'PARTIAL EXECUTION'
    case 'FAILED':
      return 'FAILED EXECUTION'
    default:
      return String(status || 'UNKNOWN')
  }
}

/**
 * Confidence band, as a word.
 *
 * `Verdict.confidence` is a STRING BAND from the backend -- `none`, `low` or
 * `moderate`. It is never `high`, by construction: `fusion._confidence` carries
 * the comment "Never 'high' -- no threshold here is validated", because no
 * threshold in this system has been calibrated against ground truth.
 *
 * It is also not a probability. `Number('low')` is `NaN`, so the previous
 * build's `(Number(verdict.confidence) * 100).toFixed(0)%` rendered the string
 * `NaN%` and fell back to a hardcoded `82%` labelled "High Confidence" -- a
 * number no part of the system ever produced, attached to a band the system
 * refuses to emit. This function is the only sanctioned way to display the
 * field: word in, word out, no arithmetic.
 */
export function confidenceBandLabel(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'moderate':
      return 'MODERATE'
    case 'low':
      return 'LOW'
    case 'none':
      return 'NONE'
    case null:
    case undefined:
    case '':
      return 'NOT REPORTED'
    default:
      // An unrecognised band is shown verbatim rather than mapped onto one of
      // the known words, which would misstate what the backend said.
      return String(confidence).toUpperCase()
  }
}

/** The one-line gloss under the band, explaining what it does and does not mean. */
export function confidenceBandNote(confidence: string | null | undefined): string {
  switch (confidence) {
    case 'moderate':
      return 'Enough signal coverage and margin for the strongest band this system emits. Not a calibrated probability.'
    case 'low':
      return 'Thin coverage or a score close to a threshold. Treat the band as a lead, not a conclusion.'
    case 'none':
      return 'No signal could be scored, so no confidence is claimed.'
    case null:
    case undefined:
    case '':
      return 'The backend did not report a confidence band for this verdict.'
    default:
      return 'Band reported by the backend fusion engine. No threshold in this build is calibrated.'
  }
}

/**
 * The evidence-base line that sits beneath the band.
 *
 * *Media-aware.* All three counts come from the backend verdict and are scoped
 * to the signals APPLICABLE to this item's media type: `signals_total` is the
 * applicable set, `signals_evaluated` how many of them ran, and
 * `signals_available` how many contributed to the fused score. Inapplicable
 * signals are in none of the numbers -- not applicable is not failed and not
 * zero, so they are hidden rather than counted.
 */
export function coverageLine(verdict: Verdict): string {
  const total = verdict.signals_total
  const available = verdict.signals_available
  return `${available} OF ${total} SIGNALS ASSESSED`
}

/** Longer form, spelling out the fraction of the applicable evidence base. */
export function coverageSentence(verdict: Verdict): string {
  const pct = Math.round((verdict.signal_coverage ?? 0) * 100)
  return `Verdict computed on ${verdict.signals_available} of ${verdict.signals_total} signals - ${pct}% of the evidence base by weight.`
}

/**
 * The media-aware summary line: Applicable / Evaluated / Contributing.
 *
 * Every value is the backend's own, scoped to this item's media type. The
 * applicability set itself is the backend's (verdict.applicable_signals when
 * present, falling back to the signal list the backend actually fused); the
 * frontend never decides applicability on its own.
 */
export function mediaAwareSummary(verdict: Verdict): {
  applicable: number
  evaluated: number
  contributing: number
  line: string
} {
  const applicable = verdict.signals_total
  const evaluated = verdict.signals_evaluated
  const contributing = verdict.signals_available
  return {
    applicable,
    evaluated,
    contributing,
    line: `Applicable: ${applicable} · Evaluated: ${evaluated} · Contributing: ${contributing}`,
  }
}
