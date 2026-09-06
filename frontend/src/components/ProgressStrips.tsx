import type { Signal, Verdict } from '../api/types'
import { statusLabel } from '../lib/signals'

/**
 * Live progress strips, one per signal, shown while analysis runs.
 *
 * The backend does not stream per-signal progress, so a running strip sweeps as
 * indeterminate rather than animating a fabricated percentage. The moment the
 * response lands each strip settles to the signal's real status.
 *
 * The labels shown while in flight are the applicable set for the media type
 * being analysed, taken from the backend's own applicability response
 * (`/api/system/signals`) -- never a hardcoded list. Applicability is decided
 * by the backend alone; a signal that does not apply to the media type never
 * gets a strip at all.
 */
export function ProgressStrips({
  running,
  signals,
  thresholds,
  pendingLabels,
}: {
  running: boolean
  signals: Signal[] | null
  thresholds?: Verdict['thresholds'] | null
  /** Applicable signal labels for the in-flight state, from backend truth. */
  pendingLabels?: ReadonlyArray<{ id: string; label: string }>
}) {
  void thresholds

  const fallback: ReadonlyArray<{ id: string; label: string }> = [
    { id: 'ai_detection', label: 'AI manipulation detector' },
  ]
  const placeholders = pendingLabels && pendingLabels.length > 0 ? pendingLabels : fallback

  const rows = signals?.length
    ? signals.map((s) => ({ id: s.signal_id, label: s.name, signal: s }))
    : placeholders.map((s) => ({ id: s.id, label: s.label, signal: null }))

  return (
    <div className="strips" aria-live="polite" aria-busy={running}>
      {rows.map(({ id, label, signal }) => {
        const done = Boolean(signal) && !running
        return (
          <div
            key={id}
            className={`strip${running ? ' strip--running' : ''}${done ? ' strip--done' : ''}`}
          >
            <span className="strip__name">{label}</span>
            <span className="strip__track">
              <span className="strip__fill" />
            </span>
            <span className="strip__state">
              {running ? 'assessing…' : signal ? statusLabel(signal.status) : 'not started'}
            </span>
          </div>
        )
      })}
    </div>
  )
}
