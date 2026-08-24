/**
 * Screen: Audit trail.
 *
 * Every action on a case is written to an append-only, hash-linked log. This
 * screen answers one question first and loudly — has the chain been tampered
 * with? — and leaves the cryptography for those who want it.
 *
 *   PRIMARY:  the chain-status hero (VALID / BROKEN / NOT YET VERIFIED) and the
 *             VERIFY CHAIN action, plus a plain event list (what happened, when,
 *             by whom).
 *   RAW:      previous/row/head/genesis hashes and the algorithm live behind
 *             "View cryptographic details" — never removed, just not the first
 *             thing an investigator has to read.
 *
 * A row's integrity is only asserted once the chain has actually been verified:
 * before that, per-row status is shown as "—", not as a guess. Recompute is the
 * backend's job (POST /audit/verify); the frontend never fakes a green tick.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { AuditTrail } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon, type IconName } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { Section } from '../components/Section'
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
  const { caseRecord, auditVerification, verifyAudit } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null


  const [trail, setTrail] = useState<AuditTrail | null>(null)
  const [loading, setLoading] = useState(Boolean(currentCaseId))
  const [error, setError] = useState<unknown>(null)

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
        <div className="screen__head">
          <h1 className="screen__title">Audit trail</h1>
          <p className="screen__lead">
            Every action on a case is written to an append-only, hash-linked log that can be
            recomputed to prove nothing was altered.
          </p>
        </div>
        <Empty>No case is selected. Open a case to view its audit trail.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View cases
          </button>
        </div>
      </div>
    )
  }

  const events = trail?.events ?? []
  const verification = isReady(auditVerification) ? auditVerification.data : null
  const verifying = auditVerification.phase === 'loading'
  const verifyError = auditVerification.phase === 'error' ? auditVerification.error : null

  const headHash = verification?.head_hash ?? trail?.head_hash ?? '—'
  const genesisHash = verification?.genesis_hash ?? trail?.genesis_hash ?? '—'
  const algorithm = verification?.algorithm ?? trail?.algorithm ?? '—'

  // The hero: what the operator reads first.
  let heroTone = 'var(--accent)'
  let heroIcon: IconName = 'lock'
  let heroText = 'NOT YET VERIFIED'
  let heroDetail =
    'The log is recorded and hash-linked. Recompute the chain to confirm nothing has been inserted, altered, deleted or reordered.'
  if (verification) {
    if (verification.valid) {
      heroTone = 'var(--ok)'
      heroIcon = 'check'
      heroText = 'CHAIN VALID'
      heroDetail = `All ${verification.case_rows} case events recompute to their recorded hashes — no insertion, edit, deletion or reordering detected.`
    } else {
      heroTone = 'var(--error)'
      heroIcon = 'alert'
      heroText = 'CHAIN BROKEN'
      heroDetail = `The chain fails to recompute at sequence ${
        verification.first_invalid_seq ?? '—'
      }. Every event from that point on is suspect.`
    }
  }

  const rowStatus = (seq: number): { label: string; tone: PillTone } | null => {
    if (!verification) return null
    const firstBad = verification.first_invalid_seq
    if (firstBad == null) return { label: 'LINKED', tone: 'ok' }
    if (seq < firstBad) return { label: 'LINKED', tone: 'ok' }
    if (seq === firstBad) return { label: 'BREAK', tone: 'error' }
    return { label: 'AFTER BREAK', tone: 'warn' }
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-6)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Audit trail</h1>
        <p className="screen__lead">
          A hash-linked record of everything done to this case. Verifying the chain recomputes every
          link to prove nothing has been inserted, altered, deleted or reordered.
        </p>
      </div>

      {/* CHAIN STATUS — the answer, first and prominent. */}
      <div
        className="card"
        style={{
          padding: 'var(--space-4)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: 'var(--space-4)',
          flexWrap: 'wrap',
        }}
      >
        <div className="row" style={{ gap: 12, alignItems: 'center', minWidth: 0 }}>
          <Icon name={heroIcon} size={28} style={{ color: heroTone, flexShrink: 0 }} />
          <div className="stack" style={{ gap: 2, minWidth: 0 }}>
            <span className="label">Chain status</span>
            <span style={{ fontSize: 'var(--text-xl)', fontWeight: 700, color: heroTone }}>
              {heroText}
            </span>
            <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
              {heroDetail}
            </span>
          </div>
        </div>
        <button
          type="button"
          className="btn btn--primary"
          disabled={verifying}
          onClick={verifyAudit}
        >
          {verifying ? <Spinner label="Verifying chain…" /> : <Icon name="lock" size={14} />}
          Verify chain
        </button>
      </div>

      {verifyError ? <ErrorBanner context="Chain verification" error={verifyError} /> : null}

      {/* EVENTS — what happened, in plain language. */}
      <Section title="Events" aside={events.length ? `${events.length} recorded` : null}>
        {loading ? (
          <Spinner label="Loading audit trail…" />
        ) : error ? (
          <ErrorBanner context="Audit trail" error={error} />
        ) : events.length === 0 ? (
          <Empty>No audit events recorded for this case yet.</Empty>
        ) : (
          <>
            <div className="table-wrapper card">
              <table className="table">
                <thead>
                  <tr>
                    <th className="table__num">#</th>
                    <th>Event</th>
                    <th>Actor</th>
                    <th>When</th>
                    <th>Integrity</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev) => {
                    const status = rowStatus(ev.seq)
                    return (
                      <tr key={ev.seq}>
                        <td className="table__num" style={{ fontWeight: 700 }}>
                          {ev.seq}
                        </td>
                        <td style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{ev.event}</td>
                        <td style={{ fontSize: 'var(--text-xs)' }}>{ev.actor}</td>
                        <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap' }}>
                          {formatTimestamp(ev.timestamp)}
                        </td>
                        <td>
                          {status ? (
                            <Pill variant={status.tone}>{status.label}</Pill>
                          ) : (
                            <span className="faint">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>

            {/* RAW CRYPTOGRAPHY — hidden by default, complete when opened. */}
            <details className="disclosure">
              <summary>
                <Icon name="arrow-right" size={14} className="disclosure__chevron" />
                View cryptographic details
              </summary>
              <div className="disclosure__panel stack" style={{ gap: 'var(--space-4)' }}>
                <dl className="dl">
                  <dt>Algorithm</dt>
                  <dd className="mono">{algorithm}</dd>
                  <dt>Head hash</dt>
                  <dd>
                    <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                      <code className="mono break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                        {headHash}
                      </code>
                      {headHash !== '—' ? (
                        <CopyButton value={headHash} label="" title="Copy head hash" />
                      ) : null}
                    </span>
                  </dd>
                  <dt>Genesis hash</dt>
                  <dd>
                    <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                      <code className="mono break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                        {genesisHash}
                      </code>
                      {genesisHash !== '—' ? (
                        <CopyButton value={genesisHash} label="" title="Copy genesis hash" />
                      ) : null}
                    </span>
                  </dd>
                </dl>

                <div className="table-wrapper">
                  <table className="table">
                    <caption className="visually-hidden">
                      Per-event hash chain: each row hash is computed over the event and the previous
                      row hash
                    </caption>
                    <thead>
                      <tr>
                        <th className="table__num">#</th>
                        <th>Previous hash</th>
                        <th>Row hash</th>
                      </tr>
                    </thead>
                    <tbody>
                      {events.map((ev) => (
                        <tr key={ev.seq}>
                          <td className="table__num">{ev.seq}</td>
                          <td>
                            <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                              <code className="mono" style={{ fontSize: 'var(--text-2xs)' }}>
                                {shortHash(ev.previous_hash)}
                              </code>
                              <CopyButton value={ev.previous_hash} label="" title="Copy previous hash" />
                            </span>
                          </td>
                          <td>
                            <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                              <code className="mono" style={{ fontSize: 'var(--text-2xs)' }}>
                                {shortHash(ev.row_hash)}
                              </code>
                              <CopyButton value={ev.row_hash} label="" title="Copy row hash" />
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </details>
          </>
        )}
      </Section>
    </div>
  )
}
