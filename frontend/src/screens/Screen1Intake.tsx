/**
 * Screen: Evidence Intake (Screen 1).
 *
 * Primary question answered:
 * "What evidence do I have, and is custody established?"
 *
 * Core digital forensics pipeline:
 * 1. UPLOADED: Raw media bytes selected & validated.
 * 2. HASHED: Client-side WebCrypto SHA-256 pre-flight digest generated.
 * 3. SEALED: Backend cryptographically binds the asset into the immutable evidence ledger.
 * 4. READY FOR ANALYSIS: Multi-signal forensics & perceptual lineage pipeline unlocked.
 *
 * Three things on this screen are decided by the backend and are therefore shown,
 * not asked for:
 *
 *   - the case number, issued server-side as the next `PRAMAAN-####` in sequence
 *   - the analyst, resolved from the bearer token of whoever is signed in
 *   - the stored evidence digest, recomputed by the backend from the bytes it received
 *
 * The form collects only what a human actually knows: which file, what the case is
 * called, why it is being examined, and optionally where it came from. Every step
 * of the pipeline strip above reflects a completed operation -- "Sealed" appears
 * when the backend has answered, never while the request is still in flight.
 *
 * The screen has two modes, and which one is active is decided by real state: with
 * a case already open it seals the file into that case (`case_id` is sent, and the
 * case's own title and description are shown read-only); with no case open it opens
 * a new one, which is when the backend requires a title and a description.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, MAX_UPLOAD_BYTES, type AuthUser } from '../api'
import { Banner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Spinner } from '../components/Feedback'
import { EvidencePreview } from '../components/EvidenceMedia'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { formatBytes, formatTimestamp, shortHash } from '../lib/format'
import { canHashLocally, sha256Hex } from '../lib/hash'
import { isImageMedia } from '../lib/media'
import type { Investigation } from '../state/useInvestigation'

type LocalHash =
  | { phase: 'idle' }
  | { phase: 'hashing' }
  | { phase: 'done'; hex: string }
  | { phase: 'unsupported' }
  | { phase: 'failed'; reason: string }

/**
 * The analyst of record, displayed from the session.
 *
 * Its own component for one reason: this is the field the whole authentication
 * layer exists to produce, and it is the one thing on the form that must never be
 * an input. Exported so it can be rendered and asserted directly -- the enclosing
 * form is gated behind a file the operator has selected, which no static render
 * can do, and "the analyst shown is the account the backend will stamp" is too
 * important a claim to leave unverified.
 *
 * `note` is the surrounding explanation, which differs between opening a new case
 * and adding an exhibit to one that already has an examiner of record.
 */
export function AnalystOfRecord({ operator, note }: { operator: AuthUser; note: string }) {
  return (
    <section className="intake-section">
      <span className="intake-section__label">Analyst</span>
      <div className="intake-analyst">
        <span className="intake-analyst__mark" aria-hidden="true">
          <Icon name="shield" size={16} />
        </span>
        <span className="intake-analyst__body">
          <span className="intake-analyst__name">{operator.display_name}</span>
          <span className="intake-analyst__meta">
            {operator.role} · signed in as {operator.username}
          </span>
        </span>
        <span className="intake-analyst__lock">
          <Icon name="lock" size={11} />
          From session
        </span>
      </div>
      <span className="field__hint">{note}</span>
    </section>
  )
}

export function Screen1Intake({
  investigation,
  operator,
  onAnalyse,
}: {
  investigation: Investigation
  /**
   * The signed-in operator, from `useAuth`.
   *
   * Displayed as the analyst and never editable here. The backend stamps this same
   * account on the case and the evidence from the bearer token, so this is a mirror
   * of what will be recorded rather than an input to it -- which is why it is not
   * optional: the console is not reachable without a session.
   */
  operator: AuthUser
  onAnalyse: () => void
}) {
  const { upload, uploadProgress, uploadFile, clearUpload, reset, health, caseRecord } =
    investigation

  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [rejection, setRejection] = useState<string | null>(null)
  const [localHash, setLocalHash] = useState<LocalHash>({ phase: 'idle' })
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [acquisitionContext, setAcquisitionContext] = useState('')

  const inputRef = useRef<HTMLInputElement>(null)

  // Generate a local object URL for pre-upload preview
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null)
      return
    }
    const url = URL.createObjectURL(file)
    setPreviewUrl(url)
    return () => {
      URL.revokeObjectURL(url)
    }
  }, [file])

  // Validate candidate file format & size bounds
  const accept = useCallback((candidate: File): string | null => {
    if (candidate.size === 0) return 'File is empty (0 bytes). Cannot ingest null payload.'
    if (candidate.size > MAX_UPLOAD_BYTES) {
      return `File exceeds maximum ingestion payload (${formatBytes(candidate.size)} > limit ${formatBytes(MAX_UPLOAD_BYTES)}).`
    }
    return null
  }, [])

  const takeFile = useCallback(
    (candidate: File | null) => {
      setRejection(null)
      setLocalHash({ phase: 'idle' })
      if (!candidate) {
        setFile(null)
        return
      }
      const problem = accept(candidate)
      if (problem) {
        setFile(null)
        setRejection(problem)
        return
      }
      setFile(candidate)
    },
    [accept],
  )

  // Compute local SHA-256 digest immediately upon file selection via WebCrypto
  useEffect(() => {
    if (!file) return
    if (!canHashLocally()) {
      setLocalHash({ phase: 'unsupported' })
      return
    }
    let cancelled = false
    setLocalHash({ phase: 'hashing' })
    sha256Hex(file).then(
      (hex) => {
        if (!cancelled) setLocalHash({ phase: 'done', hex })
      },
      (error: unknown) => {
        if (!cancelled) {
          setLocalHash({
            phase: 'failed',
            reason: error instanceof Error ? error.message : 'Digest computation failed.',
          })
        }
      },
    )
    return () => {
      cancelled = true
    }
  }, [file])

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      setDragging(false)
      takeFile(event.dataTransfer.files?.[0] ?? null)
    },
    [takeFile],
  )

  const ingested = upload.phase === 'ready' ? upload.data : null
  const busy = upload.phase === 'loading'
  const uploadError = upload.phase === 'error' ? upload.error : null

  /**
   * The case this file will be sealed into, or null when the seal will open one.
   *
   * Read from the store, not from a mode switch in the UI: arriving here from
   * "Ingest Evidence" on an open case attaches the exhibit to that case, and the
   * only honest way to show which case that is, is to show the one the store holds.
   */
  const attachTo = ingested ? null : caseRecord
  const opensNewCase = attachTo === null

  /** Clear the local form only. The case, if any, stays loaded. */
  const clearSelection = useCallback(() => {
    setFile(null)
    setRejection(null)
    setLocalHash({ phase: 'idle' })
    setTitle('')
    setDescription('')
    setAcquisitionContext('')
  }, [])

  /** Seal a second exhibit into the same case: forget the exhibit, keep the case. */
  const sealAnother = () => {
    clearSelection()
    clearUpload()
  }

  /** Leave this case entirely and start from an empty intake. */
  const startNewCase = () => {
    clearSelection()
    reset()
  }

  /**
   * The backend's own precondition, checked here first.
   *
   * This is a copy of a server-side rule, not a substitute for it: the backend
   * refuses a new case without a title and a description with a 422 that names the
   * missing field, and that response is rendered verbatim if it ever arrives. What
   * this list buys is telling the operator *before* they upload bytes -- and naming
   * the reason the seal button is disabled, rather than just greying it out.
   */
  const missing: string[] = []
  if (!file) missing.push('An evidence file')
  if (opensNewCase && !title.trim()) missing.push('Case title')
  if (opensNewCase && !description.trim()) missing.push('Incident / examination description')
  const canSeal = missing.length === 0 && !busy && health !== 'down'

  /**
   * Pre-flight digest against stored digest.
   *
   * Both are SHA-256 of the same file: one computed in this browser before the
   * upload, one recomputed by the backend from the bytes it received. They are not
   * two hashes of two things -- if they differ, the bytes changed in transit, which
   * is a finding and is reported as one.
   */
  const preflightHex = localHash.phase === 'done' ? localHash.hex : null
  const storedHex = ingested?.evidence.sha256 ?? null
  const digestsAgree =
    preflightHex && storedHex ? preflightHex.toLowerCase() === storedHex.toLowerCase() : null

  /** The case shown in the context strip: the one just sealed, or the one being added to. */
  const contextCase = ingested?.case ?? attachTo

  /**
   * What this browser can actually observe while an upload is in flight.
   *
   * `xhr.upload.onprogress` reports the request *body* and stops there. Once the
   * last byte is delivered, validation, hashing, the storage write, the custody
   * append and perceptual indexing all happen behind a single pending response
   * with no progress channel back. So there are two observable states here --
   * sending, and waiting -- and no third one that can honestly be drawn.
   */
  const transferFraction = uploadProgress?.fraction ?? null
  const transferDelivered =
    uploadProgress != null &&
    uploadProgress.total > 0 &&
    uploadProgress.loaded >= uploadProgress.total

  type StepState = 'idle' | 'active' | 'done'

  // Step state is derived from actual backend and form state
  const step1State: StepState = contextCase || (title.trim() && description.trim()) ? 'done' : 'active'
  const step2State: StepState = file || ingested ? 'done' : step1State === 'done' ? 'active' : 'idle'
  const step3State: StepState = preflightHex || ingested ? 'done' : localHash.phase === 'hashing' ? 'active' : 'idle'
  const step4State: StepState = ingested ? 'done' : busy ? 'active' : 'idle'

  const step1Status = contextCase
    ? 'Identity Recorded'
    : title.trim() && description.trim()
      ? 'Context Defined'
      : 'Define Case'

  const step2Status = ingested
    ? 'Exhibit Attached'
    : file
      ? `${formatBytes(file.size)} Selected`
      : 'Select Media'

  const step3Status = ingested
    ? 'SHA-256 On Record'
    : preflightHex
      ? 'Pre-Flight SHA-256'
      : localHash.phase === 'hashing'
        ? 'Computing Digest…'
        : localHash.phase === 'unsupported'
          ? 'Web Crypto N/A'
          : localHash.phase === 'failed'
            ? 'Digest Failed'
            : 'Pending Validation'

  const step4Status = ingested
    ? 'Custody Sealed & Indexed'
    : busy
      ? 'Sealing Custody…'
      : uploadError
        ? 'Seal Refused'
        : 'Awaiting Ingestion'

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      {/* 1. PAGE TITLE & SUPPORTING LINE */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">Evidence Intake</h1>
          <p className="screen__lead">
            Controlled digital evidence acquisition, pre-flight hashing, and custody preservation.
          </p>
        </div>
      </div>

      {/* 2. FORENSIC WORKFLOW STEPPER */}
      <nav className="forensic-pipeline-stepper" aria-label="Evidence Intake Pipeline">
        {/* Step 1: CASE */}
        <div className={`pipeline-step${step1State === 'active' ? ' pipeline-step--active' : step1State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step1State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '1'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Case</span>
            <span className="pipeline-step__status">{step1Status}</span>
          </div>
        </div>

        {/* Step 2: EVIDENCE */}
        <div className={`pipeline-step${step2State === 'active' ? ' pipeline-step--active' : step2State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step2State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '2'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Evidence</span>
            <span className="pipeline-step__status">{step2Status}</span>
          </div>
        </div>

        {/* Step 3: VALIDATION */}
        <div className={`pipeline-step${step3State === 'active' ? ' pipeline-step--active' : step3State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step3State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '3'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Validation</span>
            <span className="pipeline-step__status">{step3Status}</span>
          </div>
        </div>

        {/* Step 4: INGESTION */}
        <div className={`pipeline-step${step4State === 'active' ? ' pipeline-step--active' : step4State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step4State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '4'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Ingestion</span>
            <span className="pipeline-step__status">{step4Status}</span>
          </div>
        </div>
      </nav>

      {/*
        3. CASE CONTEXT.

        The case *number and title* are not repeated here: the shell's workflow
        bar above this screen already prints `CASE #… · title`, and the same
        identifier twice within one screen-height reads as a rendering fault
        rather than as emphasis. What this strip adds is what that bar cannot
        say -- whether this exhibit joins an existing case or opens a new one,
        who the case's examiner of record is, and its priority.
      */}
      {contextCase ? (
        <div
          className="row"
          style={{
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '10px 16px',
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius)',
            flexWrap: 'wrap',
            gap: 8,
          }}
        >
          <div className="row" style={{ gap: 10, alignItems: 'center' }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              {ingested ? 'Sealed Into' : 'Adding To'}
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              {ingested
                ? 'the case shown above — this exhibit is now on its record.'
                : 'the case shown above — no new case will be opened.'}
            </span>
          </div>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            {contextCase.examiner ? (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Examiner of record: <strong style={{ color: 'var(--text)' }}>{contextCase.examiner}</strong>
              </span>
            ) : null}
            {contextCase.priority ? (
              <Pill variant={contextCase.priority === 'high' ? 'error' : 'warn'}>
                {contextCase.priority.toUpperCase()}
              </Pill>
            ) : null}
          </div>
        </div>
      ) : null}

      {health === 'down' ? (
        <Banner
          tone="error"
          title="Backend not reachable"
          detail="Evidence cannot be ingested until the backend responds. Selected files are held locally in browser memory only."
        />
      ) : null}

      {/* 4. STATE A: INGESTED & SEALED (Forensic Evidence Dossier) */}
      {ingested ? (
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          {/* The one integrity check this screen can actually make: the digest
              computed here before the upload against the digest the backend
              computed from the bytes it received. A mismatch is not a display
              problem, so it is not shown as a subtitle. */}
          {digestsAgree === false ? (
            <Banner
              tone="error"
              title="Stored digest does not match the pre-flight digest"
              detail={`This browser hashed the selected file as ${preflightHex}, but the backend hashed the bytes it received as ${storedHex}. The two should be identical. Treat this exhibit as unverified and re-ingest the original file.`}
            />
          ) : null}

          {ingested.duplicate ? (
            <Banner
              tone="info"
              title="Identical bytes already on record"
              detail="This exact cryptographic payload was ingested previously. It is linked to this case dossier; the immutable SHA-256 seal remains identical."
            />
          ) : null}

          {ingested.warnings.length > 0 ? (
            <Banner
              tone="warn"
              title="Ingestion Warnings"
              detail={ingested.warnings.join(' ')}
            />
          ) : null}

          {/* Forensic Evidence Card */}
          <div className="forensic-evidence-card stack" style={{ gap: 0 }}>
            {/* Custody Status Header */}
            <div className="forensic-custody-banner">
              <div className="forensic-custody-banner__status">
                <div className="forensic-custody-banner__indicator" />
                <Icon name="shield" size={16} style={{ color: 'var(--ok-bright)' }} />
                <span className="forensic-custody-banner__text">
                  Chain of Custody Established · Cryptographically Sealed
                </span>
              </div>
              <div className="forensic-custody-banner__seal-tag">
                <Icon name="lock" size={12} />
                <span>SEAL #{ingested.evidence.evidence_id.slice(0, 12)}</span>
              </div>
            </div>

            {/* Visual Media Inspection Preview (if supported) */}
            <div style={{ padding: 'var(--space-4)', background: 'var(--navy-surface)' }}>
              <div className="forensic-inspection-frame">
                <EvidencePreview
                  evidenceId={ingested.evidence.evidence_id}
                  filename={ingested.evidence.filename}
                  mediaType={ingested.evidence.media_type}
                  kind={
                    ingested.evidence.media_type.startsWith('video')
                      ? 'video'
                      : isImageMedia(ingested.evidence.media_type)
                        ? 'image'
                        : 'none'
                  }
                  /* The operator's own file is still in memory from the picker,
                     so showing them what they just sealed costs no round trip. */
                  localUrl={previewUrl}
                />
                <div className="forensic-inspection-frame__badge">
                  <Icon name="lock" size={12} style={{ color: 'var(--ok-bright)' }} />
                  <span>{ingested.evidence.media_type.toUpperCase()} EVIDENCE OBJECT</span>
                </div>
              </div>
            </div>

            {/* Forensic Spec Grid: FILE, TYPE, SIZE, SHA-256, TIMESTAMP, CUSTODY STATUS, EVIDENCE SEAL */}
            <div className="forensic-spec-grid">
              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">File</span>
                <span className="forensic-spec-item__value" title={ingested.evidence.filename}>
                  {ingested.evidence.filename}
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Type</span>
                <span className="forensic-spec-item__value">
                  {ingested.evidence.media_type.toUpperCase()}
                  {ingested.evidence.mime_type ? ` · ${ingested.evidence.mime_type}` : ''}
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Size</span>
                <span className="forensic-spec-item__value">
                  {formatBytes(ingested.evidence.size_bytes)}
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Ingested</span>
                <span className="forensic-spec-item__value">
                  {formatTimestamp(ingested.evidence.ingested_at)}
                </span>
              </div>

              {/* The analyst of record, as stored on the case by the backend from the
                  bearer token. Shown here so the operator can confirm the name that
                  actually went into the ledger, not the one the UI displayed. */}
              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Analyst Of Record</span>
                <span className="forensic-spec-item__value">
                  {ingested.case.examiner ?? 'Not recorded'}
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Acquisition Context</span>
                <span className="forensic-spec-item__value">
                  {ingested.evidence.acquisition_context ?? 'Not recorded'}
                </span>
              </div>
            </div>

            {/* SHA-256 Cryptographic Inspector Strip */}
            <div className="stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 6 }}>
              <div className="forensic-hash-bar">
                <div className="forensic-hash-bar__info">
                  <span className="forensic-hash-bar__label">Stored SHA-256</span>
                  <code className="forensic-hash-bar__code">{ingested.evidence.sha256}</code>
                </div>
                <CopyButton value={ingested.evidence.sha256} title="Copy stored SHA-256 digest" />
              </div>
              {/* Same hash, same file, computed twice. Said plainly so nobody reads
                  the pre-flight and stored digests as two different exhibits. */}
              <span className="field__hint">
                {digestsAgree === true
                  ? 'Recomputed by the backend from the received bytes, and identical to the pre-flight digest computed in this browser — the same hash of the same file.'
                  : digestsAgree === false
                    ? 'Recomputed by the backend from the received bytes. It does not match the pre-flight digest — see the alert above.'
                    : 'Recomputed by the backend from the received bytes. No pre-flight digest was available in this browser to compare it against.'}
              </span>
            </div>
          </div>

          {/* Primary Action Row */}
          <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
            <button
              type="button"
              className="btn btn--primary"
              style={{ padding: '10px 24px', fontSize: 'var(--text-sm)', fontWeight: 700 }}
              onClick={onAnalyse}
            >
              Run Analysis →
            </button>
            {/* Two genuinely different actions, previously one button labelled as the
                first while doing the second: another exhibit for this case, or a
                different case altogether. */}
            <button type="button" className="btn btn--ghost" onClick={sealAnother}>
              <Icon name="upload" size={14} />
              Add Another Exhibit
            </button>
            <button type="button" className="btn btn--ghost" onClick={startNewCase}>
              <Icon name="document" size={14} />
              Start New Case
            </button>
          </div>
        </div>
      ) : null}

      {/* 5. STATE B: PRE-UPLOAD / FILE SELECTION / INGESTION FORM */}
      {!ingested ? (
        <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-4)' }}>
          {/* UPLOAD AREA: Large obvious drop zone */}
          {!file ? (
            /*
              A mouse convenience, not a control.

              This div carried `role="button"`, `tabIndex={0}` and its own Enter/
              Space handler while also containing a real "Upload Evidence"
              button. That is a focusable control nested inside something
              claiming to be a control: two tab stops that do the identical
              thing, and a screen reader announcing the outer one by reading its
              entire subtree -- heading, paragraph and all three format badges --
              as one button name.

              The inner button is the control now, and it is the only tab stop.
              Click-anywhere still works for a mouse, because that costs a
              keyboard user nothing once the div stops claiming to be a button.
            */
            <div
              className={`forensic-dropzone${dragging ? ' forensic-dropzone--active' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
            >
              <div className="forensic-dropzone__reticle forensic-dropzone__reticle--tl" />
              <div className="forensic-dropzone__reticle forensic-dropzone__reticle--tr" />
              <div className="forensic-dropzone__reticle forensic-dropzone__reticle--bl" />
              <div className="forensic-dropzone__reticle forensic-dropzone__reticle--br" />

              <div className="forensic-dropzone__icon-wrap">
                <Icon name="upload" size={26} />
              </div>

              <div className="forensic-dropzone__title">
                Drop Digital Evidence Here
              </div>
              <p className="forensic-dropzone__subtitle">
                Select an image or video file to ingest, compute pre-flight cryptographic digests, and seal into the forensic chain of custody.
              </p>

              <button
                type="button"
                className="btn btn--primary"
                style={{ pointerEvents: 'auto', padding: '8px 20px' }}
                onClick={(e) => {
                  e.stopPropagation()
                  inputRef.current?.click()
                }}
                disabled={busy}
              >
                <Icon name="upload" size={15} />
                Upload Evidence
              </button>

              <div className="forensic-dropzone__tags">
                <span className="forensic-dropzone__badge">IMAGE: JPEG · PNG · WEBP · TIFF · BMP</span>
                <span className="forensic-dropzone__badge">VIDEO: MP4 · MOV · AVI · MKV · WEBM</span>
                <span className="forensic-dropzone__badge">MAX: {formatBytes(MAX_UPLOAD_BYTES)}</span>
              </div>
            </div>
          ) : (
            /* Selected File Inspection & Case Metadata Form */
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              {/* SELECTED EVIDENCE: what the browser reports about the chosen file. */}
              <section className="intake-section">
                <span className="intake-section__label">Selected Evidence</span>
                <div
                  className="row"
                  style={{
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    background: 'var(--surface-2)',
                    border: '1px solid var(--border-strong)',
                    borderRadius: 'var(--radius)',
                    padding: '12px 16px',
                    flexWrap: 'wrap',
                    gap: 10,
                  }}
                >
                  <div className="row" style={{ gap: 12, alignItems: 'center', minWidth: 0 }}>
                    <div
                      style={{
                        width: 44,
                        height: 44,
                        borderRadius: 'var(--radius-sm)',
                        background: 'var(--surface-3)',
                        border: '1px solid var(--border)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        overflow: 'hidden',
                        flexShrink: 0,
                      }}
                    >
                      {previewUrl && file.type.startsWith('image/') ? (
                        <img
                          src={previewUrl}
                          alt="Evidence Preview"
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      ) : (
                        <Icon name="document" size={22} style={{ color: 'var(--accent)' }} />
                      )}
                    </div>
                    <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                      <span style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }} className="break-all">
                        {file.name}
                      </span>
                      {/* Only what is actually known. An empty `file.type` means the
                          browser did not report one -- said as much, rather than
                          printing a plausible-looking placeholder. Duration and
                          dimensions are deliberately absent: they are not read here. */}
                      <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                        {file.type || 'Type not reported by browser'} · {formatBytes(file.size)}
                      </span>
                    </div>
                  </div>

                  <button
                    type="button"
                    className="btn btn--ghost"
                    onClick={() => inputRef.current?.click()}
                    disabled={busy}
                    style={{ fontSize: 'var(--text-xs)' }}
                  >
                    <Icon name="refresh" size={13} />
                    Choose different file
                  </button>
                </div>
                <span className="field__hint">
                  The backend confirms the media type from the file's magic bytes at
                  ingest and refuses anything that is not a supported image or video.
                </span>
              </section>

              {/* EVIDENCE IDENTITY: the digest this browser computed, before upload. */}
              <section className="intake-section">
                <span className="intake-section__label">Evidence Identity</span>
                <div className="forensic-hash-bar">
                  <div className="forensic-hash-bar__info">
                    <span className="forensic-hash-bar__label">Pre-Flight SHA-256</span>
                    <code className="forensic-hash-bar__code">
                      {localHash.phase === 'hashing'
                        ? 'Computing local cryptographic digest…'
                        : localHash.phase === 'done'
                          ? localHash.hex
                          : localHash.phase === 'unsupported'
                            ? 'Web Crypto API unavailable in this context'
                            : localHash.phase === 'failed'
                              ? `Digest error: ${localHash.reason}`
                              : 'Pending'}
                    </code>
                  </div>
                  {localHash.phase === 'done' ? (
                    <CopyButton value={localHash.hex} title="Copy pre-flight SHA-256 digest" />
                  ) : null}
                </div>
                {/* One file, one hash, computed twice for comparison. Stated here so
                    the pre-flight and stored digests are never read as two separate
                    hashes of two separate things. */}
                <span className="field__hint">
                  Computed in this browser from the selected file before anything is
                  sent. At seal the backend computes SHA-256 again from the bytes it
                  received and stores that as the evidence digest; for an intact
                  upload the two are the same value.
                </span>
              </section>

              {/* CASE & EXAMINATION: the only facts a human supplies. */}
              <section className="intake-section">
                <span className="intake-section__label">Case &amp; Examination</span>

                {opensNewCase ? (
                  <>
                    <div className="field">
                      <label className="field__label" htmlFor="intake-title">
                        Case Title <span className="field__req" aria-hidden="true">*</span>
                      </label>
                      <input
                        id="intake-title"
                        className="input"
                        type="text"
                        placeholder="e.g. Circulated advisory clip"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        disabled={busy}
                        required
                        aria-required="true"
                      />
                      <span className="field__hint">Short identifier for this investigation.</span>
                    </div>

                    <div className="field">
                      <label className="field__label" htmlFor="intake-description">
                        Incident / Examination Description{' '}
                        <span className="field__req" aria-hidden="true">*</span>
                      </label>
                      <textarea
                        id="intake-description"
                        className="input"
                        rows={2}
                        placeholder="e.g. Clip circulated on WhatsApp claiming a curfew order; verifying authenticity for complaint CYB/2026/0413."
                        value={description}
                        onChange={(e) => setDescription(e.target.value)}
                        disabled={busy}
                        required
                        aria-required="true"
                      />
                      <span className="field__hint">
                        Why this evidence is being examined and relevant context.
                      </span>
                    </div>
                  </>
                ) : (
                  /* Attaching to an open case: its title and description already
                     exist on the record, and the backend takes neither on this
                     request. Showing them read-only is the truthful alternative to
                     rendering two inputs that would be silently discarded. */
                  <div className="stack" style={{ gap: 4 }}>
                    <div className="field">
                      <span className="field__label">Case Title</span>
                      <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-strong)', fontWeight: 600 }}>
                        {attachTo.title ?? 'Not recorded on this case'}
                      </span>
                    </div>
                    {attachTo.description ? (
                      <div className="field">
                        <span className="field__label">Incident / Examination Description</span>
                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text)' }}>
                          {attachTo.description}
                        </span>
                      </div>
                    ) : null}
                    <span className="field__hint">
                      Carried from case {attachTo.case_number}. This exhibit is sealed
                      into that case, so its title and description are not re-entered
                      here.
                    </span>
                  </div>
                )}

                <div className="field">
                  <label className="field__label" htmlFor="intake-acquisition">
                    Evidence Source / Acquisition Context{' '}
                    <span style={{ color: 'var(--text-faint)', fontWeight: 400 }}>(optional)</span>
                  </label>
                  <input
                    id="intake-acquisition"
                    className="input"
                    type="text"
                    placeholder="e.g. Received from complainant on USB drive, 04 Sep 2026"
                    value={acquisitionContext}
                    onChange={(e) => setAcquisitionContext(e.target.value)}
                    disabled={busy}
                  />
                  <span className="field__hint">
                    How this file came into your hands. Stored on the evidence record
                    and written into its ingest audit entry; left blank it reads "not
                    recorded" rather than empty.
                  </span>
                </div>
              </section>

              {/* ANALYST: displayed from the session, never typed. */}
              <AnalystOfRecord
                operator={operator}
                note={
                  opensNewCase
                    ? 'Recorded as the examiner on this case and on every exhibit you seal. Resolved by the backend from your session, so it cannot be typed or overridden here.'
                    : `Recorded against this exhibit. The case's examiner of record remains ${attachTo.examiner ?? 'unrecorded'}.`
                }
              />

              {/* In-flight state: sending, or waiting.

                  This was an "Operational Ingestion Checklist" -- four pipeline
                  steps with status marks, three of which were hardcoded. A green
                  tick sat on "Validating file bounds and format payload" from
                  the instant the request left the browser, before the server had
                  seen a byte, and it stayed there even when the file was about
                  to be rejected as an unsupported type. The last two rows
                  carried a dot that never changed, because nothing in the code
                  was ever going to change it. It read like a pipeline monitor
                  and was a drawing of one.

                  What replaces it is the two states the browser can actually
                  distinguish, plus the one genuinely finished fact -- the
                  pre-flight digest -- shown as its value rather than as a tick.
              */}
              {busy ? (
                <div className="card stack" style={{ padding: '12px 16px', gap: 10, background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
                  <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-3)' }}>
                    <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                      <Spinner />
                      <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                        {transferDelivered
                          ? 'Waiting for the server to seal this exhibit…'
                          : 'Transferring evidence to the server…'}
                      </span>
                    </div>
                    {!transferDelivered && transferFraction != null ? (
                      <span className="mono" style={{ fontSize: '11px', color: 'var(--accent-bright)', fontWeight: 700 }}>
                        {Math.round(transferFraction * 100)}%
                      </span>
                    ) : null}
                  </div>

                  {/* The bar measures the request body and nothing else, so it
                      goes away once the body is delivered instead of sitting
                      full while the server works. A bar pinned at 100% through
                      the part of the operation that actually takes the time is
                      the clearest possible way to imply progress nobody is
                      measuring. */}
                  {!transferDelivered && transferFraction != null ? (
                    <div style={{ height: 4, background: 'var(--surface-3)', borderRadius: 100, overflow: 'hidden' }}>
                      <div
                        style={{
                          width: '100%',
                          height: '100%',
                          background: 'var(--accent)',
                          transformOrigin: 'left',
                          transform: `scaleX(${transferFraction})`,
                          transition: 'transform 120ms linear',
                        }}
                      />
                    </div>
                  ) : null}

                  <div className="stack" style={{ gap: 4, fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                    {uploadProgress && !transferDelivered ? (
                      <span className="mono">
                        {formatBytes(uploadProgress.loaded)} of {formatBytes(uploadProgress.total)} sent
                      </span>
                    ) : null}

                    {preflightHex ? (
                      <span>
                        Pre-flight SHA-256 taken in this browser:{' '}
                        <span className="mono" style={{ color: 'var(--text-strong)' }}>
                          {shortHash(preflightHex)}
                        </span>
                        . It is compared against the digest the backend takes from the bytes it
                        receives, and a difference between them is reported as a finding.
                      </span>
                    ) : localHash.phase === 'hashing' ? (
                      <span>
                        Pre-flight SHA-256 is still being computed here. If it finishes, it is compared
                        against the digest the backend takes from the bytes it receives.
                      </span>
                    ) : (
                      <span>
                        No pre-flight digest is available in this browser
                        {localHash.phase === 'unsupported'
                          ? ' (Web Crypto is not available on this origin)'
                          : ''}
                        , so the backend digest stands on its own — there is nothing on this side to
                        compare it against.
                      </span>
                    )}

                    <span>
                      {transferDelivered
                        ? 'Every byte is delivered. The server is now validating the file, hashing it, writing it to evidence storage, appending the custody entry and indexing it. None of that reports progress back here, so this waits for the result rather than guessing at it.'
                        : 'Validation, hashing, storage and the custody entry all happen after the last byte arrives.'}
                    </span>
                  </div>
                </div>
              ) : null}

              {/* Primary Action Button */}
              <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  style={{ padding: '10px 24px', fontSize: 'var(--text-sm)', fontWeight: 700 }}
                  disabled={!canSeal}
                  onClick={() =>
                    uploadFile(file, {
                      // Present only in attach mode; its absence is what tells the
                      // backend to open a new case.
                      caseId: attachTo?.case_id,
                      // Sent only when a new case is being opened -- the backend
                      // ignores neither, so passing them on an attach would claim to
                      // retitle a case this screen has no mandate to retitle.
                      title: opensNewCase ? title.trim() : undefined,
                      description: opensNewCase ? description.trim() : undefined,
                      acquisitionContext: acquisitionContext.trim() || undefined,
                    })
                  }
                >
                  {busy ? <Spinner /> : <Icon name="lock" size={15} />}
                  {busy ? 'Sealing Evidence…' : 'SEAL & INGEST EVIDENCE →'}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={clearSelection}
                  disabled={busy}
                >
                  Cancel
                </button>
              </div>

              {/* Why the button is disabled, in the operator's terms. The backend
                  enforces the same requirements and is the authority; this only
                  saves a round trip to be told so. */}
              {missing.length > 0 && !busy ? (
                <ul className="intake-blockers">
                  {missing.map((item) => (
                    <li key={item}>{item} is required before this evidence can be sealed.</li>
                  ))}
                </ul>
              ) : null}
            </div>
          )}

          {rejection ? <Banner tone="warn" title="File not accepted" detail={rejection} /> : null}
          {uploadError ? (
            /* The backend's own words. `userMessage` prefers an endpoint-raised
               sentence ("A case title is required...") over FastAPI's generic
               validation wrapper, so a refused seal says which requirement failed
               instead of "Unprocessable Entity". Nothing here is presented as
               success. */
            <Banner
              tone="error"
              title="Evidence was not sealed"
              detail={
                uploadError instanceof ApiError
                  ? uploadError.userMessage
                  : uploadError instanceof Error
                    ? uploadError.message
                    : 'The file could not be ingested.'
              }
              meta="No custody record was created. Correct the problem above and seal again."
            />
          ) : null}

          <input
            ref={inputRef}
            type="file"
            accept="image/*,video/*"
            style={{ display: 'none' }}
            onChange={(e) => takeFile(e.target.files?.[0] ?? null)}
            disabled={busy}
          />
        </div>
      ) : null}
    </div>
  )
}
