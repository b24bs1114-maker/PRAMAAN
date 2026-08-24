/**
 * Screen: New case (intake).
 *
 * One job: take a media file, seal it, and open a case around it. Nothing here
 * is fabricated — no pre-written verdict, no invented signal matrix, no imagined
 * provenance timeline. Those are findings, and findings come from analysis, not
 * from the upload form.
 *
 * The one real measurement shown before anything is sent is the browser
 * pre-flight SHA-256, computed locally from the selected bytes. When the backend
 * independently hashes the received bytes, a matching digest is evidence the file
 * survived the network unchanged.
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
  const { upload, uploadProgress, uploadFile, reset, health } = investigation

  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [rejection, setRejection] = useState<string | null>(null)
  const [localHash, setLocalHash] = useState<LocalHash>({ phase: 'idle' })
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  const [title, setTitle] = useState('')
  const [examiner, setExaminer] = useState('')
  const [description, setDescription] = useState('')

  const inputRef = useRef<HTMLInputElement>(null)

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

  const accept = useCallback((candidate: File): string | null => {
    if (candidate.size === 0) return 'That file is empty (0 bytes). Nothing to ingest.'
    if (candidate.size > MAX_UPLOAD_BYTES) {
      return `That file is ${formatBytes(candidate.size)}. Backend limit: ${formatBytes(MAX_UPLOAD_BYTES)}.`
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

  // Compute the local digest as soon as a file is accepted.
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
            reason: error instanceof Error ? error.message : 'Digest failed.',
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

  // --- Ingested: show the sealed evidence and the way forward. ---------------
  if (ingested) {
    const ev = ingested.evidence
    return (
      <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
        <div className="screen__head">
          <h1 className="screen__title">Evidence sealed</h1>
          <p className="screen__lead">
            The file is stored and hashed. Run analysis to assess it, or start another case.
          </p>
        </div>

        {ingested.duplicate ? (
          <Banner
            tone="info"
            title="Identical bytes already on file"
            detail="This exact file was ingested before. It has been attached to the case rather than stored twice — the SHA-256 seal is unchanged."
          />
        ) : null}

        {ingested.warnings.length > 0 ? (
          <Banner
            tone="warn"
            title="Ingest warnings"
            detail={ingested.warnings.join(' ')}
          />
        ) : null}

        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <div className="row" style={{ gap: 10, alignItems: 'center', minWidth: 0 }}>
              <Icon name="lock" size={18} style={{ color: 'var(--ok)' }} />
              <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{ev.filename}</span>
            </div>
            <Pill variant="accent">{ev.media_type.toUpperCase()}</Pill>
          </div>

          <dl className="dl" style={{ fontSize: 'var(--text-xs)' }}>
            <dt>Case</dt>
            <dd className="mono">
              {ingested.case.case_number}
              {ingested.case.title ? ` · ${ingested.case.title}` : ''}
            </dd>
            <dt>Evidence ID</dt>
            <dd className="mono">{ev.evidence_id}</dd>
            <dt>Received</dt>
            <dd>{formatTimestamp(ev.ingested_at)}</dd>
            <dt>Size</dt>
            <dd>{formatBytes(ev.size_bytes)}</dd>
          </dl>

          <div className="hash">
            <span className="hash__label">SHA-256</span>
            <code className="hash__value break-all">{ev.sha256}</code>
            <CopyButton value={ev.sha256} title="Copy SHA-256 digest" />
          </div>
        </div>

        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={onAnalyse}>
            Proceed to analysis
            <Icon name="arrow-right" size={14} />
          </button>
          <button type="button" className="btn btn--ghost" onClick={startOver}>
            <Icon name="upload" size={14} />
            New case
          </button>
        </div>
      </div>
    )
  }

  // --- Not yet ingested: the upload form. ------------------------------------
  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <h1 className="screen__title">New case</h1>
        <p className="screen__lead">
          Upload a media file to open a case and seal it into the evidence chain.
        </p>
      </div>

      {health === 'down' ? (
        <Banner
          tone="error"
          title="Backend not reachable"
          detail="Evidence can't be ingested until the backend responds. The file below is held locally only."
        />
      ) : null}

      <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-4)' }}>
        {/* Media: preview once chosen, dropzone before. */}
        {file && previewUrl ? (
          <div className="media-hero">
            {file.type.startsWith('video/') ? (
              <video src={previewUrl} controls />
            ) : (
              <img src={previewUrl} alt="Selected evidence" />
            )}
            <div className="media-hero__overlay">
              <span>{file.name}</span>
              <span>{file.type || 'Media object'} · {formatBytes(file.size)}</span>
            </div>
          </div>
        ) : (
          <div
            className={`dropzone${dragging ? ' dropzone--active' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
          >
            <Icon name="upload" className="dropzone__glyph" size={32} />
            <span className="dropzone__lead">Drop a media file here</span>
            <span className="dropzone__hint">
              or use Browse below · images and video · up to {formatBytes(MAX_UPLOAD_BYTES)}
            </span>
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
          className="dropzone__input"
          type="file"
          accept="image/*,video/*"
          onChange={(e) => takeFile(e.target.files?.[0] ?? null)}
          disabled={busy}
        />

        {/* Local pre-flight digest — a real measurement, shown before sending. */}
        {file ? (
          <div className="hash">
            <span className="hash__label">Browser pre-flight · SHA-256</span>
            <code className="hash__value break-all">
              {localHash.phase === 'hashing'
                ? 'Computing digest…'
                : localHash.phase === 'done'
                  ? localHash.hex
                  : localHash.phase === 'unsupported'
                    ? 'Web Crypto unavailable in this context'
                    : localHash.phase === 'failed'
                      ? `Digest failed: ${localHash.reason}`
                      : '—'}
            </code>
            {localHash.phase === 'done' ? (
              <CopyButton value={localHash.hex} title="Copy local digest" />
            ) : null}
          </div>
        ) : null}

        {/* Optional case details — the backend assigns the case number. */}
        <div className="grid-2col" style={{ gap: 'var(--space-3)' }}>
          <div className="field">
            <label className="field__label" htmlFor="intake-title">
              Case title <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
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
              Examiner <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
            </label>
            <input
              id="intake-examiner"
              className="input"
              type="text"
              placeholder="Your name or ID"
              value={examiner}
              onChange={(e) => setExaminer(e.target.value)}
              disabled={busy}
            />
          </div>
        </div>
        <div className="field">
          <label className="field__label" htmlFor="intake-description">
            Description <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
          </label>
          <textarea
            id="intake-description"
            className="input"
            rows={2}
            placeholder="What is this file, and why is it under review?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
          />
        </div>

        {/* Upload progress while in flight. */}
        {busy && uploadProgress && uploadProgress.fraction != null ? (
          <div className="stack" style={{ gap: 4 }}>
            <div style={{ height: 6, background: 'var(--surface-2)', borderRadius: 100, overflow: 'hidden' }}>
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
            <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
              Uploading… {Math.round(uploadProgress.fraction * 100)}%
            </span>
          </div>
        ) : null}

        <div className="btn-row">
          <button type="button" className="btn" onClick={() => inputRef.current?.click()} disabled={busy}>
            <Icon name="upload" size={14} />
            {file ? 'Choose a different file' : 'Browse file…'}
          </button>
          {file ? (
            <button
              type="button"
              className="btn btn--primary"
              disabled={busy || health === 'down'}
              onClick={() =>
                uploadFile(file, {
                  title: title.trim() || undefined,
                  description: description.trim() || undefined,
                  examiner: examiner.trim() || undefined,
                })
              }
            >
              {busy ? <Spinner /> : <Icon name="lock" size={14} />}
              Ingest &amp; seal
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
