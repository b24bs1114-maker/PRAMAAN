/**
 * The delete-case controls.
 *
 * Kept out of the screens for two reasons. The wording of a permanent action
 * should be written once -- the queue and the dossier must not describe the same
 * consequence differently -- and neither control holds state of its own, which
 * makes both of them checkable against a real case record.
 *
 * Nothing here decides anything. What the dialog shows about the case comes from
 * the `CaseRecord` the backend returned, and what it shows about the outcome
 * comes from the `CaseDeleteResult` the backend returned. There is no local
 * arithmetic and no optimistic success.
 */

import { Banner, ErrorBanner } from './Banner'
import { Modal } from './Overlays'
import { Button } from './Primitives'
import { Icon } from './Icon'
import { canConfirm, isDeleting, type CaseDeletionState } from '../lib/casedelete'
import { orPlaceholder } from '../lib/format'
import type { CaseRecord } from '../api/types'

/**
 * The control that opens the dialog.
 *
 * The accessible name carries the case number, so a screen reader on the queue
 * announces which case a row's delete button would destroy rather than eight
 * identical "Delete" buttons.
 */
export function DeleteCaseButton({
  target,
  onClick,
  size = 'sm',
  label = 'Delete',
}: {
  target: CaseRecord
  onClick: (target: CaseRecord) => void
  size?: 'sm' | 'md'
  label?: string
}) {
  return (
    <Button
      variant="danger"
      size={size}
      aria-label={`Delete case ${target.case_number}`}
      onClick={(event) => {
        // The queue's rows are themselves clickable; opening the case underneath
        // the confirmation dialog would be a confusing way to start a delete.
        event.stopPropagation()
        onClick(target)
      }}
    >
      {label}
    </Button>
  )
}

/** One label/value pair from the case row. */
function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
      <span style={{ color: 'var(--text-faint)' }}>{label}</span>
      <span
        style={{
          color: 'var(--text-strong)',
          fontWeight: 600,
          textAlign: 'right',
          fontFamily: mono ? 'var(--mono)' : undefined,
        }}
      >
        {value}
      </span>
    </div>
  )
}

/** The id of the confirmation field. Only one delete dialog is ever open. */
const CONFIRM_FIELD_ID = 'case-delete-confirm'

export function CaseDeleteDialog({
  state,
  onTyped,
  onCancel,
  onConfirm,
}: {
  state: CaseDeletionState
  onTyped: (value: string) => void
  onCancel: () => void
  onConfirm: () => void
}) {
  const target = state.target
  if (!target) return null

  const busy = isDeleting(state)
  const ready = canConfirm(state)
  const mismatch = !busy && !ready && state.typed.trim().length > 0

  return (
    <Modal
      // Escape and the backdrop route here too. While the request is in flight
      // `dismissDeletion` refuses, so neither can close the dialog on an outcome
      // the operator has not seen yet.
      open={state.phase === 'confirming' || state.phase === 'deleting' || state.phase === 'failed'}
      onClose={onCancel}
      eyebrow="PERMANENT ACTION"
      title={`Delete case #${target.case_number}?`}
      subtitle={
        target.title?.trim() ? (
          target.title
        ) : (
          <span style={{ color: 'var(--text-faint)' }}>No title recorded</span>
        )
      }
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} disabled={!ready} onClick={onConfirm}>
            {busy ? 'Deleting…' : 'Delete permanently'}
          </Button>
        </>
      }
    >
      <div className="stack" style={{ gap: 'var(--space-4)' }}>
        <Banner
          tone="warn"
          title="This is permanent and cannot be undone."
          detail={
            'The case, its evidence records and the stored files on disk are removed ' +
            'outright. There is no archive to restore from and no undo.'
          }
        />

        <div
          className="stack"
          style={{
            gap: 6,
            fontSize: 'var(--text-xs)',
            padding: 'var(--space-3)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--surface-2)',
          }}
        >
          <Field label="Case number" value={`#${target.case_number}`} mono />
          <Field label="Title / subject" value={target.title?.trim() || 'No title recorded'} />
          <Field label="Examiner" value={orPlaceholder(target.examiner)} />
          <Field label="Status" value={target.status.replace(/_/g, ' ').toUpperCase()} />
          <Field
            label="Evidence items"
            value={`${target.evidence_count} ${target.evidence_count === 1 ? 'item' : 'items'}`}
            mono
          />
        </div>

        <div className="stack" style={{ gap: 4, fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          <span style={{ color: 'var(--text-strong)', fontWeight: 700 }}>What is kept</span>
          <span>
            The tamper-evident audit chain. A CASE_DELETED entry is appended, and this
            case&rsquo;s recorded custody history stays verifiable after the case itself is
            gone.
          </span>
        </div>

        <div className="stack" style={{ gap: 6 }}>
          <label
            htmlFor={CONFIRM_FIELD_ID}
            className="label"
            style={{ color: 'var(--text-strong)' }}
          >
            Type the case number to confirm
          </label>
          <input
            id={CONFIRM_FIELD_ID}
            className="input"
            data-autofocus
            type="text"
            value={state.typed}
            onChange={(event) => onTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && ready) onConfirm()
            }}
            placeholder={target.case_number}
            disabled={busy}
            autoComplete="off"
            spellCheck={false}
            aria-describedby={`${CONFIRM_FIELD_ID}-hint`}
            aria-invalid={mismatch || undefined}
          />
          <span
            id={`${CONFIRM_FIELD_ID}-hint`}
            style={{
              fontSize: 'var(--text-2xs)',
              color: mismatch ? 'var(--danger)' : 'var(--text-faint)',
            }}
          >
            {mismatch
              ? `That is not this case's number. Expected ${target.case_number}.`
              : `Enter ${target.case_number} exactly. The delete button stays disabled until it matches.`}
          </span>
        </div>

        {state.phase === 'failed' ? (
          <div className="stack" style={{ gap: 6 }}>
            <ErrorBanner context="Case deletion" error={state.error} />
            <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)' }}>
              <Icon name="info" size={11} /> Nothing was deleted unless the message above says
              otherwise. The case is still in the queue.
            </span>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}
