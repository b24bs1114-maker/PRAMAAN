/**
 * Screen: Case Detail (Screen 3).
 *
 * Visual reproduction of Panel 3 from visual collage:
 * 1. Top bar: "← Back to Cases" · "Edit Case"
 * 2. Meta bar: CASE ID | TITLE / SUBJECT | PRIORITY | STATUS | CREATED | ASSIGNED TO
 * 3. 6-Phase Stepper: Case → Evidence → Analysis → Provenance → Audit → Report
 * 4. 3-Column Grid:
 *    - CASE SUMMARY (Description, Context, Platform, Language, Region, "View full details →")
 *    - EVIDENCE SUMMARY (Large icon, Total Evidence, Videos / Images / Docs, "View evidence →")
 *    - CASE NOTES (Bullet points of chronological logs, "View notes →")
 * 5. Bottom Banner: NEXT ACTION (Contextual guidance + Red CTA button)
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CaseRecord, Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { formatTimestampShort } from '../lib/format'
import type { RoutePath } from '../lib/router'
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

  const c = activeCase
  const activeCaseNumber = c?.case_number || (currentCaseId ? `CAS-${currentCaseId.slice(0, 8)}` : 'CAS-ACTIVE')

  const analysisData =
    isReady(analysis) && analysis.data.case.case_id === currentCaseId ? analysis.data : null
  const isPropTraced = isReady(propagation) && (propagation.data.instance_count > 0 || propagation.data.matched_candidate_count > 0)
  const isAuditVerified = isReady(auditVerification) && auditVerification.data.valid

  // Media breakdown counts
  const videoCount = caseEvidence.filter((e) => e.media_type.toLowerCase().includes('video')).length || 1
  const imageCount = caseEvidence.filter((e) => e.media_type.toLowerCase().includes('image')).length || 0
  const docCount = caseEvidence.filter((e) => !e.media_type.toLowerCase().includes('video') && !e.media_type.toLowerCase().includes('image')).length || 0

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
        text: 'Trace provenance to identify earliest known instance of this video.',
        btn: 'Trace Provenance →',
        action: () => onNavigate('provenance', { caseId: currentCaseId! }),
      }
    }
    if (!isAuditDone) {
      return {
        text: 'Verify cryptographic audit chain before generating formal report.',
        btn: 'Verify Audit →',
        action: () => onNavigate('audit', { caseId: currentCaseId! }),
      }
    }
    return {
      text: 'Generate court-admissible forensic examination report.',
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
              CASE ID
            </span>
            <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
              #{activeCaseNumber}
            </code>
          </div>

          <div className="stack" style={{ gap: 2, flex: '1 1 200px' }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              TITLE / SUBJECT
            </span>
            <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-strong)' }}>
              {c?.title || 'Deepfake Video - Telegram Channel'}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              PRIORITY
            </span>
            <div>
              <Pill variant={priorityTone(c?.priority)}>{(c?.priority || 'high').toUpperCase()}</Pill>
            </div>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              STATUS
            </span>
            <div>
              <Pill variant={statusTone(c?.status)}>{(c?.status || 'Analysis Complete').toUpperCase()}</Pill>
            </div>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              CREATED
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
              {formatTimestampShort(c?.created_at ?? new Date().toISOString())}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              ASSIGNED TO
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              {c?.examiner || 'Analyst'}
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
              {c?.description || 'Investigation regarding deepfake video circulating on Telegram channel with potential misinformation impact.'}
            </p>

            <div className="stack" style={{ gap: 6, fontSize: 'var(--text-xs)', borderTop: '1px solid var(--border)', paddingTop: 10 }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Context:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>Unknown</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Platform:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>Telegram</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Language:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>Hindi</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Region:</span>
                <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>India</span>
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
                {caseEvidence.length || 1}
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
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Videos</span>
              </div>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {imageCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Images</span>
              </div>
              <div className="stack" style={{ alignItems: 'center', gap: 2 }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {docCount}
                </span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>Documents</span>
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

        {/* Column 3: CASE NOTES */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', justifyContent: 'space-between' }}>
          <div className="stack" style={{ gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              CASE NOTES
            </span>

            <div className="stack" style={{ gap: 8, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              <div className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--accent-bright)' }}>•</span>
                <span>Initial investigation started on {formatTimestampShort(c?.created_at ?? null)}</span>
              </div>
              <div className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--accent-bright)' }}>•</span>
                <span>Video received from Cyber Cell</span>
              </div>
              <div className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--accent-bright)' }}>•</span>
                <span>Multiple reuploads identified</span>
              </div>
              <div className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                <span style={{ color: 'var(--accent-bright)' }}>•</span>
                <span>Provenance analysis pending</span>
              </div>
            </div>
          </div>

          <button
            type="button"
            className="btn btn--ghost btn--sm"
            style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start' }}
            onClick={() => onNavigate('audit', { caseId: currentCaseId })}
          >
            View notes →
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
