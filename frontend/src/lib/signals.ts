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
export function statusLabel(status: SignalStatus | string): string {
  switch (status) {
    case 'OK':
      return 'ASSESSED'
    case 'NO_MATCH':
      return 'NO MATCH'
    case 'NOT_PRESENT':
    case 'NOT_FOUND':
      return 'NOT PRESENT'
    case 'UNAVAILABLE':
      return 'NOT AVAILABLE'
    case 'INCONCLUSIVE':
      return 'INCONCLUSIVE'
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
 * Rahul calls this "the strongest credibility sentence in the product": the
 * interface always states how much of the evidence base the verdict rests on.
 *
 * `signals_available` is the count that was actually assessed; `signals_total`
 * is the count that was considered. Reading the total as "assessed" -- which an
 * earlier phrasing did -- overstates the evidence base by however many signals
 * were unavailable.
 */
export function coverageLine(verdict: Verdict): string {
  const total = verdict.signals_total
  const available = verdict.signals_available
  return `${available} OF ${total} SIGNALS ASSESSED`
}

/** Longer form, spelling out the fraction of the evidence base. */
export function coverageSentence(verdict: Verdict): string {
  const pct = Math.round((verdict.signal_coverage ?? 0) * 100)
  return `Verdict computed on ${verdict.signals_available} of ${verdict.signals_total} signals - ${pct}% of the evidence base by weight.`
}
