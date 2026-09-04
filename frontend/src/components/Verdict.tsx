/**
 * Verdict presentation: the band, the score against the backend's own
 * thresholds, the signals that produced it, and the published arithmetic.
 *
 * Three defects in the previous build are structurally impossible here:
 *
 *  1. `manipulation_score ?? 0` -- a null score was drawn as a bar at zero,
 *     which reads as "measured, and clean". A null score now draws no marker at
 *     all and says so in words.
 *  2. `(Number(verdict.confidence) * 100).toFixed(0)%` -- confidence is a band
 *     string (`none` / `low` / `moderate`), so this rendered the literal text
 *     `NaN%`. The band is now shown as the word the backend sent.
 *  3. `'0.0000 (Excluded)'` for an excluded signal's contribution -- a number
 *     where there was no measurement. Excluded signals now read "Not included".
 *
 * Nothing in this file computes a forensic value. Zones come from
 * `verdict.thresholds`, direction from `signalPillVariant`, and the fusion sum is
 * printed verbatim from `verdict.arithmetic`.
 */

import type { ReactNode } from 'react'
import { Icon, type IconName } from './Icon'
import { HashChip } from './Hash'
import { Callout, Field, Fields, Panel, StatusPill } from './Primitives'
import { cx } from '../lib/cx'
import { formatScore, formatTimestamp, formatWeight, NOT_MEASURED } from '../lib/format'
import {
  barGeometry,
  coverageSentence,
  exclusionSummary,
  isExcluded,
  signalPillVariant,
  statusLabel,
  verdictBandLabel,
  verdictTone,
} from '../lib/signals'
import { confidenceToneOf, humanise, signalStatusToneOf } from '../lib/tone'
import type { Signal, Verdict, VerdictBand } from '../api/types'

/** Shape, not colour, carries the band. */
export function verdictIconName(band: VerdictBand | string | null | undefined): IconName {
  switch (verdictTone(band)) {
    case 'authentic':
      return 'check'
    case 'manipulated':
      return 'alert'
    default:
      return 'inconclusive'
  }
}

/**
 * The fused score plotted against the thresholds the backend published with it.
 *
 * If those thresholds are absent the track is hatched and no zones are drawn:
 * there is nothing to place the score against, and inventing a midpoint would be
 * inventing a decision boundary. If the score itself is null there is no marker.
 */
export function ScoreScale({
  score,
  thresholds,
}: {
  score: number | null
  thresholds: Verdict['thresholds'] | null | undefined
}) {
  const authenticAt = thresholds?.authentic_at_or_below
  const manipulatedAt = thresholds?.manipulated_at_or_above
  const zoned =
    typeof authenticAt === 'number' &&
    typeof manipulatedAt === 'number' &&
    manipulatedAt > authenticAt

  const label = zoned
    ? `Manipulation score ${score === null ? 'not measured' : formatScore(score)} on a 0 to 1 scale. Authentic at or below ${formatScore(authenticAt)}; manipulated at or above ${formatScore(manipulatedAt)}.`
    : `Manipulation score ${score === null ? 'not measured' : formatScore(score)} on a 0 to 1 scale. The backend published no decision thresholds with this verdict.`

  return (
    <div className="scale">
      <div className={cx('scale__track', !zoned && 'scale__track--unknown')} role="img" aria-label={label}>
        {zoned ? (
          <>
            <div className="scale__zone scale__zone--authentic" style={{ width: `${authenticAt * 100}%` }} />
            <div
              className="scale__zone scale__zone--middle"
              style={{ width: `${(manipulatedAt - authenticAt) * 100}%` }}
            />
            <div
              className="scale__zone scale__zone--manipulated"
              style={{ width: `${(1 - manipulatedAt) * 100}%` }}
            />
          </>
        ) : null}
        {score !== null ? (
          <div className="scale__marker" style={{ left: `${Math.max(0, Math.min(1, score)) * 100}%` }} />
        ) : null}
      </div>

      <div className="scale__axis">
        <span>0.000 · no manipulation evidence</span>
        <span>1.000 · maximum</span>
      </div>

      {zoned ? (
        <div className="scale__legend">
          <span className="scale__legend-item">
            <span className="scale__swatch scale__swatch--authentic" />
            Authentic at or below {formatScore(authenticAt)}
          </span>
          <span className="scale__legend-item">
            <span className="scale__swatch scale__swatch--middle" />
            Between thresholds · inconclusive
          </span>
          <span className="scale__legend-item">
            <span className="scale__swatch scale__swatch--manipulated" />
            Manipulated at or above {formatScore(manipulatedAt)}
          </span>
        </div>
      ) : (
        <div className="scale__legend">
          No decision thresholds were published with this verdict, so the score is shown without zones.
        </div>
      )}
    </div>
  )
}

/**
 * The headline assessment.
 *
 * Everything above the fold is the backend's: the band (hedged in wording, with
 * the raw token shown beside it), the confidence band as a word, and the
 * coverage the verdict rests on. INSUFFICIENT_EVIDENCE gets the same weight of
 * presentation as the other two bands -- it is a forensic result, not a failure.
 */
export function VerdictCard({ verdict, actions }: { verdict: Verdict; actions?: ReactNode }) {
  const tone = verdictTone(verdict.verdict)
  const measured = verdict.manipulation_score !== null

  return (
    <div className={cx('verdict', `verdict--${tone}`)}>
      <div className="verdict__top">
        <div className="verdict__mark">
          <Icon name={verdictIconName(verdict.verdict)} size={24} />
        </div>
        <div className="verdict__headings">
          <div className="verdict__eyebrow">Fused assessment · {humanise(verdict.media_type)} evidence</div>
          <div className="verdict__band">{verdictBandLabel(verdict.verdict)}</div>
          <div className="verdict__sub">
            Backend verdict token <span className="mono">{verdict.verdict}</span> · {verdict.filename}
          </div>
        </div>
        <div className="verdict__aside">
          <StatusPill
            tone={confidenceToneOf(verdict.confidence)}
            large
            title="The backend publishes a confidence band, not a percentage."
          >
            Confidence: {humanise(verdict.confidence)}
          </StatusPill>
          <span className="eyebrow">
            {verdict.signals_available} of {verdict.signals_total} signals available
          </span>
          {actions}
        </div>
      </div>

      <div className="verdict__body">
        <p className="verdict__rationale">{verdict.rationale}</p>

        <ScoreScale score={verdict.manipulation_score} thresholds={verdict.thresholds} />

        <Fields variant="wide">
          <Field
            label="Manipulation score"
            value={measured ? formatScore(verdict.manipulation_score) : 'Not measured'}
            mono
            strong
            unmeasured={!measured}
            note={verdict.score_semantics}
          />
          <Field
            label="Evidence base"
            value={`${verdict.signals_available} of ${verdict.signals_total} signals`}
            note={coverageSentence(verdict)}
          />
          <Field
            label="Primary signal"
            value={verdict.primary_signal_available ? 'Available' : 'Not available'}
            unmeasured={!verdict.primary_signal_available}
            note={
              verdict.primary_signal_available
                ? 'A signal capable of establishing authenticity on its own contributed to this verdict.'
                : 'No signal capable of establishing authenticity on its own was available, which caps how far the verdict can go.'
            }
          />
          <Field
            label="Fusion"
            value={`${verdict.method} · ${verdict.fusion_version}`}
            mono
          />
          <Field
            label="Fused at"
            value={formatTimestamp(verdict.fused_at)}
            mono
            unmeasured={!verdict.fused_at}
            note={verdict.cached ? 'Served from the stored result for this evidence item.' : undefined}
          />
          <Field label="Evidence SHA-256" value={<HashChip value={verdict.sha256} algo={null} length={20} />} />
        </Fields>

        {verdict.caveat ? <Callout label="Caveat">{verdict.caveat}</Callout> : null}
      </div>
    </div>
  )
}

/**
 * One signal, with its measurement and why it did or did not count.
 *
 * An excluded signal is drawn with a dashed empty track and always carries both
 * the backend's explanation and the one-line exclusion reason. Its contribution
 * reads "Not included" -- there is no number to print, and printing 0.0000 would
 * assert a measurement of zero.
 */
export function SignalRow({
  signal,
  thresholds,
  primary,
}: {
  signal: Signal
  thresholds: Verdict['thresholds'] | null | undefined
  primary?: boolean
}) {
  const excluded = isExcluded(signal)
  const variant = signalPillVariant(signal, thresholds)
  const bar = barGeometry(signal)

  return (
    <div className={cx('signal', `signal--${variant}`, excluded && 'signal--excluded')}>
      <div className="signal__main">
        <div className="signal__head">
          <Icon name={excluded ? 'inconclusive' : 'activity'} size={14} />
          <span className="signal__name">{signal.name}</span>
          <StatusPill tone={signalStatusToneOf(signal.status)}>{statusLabel(signal.status)}</StatusPill>
          {primary ? (
            <StatusPill tone="accent" title="This signal can establish authenticity on its own.">
              Primary
            </StatusPill>
          ) : null}
        </div>

        <p className="signal__explanation">{signal.explanation}</p>

        {bar ? (
          <div className="signal__bar">
            <div className="signal__bar-fill" style={{ width: `${bar.widthPercent}%` }} />
          </div>
        ) : (
          <>
            <div className="signal__bar signal__bar--empty" />
            <div className="signal__note">{exclusionSummary(signal.status)}</div>
          </>
        )}
      </div>

      <div className="signal__numbers">
        <div className="signal__num-row">
          <span className="signal__num-key">Score</span>
          <span className={cx('signal__num-val', signal.score === null && 'signal__num-val--none')}>
            {signal.score === null ? NOT_MEASURED : formatScore(signal.score, 4)}
          </span>
        </div>
        <div className="signal__num-row">
          <span className="signal__num-key">Declared weight</span>
          <span className="signal__num-val">{formatWeight(signal.weight)}</span>
        </div>
        <div className="signal__num-row">
          <span className="signal__num-key">Effective weight</span>
          <span className={cx('signal__num-val', excluded && 'signal__num-val--none')}>
            {excluded ? NOT_MEASURED : formatWeight(signal.effective_weight)}
          </span>
        </div>
        <div className="signal__num-row">
          <span className="signal__num-key">Contribution</span>
          <span
            className={cx(
              'signal__num-val',
              (excluded || signal.contribution === null) && 'signal__num-val--none',
            )}
          >
            {excluded || signal.contribution === null ? 'Not included' : formatScore(signal.contribution, 4)}
          </span>
        </div>
      </div>
    </div>
  )
}

/**
 * The signal panel. Included signals first, then excluded ones -- the reader
 * should reach the evidence that counted before the evidence that could not.
 */
export function SignalList({
  signals,
  thresholds,
  primarySignals,
  title = 'Forensic signals',
  subtitle,
  actions,
}: {
  signals: Signal[]
  thresholds: Verdict['thresholds'] | null | undefined
  primarySignals?: string[]
  title?: string
  subtitle?: ReactNode
  actions?: ReactNode
}) {
  const included = signals.filter((s) => !isExcluded(s))
  const excluded = signals.filter((s) => isExcluded(s))
  const ordered = [...included, ...excluded]

  return (
    <Panel
      title={title}
      subtitle={
        subtitle ??
        `${included.length} of ${signals.length} signals contributed to the fused score.`
      }
      actions={actions}
      flushBody
    >
      {ordered.map((signal) => (
        <SignalRow
          key={signal.signal_id}
          signal={signal}
          thresholds={thresholds}
          primary={primarySignals?.includes(signal.signal_id)}
        />
      ))}
    </Panel>
  )
}

/**
 * The fusion sum, printed exactly as the backend printed it.
 *
 * This is the interface's proof that it adds no maths of its own: the operator
 * can read the same expression the engine logged.
 */
export function Arithmetic({ verdict }: { verdict: Verdict }) {
  return (
    <div className="arithmetic">
      <div className="eyebrow">Published fusion arithmetic</div>
      <div className="arithmetic__expr">{verdict.arithmetic || NOT_MEASURED}</div>
      <div className="arithmetic__note">
        Printed by the backend fusion engine ({verdict.method} · {verdict.fusion_version}). Declared weights
        total {formatWeight(verdict.declared_weight_total)}; {formatWeight(verdict.available_weight)} of that
        was available and renormalised across the contributing signals. The interface performs no arithmetic
        of its own.
      </div>
    </div>
  )
}
