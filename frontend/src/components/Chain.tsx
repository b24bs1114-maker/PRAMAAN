/**
 * Audit chain presentation.
 *
 * The rule that governs this file: **nothing here turns green on arrival.** A
 * hash chain that has been read is not a hash chain that has been verified, so
 * the rail and the state card stay neutral until `POST /audit/verify` returns
 * `valid: true`, and go red from `first_invalid_seq` onward when it does not.
 *
 * Each row is drawn as the link it actually is -- `previous_hash` on the left,
 * `row_hash` on the right -- because the relationship between consecutive rows
 * *is* the integrity claim, and an investigator should be able to point at it.
 */

import { Icon } from './Icon'
import { CopyButton, HashChip } from './Hash'
import { Button, Field, Fields, StatusPill } from './Primitives'
import { cx } from '../lib/cx'
import { formatTimestamp, formatTimestampShort, shortHash } from '../lib/format'
import { humanise } from '../lib/tone'
import type { AuditEvent, AuditVerification } from '../api/types'

/** Verification is a tri-state, and the untested state is not a pass. */
export type ChainStatus = 'unverified' | 'verifying' | 'verified' | 'broken'

export function chainStatusOf(
  verification: AuditVerification | null,
  loading: boolean,
): ChainStatus {
  if (loading) return 'verifying'
  if (!verification) return 'unverified'
  return verification.valid ? 'verified' : 'broken'
}

const STATE_CLASS: Record<ChainStatus, string> = {
  unverified: 'chainstate--pending',
  verifying: 'chainstate--pending',
  verified: 'chainstate--verified',
  broken: 'chainstate--failed',
}

const STATE_TITLE: Record<ChainStatus, string> = {
  unverified: 'NOT YET VERIFIED',
  verifying: 'VERIFYING…',
  verified: 'CHAIN INTACT',
  broken: 'CHAIN BROKEN',
}

/**
 * The verification hero.
 *
 * Reading the trail proves nothing, so the untested state is stated as untested
 * and the button that would change that sits right next to the sentence saying
 * so. `detail` comes from the backend's own `interpretation` once a verification
 * exists; before that it is a description of what has *not* happened.
 */
export function ChainState({
  status,
  verification,
  rowCount,
  onVerify,
}: {
  status: ChainStatus
  verification: AuditVerification | null
  /** Rows currently loaded, used only in the pre-verification sentence. */
  rowCount?: number | null
  onVerify?: () => void
}) {
  const detail = (() => {
    if (status === 'verifying') {
      return 'Recomputing every row hash from the genesis hash forward and comparing each row against its recorded predecessor.'
    }
    if (status === 'unverified') {
      return typeof rowCount === 'number'
        ? `${rowCount} audit ${rowCount === 1 ? 'row has' : 'rows have'} been read from the backend. Reading a hash chain does not verify it — run verification to have the backend recompute the chain.`
        : 'The audit trail has been read but not verified. Reading a hash chain does not verify it — run verification to have the backend recompute the chain.'
    }
    if (!verification) return 'No verification result is available.'
    return verification.interpretation
  })()

  return (
    <div className={cx('chainstate', STATE_CLASS[status])}>
      <div className="chainstate__mark">
        {status === 'verifying' ? (
          <span className="spinner spinner--lg" />
        ) : (
          <Icon name={status === 'broken' ? 'alert' : status === 'verified' ? 'shield' : 'lock'} size={22} />
        )}
      </div>

      <div className="chainstate__body">
        <div className="chainstate__title">{STATE_TITLE[status]}</div>
        <div className="chainstate__detail">{detail}</div>
        {verification ? (
          <div className="chainstate__meta">
            {verification.algorithm} · scope {verification.scope} · {verification.case_rows} of{' '}
            {verification.total_rows} rows in scope
            {verification.first_invalid_seq !== null
              ? ` · first invalid sequence ${verification.first_invalid_seq}`
              : ''}
          </div>
        ) : null}
      </div>

      {onVerify ? (
        <div className="chainstate__actions">
          <Button
            variant={status === 'verified' ? 'ghost' : 'primary'}
            icon="shield"
            onClick={onVerify}
            busy={status === 'verifying'}
          >
            {status === 'unverified' ? 'Verify chain' : 'Re-verify'}
          </Button>
        </div>
      ) : null}
    </div>
  )
}

/**
 * One audit row, drawn as a link in the chain.
 *
 * `previous_hash → row_hash` is printed because that equality *is* the integrity
 * claim: this row's `previous_hash` must match the row above it. The marker
 * carries the sequence number, which is what an investigator quotes when a
 * verification names `first_invalid_seq`.
 */
function ChainLink({
  event,
  invalid,
  onOpen,
}: {
  event: AuditEvent
  invalid: boolean
  onOpen?: (event: AuditEvent) => void
}) {
  const actor = event.actor?.trim()
  const detailKeys = Object.keys(event.details ?? {})

  const body = (
    <>
      <div className="chain__rail" />
      <div className="chain__marker" title={`Sequence ${event.seq}`}>
        {event.seq}
      </div>
      <div className="chain__body">
        <div className="chain__head">
          <span className="chain__event">{humanise(event.event)}</span>
          {invalid ? <StatusPill tone="danger" icon="alert">Chain break at this row</StatusPill> : null}
          <span className="chain__time" title={formatTimestamp(event.timestamp)}>
            {formatTimestampShort(event.timestamp)}
          </span>
        </div>

        <div className="chain__meta">
          <span>
            <Icon name="fingerprint" size={12} /> {actor || 'Actor not recorded'}
          </span>
          <span className="mono">{event.audit_id}</span>
          {event.case_id ? <span className="mono">case {event.case_id}</span> : null}
          {detailKeys.length > 0 ? (
            <span>
              {detailKeys.length} recorded {detailKeys.length === 1 ? 'field' : 'fields'}
            </span>
          ) : null}
        </div>

        <div className="chain__hashes">
          <span title={`previous_hash ${event.previous_hash}`}>prev {shortHash(event.previous_hash, 14)}</span>
          <span className="chain__hash-arrow" aria-hidden="true">
            →
          </span>
          <span title={`row_hash ${event.row_hash}`}>row {shortHash(event.row_hash, 14)}</span>
          <CopyButton value={event.row_hash} label="Copy row hash" size={11} />
        </div>
      </div>
    </>
  )

  if (!onOpen) {
    return <div className={cx('chain__link', invalid && 'chain__link--invalid')}>{body}</div>
  }

  return (
    <button
      type="button"
      className={cx('chain__link', 'chain__link--button', 'focus-inset', invalid && 'chain__link--invalid')}
      onClick={() => onOpen(event)}
      aria-label={`Audit row ${event.seq}, ${humanise(event.event)}`}
    >
      {body}
    </button>
  )
}

/**
 * The chain, in the order the backend returned it.
 *
 * `status` -- not the presence of data -- decides whether the rail is green.
 * When a verification failed, every row at or after `firstInvalidSeq` is marked,
 * because a break at row *n* invalidates everything the chain asserts after it.
 */
export function ChainRail({
  events,
  status,
  firstInvalidSeq,
  onOpen,
}: {
  events: AuditEvent[]
  status: ChainStatus
  firstInvalidSeq?: number | null
  onOpen?: (event: AuditEvent) => void
}) {
  return (
    <div
      className={cx(
        'chain',
        status === 'verified' && 'chain--verified',
        status === 'broken' && 'chain--broken',
      )}
    >
      {events.map((event) => (
        <ChainLink
          key={event.audit_id || `${event.seq}`}
          event={event}
          invalid={
            status === 'broken' &&
            typeof firstInvalidSeq === 'number' &&
            event.seq >= firstInvalidSeq
          }
          onOpen={onOpen}
        />
      ))}
    </div>
  )
}

/**
 * Chain anchors: the genesis hash the chain starts from and the head hash it
 * currently ends at. Printed as data, with no claim about whether they match a
 * recomputation -- that is `ChainState`'s job.
 */
export function ChainAnchors({
  genesisHash,
  headHash,
  algorithm,
  totalRows,
  loadedRows,
  truncated,
}: {
  genesisHash: string
  headHash: string
  algorithm: string
  totalRows: number
  loadedRows: number
  truncated?: boolean
}) {
  return (
    <Fields variant="wide">
      <Field label="Hash algorithm" value={algorithm} mono />
      <Field
        label="Rows in chain"
        value={`${totalRows}`}
        note={
          truncated
            ? `${loadedRows} most recent rows loaded for display; the chain the backend verifies is the full ${totalRows}.`
            : 'All rows in scope are loaded.'
        }
      />
      <Field label="Genesis hash" value={<HashChip value={genesisHash} algo={null} length={18} />} />
      <Field label="Head hash" value={<HashChip value={headHash} algo={null} length={18} />} />
    </Fields>
  )
}

/**
 * Custody strip: the recorded SHA-256 and, optionally, the digest this browser
 * computed from the file it just read.
 *
 * The "match" claim is only made when `computed` is a real string produced by
 * `sha256Hex` in this session. Absent a local digest the strip says only that
 * the backend recorded a hash -- it does not imply the bytes were re-checked.
 */
export function CustodyStrip({
  recorded,
  computed,
  filename,
}: {
  recorded: string | null | undefined
  /** Digest computed in the browser, or null if no local hash was performed. */
  computed?: string | null
  filename?: string | null
}) {
  // A comparison exists only when both sides are real strings. Anything less is
  // "recorded digest", never "match" and never "mismatch".
  const pair =
    recorded && computed
      ? { recorded, computed, matches: recorded.toLowerCase() === computed.toLowerCase() }
      : null
  const subject = filename ? <span className="mono">{filename}</span> : 'the selected file'

  return (
    <div className="custody">
      <span className="custody__seal">
        <Icon name={pair ? (pair.matches ? 'check' : 'alert') : 'lock'} size={12} />
        {pair ? (pair.matches ? 'Digest match' : 'Digest mismatch') : 'Recorded digest'}
      </span>

      <span className="custody__text">
        {pair === null ? (
          <>
            SHA-256 recorded at ingest. No digest has been recomputed in this browser, so this is the
            backend's recorded value rather than a re-verification of the bytes.
          </>
        ) : pair.matches ? (
          <>
            This browser recomputed SHA-256 over {subject} and the digest equals the value recorded at
            ingest.
          </>
        ) : (
          <>
            This browser recomputed SHA-256 over {subject} and the digest <strong>does not</strong> equal
            the value recorded at ingest. Computed{' '}
            <span className="mono">{shortHash(pair.computed, 16)}</span>, recorded{' '}
            <span className="mono">{shortHash(pair.recorded, 16)}</span>.
          </>
        )}
      </span>

      {recorded ? <HashChip value={recorded} algo={null} length={20} /> : null}
    </div>
  )
}

