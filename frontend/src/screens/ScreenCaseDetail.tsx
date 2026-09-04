/**
 * Screen: Case Detail.
 *
 * The dossier header for one case: identifiers, workflow position, evidence
 * counts and the recorded custody events.
 *
 * Every field here is a column on the case row, a count over the real evidence
 * list, or an audit event fetched from the backend. The previous build filled
 * the same layout with a fictional case when a field was absent -- title
 * "Deepfake Video - Telegram Channel", priority "high", status "Analysis
 * Complete", examiner "Analyst", a description about a Telegram
 * misinformation channel, and a CASE NOTES column of four invented log lines
 * ("Video received from Cyber Cell", "Multiple reuploads identified"). It also
 * showed `Platform: Telegram / Language: Hindi / Region: India` as though they
 * were case attributes; the backend stores none of those three on a case, and
 * `platform` exists only per evidence item, where it may be null.
 *
 * A case with an unset field now reads as unset. That is the difference between
 * a dossier and a mock-up.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { AuditEvent, CaseRecord, Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { NOT_MEASURED, formatTimestampShort, orPlaceholder, shortHash } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel } from '../lib/signals'
import { isReady, type Investigation } from '../state/useInvestigation'

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  return 'warn'
}

function statusTone(status: string | undefined): PillTone {
  const s = (status || '').toLowerCase()
  if (s.includes('closed') || s.includes('archived')) return 'neutral'
  if (s.includes('review') || s.includes('pending')) return 'warn'
  if (s.includes('complete') || s.includes('verified')) return 'ok'
  return 'accent'
}

export function ScreenCaseDetail({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, runAnalysis, analysis, propagation, auditVerification } = investigation
  const [activeCase, setActiveCase] = useState<CaseRecord | null>(caseRecord)
  const [caseEvidence, setCaseEvidence] = useState<Evidence[]>(evidence)
  const [loading, setLoading] = useState(!caseRecord && Boolean(caseId))
  const [error, setError] = useState<unknown>(null)
  /**
   * Recorded custody events for the third column.
   *
   * Held separately from `error` on purpose: the audit read is supplementary, so
   * a failure there must not blank out a case dossier that loaded fine. It is
   * still surfaced in the column rather than swallowed.
   */
  const [auditEvents, setAuditEvents] = useState<AuditEvent[] | null>(null)
  const [auditError, setAuditError] = useState<unknown>(null)

  const currentCaseId = caseId || caseRecord?.case_id || null

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    setLoading(true)
    Promise.all([api.getCase(currentCaseId), api.listEvidence(currentCaseId)])
      .then(([c, ev]) => {
        if (active) {
          setActiveCase(c)
          setCaseEvidence(ev.evidence)
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
  }, [currentCaseId])

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    setAuditEvents(null)
    setAuditError(null)
    api
      .auditTrail(currentCaseId)
      .then((trail) => {
        if (active) setAuditEvents(trail.events)
      })
      .catch((err) => {
        if (active) setAuditError(err)
      })
    return () => {
      active = false
    }
  }, [currentCaseId])

  const c = activeCase
  /**
   * The case number as issued by the backend.
   *
   * No synthesised alternative: the old fallback chain minted
   * `CAS-${case_id.slice(0, 8)}` and finally the literal `CAS-ACTIVE`, both of
   * which look exactly like a real case number and are not one. If the row has
   * no `case_number`, the internal id is shown under its own label instead.
   */
  const activeCaseNumber = c?.case_number || null

  const analysisData =
    isReady(analysis) && analysis.data.case.case_id === currentCaseId ? analysis.data : null
  const isPropTraced = isReady(propagation) && (propagation.data.instance_count > 0 || propagation.data.matched_candidate_count > 0)
  const isAuditVerified = isReady(auditVerification) && auditVerification.data.valid

  // Media breakdown. Counts are counts -- a case with no video has no video, and
  // the old `|| 1` reported one anyway on every single case.
  const videoCount = caseEvidence.filter((e) => e.media_type.toLowerCase().includes('video')).length
  const imageCount = caseEvidence.filter((e) => e.media_type.toLowerCase().includes('image')).length
  const audioCount = caseEvidence.filter((e) => e.media_type.toLowerCase().includes('audio')).length
  const otherCount = caseEvidence.length - videoCount - imageCount - audioCount

  /** Platforms actually recorded against this case's evidence rows. */
  const platforms = useMemo(
    () =>
      Array.from(
        new Set(
          caseEvidence
            .map((e) => e.platform)
            .filter((p): p is string => Boolean(p && p.trim())),
        ),
      ),
    [caseEvidence],
  )

  // 6-step workflow states
  const isCaseDone = true
  const isEvidenceDone = caseEvidence.length > 0
  const isAnalysisDone = Boolean(analysisData || c?.latest_verdict)
  const isProvenanceDone = isPropTraced
  const isAuditDone = isAuditVerified
  const isReportDone = Boolean(c?.status?.includes('report'))

  const nextAction = useMemo(() => {
    if (caseEvidence.length === 0) {
      return {
        text: 'Upload and ingest initial media evidence to establish custody.',
        btn: 'Upload Evidence →',
        action: () => onNavigate('intake'),
      }
    }
    if (!isAnalysisDone) {
      return {
        text: 'Run multi-signal forensic analysis on ingested evidence.',
        btn: 'Run Analysis →',
        action: () => {
          runAnalysis()
          onNavigate('analysis', { caseId: currentCaseId! })
        },
      }
    }
    if (!isProvenanceDone) {
      return {
        text: 'Trace propagation to find the earliest known instance of this media in the indexed evidence corpus.',
        btn: 'Trace Provenance →',
        action: () => onNavigate('provenance', { caseId: currentCaseId! }),
      }
    }
    if (!isAuditDone) {
      return {
        text: 'Verify the custody hash chain before generating the formal report.',
        btn: 'Verify Audit →',
        action: () => onNavigate('audit', { caseId: currentCaseId! }),
      }
    }
    return {
      text: 'Generate the backend-rendered forensic examination report for this case.',
      btn: 'Generate Report →',
      action: () => onNavigate('reports', { caseId: currentCaseId! }),
    }
  }, [caseEvidence.length, isAnalysisDone, isProvenanceDone, isAuditDone, currentCaseId, onNavigate, runAnalysis])

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
        <Empty>No case selected. Open a case from the investigation list.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View Cases
          </button>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="screen" style={{ padding: 'var(--space-6)' }}>
        <Spinner label="Loading investigation dossier…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="screen">
        <ErrorBanner context="Case Overview" error={error} />
      </div>
    )
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. TOP BAR: Back Navigation & Edit */}
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          style={{ gap: 6 }}
          onClick={() => onNavigate('cases')}
        >
          <Icon name="arrow-left" size={14} />
          Back to Cases
        </button>

        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={() => onNavigate('intake')}
        >
          <Icon name="settings" size={14} />
          Edit Case
        </button>
      </div>

      {/* 2. CASE META BAR */}
      <div
        className="card"
        style={{
          padding: '12px 18px',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
        }}
      >
        <div className="row row--wrap" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 16 }}>
          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              {activeCaseNumber ? 'CASE NUMBER' : 'INTERNAL CASE ID'}
            </span>
            <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
              {activeCaseNumber ? `#${activeCaseNumber}` : shortHash(currentCaseId, 12)}
            </code>
          </div>

          <div className="stack" style={{ gap: 2, flex: '1 1 200px' }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              TITLE / SUBJECT
            </span>
            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-strong)' }}>
              {c?.title?.trim() ? c.title : <span style={{ color: 'var(--text-faint)', fontWeight: 500 }}>No title recorded</span>}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              PRIORITY
            </span>
            <div>
              {c?.priority ? (
                <Pill variant={priorityTone(c.priority)}>{c.priority.toUpperCase()}</Pill>
              ) : (
                <Pill variant="neutral">{NOT_MEASURED}</Pill>
              )}
            </div>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              STATUS
            </span>
            <div>
              {c?.status ? (
                <Pill variant={statusTone(c.status)}>{c.status.toUpperCase()}</Pill>
              ) : (
                <Pill variant="neutral">{NOT_MEASURED}</Pill>
              )}
            </div>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              CREATED
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
              {/* Not `?? new Date()`: an unrecorded creation time is not now. */}
              {formatTimestampShort(c?.created_at ?? null)}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              EXAMINER
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              {c?.examiner?.trim() ? c.examiner : 'Not specified'}
            </span>
          </div>
        </div>
      </div>

      {/* 3. 6-PHASE STEPPER */}
      <div
        className="card row"
        style={{
          padding: '12px 18px',
          justifyContent: 'space-around',
          alignItems: 'center',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          flexWrap: 'wrap',
          gap: 8,
        }}
      >
        {[
          { label: 'Case', num: 1, done: isCaseDone, active: true, path: 'case-detail' as RoutePath },
          { label: 'Evidence', num: 2, done: isEvidenceDone, active: false, path: 'evidence' as RoutePath },
          { label: 'Analysis', num: 3, done: isAnalysisDone, active: false, path: 'analysis' as RoutePath },
          { label: 'Provenance', num: 4, done: isProvenanceDone, active: false, path: 'provenance' as RoutePath },
          { label: 'Audit', num: 5, done: isAuditDone, active: false, path: 'audit' as RoutePath },
          { label: 'Report', num: 6, done: isReportDone, active: false, path: 'reports' as RoutePath },
        ].map((step, idx, arr) => (
          <div key={step.label} className="row" style={{ alignItems: 'center', gap: 8 }}>
            <button
              type="button"
              onClick={() => onNavigate(step.path, { caseId: currentCaseId })}
              style={{
                background: 'none',
                border: 'none',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                cursor: 'pointer',
                padding: '4px 8px',
                borderRadius: 'var(--radius-sm)',
              }}
            >
              <span
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: '50%',
                  background: step.done ? 'var(--ok-bright)' : 'var(--surface-3)',
                  color: step.done ? '#ffffff' : 'var(--text-faint)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '11px',
                  fontWeight: 700,
                  fontFamily: 'var(--mono)',
                }}
              >
                {step.done ? '✓' : step.num}
              </span>
              <div className="stack" style={{ gap: 0, textAlign: 'left' }}>
                <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                  {step.label}
                </span>
                <span style={{ fontSize: '10px', color: step.done ? 'var(--ok-bright)' : 'var(--text-faint)' }}>
                  {step.done ? 'Completed' : 'Pending'}
                </span>
              </div>
            </button>
            {idx < arr.length - 1 ? (
              <span style={{ color: 'var(--text-faint)', fontSize: '12px' }}>→</span>
            ) : null}
          </div>
        ))}
      </div>

      {/* 4. 3-COLUMN GRID: CASE SUMMARY | EVIDENCE SUMMARY | CASE NOTES */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
          gap: 'var(--space-4)',
        }}
      >
        {/* Column 1: CASE SUMMARY */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', justifyContent: 'space-between' }}>
          <div className="stack" style={{ gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              CASE SUMMARY
            </span>
            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)', margin: 0 }}>
              {c?.description?.trim() ? (
                c.description
              ) : (
                <span style={{ color: 'var(--text-faint)' }}>
                  No description was recorded when this case was opened.
                </span>
              )}
            </p>

            {/*
              Only fields the case row or the evidence rows actually carry.
              `Language` and `Region` are gone entirely -- the backend has no such
              columns, so the previous "Hindi" and "India" were not defaults, they
              were assertions about a case nobody had entered. `Platform` is a
              per-evidence field and is reported as observed, never inferred.
            */}
            <div className="stack" style={{ gap: 6, fontSize: 'var(--text-xs)', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--text-faint)' }}>Complaint ref:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
                  {orPlaceholder(c?.complaint_reference)}
                </span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--text-faint)' }}>Platforms observed:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600, textAlign: 'right' }}>
                  {platforms.length > 0 ? platforms.join(', ') : 'None recorded'}
                </span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--text-faint)' }}>Latest verdict:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
                  {c?.latest_verdict ? verdictBandLabel(c.latest_verdict) : 'Not analysed'}
                </span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', gap: 10 }}>
                <span style={{ color: 'var(--text-faint)' }}>Last updated:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600, fontFamily: 'var(--mono)' }}>
                  {formatTimestampShort(c?.updated_at ?? null)}
                </span>
              </div>
            </div>
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start' }}
            onClick={() => onNavigate('evidence', { caseId: currentCaseId })}
          >
            View full details →
          </button>
        </div>

        {/* Column 2: EVIDENCE SUMMARY */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', justifyContent: 'space-between' }}>
          <div className="stack" style={{ gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              EVIDENCE SUMMARY
            </span>

            <div style={{ textAlign: 'center', padding: '14px 0' }}>
              <div
                style={{
                  width: 52,
                  height: 52,
                  borderRadius: 'var(--radius)',
                  background: 'var(--accent-wash)',
                  border: '1px solid var(--accent-line)',
                  color: 'var(--accent-bright)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  margin: '0 auto 8px',
                }}
              >
                <Icon name="document" size={26} />
              </div>
              <div style={{ fontSize: 'var(--text-xl)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                {caseEvidence.length}
              </div>
              <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Total Evidence
              </span>
            </div>

            <div className="row" style={{ justifyContent: 'space-around', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {videoCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Video</span>
              </div>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {imageCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Image</span>
              </div>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {audioCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Audio</span>
              </div>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {otherCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Other</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start' }}
            onClick={() => onNavigate('evidence', { caseId: currentCaseId })}
          >
            View evidence →
          </button>
        </div>

        {/*
          Column 3: RECORDED CUSTODY EVENTS.

          This replaces a "CASE NOTES" column that listed four fixed bullets on
          every case ("Video received from Cyber Cell", "Multiple reuploads
          identified", "Provenance analysis pending"). The backend stores no
          free-text case notes, so there was nothing behind them. What it does
          store is the append-only audit trail, which is the case's actual
          chronological record -- so that is what is shown.
        */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', justifyContent: 'space-between' }}>
          <div className="stack" style={{ gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              RECORDED CUSTODY EVENTS
            </span>

            {auditError ? (
              <ErrorBanner context="Audit trail" error={auditError} />
            ) : auditEvents === null ? (
              <Spinner label="Reading custody chain…" />
            ) : auditEvents.length === 0 ? (
              <Empty>No custody events are recorded against this case yet.</Empty>
            ) : (
              <div className="stack" style={{ gap: 8, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                {auditEvents
                  .slice(-5)
                  .reverse()
                  .map((ev) => (
                    <div key={ev.audit_id} className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                      <span style={{ color: 'var(--accent-bright)' }}>•</span>
                      <div className="stack" style={{ gap: 1 }}>
                        <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
                          {ev.event}
                        </span>
                        <span style={{ fontFamily: 'var(--mono)', fontSize: '10px', color: 'var(--text-faint)' }}>
                          #{ev.seq} · {formatTimestampShort(ev.timestamp)} · {ev.actor} ·{' '}
                          {shortHash(ev.row_hash, 8)}
                        </span>
                      </div>
                    </div>
                  ))}
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                  Showing the {Math.min(5, auditEvents.length)} most recent of {auditEvents.length}{' '}
                  recorded event(s).
                </span>
              </div>
            )}
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start' }}
            onClick={() => onNavigate('audit', { caseId: currentCaseId })}
          >
            View full custody chain →
          </button>
        </div>
      </div>

      {/* 5. BOTTOM BANNER: NEXT ACTION */}
      <div
        className="card row"
        style={{
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--space-3)',
          padding: 'var(--space-4)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border-accent)',
        }}
      >
        <div className="stack" style={{ gap: 2 }}>
          <span style={{ fontWeight: 800, fontSize: '11px', textTransform: 'uppercase', color: 'var(--accent-bright)', fontFamily: 'var(--mono)', letterSpacing: '0.06em' }}>
            NEXT ACTION
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
            {nextAction.text}
          </span>
        </div>

        <button
          type="button"
          className="btn btn--primary"
          style={{ padding: '8px 22px', fontWeight: 700 }}
          onClick={nextAction.action}
        >
          {nextAction.btn}
        </button>
      </div>
    </div>
  )
}
