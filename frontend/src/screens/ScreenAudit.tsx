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

import { Fragment, useEffect, useState } from 'react'
import { api } from '../api'
import type { AuditTrail } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, NoCaseSelected, Spinner } from '../components/Feedback'
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
  const { caseRecord, evidence, auditVerification, verifyAudit } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  const [trail, setTrail] = useState<AuditTrail | null>(null)
  const [loading, setLoading] = useState(Boolean(currentCaseId))
  const [error, setError] = useState<unknown>(null)
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false)
  // Which event rows are expanded to reveal their recorded payload and the two
  // hashes that link them into the chain. Keyed by the row's sequence number.
  const [expandedSeqs, setExpandedSeqs] = useState<Set<number>>(new Set())

  const toggleRow = (seq: number) => {
    setExpandedSeqs((prev) => {
      const next = new Set(prev)
      if (next.has(seq)) next.delete(seq)
      else next.add(seq)
      return next
    })
  }

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
    return <NoCaseSelected purpose="inspect its audit trail" onViewCases={() => onNavigate('cases')} />
  }

  const events = trail?.events ?? []
  // Case-scoped guard: the verification slice is shared and survives a case
  // switch, so Case A's "CHAIN INTACT" must not be presented as the result for
  // Case B's rows. A verification whose scope is global (case_id null) is
  // still applicable; a case-scoped one for another case is not.
  const verification =
    isReady(auditVerification) &&
    (auditVerification.data.case_id === null || auditVerification.data.case_id === currentCaseId)
      ? auditVerification.data
      : null
  const verifying = auditVerification.phase === 'loading'
  const verifyError = auditVerification.phase === 'error' ? auditVerification.error : null

  /**
   * The case this ledger belongs to, but only once the store agrees it is loaded.
   *
   * This screen can be opened with a `?caseId=` the store has not caught up to,
   * so anything read off `caseRecord` has to be scoped or it reports another
   * case's facts under this case's chain.
   */
  const scopedCase =
    currentCaseId && caseRecord?.case_id === currentCaseId ? caseRecord : null

  /**
   * How many exhibits this ledger covers, as the backend counts them.
   *
   * Taken from the case record, not from the length of the store's evidence
   * array: that array is legitimately empty while its fetch is still in flight,
   * so counting it would print "None ingested" -- a factual claim about the
   * case -- for a case whose exhibits simply had not arrived yet. `null` here
   * means the count is genuinely not loaded, which is a third state and is
   * rendered as such rather than collapsed into zero.
   */
  const evidenceCount = scopedCase?.evidence_count ?? null
  const caseEvidence = scopedCase ? evidence : []

  /**
   * The evidence item this ledger is anchored to, if there is one on record.
   *
   * No synthesised identifier: the old fallback produced
   * `EV-2026-09-01-${case_id.slice(0, 4)}`, a plausible-looking evidence id in
   * the system's own format for an item that was never ingested.
   */
  const primaryEvidenceId =
    caseEvidence[0]?.evidence_id ??
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

  const totalRows = trail?.total_rows ?? events.length

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* The case identity strip and the workflow stepper (Case → Evidence →
          Analysis → …) are owned by the app shell and rendered once, above
          this screen. They are not repeated here. */}

      {/* 2. PAGE HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">AUDIT &amp; EVIDENCE INTEGRITY</h1>
          <p className="screen__lead">Verify the recorded chain of custody and the integrity of the ledger.</p>
        </div>
      </div>

      {/* 3. 2-COLUMN MAIN LAYOUT (MATCHING PANEL 7 IN COLLAGE).

          `workspace-2col` collapses the rail+table pair to one column under
          960px; the inline columns apply at desktop widths only. */}
      <div
        className="workspace-2col"
        style={{
          gridTemplateColumns: '280px minmax(0, 1fr)',
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

            {/*
              The act this screen exists for, ranked by whether it still needs
              doing.

              An unverified chain is the one state the operator came here to
              resolve, so the control that resolves it leads; once the chain has
              been checked, re-checking is a follow-up and steps back. This was
              `btn--ghost btn--sm` in both states -- the weakest rank the system
              has -- so a headline reading "NOT VERIFIED" sat directly above a
              control styled as an afterthought.
            */}
            <button
              type="button"
              className={`btn btn--sm ${chainState === 'unverified' ? 'btn--primary' : 'btn--ghost'}`}
              // Double-submit prevention: each run recomputes every row hash and
              // appends an AUDIT_VERIFIED row, so a second click mid-flight
              // would duplicate a real entry in the case's chain.
              disabled={verifying}
              onClick={verifyAudit}
              style={{ marginTop: 4 }}
            >
              {/*
                One label, not two. The spinner carried its own "Verifying..."
                caption while the button's own text went on rendering beside it,
                so mid-flight the control read "Verifying... Verify Audit Chain".
              */}
              {verifying ? <Spinner /> : <Icon name="lock" size={13} />}
              {verifying
                ? 'Verifying…'
                : chainState === 'unverified'
                  ? 'Verify Audit Chain'
                  : 'Re-Verify Audit Chain'}
            </button>
          </div>

          {/* Card 2: CHAIN SUMMARY */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              INTEGRITY SUMMARY
            </span>

            <div className="stack" style={{ gap: 10, fontSize: 'var(--text-xs)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-faint)' }}>Events</span>
                <span style={{ fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {/* Real count. The old `|| 8` reported eight rows for an empty ledger. */}
                  {totalRows}
                </span>
              </div>

              {/*
                What this ledger covers, which is the case -- not one exhibit.

                This slot printed `evidence[0].evidence_id` under a bare
                "Evidence ID" label, so a case holding four exhibits named the
                first one and silently omitted the rest, reading as though the
                chain were anchored to that single item. A single id is only
                stated when there genuinely is a single exhibit.
              */}
              {evidenceCount === 1 ? (
                <div className="stack" style={{ gap: 2 }}>
                  <span style={{ color: 'var(--text-faint)' }}>Evidence covered</span>
                  <code className="mono" style={{ fontSize: '11px', color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                    {primaryEvidenceId ?? NOT_MEASURED}
                  </code>
                </div>
              ) : (
                <div className="row" style={{ justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-faint)' }}>Evidence covered</span>
                  <span style={{ fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                    {evidenceCount === null
                      ? NOT_MEASURED
                      : evidenceCount === 0
                        ? 'None ingested'
                        : `${evidenceCount} exhibits`}
                  </span>
                </div>
              )}

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
                    <th style={{ width: 32 }} />
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
                    const isOpen = expandedSeqs.has(ev.seq)
                    // CASE_DELETED is the event that outlives its own case: the
                    // case row is gone, the ledger entry stays. It is called out
                    // so a reader does not skim past the fact of a deletion.
                    const isDeletion = ev.event === 'CASE_DELETED'
                    const detailEntries = Object.entries(ev.details ?? {})
                    return (
                      <Fragment key={ev.seq}>
                      <tr>
                        <td style={{ width: 32 }}>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            style={{ padding: '2px 6px' }}
                            aria-expanded={isOpen}
                            aria-label={isOpen ? 'Collapse event detail' : 'Expand event detail'}
                            onClick={() => toggleRow(ev.seq)}
                          >
                            <Icon
                              name="arrow-right"
                              size={12}
                              style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
                            />
                          </button>
                        </td>
                        <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                          {formatTimestamp(ev.timestamp)}
                        </td>
                        <td style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
                          {ev.actor}
                        </td>
                        <td style={{ fontWeight: 600, fontSize: 'var(--text-xs)', color: isDeletion ? 'var(--danger-bright)' : 'var(--text-strong)' }}>
                          <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                            {ev.event}
                            {isDeletion ? <Pill variant="error">CASE REMOVED</Pill> : null}
                          </span>
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
                      {isOpen ? (
                        <tr>
                          <td colSpan={6} style={{ background: 'var(--surface-2)', padding: 'var(--space-3)' }}>
                            <div className="stack" style={{ gap: 'var(--space-3)' }}>
                              <div className="grid-2col" style={{ gap: 'var(--space-3)', fontSize: 'var(--text-xs)' }}>
                                <div className="stack" style={{ gap: 2 }}>
                                  <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>SEQUENCE</span>
                                  <span className="mono" style={{ color: 'var(--text-strong)', fontWeight: 700 }}>#{ev.seq}</span>
                                </div>
                                <div className="stack" style={{ gap: 2 }}>
                                  <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>AUDIT ID</span>
                                  <code className="mono break-all" style={{ color: 'var(--text-strong)' }}>{ev.audit_id}</code>
                                </div>
                              </div>
                              {/*
                                The two hashes that place this row in the linear
                                chain: it carries its predecessor's row hash, and
                                its own row hash is the successor's predecessor.
                              */}
                              <div className="stack" style={{ gap: 2, fontSize: 'var(--text-xs)' }}>
                                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>PREVIOUS HASH</span>
                                <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                                  <code className="mono break-all" style={{ color: 'var(--text-muted)' }}>{ev.previous_hash}</code>
                                  <CopyButton value={ev.previous_hash} title="Copy previous hash" />
                                </div>
                              </div>
                              <div className="stack" style={{ gap: 2, fontSize: 'var(--text-xs)' }}>
                                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>ROW HASH</span>
                                <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                                  <code className="mono break-all" style={{ color: 'var(--accent-bright)' }}>{ev.row_hash}</code>
                                  <CopyButton value={ev.row_hash} title="Copy row hash" />
                                </div>
                              </div>
                              {detailEntries.length > 0 ? (
                                <div className="stack" style={{ gap: 4 }}>
                                  <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>RECORDED PAYLOAD</span>
                                  <div className="table-wrapper">
                                    <table className="table">
                                      <tbody>
                                        {detailEntries.map(([key, value]) => (
                                          <tr key={key}>
                                            <td className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{key}</td>
                                            <td className="mono break-all" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-strong)' }}>
                                              {typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                </div>
                              ) : (
                                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-faint)' }}>No additional payload recorded for this event.</span>
                              )}
                            </div>
                          </td>
                        </tr>
                      ) : null}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* LINEAR HASH CHAIN VISUALISATION */}
      {events.length > 0 ? (
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              LINEAR HASH CHAIN
            </span>
            <span style={{ fontSize: '10.5px', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
              {/*
                Named for what it is. Each block hashes the one before it in a
                single line -- there is no Merkle tree, so this is drawn as a
                chain, not a branching structure, to match the guarantee.
              */}
              row_hash = SHA-256(prev ‖ payload)
            </span>
          </div>

          <p style={{ margin: 0, fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)' }}>
            Each block carries its predecessor&rsquo;s hash, so altering any earlier row changes every hash after it. This is a
            linear chain, not a Merkle tree: integrity flows strictly forward from genesis to head.
          </p>

          <div className="hashchain" style={{ display: 'flex', alignItems: 'stretch', gap: 0, overflowX: 'auto', paddingBottom: 6 }}>
            {/* GENESIS anchor -- the fixed root every chain descends from. */}
            <div className="hashchain__node" style={{ flex: 'none', minWidth: 132, padding: '10px 12px', border: '1px dashed var(--border)', borderRadius: 'var(--radius)', background: 'var(--surface-3)' }}>
              <div style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>GENESIS</div>
              <code className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>{shortHash(genesisHash, 10)}</code>
            </div>
            {/* CHAIN_NODES_PLACEHOLDER */}
            {events.map((ev) => {
              // Same per-row truth as the table: unverified is neither pass nor
              // fail, and a break taints this block and every one after it.
              const rowState: ChainState =
                chainState === 'unverified'
                  ? 'unverified'
                  : firstInvalidSeq !== null && ev.seq >= firstInvalidSeq
                    ? 'invalid'
                    : chainState
              const tone =
                rowState === 'valid'
                  ? { line: 'var(--ok-line)', text: 'var(--ok-bright)', wash: 'var(--ok-wash)' }
                  : rowState === 'invalid'
                    ? { line: 'var(--danger-line)', text: 'var(--danger-bright)', wash: 'var(--danger-wash)' }
                    : { line: 'var(--border)', text: 'var(--text-muted)', wash: 'var(--surface-2)' }
              const isDeletion = ev.event === 'CASE_DELETED'
              return (
                <Fragment key={ev.seq}>
                  {/* The link between blocks: a forward arrow, never a fork. */}
                  <div style={{ flex: 'none', display: 'flex', alignItems: 'center', color: tone.line, padding: '0 2px' }} aria-hidden>
                    <Icon name="arrow-right" size={14} />
                  </div>
                  <button
                    type="button"
                    className="hashchain__node"
                    onClick={() => toggleRow(ev.seq)}
                    aria-label={`Block ${ev.seq}: ${ev.event}`}
                    style={{
                      flex: 'none',
                      minWidth: 132,
                      textAlign: 'left',
                      padding: '10px 12px',
                      border: `1px solid ${tone.line}`,
                      borderRadius: 'var(--radius)',
                      background: expandedSeqs.has(ev.seq) ? tone.wash : 'var(--surface-2)',
                      cursor: 'pointer',
                    }}
                  >
                    <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 4 }}>
                      <span style={{ fontSize: '9px', textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                        #{ev.seq}
                      </span>
                      <Icon
                        name={rowState === 'valid' ? 'check' : rowState === 'invalid' ? 'error' : 'lock'}
                        size={11}
                        style={{ color: tone.text }}
                      />
                    </div>
                    <div style={{ fontSize: '10px', color: isDeletion ? 'var(--danger-bright)' : 'var(--text-strong)', fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 108 }}>
                      {ev.event}
                    </div>
                    <code className="mono" style={{ fontSize: '10px', color: 'var(--text-muted)', wordBreak: 'break-all' }}>
                      {shortHash(ev.row_hash, 10)}
                    </code>
                  </button>
                </Fragment>
              )
            })}
          </div>

          {trail?.truncated ? (
            <p style={{ margin: 0, fontSize: '10.5px', color: 'var(--text-faint)' }}>
              Showing {events.length} of {totalRows} blocks — the head hash above anchors the full chain.
            </p>
          ) : null}
        </div>
      ) : null}

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
          Generate Forensic Report →
        </button>
      </div>
    </div>
  )
}
