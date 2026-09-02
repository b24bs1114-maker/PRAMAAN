import type { Verdict, VerdictBand } from '../api/types'
import { formatScore } from '../lib/format'
import { coverageLine, coverageSentence, verdictBandLabel, verdictTone } from '../lib/signals'
import { Empty } from './Feedback'
import { Icon } from './Icon'

export function VerdictCard({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) {
    return (
      <Empty>
        No verdict produced. No evidence item in this case could be scored - this is not a finding of authenticity or manipulation.
      </Empty>
    )
  }

  const tone = verdictTone(verdict.verdict)
  const score = verdict.manipulation_score ?? 0
  const scorePercent = Math.min(100, Math.max(0, score * 100))

  return (
    <div className={`verdict verdict--${tone}`} style={{ animation: 'verdictReveal 240ms cubic-bezier(0.16, 1, 0.3, 1) backwards' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <span className="verdict__label">FORENSIC SYNTHESIS VERDICT</span>
          <div className="verdict__band">{verdictBandLabel(verdict.verdict)}</div>
        </div>
        <div className="verdict__coverage_badge">
          <span>SIGNAL COVERAGE</span><br />
          <strong style={{ color: 'var(--text-strong)', fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)' }}>
            {coverageLine(verdict)}
          </strong>
        </div>
      </div>

      {/* Manipulation Score Bar */}
      {verdict.manipulation_score !== null ? (
        <div style={{ marginTop: 'var(--space-3)', marginBottom: 'var(--space-2)' }}>
          <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-2xs)', fontFamily: 'var(--mono)', color: 'var(--text-muted)', marginBottom: 4 }}>
            <span>MANIPULATION INDEX</span>
            <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>
              {(scorePercent).toFixed(1)}% ({formatScore(verdict.manipulation_score, 4)})
            </span>
          </div>
          <div style={{ height: 6, background: 'var(--surface-3)', borderRadius: 100, overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                width: `${scorePercent}%`,
                background: tone === 'manipulated' ? 'var(--danger-bright)' : tone === 'authentic' ? 'var(--ok-bright)' : 'var(--warn-bright)',
                borderRadius: 'inherit',
                transition: 'width 300ms cubic-bezier(0.23, 1, 0.32, 1)',
              }}
            />
          </div>
        </div>
      ) : null}

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
            <dt>Confidence</dt>
            <dd className="mono">
              {verdict.confidence ? `${(Number(verdict.confidence) * 100).toFixed(0)}%` : '-'}
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
