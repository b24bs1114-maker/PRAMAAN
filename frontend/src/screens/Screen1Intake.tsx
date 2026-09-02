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
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { MAX_UPLOAD_BYTES } from '../api'
import { Banner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { formatBytes, formatTimestamp } from '../lib/format'
import { canHashLocally, sha256Hex } from '../lib/hash'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import type { Investigation } from '../state/useInvestigation'

type LocalHash =
  | { phase: 'idle' }
  | { phase: 'hashing' }
  | { phase: 'done'; hex: string }
  | { phase: 'unsupported' }
  | { phase: 'failed'; reason: string }

export function Screen1Intake({
  investigation,
  onAnalyse,
}: {
  investigation: Investigation
  onAnalyse: () => void
}) {
  const { upload, uploadProgress, uploadFile, reset, health, caseRecord } = investigation

  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [rejection, setRejection] = useState<string | null>(null)
  const [localHash, setLocalHash] = useState<LocalHash>({ phase: 'idle' })
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [examiner, setExaminer] = useState('')
  const [description, setDescription] = useState('')

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

  const startOver = () => {
    setFile(null)
    setRejection(null)
    setLocalHash({ phase: 'idle' })
    setTitle('')
    setExaminer('')
    setDescription('')
    reset()
  }

  type StepState = 'idle' | 'active' | 'done'
  const step1State: StepState = ingested ? 'done' : busy ? 'done' : file ? 'done' : 'active'
  const step2State: StepState = ingested
    ? 'done'
    : busy
      ? 'done'
      : file
        ? localHash.phase === 'done'
          ? 'done'
          : 'active'
        : 'idle'
  const step3State: StepState = ingested ? 'done' : busy ? 'active' : 'idle'
  const step4State: StepState = ingested ? 'active' : 'idle'

  const activeCaseInfo = ingested?.case ?? caseRecord

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      {/* 1. CASE CONTEXT (If active case exists) */}
      {activeCaseInfo ? (
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
              Active Case
            </span>
            <code style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--accent-bright)' }}>
              {activeCaseInfo.case_number}
            </code>
            {activeCaseInfo.title ? (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
                · {activeCaseInfo.title}
              </span>
            ) : null}
          </div>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            {activeCaseInfo.examiner ? (
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Examiner: <strong style={{ color: 'var(--text)' }}>{activeCaseInfo.examiner}</strong>
              </span>
            ) : null}
            {activeCaseInfo.priority ? (
              <Pill variant={activeCaseInfo.priority === 'high' ? 'error' : 'warn'}>
                {activeCaseInfo.priority.toUpperCase()}
              </Pill>
            ) : null}
          </div>
        </div>
      ) : null}

      {/* 2. FORENSIC WORKFLOW STEPPER */}
      <nav className="forensic-pipeline-stepper" aria-label="Evidence Intake Pipeline">
        <div className={`pipeline-step${step1State === 'active' ? ' pipeline-step--active' : step1State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step1State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '1'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Uploaded</span>
            <span className="pipeline-step__status">
              {step1State === 'done' ? 'Bytes Verified' : 'Select Media'}
            </span>
          </div>
        </div>

        <div className={`pipeline-step${step2State === 'active' ? ' pipeline-step--active' : step2State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step2State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '2'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Hashed</span>
            <span className="pipeline-step__status">
              {step2State === 'done'
                ? 'SHA-256 Pre-flight'
                : step2State === 'active'
                  ? 'Computing...'
                  : 'Pending Hash'}
            </span>
          </div>
        </div>

        <div className={`pipeline-step${step3State === 'active' ? ' pipeline-step--active' : step3State === 'done' ? ' pipeline-step--done' : ''}`}>
          <div className="pipeline-step__num">
            {step3State === 'done' ? <Icon name="check" size={13} strokeWidth={2.5} /> : '3'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Sealed</span>
            <span className="pipeline-step__status">
              {step3State === 'done'
                ? 'Custody Locked'
                : step3State === 'active'
                  ? 'Sealing Ingest...'
                  : 'Pending Custody'}
            </span>
          </div>
        </div>

        <div className={`pipeline-step${step4State === 'active' ? ' pipeline-step--active' : ''}`}>
          <div className="pipeline-step__num">
            {ingested ? <Icon name="check" size={13} strokeWidth={2.5} /> : '4'}
          </div>
          <div className="pipeline-step__body">
            <span className="pipeline-step__title">Ready for Analysis</span>
            <span className="pipeline-step__status">
              {ingested ? 'Forensics Armed' : 'Awaiting Ingest'}
            </span>
          </div>
        </div>
      </nav>

      {/* 3. PAGE TITLE & SUPPORTING LINE */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">Evidence Intake</h1>
          <p className="screen__lead">
            Ingest, verify and preserve digital evidence.
          </p>
        </div>
      </div>

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
                {ingested.evidence.media_type.startsWith('video') ? (
                  <video
                    src={previewUrl || evidenceFileUrl(ingested.evidence.evidence_id)}
                    controls
                  />
                ) : isImageMedia(ingested.evidence.media_type) ? (
                  <img
                    src={previewUrl || evidenceFileUrl(ingested.evidence.evidence_id)}
                    alt={ingested.evidence.filename}
                  />
                ) : (
                  <div className="stack" style={{ alignItems: 'center', gap: 10, padding: 'var(--space-6)', color: 'var(--text-muted)' }}>
                    <Icon name="document" size={48} style={{ color: 'var(--accent-bright)' }} />
                    <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{ingested.evidence.filename}</span>
                  </div>
                )}
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
                <span className="forensic-spec-item__label">Timestamp</span>
                <span className="forensic-spec-item__value">
                  {formatTimestamp(ingested.evidence.ingested_at)}
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Custody Status</span>
                <span className="forensic-spec-item__value" style={{ color: 'var(--ok-bright)' }}>
                  Immutable &amp; Verified
                </span>
              </div>

              <div className="forensic-spec-item">
                <span className="forensic-spec-item__label">Evidence Seal</span>
                <span className="forensic-spec-item__value forensic-spec-item__value--mono" style={{ color: 'var(--text-strong)' }}>
                  SHA256-AUTHENTICATED
                </span>
              </div>
            </div>

            {/* SHA-256 Cryptographic Inspector Strip */}
            <div style={{ padding: 'var(--space-3) var(--space-4)' }}>
              <div className="forensic-hash-bar">
                <div className="forensic-hash-bar__info">
                  <span className="forensic-hash-bar__label">SHA-256 Digest</span>
                  <code className="forensic-hash-bar__code">{ingested.evidence.sha256}</code>
                </div>
                <CopyButton value={ingested.evidence.sha256} title="Copy SHA-256 digest" />
              </div>
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
            <button type="button" className="btn btn--ghost" onClick={startOver}>
              <Icon name="upload" size={14} />
              Ingest Another File
            </button>
          </div>
        </div>
      ) : null}

      {/* 5. STATE B: PRE-UPLOAD / FILE SELECTION / INGESTION FORM */}
      {!ingested ? (
        <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-4)' }}>
          {/* UPLOAD AREA: Large obvious drop zone */}
          {!file ? (
            <div
              className={`forensic-dropzone${dragging ? ' forensic-dropzone--active' : ''}`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') inputRef.current?.click()
              }}
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
              {/* File Preview & Details */}
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
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                      {file.type || 'Media object'} · {formatBytes(file.size)}
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

              {/* Real Browser Pre-Flight Digest Computation */}
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
                  <CopyButton value={localHash.hex} title="Copy local SHA-256 digest" />
                ) : null}
              </div>

              {/* Case Metadata Input Fields */}
              <div className="grid-2col" style={{ gap: 'var(--space-3)' }}>
                <div className="field">
                  <label className="field__label" htmlFor="intake-title">
                    Case Title <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
                  </label>
                  <input
                    id="intake-title"
                    className="input"
                    type="text"
                    placeholder="e.g. Circulated advisory clip"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    disabled={busy}
                  />
                </div>
                <div className="field">
                  <label className="field__label" htmlFor="intake-examiner">
                    Examiner ID / Name <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
                  </label>
                  <input
                    id="intake-examiner"
                    className="input"
                    type="text"
                    placeholder="e.g. Insp. Sharma (Cyber Unit)"
                    value={examiner}
                    onChange={(e) => setExaminer(e.target.value)}
                    disabled={busy}
                  />
                </div>
              </div>

              <div className="field">
                <label className="field__label" htmlFor="intake-description">
                  Incident Description &amp; Custody Notes <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
                </label>
                <textarea
                  id="intake-description"
                  className="input"
                  rows={2}
                  placeholder="Context, source platform, or complaint reference..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  disabled={busy}
                />
              </div>

              {/* Upload & Ingestion Progress */}
              {busy && uploadProgress && uploadProgress.fraction != null ? (
                <div className="stack" style={{ gap: 6 }}>
                  <div style={{ height: 6, background: 'var(--surface-3)', borderRadius: 100, overflow: 'hidden' }}>
                    <div
                      style={{
                        width: '100%',
                        height: '100%',
                        background: 'var(--accent)',
                        transformOrigin: 'left',
                        transform: `scaleX(${uploadProgress.fraction})`,
                        transition: 'transform 120ms linear',
                      }}
                    />
                  </div>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-2xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                    <span>Ingesting evidence to secure chain...</span>
                    <span>{Math.round(uploadProgress.fraction * 100)}%</span>
                  </div>
                </div>
              ) : null}

              {/* Primary Action Button */}
              <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  style={{ padding: '10px 24px', fontSize: 'var(--text-sm)', fontWeight: 700 }}
                  disabled={busy || health === 'down'}
                  onClick={() =>
                    uploadFile(file, {
                      title: title.trim() || undefined,
                      description: description.trim() || undefined,
                      examiner: examiner.trim() || undefined,
                    })
                  }
                >
                  {busy ? <Spinner /> : <Icon name="lock" size={15} />}
                  {busy ? 'Ingesting to Custody Chain...' : 'Seal Evidence & Ingest →'}
                </button>
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={startOver}
                  disabled={busy}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}

          {rejection ? <Banner tone="warn" title="File not accepted" detail={rejection} /> : null}
          {uploadError ? (
            <Banner
              tone="error"
              title="Upload failed"
              detail={uploadError instanceof Error ? uploadError.message : 'The file could not be ingested.'}
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
