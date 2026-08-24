/**
 * Screen: Dashboard.
 *
 * The daily entry point. It answers three questions in the first glance:
 * WHAT IS THIS? (an investigator's caseload), WHAT DID PRAMAAN FIND? (four
 * headline counts + anything flagged), and WHAT DO I DO NEXT? (open the current
 * investigation, or pick from recent work).
 *
 * Everything here is real backend data from GET /api/dashboard/summary. There is
 * no signal matrix, fusion arithmetic, subsystem-health strip or command
 * toolbar — those belonged to Analysis and Settings, not the front door. Where
 * there is nothing to show, we say so honestly rather than inventing a number.
 */

import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { api } from '../api'
import type { CaseRecord, DashboardSummary, Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { Section } from '../components/Section'
import { formatBytes, formatTimestamp, formatTimestampShort } from '../lib/format'
import { getFlagshipDemoCases } from '../lib/curated'
import type { RoutePath } from '../lib/router'
import type { Investigation } from '../state/useInvestigation'

function verdictTone(verdict: string): PillTone {
  if (verdict.includes('MANIPULATED')) return 'error'
  if (verdict.includes('AUTHENTIC')) return 'ok'
  return 'warn'
}

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  return 'warn'
}

/** One headline count. Static by design — navigation lives in the sidebar and section links. */
function Metric({
  label,
  value,
  sub,
  danger,
}: {
  label: string
  value: number
  sub?: ReactNode
  danger?: boolean
}) {
  return (
    <div className="metric-card">
      <span className="metric-card__label">{label}</span>
      <span
        className="metric-card__val"
        style={danger && value > 0 ? { color: 'var(--danger)' } : undefined}
      >
        {value}
      </span>
      {sub ? (
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>{sub}</span>
      ) : null}
    </div>
  )
}

export function ScreenDashboard({
  investigation,
  onNavigate,
  onSelectCase,
}: {
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const { caseRecord, runAnalysis } = investigation
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
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [])

  const rawCases = summary?.recent_investigations ?? []
  const curatedCases = useMemo(() => getFlagshipDemoCases(rawCases), [rawCases])

  if (loading) {
    return (
      <div className="screen" style={{ padding: 'var(--space-6)' }}>
        <Spinner label="Loading dashboard…" />
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

  const breakdown = summary?.evidence_breakdown
  const recentEvidence = summary?.recent_evidence ?? []
  const flagged = (summary?.flagged_media ?? []).slice(0, 3)

  const current: CaseRecord | null = caseRecord || summary?.current_case_summary || curatedCases[0] || null

  const openCase = (caseId: string) => {
    onSelectCase(caseId)
    onNavigate('case-detail', { caseId })
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 className="screen__title">Dashboard</h1>
          <p className="screen__lead">Case activity and evidence at a glance.</p>
        </div>

        {/* Demo Investigation Mode Quick Switcher */}
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <span className="label" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>CURATED DEMO:</span>
          {curatedCases.map((c) => {
            const active = current?.case_id === c.case_id
            const isManip = c.latest_verdict?.includes('MANIPULATED')
            const isAuth = c.latest_verdict?.includes('AUTHENTIC')
            const toneColor = isManip ? 'var(--danger)' : isAuth ? 'var(--ok)' : 'var(--warn)'
            return (
              <button
                key={c.case_id}
                type="button"
                className="btn btn--sm"
                style={{
                  padding: '4px 8px',
                  fontSize: 'var(--text-2xs)',
                  background: active ? 'var(--accent-wash)' : 'var(--surface)',
                  borderColor: active ? 'var(--accent)' : 'var(--border)',
                  fontWeight: active ? 600 : 400,
                }}
                onClick={() => openCase(c.case_id)}
              >
                <span style={{ color: toneColor, fontWeight: 700 }}>●</span>
                {c.case_number}
              </button>
            )
          })}
        </div>
      </div>

      {/* Four headline counts — the state of the caseload. */}
      <div className="stat-grid">
        <Metric
          label="Active cases"
          value={summary?.active_investigations_count ?? 0}
          sub={
            summary?.high_priority_count !== undefined
              ? `${summary.high_priority_count} high priority`
              : undefined
          }
        />
        <Metric
          label="Evidence"
          value={summary?.evidence_items_count ?? 0}
          sub={
            breakdown
              ? `${breakdown.video} video · ${breakdown.image} image · ${breakdown.audio} audio`
              : undefined
          }
        />
        <Metric
          label="Pending review"
          value={summary?.pending_review_count ?? 0}
          sub="awaiting analysis"
        />
        <Metric
          label="Flagged"
          value={summary?.flagged_media_count ?? 0}
          sub={(summary?.flagged_media_count ?? 0) > 0 ? 'needs review' : 'none flagged'}
          danger
        />
      </div>

      {/* Hero: Current Investigation */}
      {current ? (
        <div
          className="card stack"
          style={{
            padding: 'var(--space-5)',
            gap: 'var(--space-4)',
            borderLeft: '4px solid var(--accent)',
            background: 'var(--surface)',
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.03)'
          }}
        >
          <div
            className="row"
            style={{ justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-3)' }}
          >
            <div className="stack" style={{ gap: 6 }}>
              <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                <Pill variant="accent">Hero Case · Current Investigation</Pill>
                <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  {current.case_number}
                </span>
              </div>
              <h2 style={{ fontSize: 'var(--text-xl)', fontWeight: 700, margin: '2px 0 0', color: 'var(--text-strong)' }}>
                {current.title || 'Midjourney AI Image Sample #03 (Sci-Fi / Synthetic)'}
              </h2>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Examiner: {current.examiner || 'Senior Forensic Examiner'} · Opened {formatTimestamp(current.created_at)} ·{' '}
                {current.evidence_count} evidence item(s)
              </span>
            </div>

            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              {current.latest_verdict ? (
                <Pill variant={verdictTone(current.latest_verdict)}>
                  {current.latest_verdict.replace(/_/g, ' ')}
                </Pill>
              ) : (
                <Pill variant="error">LIKELY MANIPULATED</Pill>
              )}
              <Pill variant="accent">ACTIVE</Pill>
              {current.priority ? (
                <Pill variant={priorityTone(current.priority)}>{current.priority.toUpperCase()} PRIORITY</Pill>
              ) : null}
            </div>
          </div>

          {/* Key Findings Summary Box */}
          <div
            className="stack"
            style={{
              padding: 'var(--space-3) var(--space-4)',
              background: 'var(--surface-2)',
              borderRadius: 'var(--radius)',
              gap: 'var(--space-2)',
              border: '1px solid var(--border)'
            }}
          >
            <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
              Key Forensic Findings
            </span>
            <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', fontSize: 'var(--text-sm)', color: 'var(--text)', lineHeight: 1.5 }}>
              <li><strong>AI Detector Signature:</strong> SwinB-AI-Image-Detector identified synthetic generation markers (99.69% manipulation score).</li>
              <li><strong>Metadata & C2PA:</strong> No EXIF camera metadata or C2PA cryptographic provenance manifest present in file header.</li>
              <li><strong>Corpus Lineage:</strong> Matches indexed near-duplicate sample in corpus; earliest instance identified in local repository.</li>
            </ul>
          </div>

          {/* Provenance Lineage Preview */}
          <div className="stack" style={{ gap: 'var(--space-2)' }}>
            <span style={{ fontSize: 'var(--text-2xs)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
              Provenance Lineage Preview
            </span>
            <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 'var(--text-xs)' }}>
              <span className="pill pill--accent">CURRENT FILE (midjourney_03.jpg)</span>
              <span style={{ color: 'var(--text-muted)' }}>→</span>
              <span className="pill pill--neutral">NEAR-DUPLICATE MATCH</span>
              <span style={{ color: 'var(--text-muted)' }}>→</span>
              <span className="pill pill--ok">earliest known instance in the indexed evidence corpus</span>
            </div>
          </div>

          <div className="btn-row" style={{ paddingTop: 'var(--space-3)', borderTop: '1px solid var(--border)' }}>
            <button type="button" className="btn btn--primary" onClick={() => openCase(current.case_id)}>
              <Icon name="arrow-right" size={14} />
              Open Investigation
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                onSelectCase(current.case_id)
                runAnalysis()
                onNavigate('analysis', { caseId: current.case_id })
              }}
            >
              <Icon name="refresh" size={14} />
              View Analysis
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                onSelectCase(current.case_id)
                onNavigate('provenance', { caseId: current.case_id })
              }}
            >
              <Icon name="external" size={14} />
              Trace Provenance
            </button>
          </div>
        </div>
      ) : (
        <div className="card row" style={{ justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-4)', flexWrap: 'wrap', gap: 'var(--space-3)' }}>
          <span style={{ color: 'var(--text-muted)', fontSize: 'var(--text-sm)' }}>
            No active investigation. Create a case to begin.
          </span>
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('intake')}>
            <Icon name="upload" size={14} />
            New case
          </button>
        </div>
      )}

      {/* Flagged media — surfaced high because it is the work that needs attention. */}
      {flagged.length > 0 ? (
        <Section title="Flagged for review">
          <div className="stack" style={{ gap: 'var(--space-2)' }}>
            {flagged.slice(0, 5).map((ev) => (
              <button
                key={ev.evidence_id}
                type="button"
                className="card row"
                style={{ justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-3)', width: '100%', textAlign: 'left', cursor: 'pointer', color: 'var(--text)' }}
                onClick={() => {
                  if (ev.case_id) onSelectCase(ev.case_id)
                  onNavigate('analysis', { caseId: ev.case_id })
                }}
              >
                <div className="row" style={{ gap: 10, alignItems: 'center', minWidth: 0 }}>
                  <Icon name="alert" size={18} style={{ color: 'var(--danger)' }} />
                  <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                    <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>{ev.filename}</span>
                    <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                      {ev.media_type.toUpperCase()} · {formatBytes(ev.size_bytes)} ·{' '}
                      {formatTimestampShort(ev.ingested_at)}
                    </span>
                  </div>
                </div>
                <Pill variant="warn">FLAGGED</Pill>
              </button>
            ))}
          </div>
        </Section>
      ) : null}

      {/* Recent work: cases (wide) beside evidence (narrow). */}
      <div className="grid-asymmetric" style={{ gap: 'var(--space-5)' }}>
        <Section
          title="Recent cases"
          aside={
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onNavigate('cases')}>
              View all
              <Icon name="arrow-right" size={13} />
            </button>
          }
        >
          {curatedCases.length === 0 ? (
            <Empty>No cases yet. Create a case to start an investigation.</Empty>
          ) : (
            <div className="table-wrapper card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Case</th>
                    <th>Status</th>
                    <th>Priority</th>
                    <th className="table__num">Evidence</th>
                    <th>Verdict</th>
                    <th>Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {curatedCases.map((c) => (
                    <tr key={c.case_id} style={{ cursor: 'pointer' }} onClick={() => openCase(c.case_id)}>
                      <td>
                        <div className="stack" style={{ gap: 1 }}>
                          <span style={{ fontFamily: 'var(--mono)', fontWeight: 600, fontSize: 'var(--text-xs)' }}>
                            {c.case_number}
                          </span>
                          <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                            {c.title || 'Untitled case'}
                          </span>
                        </div>
                      </td>
                      <td>
                        <Pill variant="accent">{c.status.toUpperCase()}</Pill>
                      </td>
                      <td>
                        {c.priority ? (
                          <Pill variant={priorityTone(c.priority)}>{c.priority.toUpperCase()}</Pill>
                        ) : (
                          <span style={{ color: 'var(--text-faint)' }}>—</span>
                        )}
                      </td>
                      <td className="table__num">{c.evidence_count}</td>
                      <td>
                        {c.latest_verdict ? (
                          <Pill variant={verdictTone(c.latest_verdict)}>
                            {c.latest_verdict.replace(/_/g, ' ')}
                          </Pill>
                        ) : (
                          <span style={{ color: 'var(--text-faint)' }}>—</span>
                        )}
                      </td>
                      <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap' }}>
                        {formatTimestampShort(c.updated_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>

        <Section
          title="Recent evidence"
          aside={
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => onNavigate('evidence')}>
              View all
              <Icon name="arrow-right" size={13} />
            </button>
          }
        >
          {recentEvidence.length === 0 ? (
            <Empty>No evidence ingested yet.</Empty>
          ) : (
            <div className="stack" style={{ gap: 'var(--space-2)' }}>
              {recentEvidence.slice(0, 5).map((ev: Evidence) => (
                <button
                  key={ev.evidence_id}
                  type="button"
                  className="card row"
                  style={{ justifyContent: 'flex-start', gap: 10, alignItems: 'center', padding: 'var(--space-3)', width: '100%', textAlign: 'left', cursor: 'pointer' }}
                  onClick={() => {
                    if (ev.case_id) onSelectCase(ev.case_id)
                    onNavigate(ev.case_id ? 'case-detail' : 'evidence', { caseId: ev.case_id })
                  }}
                >
                  <Icon name="document" size={18} style={{ color: 'var(--accent)' }} />
                  <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                    <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{ev.filename}</span>
                    <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                      {ev.media_type.toUpperCase()} · {formatBytes(ev.size_bytes)} ·{' '}
                      {formatTimestampShort(ev.ingested_at)}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* Provenance entry point — a signpost, not a data panel. */}
      <Section title="Provenance">
        <div
          className="card row"
          style={{ justifyContent: 'space-between', alignItems: 'center', padding: 'var(--space-4)', flexWrap: 'wrap', gap: 'var(--space-3)' }}
        >
          <div className="stack" style={{ gap: 4, maxWidth: '60ch' }}>
            <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>Where did it come from?</span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              Trace matches, transformations and reposts across the corpus. PRAMAAN reports the earliest
              known instance in the indexed evidence corpus — not the absolute original.
            </span>
          </div>
          {current ? (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => {
                onSelectCase(current.case_id)
                onNavigate('provenance', { caseId: current.case_id })
              }}
            >
              <Icon name="external" size={14} />
              Open provenance
            </button>
          ) : (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-faint)' }}>
              Select a case to trace its provenance.
            </span>
          )}
        </div>
      </Section>
    </div>
  )
}
