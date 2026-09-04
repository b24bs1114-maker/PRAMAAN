/**
 * Screen: Dashboard - PRAMAAN Investigation Command Center.
 *
 * Every number on this screen comes from `GET /api/dashboard/summary`. The
 * previous build derived most of them from the row index instead, which is worth
 * spelling out because the result looked completely convincing:
 *
 *   - PRIORITY CASES assigned `priority`, a status dot, a status sentence, a
 *     verdict, a verdict dot and an SLA countdown from `idx`. Row 0 was always
 *     "high / Analysis Complete / Manipulated / 02:14:37 Remaining" no matter
 *     which real case landed there. Two real cases could not both be shown as
 *     the same priority, and a case with no verdict was still given one.
 *   - The evidence count fell back to `idx === 0 ? 5 : ...` and the case number
 *     to `20260901-000${8 - idx}`.
 *   - RECENT ACTIVITY was five hardcoded tiles: `video_deepfake.mp4` uploaded
 *     5m ago, report `#2047` generated 15m ago, and so on. None of it referred
 *     to anything in the database.
 *   - RISK OVERVIEW split the evidence into high/medium/low risk, where medium
 *     was `Math.round(totalEvidence * 0.17)` -- a risk tier computed from a
 *     constant. The backend publishes `verdict_breakdown`, which is the real
 *     disposition of the evidence, so that is what the donut now shows.
 *   - The four KPI cards fell back to 12 / 84 / 7 / 3 when the request failed
 *     or returned nothing, so an empty deployment looked like a busy unit.
 *
 * The rule applied throughout: a field the backend did not send is rendered as
 * `NOT_MEASURED`, and a section with no rows says it has no rows.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { DashboardSummary, Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon, mediaIcon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { NOT_MEASURED, formatBytes, formatTimestampShort, orPlaceholder } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel, verdictTone } from '../lib/signals'
import type { Investigation } from '../state/useInvestigation'

/**
 * The disposition bands the donut draws, in ring order.
 *
 * These are the backend's own verdict tokens plus one derived band for evidence
 * that has not been analysed yet. "Not yet analysed" is not a risk level and is
 * not shaded as one -- it is the absence of a measurement.
 */
const DISPOSITION_BANDS = [
  { key: 'MANIPULATED', label: 'Evidence supports manipulation', colour: '#ef4444' },
  { key: 'INSUFFICIENT_EVIDENCE', label: 'Inconclusive', colour: '#f59e0b' },
  { key: 'AUTHENTIC', label: 'No manipulation evidence found', colour: '#10b981' },
  { key: '__UNANALYSED__', label: 'Not yet analysed', colour: 'var(--surface-3)' },
] as const

export function ScreenDashboard({
  investigation: _investigation,
  onNavigate,
  onSelectCase,
}: {
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let active = true
    setLoading(true)
    api
      .getDashboardSummary()
      .then((data) => {
        if (active) {
          setSummary(data)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setError(err)
        }
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  /**
   * The case queue, in the order the backend returned it.
   *
   * `recent_investigations` is ordered by the backend. It is not re-sorted or
   * truncated to a curated three here: hiding real cases from an investigator's
   * queue, or reordering them to make a demo read well, is not a presentation
   * choice a case-management screen gets to make.
   */
  const caseQueue = useMemo(
    () => (summary?.recent_investigations ?? []).slice(0, 6),
    [summary?.recent_investigations],
  )

  const openCase = (caseId: string) => {
    onSelectCase(caseId)
    onNavigate('case-detail', { caseId })
  }

  const totalEvidence = summary?.evidence_items_count ?? 0
  const analysed = summary?.analysed_evidence_count ?? 0
  const breakdown = summary?.verdict_breakdown ?? {}

  /** Real per-verdict counts, with the unanalysed remainder as its own band. */
  const dispositions = useMemo(() => {
    const unanalysed = Math.max(0, totalEvidence - analysed)
    return DISPOSITION_BANDS.map((band) => ({
      ...band,
      count: band.key === '__UNANALYSED__' ? unanalysed : (breakdown[band.key] ?? 0),
    }))
  }, [breakdown, totalEvidence, analysed])

  /**
   * Donut geometry. Percentages are of the whole evidence set, so the ring is
   * only complete when every item has been analysed -- the gap is the honest
   * visual for "we have not looked at these yet".
   */
  const C = 490.088 // circumference at r=78
  const denominator = dispositions.reduce((sum, d) => sum + d.count, 0)
  let offset = 0
  const arcs = dispositions.map((d) => {
    const fraction = denominator > 0 ? d.count / denominator : 0
    const dash = fraction * C
    const arc = { ...d, dash, offset, percent: Math.round(fraction * 100) }
    offset += dash
    return arc
  })

  if (loading) {
    return (
      <div className="screen" style={{ padding: 'var(--space-6)' }}>
        <Spinner label="Loading forensic command center…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="screen">
        <ErrorBanner context="Dashboard" error={error} />
      </div>
    )
  }

  return (
    <div className="screen stack dashboard-container" style={{ gap: 'var(--space-4)' }}>
      {/* 1. Header Row */}
      <div className="dashboard-welcome-row">
        <div>
          <h1
            className="dashboard-welcome-title"
            style={{ letterSpacing: '0.04em', textTransform: 'uppercase' }}
          >
            INVESTIGATION COMMAND CENTER
          </h1>
          <p className="dashboard-welcome-subtitle">
            Case load, evidence disposition and system state — all counts read from the case
            database
          </p>
        </div>

        <button type="button" className="btn-new-case" onClick={() => onNavigate('intake')}>
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
          <span>New Case</span>
        </button>
      </div>

      {/* 2. Top 4 Metric Cards. No fallback literals: 0 means zero rows matched. */}
      <div className="dashboard-stat-row">
        <StatCard
          label="ACTIVE CASES"
          value={summary?.active_investigations_count}
          tone="red"
          icon="layers"
        />
        <StatCard label="EVIDENCE ITEMS" value={summary?.evidence_items_count} tone="cyan" icon="evidence" />
        <StatCard label="FLAGGED EVIDENCE" value={summary?.flagged_media_count} tone="amber" icon="flag" />
        <StatCard label="PENDING REVIEW" value={summary?.pending_review_count} tone="purple" icon="clock" />
      </div>

      {/* 3. QUICK ACTIONS */}
      <div className="dashboard-quick-actions-box">
        <div className="dashboard-quick-actions-box__title">QUICK ACTIONS</div>
        <div className="dashboard-quick-actions-grid">
          <QuickAction
            primary
            tone="red"
            icon="upload"
            name="Upload Evidence"
            desc="Ingest &amp; seal new media"
            onClick={() => onNavigate('intake')}
          />
          <QuickAction
            tone="blue"
            icon="document"
            name="Generate Report"
            desc="Backend-rendered forensic PDF"
            onClick={() => onNavigate('reports')}
          />
          <QuickAction
            tone="purple"
            icon="lock"
            name="Audit Trail"
            desc="Verify the custody hash chain"
            onClick={() => onNavigate('audit')}
          />
          <QuickAction
            tone="green"
            icon="search"
            name="Browse Cases"
            desc="Open the full case list"
            onClick={() => onNavigate('cases')}
          />
        </div>
      </div>

      {/* 4. EVIDENCE DISPOSITION + CASE QUEUE */}
      <div className="dashboard-main-split-grid">
        <div className="card dashboard-card risk-overview-panel">
          <div className="dashboard-card__header-row">
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <Icon name="shield" size={15} style={{ color: 'var(--text-muted)' }} />
              <span className="dashboard-card__title">EVIDENCE DISPOSITION</span>
            </div>
          </div>

          <div className="risk-overview-body">
            <div className="risk-donut-box">
              <svg width="200" height="200" viewBox="0 0 200 200" className="risk-donut-svg-200">
                <circle
                  cx="100"
                  cy="100"
                  r="78"
                  fill="transparent"
                  stroke="var(--surface-3)"
                  strokeWidth="18"
                />
                {arcs.map((arc) => (
                  <circle
                    key={arc.key}
                    cx="100"
                    cy="100"
                    r="78"
                    fill="transparent"
                    stroke={arc.colour}
                    strokeWidth="18"
                    strokeDasharray={`${arc.dash} ${C}`}
                    strokeDashoffset={`-${arc.offset}`}
                    transform="rotate(-90 100 100)"
                  />
                ))}
              </svg>
              <div className="risk-donut-box__center">
                <span className="risk-donut-box__num">{totalEvidence}</span>
                <span className="risk-donut-box__label">TOTAL EVIDENCE</span>
              </div>
            </div>

            <div className="risk-breakdown-legend">
              {arcs.map((arc) => (
                <div className="risk-legend-row" key={arc.key}>
                  <div className="risk-legend-row__left">
                    <span
                      className="risk-color-box"
                      style={{ background: arc.colour }}
                      aria-hidden="true"
                    />
                    <span className="risk-legend-row__label">{arc.label}</span>
                  </div>
                  <span className="risk-legend-row__val">
                    {arc.count}
                    {denominator > 0 ? ` (${arc.percent}%)` : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="risk-overview-footer">
            <p
              style={{
                fontSize: '10.5px',
                color: 'var(--text-muted)',
                margin: 0,
                lineHeight: 1.5,
              }}
            >
              {analysed} of {totalEvidence} evidence items have a fused verdict on record. Bands are
              the backend's verdict tokens, not risk scores — “no manipulation evidence found” is a
              non-finding, not a verification of authenticity.
            </p>
          </div>
        </div>

        {/* CASE QUEUE — every column is a real field or a placeholder */}
        <div className="card dashboard-card priority-cases-panel">
          <div className="dashboard-card__header-row">
            <span className="dashboard-card__title">CASE QUEUE</span>
            <button
              type="button"
              className="dashboard-card__view-all"
              onClick={() => onNavigate('cases')}
            >
              View all cases →
            </button>
          </div>

          {caseQueue.length === 0 ? (
            <div style={{ padding: 'var(--space-4)' }}>
              <Empty>
                No case has been opened yet. Ingest evidence to create the first case record.
              </Empty>
            </div>
          ) : (
            <div className="priority-cases-table-wrap">
              <table className="priority-cases-table">
                <thead>
                  <tr>
                    <th>PRIORITY</th>
                    <th>CASE ID</th>
                    <th>STATUS</th>
                    <th>EVIDENCE</th>
                    <th>LATEST VERDICT</th>
                    <th>UPDATED</th>
                    <th aria-label="Action" style={{ width: 24 }} />
                  </tr>
                </thead>
                <tbody>
                  {caseQueue.map((c) => {
                    const priority = (c.priority ?? '').toLowerCase()
                    const tone = verdictTone(c.latest_verdict ?? null)
                    return (
                      <tr
                        key={c.case_id}
                        className="priority-case-tr"
                        onClick={() => openCase(c.case_id)}
                        tabIndex={0}
                        role="button"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            openCase(c.case_id)
                          }
                        }}
                      >
                        <td>
                          {priority ? (
                            <span className={`badge-risk badge-risk--${priority}`}>
                              {priority.toUpperCase()}
                            </span>
                          ) : (
                            <span className="priority-status-text" title="No priority recorded">
                              {NOT_MEASURED}
                            </span>
                          )}
                        </td>
                        <td>
                          <span className="priority-case-id-text">
                            #{orPlaceholder(c.case_number)}
                          </span>
                        </td>
                        <td>
                          <span className="priority-status-text">{orPlaceholder(c.status)}</span>
                        </td>
                        <td>
                          <span className="priority-evidence-text">{c.evidence_count} items</span>
                        </td>
                        <td>
                          {c.latest_verdict ? (
                            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                              <span
                                className={`status-circle status-circle--${
                                  tone === 'manipulated' ? 'red' : tone === 'authentic' ? 'green' : 'amber'
                                }`}
                              />
                              <span className="priority-verdict-text">
                                {verdictBandLabel(c.latest_verdict)}
                              </span>
                            </div>
                          ) : (
                            <span className="priority-verdict-text" title="Not analysed yet">
                              Not analysed
                            </span>
                          )}
                        </td>
                        <td>
                          <span className="priority-sla-text">
                            {formatTimestampShort(c.updated_at)}
                          </span>
                        </td>
                        <td>
                          <span className="priority-row-chevron">›</span>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* 5. RECENT EVIDENCE — real ingest rows, not a scripted activity feed */}
      <div className="card dashboard-card recent-activity-box">
        <div className="dashboard-card__header-row">
          <span className="dashboard-card__title">RECENT EVIDENCE INGEST</span>
          <button
            type="button"
            className="dashboard-card__view-all"
            onClick={() => onNavigate('evidence')}
          >
            View all evidence →
          </button>
        </div>

        {(summary?.recent_evidence ?? []).length === 0 ? (
          <div style={{ padding: 'var(--space-4)' }}>
            <Empty>
              No evidence has been ingested yet. This feed lists real ingest rows with their
              recorded timestamps.
            </Empty>
          </div>
        ) : (
          <div className="recent-activity-horizontal-feed">
            {(summary?.recent_evidence ?? []).slice(0, 5).map((ev) => (
              <EvidenceTile key={ev.evidence_id} evidence={ev} />
            ))}
          </div>
        )}
      </div>

      {/* System state and measurement basis, straight from the backend. */}
      <div
        className="card row row--wrap"
        style={{ padding: '10px 14px', gap: 14, alignItems: 'center' }}
      >
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          System: <strong style={{ color: 'var(--text-strong)' }}>{orPlaceholder(summary?.system_status)}</strong>
        </span>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          Mean analysis time:{' '}
          <span className="mono">
            {typeof summary?.avg_processing_time_ms === 'number'
              ? `${summary.avg_processing_time_ms.toFixed(0)} ms`
              : `${NOT_MEASURED} (no run has been timed)`}
          </span>
          {summary?.timed_analysis_runs ? ` over ${summary.timed_analysis_runs} run(s)` : ''}
        </span>
        {Object.entries(summary?.system_status_details ?? {}).map(([key, value]) => (
          <span
            key={key}
            style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}
            className="row"
          >
            {key.replace(/_/g, ' ')}:&nbsp;
            <Pill variant={value === 'ONLINE' || value === 'AVAILABLE' ? 'ok' : 'unavailable'}>
              {value}
            </Pill>
          </span>
        ))}
      </div>

      {(summary?.notes ?? []).length > 0 ? (
        <ul
          style={{
            margin: 0,
            paddingLeft: 18,
            fontSize: 'var(--text-xs)',
            color: 'var(--text-muted)',
            lineHeight: 1.6,
          }}
        >
          {(summary?.notes ?? []).map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

/**
 * One KPI card.
 *
 * `value` is `number | undefined`, and `undefined` renders as `NOT_MEASURED`
 * rather than a plausible-looking integer. The previous cards fell back to 12,
 * 84, 7 and 3, so a failed request or a fresh deployment displayed the workload
 * of a unit that does not exist.
 */
function StatCard({
  label,
  value,
  tone,
  icon,
}: {
  label: string
  value: number | undefined
  tone: 'red' | 'cyan' | 'amber' | 'purple'
  icon: Parameters<typeof Icon>[0]['name']
}) {
  return (
    <div className={`dashboard-stat-card dashboard-stat-card--${tone}`}>
      <div className="dashboard-stat-card__left">
        <span className="dashboard-stat-card__label">{label}</span>
        <div className="dashboard-stat-card__val">
          <span className="dashboard-stat-card__val-num">
            {typeof value === 'number' ? value : NOT_MEASURED}
          </span>
        </div>
      </div>
      <div className={`dashboard-stat-card__icon-box dashboard-stat-card__icon-box--${tone}`}>
        <Icon name={icon} size={20} />
      </div>
    </div>
  )
}

function QuickAction({
  primary = false,
  tone,
  icon,
  name,
  desc,
  onClick,
}: {
  primary?: boolean
  tone: 'red' | 'blue' | 'purple' | 'green'
  icon: Parameters<typeof Icon>[0]['name']
  name: string
  desc: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      className={`quick-action-card${primary ? ' quick-action-card--primary' : ''}`}
      onClick={onClick}
    >
      <div className={`quick-action-card__icon quick-action-card__icon--${tone}`}>
        <Icon name={icon} size={22} />
      </div>
      <div className="quick-action-card__content">
        <div className="quick-action-card__name">{name}</div>
        <div className="quick-action-card__desc">{desc}</div>
      </div>
    </button>
  )
}

/** One real ingest row: its own filename, media type, size and recorded time. */
function EvidenceTile({ evidence }: { evidence: Evidence }) {
  return (
    <div className="recent-activity-tile">
      <div className="recent-activity-tile__icon recent-activity-tile__icon--cyan">
        <Icon name={mediaIcon(evidence.media_type)} size={16} />
      </div>
      <div className="recent-activity-tile__content">
        <div className="recent-activity-tile__desc">
          <strong title={evidence.filename}>{evidence.filename}</strong> ingested
          {evidence.is_synthetic ? ' · SYNTHETIC DEMO DATA' : ''}
        </div>
        <div className="recent-activity-tile__time">
          {formatTimestampShort(evidence.ingested_at)} · {formatBytes(evidence.size_bytes)}
        </div>
      </div>
    </div>
  )
}
