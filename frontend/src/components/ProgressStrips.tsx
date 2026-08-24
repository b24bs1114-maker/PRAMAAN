import type { Signal, Verdict } from '../api/types'
import { statusLabel } from '../lib/signals'

/**
 * The five live progress strips, one per signal, shown while analysis runs.
 *
 * The backend does not stream per-signal progress, so a running strip sweeps as
 * indeterminate rather than animating a fabricated percentage. The moment the
 * response lands each strip settles to the signal's real status. The labels
 * below are display placeholders for the in-flight state only; once the response
 * arrives the authoritative names come from the backend.
 */
const PLACEHOLDER_SIGNALS: ReadonlyArray<{ id: string; label: string }> = [
  { id: 'ai_detection', label: 'AI manipulation detector' },
  { id: 'perceptual_duplication', label: 'Perceptual near-duplicate analysis' },
  { id: 'metadata_integrity', label: 'Metadata integrity' },
  { id: 'provenance_c2pa', label: 'C2PA provenance manifest' },
  { id: 'compression_forensics', label: 'Compression forensics' },
]

export function ProgressStrips({
  running,
  signals,
  thresholds,
}: {
  running: boolean
  signals: Signal[] | null
  thresholds?: Verdict['thresholds'] | null
}) {
  void thresholds

  const rows = signals?.length
    ? signals.map((s) => ({ id: s.signal_id, label: s.name, signal: s }))
    : PLACEHOLDER_SIGNALS.map((s) => ({ id: s.id, label: s.label, signal: null }))

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
