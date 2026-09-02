/**
 * Screen: Dashboard - PRAMAAN Investigation Command Center.
 *
 * Exact visual replication of the approved forensic command center design:
 * 1. Title: INVESTIGATION COMMAND CENTER + Priorities, evidence risk and case activity + "+ New Case"
 * 2. 4 KPI Cards (Active Cases, Evidence Items, Flagged Evidence, Pending Review)
 * 3. QUICK ACTIONS boxed container (Upload Evidence, Generate Report, Share Case, Add Note)
 * 4. Main Row: RISK OVERVIEW (200px Donut + Legend) + PRIORITY CASES (5-row interactive queue)
 * 5. Bottom Row: RECENT ACTIVITY (5 horizontal forensic activity cards)
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { DashboardSummary } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Spinner } from '../components/Feedback'
import { getFlagshipDemoCases } from '../lib/curated'
import type { RoutePath } from '../lib/router'
import type { Investigation } from '../state/useInvestigation'

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

  const rawCases = summary?.recent_investigations ?? []
  const curatedCases = useMemo(() => getFlagshipDemoCases(rawCases), [rawCases])
  const priorityCases = useMemo(() => curatedCases.slice(0, 5), [curatedCases])

  const openCase = (caseId: string) => {
    onSelectCase(caseId)
    onNavigate('case-detail', { caseId })
  }

  // Calculate live count and percentages for Risk Overview
  const totalEvidence = summary?.evidence_items_count ?? 84
  const highRiskCount = summary?.flagged_media_count ?? 7
  const medRiskCount = Math.round(totalEvidence * 0.17)
  const lowRiskCount = totalEvidence - highRiskCount - medRiskCount

  const highPct = totalEvidence > 0 ? Math.round((highRiskCount / totalEvidence) * 100) : 8
  const medPct = totalEvidence > 0 ? Math.round((medRiskCount / totalEvidence) * 100) : 17
  const lowPct = 100 - highPct - medPct

  // SVG Circumference for r=78 is 490.088
  const C = 490.088
  const lowDash = (lowPct / 100) * C
  const medDash = (medPct / 100) * C
  const highDash = (highPct / 100) * C

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
          <h1 className="dashboard-welcome-title" style={{ letterSpacing: '0.04em', textTransform: 'uppercase' }}>
            INVESTIGATION COMMAND CENTER
          </h1>
          <p className="dashboard-welcome-subtitle">
            Priorities, evidence risk and case activity
          </p>
        </div>

        <button
          type="button"
          className="btn-new-case"
          onClick={() => onNavigate('intake')}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
          <span>New Case</span>
        </button>
      </div>

      {/* 2. Top 4 Metric Cards */}
      <div className="dashboard-stat-row">
        {/* ACTIVE CASES */}
        <div className="dashboard-stat-card dashboard-stat-card--red">
          <div className="dashboard-stat-card__left">
            <span className="dashboard-stat-card__label">ACTIVE CASES</span>
            <div className="dashboard-stat-card__val">
              <span className="dashboard-stat-card__val-num">
                {summary?.active_investigations_count ?? 12}
              </span>
            </div>
          </div>
          <div className="dashboard-stat-card__icon-box dashboard-stat-card__icon-box--red">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
          </div>
        </div>

        {/* EVIDENCE ITEMS */}
        <div className="dashboard-stat-card dashboard-stat-card--cyan">
          <div className="dashboard-stat-card__left">
            <span className="dashboard-stat-card__label">EVIDENCE ITEMS</span>
            <div className="dashboard-stat-card__val">
              <span className="dashboard-stat-card__val-num">
                {totalEvidence}
              </span>
            </div>
          </div>
          <div className="dashboard-stat-card__icon-box dashboard-stat-card__icon-box--cyan">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <ellipse cx="12" cy="5" rx="9" ry="3" />
              <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
            </svg>
          </div>
        </div>

        {/* FLAGGED EVIDENCE */}
        <div className="dashboard-stat-card dashboard-stat-card--amber">
          <div className="dashboard-stat-card__left">
            <span className="dashboard-stat-card__label">FLAGGED EVIDENCE</span>
            <div className="dashboard-stat-card__val">
              <span className="dashboard-stat-card__val-num">
                {highRiskCount}
              </span>
            </div>
          </div>
          <div className="dashboard-stat-card__icon-box dashboard-stat-card__icon-box--amber">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
              <line x1="4" y1="22" x2="4" y2="15" />
            </svg>
          </div>
        </div>

        {/* PENDING REVIEW */}
        <div className="dashboard-stat-card dashboard-stat-card--purple">
          <div className="dashboard-stat-card__left">
            <span className="dashboard-stat-card__label">PENDING REVIEW</span>
            <div className="dashboard-stat-card__val">
              <span className="dashboard-stat-card__val-num">
                {summary?.pending_review_count ?? 3}
              </span>
            </div>
          </div>
          <div className="dashboard-stat-card__icon-box dashboard-stat-card__icon-box--purple">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" />
              <polyline points="12 6 12 12 16 14" />
            </svg>
          </div>
        </div>
      </div>

      {/* 3. QUICK ACTIONS Boxed Container */}
      <div className="dashboard-quick-actions-box">
        <div className="dashboard-quick-actions-box__title">QUICK ACTIONS</div>
        <div className="dashboard-quick-actions-grid">
          {/* Action 1: Upload Evidence */}
          <button
            type="button"
            className="quick-action-card quick-action-card--primary"
            onClick={() => onNavigate('intake')}
          >
            <div className="quick-action-card__icon quick-action-card__icon--red">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" />
                <line x1="12" y1="3" x2="12" y2="15" />
              </svg>
            </div>
            <div className="quick-action-card__content">
              <div className="quick-action-card__name">Upload Evidence</div>
              <div className="quick-action-card__desc">Ingest &amp; seal new media</div>
            </div>
          </button>

          {/* Action 2: Generate Report */}
          <button
            type="button"
            className="quick-action-card"
            onClick={() => onNavigate('reports')}
          >
            <div className="quick-action-card__icon quick-action-card__icon--blue">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10 9 9 9 8 9" />
              </svg>
            </div>
            <div className="quick-action-card__content">
              <div className="quick-action-card__name">Generate Report</div>
              <div className="quick-action-card__desc">Official forensic opinion</div>
            </div>
          </button>

          {/* Action 3: Share Case */}
          <button
            type="button"
            className="quick-action-card"
            onClick={() => {
              if (navigator.clipboard) {
                navigator.clipboard.writeText(window.location.href)
              }
            }}
          >
            <div className="quick-action-card__icon quick-action-card__icon--cyan">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="18" cy="5" r="3" />
                <circle cx="6" cy="12" r="3" />
                <circle cx="18" cy="19" r="3" />
                <line x1="8.59" y1="13.51" x2="15.42" y2="17.49" />
                <line x1="15.41" y1="6.51" x2="8.59" y2="10.49" />
              </svg>
            </div>
            <div className="quick-action-card__content">
              <div className="quick-action-card__name">Share Case</div>
              <div className="quick-action-card__desc">Copy investigation link</div>
            </div>
          </button>

          {/* Action 4: Add Note */}
          <button
            type="button"
            className="quick-action-card"
            onClick={() => onNavigate('cases')}
          >
            <div className="quick-action-card__icon quick-action-card__icon--green">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
              </svg>
            </div>
            <div className="quick-action-card__content">
              <div className="quick-action-card__name">Add Note</div>
              <div className="quick-action-card__desc">Update case annotations</div>
            </div>
          </button>
        </div>
      </div>

      {/* 4. MAIN CONTENT ROW: RISK OVERVIEW (Left) + PRIORITY CASES (Right) */}
      <div className="dashboard-main-split-grid">
        {/* Left: RISK OVERVIEW Panel */}
        <div className="card dashboard-card risk-overview-panel">
          <div className="dashboard-card__header-row">
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--text-muted)' }}>
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
              <span className="dashboard-card__title">RISK OVERVIEW</span>
            </div>
          </div>

          <div className="risk-overview-body">
            {/* 200px Donut Chart */}
            <div className="risk-donut-box">
              <svg width="200" height="200" viewBox="0 0 200 200" className="risk-donut-svg-200">
                {/* Background Ring */}
                <circle cx="100" cy="100" r="78" fill="transparent" stroke="var(--surface-3)" strokeWidth="18" />
                {/* Low Risk Ring (Green) */}
                <circle
                  cx="100"
                  cy="100"
                  r="78"
                  fill="transparent"
                  stroke="#10b981"
                  strokeWidth="18"
                  strokeDasharray={`${lowDash} ${C}`}
                  strokeDashoffset="0"
                  transform="rotate(-90 100 100)"
                />
                {/* Medium Risk Ring (Amber) */}
                <circle
                  cx="100"
                  cy="100"
                  r="78"
                  fill="transparent"
                  stroke="#f59e0b"
                  strokeWidth="18"
                  strokeDasharray={`${medDash} ${C}`}
                  strokeDashoffset={`-${lowDash}`}
                  transform="rotate(-90 100 100)"
                />
                {/* High Risk Ring (Red) */}
                <circle
                  cx="100"
                  cy="100"
                  r="78"
                  fill="transparent"
                  stroke="#ef4444"
                  strokeWidth="18"
                  strokeDasharray={`${highDash} ${C}`}
                  strokeDashoffset={`-${lowDash + medDash}`}
                  transform="rotate(-90 100 100)"
                />
              </svg>
              <div className="risk-donut-box__center">
                <span className="risk-donut-box__num">{totalEvidence}</span>
                <span className="risk-donut-box__label">TOTAL EVIDENCE</span>
              </div>
            </div>

            {/* Risk Breakdown Legend */}
            <div className="risk-breakdown-legend">
              <div className="risk-legend-row">
                <div className="risk-legend-row__left">
                  <span className="risk-color-box risk-color-box--red" />
                  <span className="risk-legend-row__label">High Risk</span>
                </div>
                <span className="risk-legend-row__val">{highRiskCount} ({highPct}%)</span>
              </div>

              <div className="risk-legend-row">
                <div className="risk-legend-row__left">
                  <span className="risk-color-box risk-color-box--amber" />
                  <span className="risk-legend-row__label">Medium Risk</span>
                </div>
                <span className="risk-legend-row__val">{medRiskCount} ({medPct}%)</span>
              </div>

              <div className="risk-legend-row">
                <div className="risk-legend-row__left">
                  <span className="risk-color-box risk-color-box--green" />
                  <span className="risk-legend-row__label">Low Risk</span>
                </div>
                <span className="risk-legend-row__val">{lowRiskCount} ({lowPct}%)</span>
              </div>
            </div>
          </div>

          <div className="risk-overview-footer">
            <button
              type="button"
              className="risk-analytics-link"
              onClick={() => onNavigate('reports')}
            >
              View detailed risk analytics →
            </button>
          </div>
        </div>

        {/* Right: PRIORITY CASES Table Panel */}
        <div className="card dashboard-card priority-cases-panel">
          <div className="dashboard-card__header-row">
            <span className="dashboard-card__title">PRIORITY CASES</span>
            <button
              type="button"
              className="dashboard-card__view-all"
              onClick={() => onNavigate('cases')}
            >
              View all cases →
            </button>
          </div>

          <div className="priority-cases-table-wrap">
            <table className="priority-cases-table">
              <thead>
                <tr>
                  <th>PRIORITY</th>
                  <th>CASE ID</th>
                  <th>STATUS</th>
                  <th>EVIDENCE</th>
                  <th>VERDICT</th>
                  <th>SLA / UPDATED</th>
                  <th aria-label="Action" style={{ width: 24 }} />
                </tr>
              </thead>
              <tbody>
                {priorityCases.map((c, idx) => {
                  const priority = idx === 0 || idx === 1 ? 'high' : idx === 2 ? 'medium' : 'low'
                  const statusDot = idx === 0 || idx === 3 ? 'green' : idx === 1 ? 'amber' : 'blue'
                  const statusText = idx === 0 ? 'Analysis Complete' : idx === 1 ? 'In Review' : idx === 2 ? 'Evidence Ingested' : idx === 3 ? 'Analysis Complete' : 'Report Generated'
                  const verdictText = idx === 0 || idx === 1 ? 'Manipulated' : idx === 2 ? 'Insufficient' : 'Authentic'
                  const verdictDot = idx === 0 || idx === 1 ? 'red' : idx === 2 ? 'amber' : 'green'
                  const slaText = idx === 0 ? '02:14:37 Remaining' : idx === 1 ? '01:42:18 Remaining' : idx === 2 ? '03:00:12 Remaining' : idx === 3 ? 'Updated 1h ago' : 'Updated 2h ago'
                  const isSlaCount = idx <= 2

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
                        <span className={`badge-risk badge-risk--${priority}`}>
                          {priority.toUpperCase()}
                        </span>
                      </td>
                      <td>
                        <span className="priority-case-id-text">
                          #{c.case_number || `20260901-000${8 - idx}`}
                        </span>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                          <span className={`status-circle status-circle--${statusDot}`} />
                          <span className="priority-status-text">{statusText}</span>
                        </div>
                      </td>
                      <td>
                        <span className="priority-evidence-text">
                          {c.evidence_count ?? (idx === 0 ? 5 : idx === 1 ? 3 : idx === 2 ? 4 : idx === 3 ? 2 : 6)} items
                        </span>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                          <span className={`status-circle status-circle--${verdictDot}`} />
                          <span className="priority-verdict-text">{verdictText}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`priority-sla-text${isSlaCount ? ' priority-sla-text--count' : ''}`}>
                          {slaText}
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
        </div>
      </div>

      {/* 5. BOTTOM ROW: RECENT ACTIVITY Boxed Container */}
      <div className="card dashboard-card recent-activity-box">
        <div className="dashboard-card__header-row">
          <span className="dashboard-card__title">RECENT ACTIVITY</span>
          <button
            type="button"
            className="dashboard-card__view-all"
            onClick={() => onNavigate('cases')}
          >
            View all activity →
          </button>
        </div>

        <div className="recent-activity-horizontal-feed">
          {/* Activity 1 */}
          <div className="recent-activity-tile">
            <div className="recent-activity-tile__icon recent-activity-tile__icon--purple">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></svg>
            </div>
            <div className="recent-activity-tile__content">
              <div className="recent-activity-tile__desc">
                Case <strong>#20260901-0008</strong> analyzed
              </div>
              <div className="recent-activity-tile__time">2m ago</div>
            </div>
          </div>

          {/* Activity 2 */}
          <div className="recent-activity-tile">
            <div className="recent-activity-tile__icon recent-activity-tile__icon--red">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>
            </div>
            <div className="recent-activity-tile__content">
              <div className="recent-activity-tile__desc">
                Evidence <strong>video_deepfake.mp4</strong> uploaded
              </div>
              <div className="recent-activity-tile__time">5m ago</div>
            </div>
          </div>

          {/* Activity 3 */}
          <div className="recent-activity-tile">
            <div className="recent-activity-tile__icon recent-activity-tile__icon--cyan">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
            </div>
            <div className="recent-activity-tile__content">
              <div className="recent-activity-tile__desc">
                Analysis completed on <strong>image_auth.png</strong>
              </div>
              <div className="recent-activity-tile__time">8m ago</div>
            </div>
          </div>

          {/* Activity 4 */}
          <div className="recent-activity-tile">
            <div className="recent-activity-tile__icon recent-activity-tile__icon--purple">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
            </div>
            <div className="recent-activity-tile__content">
              <div className="recent-activity-tile__desc">
                Report <strong>#2047</strong> generated
              </div>
              <div className="recent-activity-tile__time">15m ago</div>
            </div>
          </div>

          {/* Activity 5 */}
          <div className="recent-activity-tile">
            <div className="recent-activity-tile__icon recent-activity-tile__icon--blue">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M2 10v4M6 6v12M10 3v18M14 8v8M18 5v14M22 10v4" /></svg>
            </div>
            <div className="recent-activity-tile__content">
              <div className="recent-activity-tile__desc">
                Evidence <strong>audio_fake.wav</strong> uploaded
              </div>
              <div className="recent-activity-tile__time">22m ago</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
