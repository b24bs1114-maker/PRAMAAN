import type { Verdict, VerdictBand } from '../api/types'
import { formatScore } from '../lib/format'
import { coverageLine, coverageSentence, verdictBandLabel, verdictTone } from '../lib/signals'
import { Empty } from './Feedback'
import { Icon } from './Icon'

export function VerdictCard({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) {
    return (
      <Empty>
        No verdict produced. No evidence item in this case could be scored — this is not a finding of authenticity or manipulation.
      </Empty>
    )
  }

  const tone = verdictTone(verdict.verdict)

  return (
    <div className={`verdict verdict--${tone}`}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <span className="verdict__label">FORENSIC ASSESSMENT</span>
          <div className="verdict__band">{verdictBandLabel(verdict.verdict)}</div>
        </div>
        <div className="verdict__coverage_badge">
          VERDICT <br />
          <strong>{coverageLine(verdict)}</strong>
        </div>
      </div>

      <p className="verdict__rationale">{verdict.rationale}</p>
      <p className="verdict__sub">{coverageSentence(verdict)}</p>

      <details className="disclosure" style={{ marginTop: 'var(--space-3)' }}>
        <summary>
          <Icon name="arrow-right" className="disclosure__chevron" size={13} />
          Technical Fusion Details
        </summary>
        <div className="disclosure__panel">
          <dl className="dl">
            <dt>Backend Verdict</dt>
            <dd>
              <code>{verdict.verdict as VerdictBand}</code>
            </dd>
            <dt>Fused Score</dt>
            <dd className="mono">
              {formatScore(verdict.manipulation_score, 4)} (0–1 scale)
            </dd>

            <dt>Arithmetic</dt>
            <dd className="mono break-all" style={{ fontSize: 'var(--text-2xs)' }}>
              {verdict.arithmetic}
            </dd>
            <dt>Method</dt>
            <dd>
              {verdict.method} <span className="muted">({verdict.fusion_version})</span>
            </dd>
          </dl>
        </div>
      </details>
    </div>
  )
}
