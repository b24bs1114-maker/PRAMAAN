/**
 * The edit-case dialog.
 *
 * "Edit Case" used to route to Evidence Intake, where the case's title and
 * description are shown *read-only* -- so the one control named "Edit Case"
 * could not edit the case, and the button beside it ("+ Ingest Exhibit")
 * already covered the add-evidence path it actually led to. This dialog is the
 * real editor, wired to `PATCH /api/cases/{id}`.
 *
 * It sends only the fields the operator changed. The backend writes only the
 * fields it is sent and records a `CASE_UPDATED` audit entry naming exactly
 * those, so an edit is a custody event over what changed rather than a rewrite
 * of the whole record. Title and description are required on a case, so the
 * dialog refuses to submit them blank -- clearing a field the backend requires
 * is not an edit it should let through. There is no optimistic update: the save
 * resolves to the case as the backend now holds it, and that is what the caller
 * renders.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CaseRecord } from '../api/types'
import type { CaseUpdateFields } from '../api/client'
import { ErrorBanner } from './Banner'
import { Modal } from './Overlays'
import { Button } from './Primitives'

/** The priorities the backend triages against. `medium` is the unset default. */
const PRIORITIES = ['high', 'medium', 'low'] as const

/**
 * The statuses the queue and dossier already colour.
 *
 * These mirror the tones `statusTone` recognises so a chosen status is one the
 * rest of the UI can render meaningfully, rather than free text that lands as
 * the fallback colour. A record whose stored status is none of these is still
 * offered as its own option below, so editing another field never silently
 * rewrites an unfamiliar status.
 */
const STATUSES = [
  'open',
  'in_review',
  'pending',
  'analysis_complete',
  'verified',
  'closed',
  'archived',
] as const

function humaniseStatus(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function CaseEditDialog({
  open,
  target,
  onClose,
  onSaved,
}: {
  open: boolean
  /** The case being edited. `null` closes the dialog with nothing to show. */
  target: CaseRecord | null
  onClose: () => void
  /** Handed the updated record the backend returned, so the screen re-renders stored truth. */
  onSaved: (updated: CaseRecord) => void
}) {
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [examiner, setExaminer] = useState('')
  const [status, setStatus] = useState('open')
  const [priority, setPriority] = useState('medium')
  const [complaintReference, setComplaintReference] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)

  // Reseed the form from the record every time the dialog is opened for a case,
  // so it always starts from stored truth rather than a stale prior edit.
  useEffect(() => {
    if (!open || !target) return
    setTitle(target.title ?? '')
    setDescription(target.description ?? '')
    setExaminer(target.examiner ?? '')
    setStatus(target.status || 'open')
    setPriority(target.priority || 'medium')
    setComplaintReference(target.complaint_reference ?? '')
    setError(null)
    setSaving(false)
  }, [open, target])

  // The status the record already holds may not be one of the known set; offer
  // it too so saving another field cannot force a rewrite to a familiar value.
  const statusOptions = useMemo(() => {
    const known = [...STATUSES] as string[]
    if (status && !known.includes(status)) known.unshift(status)
    return known
  }, [status])

  if (!target) return null

  const titleBlank = !title.trim()
  const descriptionBlank = !description.trim()

  /**
   * Exactly the fields that changed, and only those.
   *
   * A field equal to what the record already holds is omitted, so the audit
   * entry names a genuine change and an accidental no-op save writes nothing.
   * Comparisons are against the record's own value with the same empty-to-null
   * convention the backend uses.
   */
  const changes = useMemo<CaseUpdateFields>(() => {
    const out: CaseUpdateFields = {}
    const trimmedTitle = title.trim()
    const trimmedDescription = description.trim()
    const trimmedExaminer = examiner.trim()
    const trimmedComplaint = complaintReference.trim()

    if (trimmedTitle && trimmedTitle !== (target.title ?? '')) out.title = trimmedTitle
    if (trimmedDescription && trimmedDescription !== (target.description ?? '')) {
      out.description = trimmedDescription
    }
    if (trimmedExaminer !== (target.examiner ?? '')) out.examiner = trimmedExaminer
    if (status && status !== target.status) out.status = status
    if (priority && priority !== (target.priority ?? 'medium')) out.priority = priority
    if (trimmedComplaint !== (target.complaint_reference ?? '')) {
      out.complaint_reference = trimmedComplaint
    }
    return out
  }, [title, description, examiner, status, priority, complaintReference, target])

  const nothingChanged = Object.keys(changes).length === 0
  const canSave = !saving && !titleBlank && !descriptionBlank && !nothingChanged

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateCase(target.case_id, changes)
      onSaved(updated)
      onClose()
    } catch (err) {
      setError(err)
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={saving ? () => {} : onClose}
      eyebrow="EDIT CASE"
      title={`Edit case #${target.case_number}`}
      subtitle="Changes are recorded as a CASE_UPDATED entry in the audit chain."
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" busy={saving} disabled={!canSave} onClick={save}>
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>
        </>
      }
    >
      <div className="stack" style={{ gap: 'var(--space-4)' }}>
        <div className="stack" style={{ gap: 6 }}>
          <label htmlFor="case-edit-title" className="label" style={{ color: 'var(--text-strong)' }}>
            Title / subject
          </label>
          <input
            id="case-edit-title"
            className="input"
            data-autofocus
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={saving}
            aria-invalid={titleBlank || undefined}
            aria-describedby="case-edit-title-hint"
          />
          {titleBlank ? (
            <span id="case-edit-title-hint" style={{ fontSize: 'var(--text-2xs)', color: 'var(--danger)' }}>
              A case must keep a title. This cannot be saved blank.
            </span>
          ) : null}
        </div>

        <div className="stack" style={{ gap: 6 }}>
          <label
            htmlFor="case-edit-description"
            className="label"
            style={{ color: 'var(--text-strong)' }}
          >
            Incident / examination description
          </label>
          <textarea
            id="case-edit-description"
            className="input"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={saving}
            style={{ resize: 'vertical', fontFamily: 'inherit' }}
            aria-invalid={descriptionBlank || undefined}
            aria-describedby="case-edit-description-hint"
          />
          {descriptionBlank ? (
            <span
              id="case-edit-description-hint"
              style={{ fontSize: 'var(--text-2xs)', color: 'var(--danger)' }}
            >
              A case must keep a description. This cannot be saved blank.
            </span>
          ) : null}
        </div>

        <div className="row" style={{ gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          <div className="stack" style={{ gap: 6, flex: '1 1 140px' }}>
            <label htmlFor="case-edit-priority" className="label" style={{ color: 'var(--text-strong)' }}>
              Priority
            </label>
            <select
              id="case-edit-priority"
              className="input"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              disabled={saving}
            >
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>
                  {p.toUpperCase()}
                </option>
              ))}
            </select>
          </div>

          <div className="stack" style={{ gap: 6, flex: '1 1 140px' }}>
            <label htmlFor="case-edit-status" className="label" style={{ color: 'var(--text-strong)' }}>
              Status
            </label>
            <select
              id="case-edit-status"
              className="input"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              disabled={saving}
            >
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {humaniseStatus(s)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row" style={{ gap: 'var(--space-3)', flexWrap: 'wrap' }}>
          <div className="stack" style={{ gap: 6, flex: '1 1 180px' }}>
            <label htmlFor="case-edit-examiner" className="label" style={{ color: 'var(--text-strong)' }}>
              Examiner of record
            </label>
            <input
              id="case-edit-examiner"
              className="input"
              type="text"
              value={examiner}
              onChange={(e) => setExaminer(e.target.value)}
              disabled={saving}
              placeholder="Not specified"
            />
          </div>

          <div className="stack" style={{ gap: 6, flex: '1 1 180px' }}>
            <label
              htmlFor="case-edit-complaint"
              className="label"
              style={{ color: 'var(--text-strong)' }}
            >
              Complaint reference
            </label>
            <input
              id="case-edit-complaint"
              className="input"
              type="text"
              value={complaintReference}
              onChange={(e) => setComplaintReference(e.target.value)}
              disabled={saving}
              placeholder="None recorded"
            />
          </div>
        </div>

        {nothingChanged && !titleBlank && !descriptionBlank ? (
          <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)' }}>
            No changes yet. Save stays disabled until a field differs from what is on record.
          </span>
        ) : null}

        {error ? <ErrorBanner context="Case update" error={error} /> : null}
      </div>
    </Modal>
  )
}
