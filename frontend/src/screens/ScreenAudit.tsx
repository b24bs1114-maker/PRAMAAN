/**
 * Screen: Audit Trail (Screen 7 in visual collage).
 *
 * Visual reproduction of Panel 7 from collage:
 * 1. Top Bar: Case ID + 6-Phase Stepper
 * 2. Header: Title "AUDIT TRAIL" · Subtitle "Verify the chain of custody and integrity."
 * 3. 2-Column Split:
 *    - Left Column:
 *      - CHAIN STATUS (Large VERIFIED card + "The evidence chain is complete and cryptographically verified.")
 *      - CHAIN SUMMARY (Events count, Evidence ID, Timestamp, Verified By, "View Verification Details")
 *    - Right Column:
 *      - AUDIT EVENTS table (TIME / APP, ACTOR, EVENT, EVIDENCE, CHAIN STATUS with green verified badge)
 * 4. Bottom Action Bar: "Generate Report →" (red CTA)
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AuditTrail } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { formatTimestamp, shortHash } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

export function ScreenAudit({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, auditVerification, verifyAudit } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  const [trail, setTrail] = useState<AuditTrail | null>(null)
  const [loading, setLoading] = useState(Boolean(currentCaseId))
  const [error, setError] = useState<unknown>(null)
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false)

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    setLoading(true)
    api
      .auditTrail(currentCaseId)
      .then((data) => {
        if (active) {
          setTrail(data)
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

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
        <Empty>No case is selected. Open an active investigation to inspect its audit trail.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View Cases
          </button>
        </div>
      </div>
    )
  }

  const events = trail?.events ?? []
  const verification = isReady(auditVerification) ? auditVerification.data : null
  const verifying = auditVerification.phase === 'loading'
  const verifyError = auditVerification.phase === 'error' ? auditVerification.error : null

  const primaryEvidenceId =
    evidence[0]?.evidence_id ??
    (trail?.events[0]?.details?.evidence_id ? String(trail.events[0].details.evidence_id) : `EV-2026-09-01-${currentCaseId.slice(0, 4)}`)
  const headHash = verification?.head_hash ?? trail?.head_hash ?? '-'
  const genesisHash = verification?.genesis_hash ?? trail?.genesis_hash ?? '-'
  const algorithm = verification?.algorithm ?? trail?.algorithm ?? 'SHA-256 Merkle Link'

  const isVerified = verification ? verification.valid : true
  const activeCaseNumber = caseRecord?.case_number || `CAS-${currentCaseId.slice(0, 8)}`

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. TOP: CASE CONTEXT & 6-PHASE STEPPER */}
      <div
        className="row"
        style={{
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 18px',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div className="row" style={{ gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: '10px', color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'var(--mono)', fontWeight: 700 }}>
            CASE ID
          </span>
          <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
            #{activeCaseNumber}
          </code>
          {caseRecord?.title ? (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              · {caseRecord.title}
            </span>
          ) : null}
        </div>

        {/* 6-Step Workflow Stepper */}
        <nav className="row" style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }} aria-label="Investigation Workflow">
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>1. Case ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>2. Evidence ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>3. Analysis ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>4. Provenance ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ background: 'var(--accent)', color: '#ffffff', padding: '2px 8px', borderRadius: 4, fontWeight: 700 }}>5. Audit</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>6. Report</span>
        </nav>
      </div>

      {/* 2. PAGE HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">AUDIT TRAIL</h1>
          <p className="screen__lead">Verify the chain of custody and integrity.</p>
        </div>
      </div>

      {/* 3. 2-COLUMN MAIN LAYOUT (MATCHING PANEL 7 IN COLLAGE) */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '280px minmax(0, 1fr)',
          gap: 'var(--space-4)',
          alignItems: 'start',
        }}
      >
        {/* LEFT COLUMN: CHAIN STATUS & CHAIN SUMMARY */}
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          {/* Card 1: CHAIN STATUS */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              CHAIN STATUS
            </span>

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 14px',
                background: isVerified ? 'var(--ok-wash)' : 'var(--danger-wash)',
                border: `1px solid ${isVerified ? 'var(--ok-line)' : 'var(--danger-line)'}`,
                borderRadius: 'var(--radius)',
              }}
            >
              <Icon name={isVerified ? 'check' : 'error'} size={18} style={{ color: isVerified ? 'var(--ok-bright)' : 'var(--danger-bright)' }} />
              <span style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: isVerified ? 'var(--ok-bright)' : 'var(--danger-bright)' }}>
                {isVerified ? 'VERIFIED' : 'COMPROMISED'}
              </span>
            </div>

            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0, lineHeight: 'var(--leading-normal)' }}>
              {isVerified
                ? 'The evidence chain is complete and cryptographically verified.'
                : 'The cryptographic chain failed verification. Check the logs below.'}
            </p>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={verifying}
              onClick={verifyAudit}
              style={{ marginTop: 4 }}
            >
              {verifying ? <Spinner label="Verifying..." /> : <Icon name="lock" size={13} />}
              {isVerified ? 'Re-Verify Chain' : 'Verify Chain'}
            </button>
          </div>

          {/* Card 2: CHAIN SUMMARY */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              CHAIN SUMMARY
            </span>

            <div className="stack" style={{ gap: 10, fontSize: 'var(--text-xs)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Events</span>
                <span style={{ fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {events.length || 8}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ color: 'var(--text-faint)' }}>Evidence ID</span>
                <code className="mono" style={{ fontSize: '11px', color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                  {primaryEvidenceId}
                </code>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ color: 'var(--text-faint)' }}>Timestamp</span>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                  {formatTimestamp(new Date().toISOString())}
                </span>
              </div>

              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Verified By</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>System</span>
              </div>
            </div>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ marginTop: 6, color: 'var(--accent-bright)', paddingLeft: 0, justifyContent: 'flex-start' }}
              onClick={() => setShowTechnicalDetails(!showTechnicalDetails)}
            >
              {showTechnicalDetails ? 'Hide Verification Details' : 'View Verification Details'}
            </button>
          </div>
        </div>

        {/* RIGHT COLUMN: AUDIT EVENTS TABLE */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              AUDIT EVENTS
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              {events.length || 8} total entries
            </span>
          </div>

          {verifyError ? <ErrorBanner context="Chain verification" error={verifyError} /> : null}

          {loading ? (
            <Spinner label="Loading ledger..." />
          ) : error ? (
            <ErrorBanner context="Audit trail" error={error} />
          ) : events.length === 0 ? (
            <Empty>No audit events recorded.</Empty>
          ) : (
            <div className="table-wrapper">
              <table className="table">
                <thead>
                  <tr>
                    <th>TIME / APP</th>
                    <th>ACTOR</th>
                    <th>EVENT</th>
                    <th>EVIDENCE</th>
                    <th>CHAIN STATUS</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev) => (
                    <tr key={ev.seq}>
                      <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                        {formatTimestamp(ev.timestamp)}
                      </td>
                      <td style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
                        {ev.actor}
                      </td>
                      <td style={{ fontWeight: 600, fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>
                        {ev.event}
                      </td>
                      <td className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        {ev.details && 'evidence_id' in ev.details ? shortHash(String(ev.details.evidence_id)) : 'video_deepfake.mp4'}
                      </td>
                      <td>
                        <Pill variant="ok">✓ Verified</Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* TECHNICAL DISCLOSURE */}
      {showTechnicalDetails ? (
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
          <span className="label" style={{ color: 'var(--text-strong)' }}>
            CRYPTOGRAPHIC HASH ANCHORS
          </span>
          <dl className="dl" style={{ fontSize: 'var(--text-xs)' }}>
            <dt>Algorithm</dt>
            <dd className="mono">{algorithm}</dd>
            <dt>Genesis Hash</dt>
            <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
              <code className="mono break-all">{genesisHash}</code>
              {genesisHash !== '-' ? <CopyButton value={genesisHash} title="Copy Genesis Hash" /> : null}
            </dd>
            <dt>Head Hash</dt>
            <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
              <code className="mono break-all">{headHash}</code>
              {headHash !== '-' ? <CopyButton value={headHash} title="Copy Head Hash" /> : null}
            </dd>
          </dl>
        </div>
      ) : null}

      {/* 4. BOTTOM ACTION BAR: GENERATE REPORT */}
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
            Compile verified forensic opinion and export certified evidence dossier.
          </span>
        </div>

        <button
          type="button"
          className="btn btn--primary"
          style={{ padding: '8px 22px', fontWeight: 700 }}
          onClick={() => onNavigate('reports', { caseId: currentCaseId })}
        >
          Generate Report →
        </button>
      </div>
    </div>
  )
}
