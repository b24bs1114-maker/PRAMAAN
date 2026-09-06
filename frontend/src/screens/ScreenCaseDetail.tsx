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
import type { AuditEvent, CaseRecord, Evidence, StoredReport } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CaseDeleteDialog, DeleteCaseButton } from '../components/CaseDelete'
import { CaseEditDialog } from '../components/CaseEdit'
import { caseWorkflowDone } from '../components/CaseWorkflowStepper'
import { CopyButton } from '../components/CopyButton'
import { NoCaseSelected, Spinner } from '../components/Feedback'
import { EvidenceThumbnail } from '../components/EvidenceMedia'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { ReportFileActions } from '../components/ReportActions'
import { NOT_MEASURED, formatBytes, formatTimestampShort, orPlaceholder, shortHash } from '../lib/format'
import { isImageMedia } from '../lib/media'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel, verdictPillTone } from '../lib/signals'
import { useCaseDeletion } from '../state/useCaseDeletion'
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
  const { caseRecord, evidence, runAnalysis, analysis, propagation, report, reset: resetInvestigation } = investigation
  const [activeCase, setActiveCase] = useState<CaseRecord | null>(caseRecord)
  const [caseEvidence, setCaseEvidence] = useState<Evidence[]>(evidence)
  const [loading, setLoading] = useState(!caseRecord && Boolean(caseId))
  const [error, setError] = useState<unknown>(null)

  // Reports state. The download/open actions and their busy state belong to
  // components/ReportActions, which is shared with the Reports screen.
  const [reports, setReports] = useState<StoredReport[]>([])
  /**
   * Recorded custody events for the third column.
   *
   * Held separately from `error` on purpose: the audit read is supplementary, so
   * a failure there must not blank out a case dossier that loaded fine. It is
   * still surfaced in the column rather than swallowed.
   */
  const [auditEvents, setAuditEvents] = useState<AuditEvent[] | null>(null)
  const [auditError, setAuditError] = useState<unknown>(null)

  // The edit dialog. Opening it edits case fields in place via PATCH; it does
  // not navigate away, so the dossier stays put and re-renders the saved record.
  const [editing, setEditing] = useState(false)
  // Bumped after a successful edit so the audit panel re-reads the chain -- a
  // save appends a CASE_UPDATED entry, and the recorded-events count should show
  // it without a full navigation away and back.
  const [auditReloadKey, setAuditReloadKey] = useState(0)

  const currentCaseId = caseId || caseRecord?.case_id || null

  /*
   * Deleting from the dossier leaves the screen: once the backend confirms, the
   * case this screen is about no longer exists, and every panel below would be
   * describing a case that is gone. The queue then re-lists from the backend, so
   * the case's absence there is the confirmation -- not a message this screen
   * wrote on its way out.
   */
  const deletion = useCaseDeletion(() => {
    // The dossier's case is gone from the backend. If it was also the case
    // loaded in the shared store, clear the store so no screen can render its
    // analysis or offer its reports from here on.
    if (!caseId || caseRecord?.case_id === caseId) resetInvestigation()
    onNavigate('cases')
  })

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
  }, [currentCaseId, auditReloadKey])

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    api
      .listReports(currentCaseId)
      .then((res) => {
        if (active) setReports(res.reports)
      })
      .catch(() => {})
    return () => {
      active = false
    }
  }, [currentCaseId, report?.data])

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

  /*
   * Workflow completion, as this screen needs it.
   *
   * Only the three states the dossier itself reads: they pick the NEXT STEP
   * card's wording and the per-stage panels below. They come from
   * `caseWorkflowDone` -- the same function the workflow row above this screen
   * is drawn from -- because this screen used to keep its own copy of the rules,
   * and the copy had already drifted. It ticked provenance from
   * `instance_count > 0`, which counts the instances in the reconstructed
   * timeline and therefore counts the case's own exhibits: a case with three
   * exhibits and no trace ever run was told its next step was the audit, and the
   * dossier and the stepper could disagree about the same case at the same time.
   */
  const workflowDone = caseWorkflowDone(investigation, currentCaseId)
  const isAnalysisDone = workflowDone.analysis
  const isProvenanceDone = workflowDone.provenance
  const isAuditDone = workflowDone.audit

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
        // Named exactly as the control it sends the operator to, like the
        // provenance step above it. "Verify Audit" matched no button on the
        // audit screen, so the instruction and the destination disagreed.
        btn: 'Verify Audit Chain →',
        action: () => onNavigate('audit', { caseId: currentCaseId! }),
      }
    }
    return {
      text: 'Generate the backend-rendered forensic examination report for this case.',
      btn: 'Generate Forensic Report →',
      action: () => onNavigate('reports', { caseId: currentCaseId! }),
    }
  }, [caseEvidence.length, isAnalysisDone, isProvenanceDone, isAuditDone, currentCaseId, onNavigate, runAnalysis])

  if (!currentCaseId) {
    return (
      <NoCaseSelected
        purpose="review its case file"
        onViewCases={() => onNavigate('cases')}
      />
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

        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          {/* Opens the edit dialog in place. This used to route to Evidence
              Intake, where the title and description are read-only -- so the
              control named "Edit Case" could not edit the case, and it led to
              the same screen the "+ Ingest Exhibit" button already covers. */}
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setEditing(true)}
            disabled={!c}
            title={c ? 'Edit this case’s details' : 'Case record still loading'}
          >
            <Icon name="settings" size={14} />
            Edit Case
          </button>

          {/*
            Only offered once the real case record is in hand: the dialog requires
            the case number the backend issued, and there is nothing to type back
            if the record has not loaded.
          */}
          {c ? <DeleteCaseButton target={c} onClick={deletion.ask} label="Delete Case" /> : null}
        </div>
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
              ANALYST
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              {c?.examiner?.trim() ? c.examiner : 'Not specified'}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              LAST UPDATED
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
              {formatTimestampShort(c?.updated_at ?? c?.created_at ?? null)}
            </span>
          </div>
        </div>
      </div>

      {/* The workflow stepper (Case → Evidence → Analysis → Provenance →
          Audit → Report) is owned by the app shell and rendered once, above
          this screen. A second copy here is the duplication this structure
          exists to prevent -- the dossier's own contribution to the workflow
          is the NEXT STEP card below, which says what to do rather than
          repeating where the case is. */}

      {/* SECTION 1: CASE SUMMARY */}
      <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
            CASE SUMMARY &amp; EXAMINATION CONTEXT
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            Case ID: <code className="mono">{currentCaseId}</code>
          </span>
        </div>

        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)', margin: 0 }}>
          {c?.description?.trim() ? (
            c.description
          ) : (
            <span style={{ color: 'var(--text-faint)' }}>
              No description was recorded when this investigation was opened.
            </span>
          )}
        </p>

        <div className="grid-3col" style={{ gap: 12, borderTop: '1px solid var(--border)', paddingTop: 10, fontSize: 'var(--text-xs)' }}>
          <div className="stack" style={{ gap: 2 }}>
            <span style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>Complaint Reference</span>
            <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
              {orPlaceholder(c?.complaint_reference)}
            </span>
          </div>

          <div className="stack" style={{ gap: 2 }}>
            <span style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>Platforms Observed</span>
            <span style={{ color: 'var(--text-strong)', fontWeight: 600 }}>
              {platforms.length > 0 ? platforms.join(', ') : 'None recorded'}
            </span>
          </div>

          <div className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>Internal Storage Record</span>
            <div className="row row--wrap" style={{ gap: 4, alignItems: 'center', minWidth: 0 }}>
              <code className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>{shortHash(currentCaseId, 18)}</code>
              <CopyButton value={currentCaseId} title="Copy Internal Case ID" />
            </div>
          </div>
        </div>
      </div>

      {/* SECTION 2: EVIDENCE EXHIBITS */}
      <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              EVIDENCE EXHIBITS ({caseEvidence.length})
            </span>
            <Pill variant="neutral">{caseEvidence.length} {caseEvidence.length === 1 ? 'item' : 'items'}</Pill>
          </div>
          <button
            type="button"
            className="btn btn--primary btn--sm"
            onClick={() => onNavigate('intake')}
            style={{ fontWeight: 700 }}
          >
            + Ingest Exhibit
          </button>
        </div>

        {caseEvidence.length === 0 ? (
          <div style={{ padding: 'var(--space-4)', background: 'var(--surface-2)', borderRadius: 'var(--radius)', textAlign: 'center' }}>
            <p style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              No digital evidence exhibits have been ingested into this case yet.
            </p>
            <button type="button" className="btn btn--primary btn--sm" onClick={() => onNavigate('intake')}>
              Ingest Initial Evidence
            </button>
          </div>
        ) : (
          <div className="table-wrapper" style={{ overflowX: 'auto' }}>
            <table className="table" style={{ width: '100%', fontSize: 'var(--text-xs)' }}>
              <thead>
                <tr>
                  <th style={{ width: 48 }}>PREVIEW</th>
                  <th>FILENAME / EVIDENCE ID</th>
                  <th>CRYPTOGRAPHIC SHA-256</th>
                  <th>TYPE / SPECS</th>
                  <th>ACQUISITION CONTEXT</th>
                  <th>INGESTED / ANALYST</th>
                  <th style={{ textAlign: 'right' }}>ACTION</th>
                </tr>
              </thead>
              <tbody>
                {caseEvidence.map((ev) => {
                  const isImg = isImageMedia(ev.media_type)
                  const specs: string[] = [formatBytes(ev.size_bytes)]
                  if (ev.width && ev.height) specs.push(`${ev.width}×${ev.height}px`)
                  if (ev.format) specs.push(ev.format.toUpperCase())

                  return (
                    <tr key={ev.evidence_id}>
                      <td>
                        <div style={{ width: 44, height: 44, borderRadius: 'var(--radius-sm)', overflow: 'hidden', background: 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid var(--border)' }}>
                          {isImg ? (
                            <EvidenceThumbnail evidenceId={ev.evidence_id} iconSize={18} />
                          ) : (
                            <Icon name="document" size={18} style={{ color: 'var(--accent-bright)' }} />
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="stack" style={{ gap: 2 }}>
                          <span style={{ fontWeight: 700, color: 'var(--text-strong)', wordBreak: 'break-all' }}>{ev.filename}</span>
                          <div className="row" style={{ gap: 4, alignItems: 'center' }}>
                            <code style={{ fontSize: '10px', color: 'var(--text-faint)' }}>{ev.evidence_id}</code>
                            <CopyButton value={ev.evidence_id} title="Copy Evidence ID" />
                          </div>
                        </div>
                      </td>
                      <td>
                        <div className="row" style={{ gap: 4, alignItems: 'center' }}>
                          <code className="mono" style={{ fontSize: '11px', color: 'var(--accent-bright)' }}>
                            {shortHash(ev.sha256, 18)}
                          </code>
                          <CopyButton value={ev.sha256} title="Copy SHA-256 Digest" />
                        </div>
                      </td>
                      <td>
                        <div className="stack" style={{ gap: 2 }}>
                          <Pill variant="neutral">{ev.media_type.toUpperCase()}</Pill>
                          <span style={{ fontSize: '10.5px', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                            {specs.join(' · ')}
                          </span>
                        </div>
                      </td>
                      <td>
                        <span style={{ color: ev.acquisition_context ? 'var(--text-strong)' : 'var(--text-faint)', fontSize: '11px' }}>
                          {orPlaceholder(ev.acquisition_context)}
                        </span>
                      </td>
                      <td>
                        <div className="stack" style={{ gap: 2 }}>
                          <span style={{ fontSize: '10.5px', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                            {formatTimestampShort(ev.ingested_at)}
                          </span>
                          <span style={{ fontSize: '11px', color: 'var(--text-strong)' }}>
                            {c?.examiner || 'From session'}
                          </span>
                        </div>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          style={{ fontSize: '11px', padding: '3px 8px', color: 'var(--accent-bright)', fontWeight: 700 }}
                          onClick={() => {
                            if (!isAnalysisDone) runAnalysis()
                            onNavigate('analysis', { caseId: currentCaseId })
                          }}
                        >
                          {isAnalysisDone ? 'VIEW ANALYSIS →' : 'ANALYSE →'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* SECTIONS 3 & 4: CURRENT FINDING & PROVENANCE */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--space-4)' }}>
        {/* SECTION 3: CURRENT FINDING & FORENSIC SIGNALS */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              CURRENT FINDING &amp; FORENSIC SIGNALS
            </span>
            {c?.latest_verdict ? (
              <Pill variant={verdictPillTone(c.latest_verdict)}>{verdictBandLabel(c.latest_verdict)}</Pill>
            ) : (
              <Pill variant="neutral">NOT YET ANALYSED</Pill>
            )}
          </div>

          {c?.latest_verdict || analysisData ? (
            <div className="stack" style={{ gap: 10 }}>
              {(() => {
                const activeVerdict = c?.latest_verdict ?? analysisData?.verdict?.verdict
                const activeTone = verdictPillTone(activeVerdict)
                return (
                  <div
                    style={{
                      padding: '12px 16px',
                      background: 'var(--surface-2)',
                      borderRadius: 'var(--radius)',
                      borderLeft: activeTone === 'error'
                        ? '4px solid var(--danger-bright)'
                        : activeTone === 'ok'
                        ? '4px solid var(--ok-bright)'
                        : '4px solid var(--warn-bright)',
                    }}
                  >
                    <div
                      style={{
                        fontSize: 'var(--text-lg)',
                        fontWeight: 800,
                        color: activeTone === 'error'
                          ? 'var(--danger-bright)'
                          : activeTone === 'ok'
                          ? 'var(--ok-bright)'
                          : 'var(--warn-bright)',
                      }}
                    >
                      {verdictBandLabel(activeVerdict)}
                    </div>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 1.4, display: 'block', marginTop: 4 }}>
                      {activeVerdict?.includes('MANIPULATED')
                        ? 'Assessed signals support manipulation. Decision aid only, not a judicial conclusion.'
                        : activeVerdict?.includes('AUTHENTIC')
                        ? 'Assessed signals did not support manipulation. This is not a verification of authenticity.'
                        : 'Insufficient signal coverage to reach a definitive finding.'}
                    </span>
                  </div>
                )
              })()}

              {analysisData?.verdict ? (
                <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)', borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Confidence Band:</span>
                  <span style={{ fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text-strong)' }}>
                    {analysisData.verdict.confidence.toUpperCase()}
                  </span>
                </div>
              ) : null}

              <button
                type="button"
                className="btn btn--ghost btn--sm"
                style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start', fontWeight: 700 }}
                onClick={() => onNavigate('analysis', { caseId: currentCaseId })}
              >
                Open Forensic Analysis Console →
              </button>
            </div>
          ) : (
            <div className="stack" style={{ gap: 8 }}>
              <p style={{ margin: 0, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Evidence custody is established. Multi-signal forensic analysis has not been executed yet.
              </p>
              <button
                type="button"
                className="btn btn--primary btn--sm"
                style={{ width: 'fit-content', fontWeight: 700 }}
                onClick={() => {
                  runAnalysis()
                  onNavigate('analysis', { caseId: currentCaseId })
                }}
              >
                Run Forensic Analysis →
              </button>
            </div>
          )}
        </div>

        {/* SECTION 4: PROVENANCE & SOURCE TRACE */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              PROVENANCE &amp; SOURCE TRACE
            </span>
            {isProvenanceDone ? (
              <Pill variant="ok">TRACED</Pill>
            ) : (
              <Pill variant="neutral">NOT TRACED</Pill>
            )}
          </div>

          <div className="stack" style={{ gap: 10 }}>
            <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
              <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                EARLIEST KNOWN INSTANCE
              </span>
              <div style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', marginTop: 4 }}>
                {/*
                  Three states, not two. This printed "No earlier instance
                  confirmed in indexed corpus" whenever no origin was on record,
                  including for cases where no trace had ever been run -- which
                  reports the absence of a search as the result of one.
                */}
                {isProvenanceDone && isReady(propagation) && propagation.data.origin
                  ? propagation.data.origin.filename || 'Indexed Corpus Candidate'
                  : isProvenanceDone
                    ? 'No earlier instance found in the indexed corpus'
                    : 'NOT MEASURED — no provenance trace has been run'}
              </div>
              <span style={{ fontSize: '10px', color: 'var(--text-muted)', display: 'block', marginTop: 2 }}>
                earliest known instance in the indexed evidence corpus
              </span>
            </div>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start', fontWeight: 700 }}
              onClick={() => onNavigate('provenance', { caseId: currentCaseId })}
            >
              Trace Provenance &amp; Lineage →
            </button>
          </div>
        </div>
      </div>

      {/* SECTIONS 5 & 6: AUDIT & REPORT SUMMARY */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 'var(--space-4)' }}>
        {/* SECTION 5: AUDIT & EVIDENCE INTEGRITY */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              AUDIT &amp; EVIDENCE INTEGRITY
            </span>
            {isAuditDone ? (
              <Pill variant="ok">CHAIN INTACT</Pill>
            ) : (
              <Pill variant="neutral">NOT VERIFIED</Pill>
            )}
          </div>

          <div className="stack" style={{ gap: 10 }}>
            {auditError ? <ErrorBanner context="Audit trail" error={auditError} /> : null}
            <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Recorded Events:</span>
                <span style={{ fontWeight: 700, fontFamily: 'var(--mono)', color: 'var(--text-strong)' }}>
                  {auditEvents ? auditEvents.length : NOT_MEASURED}
                </span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)', marginTop: 4 }}>
                <span style={{ color: 'var(--text-muted)' }}>Chain Construction:</span>
                <span style={{ fontFamily: 'var(--mono)', fontSize: '11px', color: 'var(--text-strong)' }}>
                  SHA-256 linear hash chain
                </span>
              </div>
            </div>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start', fontWeight: 700 }}
              onClick={() => onNavigate('audit', { caseId: currentCaseId })}
            >
              Inspect Immutable Audit Ledger →
            </button>
          </div>
        </div>

        {/* SECTION 6: FORENSIC EXAMINATION REPORT */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
              FORENSIC EXAMINATION REPORT
            </span>
            <Pill variant={reports.length > 0 ? 'ok' : 'neutral'}>
              {reports.length} {reports.length === 1 ? 'REPORT' : 'REPORTS'}
            </Pill>
          </div>

          <div className="stack" style={{ gap: 10 }}>
            {reports.length > 0 ? (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                  LATEST CANONICAL PDF
                </span>
                <div style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', marginTop: 4 }}>
                  {reports[0].filename}
                </div>
                <div className="row" style={{ gap: 6, alignItems: 'center', marginTop: 4 }}>
                  <code className="mono" style={{ fontSize: '10.5px', color: 'var(--accent-bright)' }}>
                    {shortHash(reports[0].sha256, 18)}
                  </code>
                  <CopyButton value={reports[0].sha256} title="Copy PDF SHA-256" />
                </div>
                <div style={{ marginTop: 8 }}>
                  <ReportFileActions report={reports[0]} />
                </div>
              </div>
            ) : (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  No formal report generated yet for this case.
                </span>
              </div>
            )}

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start', fontWeight: 700 }}
              onClick={() => onNavigate('reports', { caseId: currentCaseId })}
            >
              Open Reports Console →
            </button>
          </div>
        </div>
      </div>

      {/* SECTION 7: NEXT ACTION BANNER */}
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

      <CaseEditDialog
        open={editing}
        target={c}
        onClose={() => setEditing(false)}
        onSaved={(updated) => {
          // Render the record the backend returned. The store may also hold this
          // case; if it does, it is refreshed by its own load on next navigation,
          // so the dossier does not reach into the shared slice from here.
          setActiveCase(updated)
          // The save appended a CASE_UPDATED entry; re-read the chain so the
          // audit panel's recorded-events count reflects it.
          setAuditReloadKey((k) => k + 1)
        }}
      />

      <CaseDeleteDialog
        state={deletion.state}
        onTyped={deletion.type}
        onCancel={deletion.dismiss}
        onConfirm={deletion.confirm}
      />
    </div>
  )
}
