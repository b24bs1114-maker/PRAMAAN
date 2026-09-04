/**
 * Screen: Audit Trail.
 *
 * The custody ledger for one case, plus the result of verifying it.
 *
 * The most serious defect this screen used to carry was `const isVerified =
 * verification ? verification.valid : true` -- so before anyone pressed "Verify
 * Chain", the card read **VERIFIED** in green and claimed "the evidence chain is
 * complete and cryptographically verified". A cryptographic verification that
 * has not been performed is not a pass. Verification is now tri-state: not yet
 * run, valid, or failed, and only the middle one is green.
 *
 * Also corrected here:
 *   - The algorithm fallback read "SHA-256 Merkle Link". The chain is a LINEAR
 *     hash chain -- `row_hash = SHA-256(previous_hash || canonical_json(payload))`
 *     -- with no Merkle tree anywhere in it. Naming the wrong construction in a
 *     court-facing tool misdescribes what the integrity guarantee actually is.
 *   - The event count fell back to `8` and the per-row evidence cell to
 *     `video_deepfake.mp4`, so an empty ledger displayed eight rows' worth of
 *     count and every row named a file that does not exist.
 *   - Every row carried a green "✓ Verified" pill unconditionally. A row's
 *     status now comes from the verification response's `first_invalid_seq`, and
 *     says "Not verified" when no verification has been run.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { AuditTrail } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { NOT_MEASURED, formatTimestamp, shortHash } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

/** The three genuinely different states of chain verification. */
type ChainState = 'unverified' | 'valid' | 'invalid'

export function ScreenAudit({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, analysis, propagation, auditVerification, verifyAudit } = investigation
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

  /**
   * The evidence item this ledger is anchored to, if there is one on record.
   *
   * No synthesised identifier: the old fallback produced
   * `EV-2026-09-01-${case_id.slice(0, 4)}`, a plausible-looking evidence id in
   * the system's own format for an item that was never ingested.
   */
  const primaryEvidenceId =
    evidence[0]?.evidence_id ??
    (trail?.events[0]?.details?.evidence_id
      ? String(trail.events[0].details.evidence_id)
      : null)

  const headHash = verification?.head_hash ?? trail?.head_hash ?? NOT_MEASURED
  const genesisHash = verification?.genesis_hash ?? trail?.genesis_hash ?? NOT_MEASURED
  /**
   * The construction the backend names, verbatim.
   *
   * The fallback describes what `audit.py` actually computes -- a linear chain,
   * each row hashing its predecessor's hash together with its own canonical
   * payload. It is deliberately NOT called a Merkle link: there is no tree, no
   * sibling hashing and no inclusion proof, so claiming one would overstate the
   * cryptographic property on a screen an examiner may be asked to explain.
   */
  const algorithm =
    verification?.algorithm ?? trail?.algorithm ?? 'SHA-256 linear hash chain'

  /**
   * Verification state.
   *
   * `trail.chain_valid` is honoured when the embedded trail carries it, but the
   * absence of any verification result is `unverified` -- never a pass.
   */
  const chainState: ChainState = verification
    ? verification.valid
      ? 'valid'
      : 'invalid'
    : trail?.chain_valid === true
      ? 'valid'
      : trail?.chain_valid === false
        ? 'invalid'
        : 'unverified'

  /** The sequence number at which verification first failed, when it did. */
  const firstInvalidSeq = verification?.first_invalid_seq ?? trail?.first_invalid_seq ?? null

  const activeCaseNumber = caseRecord?.case_number || null
  const totalRows = trail?.total_rows ?? events.length

  /**
   * Workflow position, derived from what has actually happened this session.
   *
   * The stepper used to hard-code steps 1-4 as complete with green ticks on
   * every visit, which told the examiner that provenance tracing had been done
   * on cases where it had not.
   */
  const steps = useMemo(
    () => [
      { label: 'Case', done: Boolean(caseRecord) },
      { label: 'Evidence', done: evidence.length > 0 },
      { label: 'Analysis', done: isReady(analysis) || Boolean(caseRecord?.latest_verdict) },
      {
        label: 'Provenance',
        done:
          isReady(propagation) &&
          (propagation.data.instance_count > 0 || propagation.data.matched_candidate_count > 0),
      },
    ],
    [caseRecord, evidence.length, analysis, propagation],
  )

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
            {activeCaseNumber ? 'CASE NUMBER' : 'INTERNAL CASE ID'}
          </span>
          <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
            {activeCaseNumber ? `#${activeCaseNumber}` : shortHash(currentCaseId, 12)}
          </code>
          {caseRecord?.title ? (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              · {caseRecord.title}
            </span>
          ) : null}
        </div>

        {/* Workflow stepper. Ticks reflect real state; nothing is pre-ticked. */}
        <nav className="row" style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }} aria-label="Investigation Workflow">
          {steps.map((step, idx) => (
            <span key={step.label} className="row" style={{ gap: 6, alignItems: 'center' }}>
              <span
                style={{
                  color: step.done ? 'var(--ok-bright)' : 'var(--text-faint)',
                  fontWeight: step.done ? 600 : 400,
                }}
              >
                {idx + 1}. {step.label} {step.done ? '✓' : '·'}
              </span>
              <span style={{ color: 'var(--text-faint)' }}>→</span>
            </span>
          ))}
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
                background:
                  chainState === 'valid'
                    ? 'var(--ok-wash)'
                    : chainState === 'invalid'
                      ? 'var(--danger-wash)'
                      : 'var(--surface-3)',
                border: `1px solid ${
                  chainState === 'valid'
                    ? 'var(--ok-line)'
                    : chainState === 'invalid'
                      ? 'var(--danger-line)'
                      : 'var(--border)'
                }`,
                borderRadius: 'var(--radius)',
              }}
            >
              <Icon
                name={chainState === 'valid' ? 'check' : chainState === 'invalid' ? 'error' : 'lock'}
                size={18}
                style={{
                  color:
                    chainState === 'valid'
                      ? 'var(--ok-bright)'
                      : chainState === 'invalid'
                        ? 'var(--danger-bright)'
                        : 'var(--text-muted)',
                }}
              />
              <span
                style={{
                  fontSize: 'var(--text-sm)',
                  fontWeight: 800,
                  color:
                    chainState === 'valid'
                      ? 'var(--ok-bright)'
                      : chainState === 'invalid'
                        ? 'var(--danger-bright)'
                        : 'var(--text-muted)',
                }}
              >
                {chainState === 'valid'
                  ? 'CHAIN INTACT'
                  : chainState === 'invalid'
                    ? 'CHAIN BROKEN'
                    : 'NOT VERIFIED'}
              </span>
            </div>

            <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0, lineHeight: 'var(--leading-normal)' }}>
              {chainState === 'valid'
                ? 'Every recorded row re-hashes to its successor, so the ledger has not been altered since it was written. This attests to the integrity of the record, not to the truth of what the rows describe.'
                : chainState === 'invalid'
                  ? `Recomputation does not match the stored hashes${
                      firstInvalidSeq !== null ? ` from sequence #${firstInvalidSeq} onward` : ''
                    }. Treat the ledger from that point as unreliable.`
                  : 'The chain has not been verified in this session. Run verification to recompute every row hash — an unverified chain is neither intact nor broken, it is unchecked.'}
            </p>

            <button
              type="button"
              className="btn btn--ghost btn--sm"
              disabled={verifying}
              onClick={verifyAudit}
              style={{ marginTop: 4 }}
            >
              {verifying ? <Spinner label="Verifying..." /> : <Icon name="lock" size={13} />}
              {chainState === 'unverified' ? 'Verify Chain' : 'Re-Verify Chain'}
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
                  {/* Real count. The old `|| 8` reported eight rows for an empty ledger. */}
                  {totalRows}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ color: 'var(--text-faint)' }}>Evidence ID</span>
                <code className="mono" style={{ fontSize: '11px', color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                  {primaryEvidenceId ?? NOT_MEASURED}
                </code>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ color: 'var(--text-faint)' }}>Last recorded event</span>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                  {/*
                    The timestamp of the newest row, not `new Date()`. The old code
                    rendered the current clock under the label "Timestamp", which
                    reads as the moment the chain was last written to.
                  */}
                  {events.length > 0
                    ? formatTimestamp(events[events.length - 1].timestamp)
                    : NOT_MEASURED}
                </span>
              </div>

              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Verification</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>
                  {chainState === 'unverified'
                    ? 'Not run'
                    : `${verification ? 'POST /audit/verify' : 'Embedded in analysis'}`}
                </span>
              </div>

              {verification ? (
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-faint)' }}>Rows recomputed</span>
                  <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                    {verification.case_rows} of {verification.total_rows}
                  </span>
                </div>
              ) : null}

              {trail?.truncated ? (
                <p style={{ margin: 0, color: 'var(--warn-bright, var(--text-muted))', fontSize: '10.5px' }}>
                  This view is truncated: {events.length} of {totalRows} rows are listed.
                </p>
              ) : null}
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
              {totalRows} total {totalRows === 1 ? 'entry' : 'entries'}
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
                  {events.map((ev) => {
                    /*
                     * Per-row status. A linear chain breaks from a point onward,
                     * so every row at or after `first_invalid_seq` is suspect and
                     * everything before it still verifies. With no verification
                     * result there is nothing to report but that fact -- the old
                     * code printed a green "✓ Verified" pill on every row
                     * regardless, including on a chain nobody had checked.
                     */
                    const rowState: ChainState =
                      chainState === 'unverified'
                        ? 'unverified'
                        : firstInvalidSeq !== null && ev.seq >= firstInvalidSeq
                          ? 'invalid'
                          : chainState
                    const evidenceId =
                      ev.details && 'evidence_id' in ev.details && ev.details.evidence_id
                        ? shortHash(String(ev.details.evidence_id))
                        : NOT_MEASURED
                    return (
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
                          {evidenceId}
                        </td>
                        <td>
                          {rowState === 'valid' ? (
                            <Pill variant="ok">✓ Hash matches</Pill>
                          ) : rowState === 'invalid' ? (
                            <Pill variant="error">✕ Hash mismatch</Pill>
                          ) : (
                            <Pill variant="unavailable">Not verified</Pill>
                          )}
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

      {/* TECHNICAL DISCLOSURE */}
      {showTechnicalDetails ? (
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
          <span className="label" style={{ color: 'var(--text-strong)' }}>
            CRYPTOGRAPHIC HASH ANCHORS
          </span>
          <dl className="dl" style={{ fontSize: 'var(--text-xs)' }}>
            <dt>Construction</dt>
            <dd className="mono">{algorithm}</dd>
            <dt>Row hash</dt>
            <dd className="mono">SHA-256(previous_hash ‖ canonical_json(payload))</dd>
            <dt>Genesis Hash</dt>
            <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
              <code className="mono break-all">{genesisHash}</code>
              {genesisHash !== NOT_MEASURED ? <CopyButton value={genesisHash} title="Copy Genesis Hash" /> : null}
            </dd>
            <dt>Head Hash</dt>
            <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
              <code className="mono break-all">{headHash}</code>
              {headHash !== NOT_MEASURED ? <CopyButton value={headHash} title="Copy Head Hash" /> : null}
            </dd>
          </dl>
          {trail?.interpretation ? (
            <p style={{ margin: 0, fontSize: '10.5px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              {trail.interpretation}
            </p>
          ) : null}
          {(verification?.issues ?? trail?.issues ?? []).length > 0 ? (
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: '10.5px', color: 'var(--danger-bright)' }}>
              {(verification?.issues ?? trail?.issues ?? []).map((issue) => (
                <li key={issue}>{issue}</li>
              ))}
            </ul>
          ) : null}
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
            Compile the recorded findings and custody chain into the backend-rendered report.
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
