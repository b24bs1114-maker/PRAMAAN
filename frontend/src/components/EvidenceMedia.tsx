/**
 * The one canonical way this console displays stored evidence bytes.
 *
 * Every preview -- the intake seal confirmation, the analysis viewport, the
 * evidence queue thumbnails, the case dossier table -- renders through here.
 *
 * Why a component and not a URL: `GET /api/evidence/{id}/file` requires the
 * operator's bearer token, and a browser-initiated `<img src>` request cannot
 * carry an Authorization header. So the bytes are fetched through the same
 * authenticated transport as every other call, wrapped in an object URL, and
 * revoked when the view goes away. There used to be six hand-rolled `<img>` tags
 * pointed straight at that path with three different error behaviours between
 * them (one set a state flag, two hid themselves with inline `display:none`, one
 * did nothing at all and left a broken-image glyph in the report-ready dossier).
 *
 * What is shown when the bytes cannot be had is a statement, not a blank: the
 * file is sealed and recorded either way, and a preview that silently vanishes
 * invites the reading that the evidence itself is gone.
 */

import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { Icon } from './Icon'

type Phase = 'loading' | 'ready' | 'failed'

interface ObjectUrlState {
  url: string | null
  phase: Phase
  error: unknown
}

/**
 * Hold the stored bytes of one evidence item as an object URL.
 *
 * `localUrl` wins when present: on the intake screen the operator's own file is
 * already in memory from the file picker, so there is no reason to pull it back
 * over the network to show them what they just uploaded.
 */
function useEvidenceObjectUrl(evidenceId: string | null, localUrl?: string | null): ObjectUrlState {
  const [state, setState] = useState<ObjectUrlState>(() =>
    localUrl ? { url: localUrl, phase: 'ready', error: null } : { url: null, phase: 'loading', error: null },
  )

  useEffect(() => {
    if (localUrl) {
      setState({ url: localUrl, phase: 'ready', error: null })
      return
    }
    if (!evidenceId) {
      setState({ url: null, phase: 'failed', error: null })
      return
    }

    const controller = new AbortController()
    let objectUrl: string | null = null
    let cancelled = false

    setState({ url: null, phase: 'loading', error: null })
    api
      .evidenceFile(evidenceId, controller.signal)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setState({ url: objectUrl, phase: 'ready', error: null })
      })
      .catch((cause) => {
        // `cancelled` is set before the abort, so an unmount lands here as a
        // no-op rather than as a missing exhibit.
        if (cancelled) return
        setState({ url: null, phase: 'failed', error: cause })
      })

    return () => {
      cancelled = true
      controller.abort()
      // Revoked on the way out: without this every case switch and every scroll
      // through the evidence queue leaks the full bytes of each exhibit for the
      // lifetime of the tab.
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [evidenceId, localUrl])

  return state
}

/** Why no picture is on screen, in the operator's terms. */
function failureNote(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return 'Stored bytes not found on the server'
    if (error.status === 401 || error.status === 403) return 'Not authorised to view these bytes'
    if (error.kind === 'network') return 'Backend unreachable'
    return error.message
  }
  return 'Preview could not be loaded'
}

/**
 * A thumbnail-sized preview, for tables and queues.
 *
 * Fills its parent and falls back to the document icon the surrounding cells
 * already use for non-image media, so a failed fetch reads the same as "not an
 * image" rather than as a hole in the layout.
 */
export function EvidenceThumbnail({
  evidenceId,
  alt = '',
  iconSize = 20,
}: {
  evidenceId: string
  alt?: string
  iconSize?: number
}) {
  const { url, phase, error } = useEvidenceObjectUrl(evidenceId)

  if (phase === 'ready' && url) {
    return <img src={url} alt={alt} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
  }
  return (
    <Icon
      name="document"
      size={iconSize}
      style={{ color: phase === 'failed' ? 'var(--text-faint)' : 'var(--accent-bright)' }}
      title={phase === 'failed' ? failureNote(error) : undefined}
    />
  )
}

/**
 * A full inspection preview, for the intake and analysis viewports.
 *
 * `kind` follows the evidence record's own media type: `'none'` is the honest
 * state for media this build cannot draw inline (audio, and video where no
 * decoder is offered), and it does not go to the network to discover that. A
 * video is played from the same fetched bytes rather than from a second,
 * unauthenticated request.
 */
export function EvidencePreview({
  evidenceId,
  filename,
  mediaType,
  kind,
  detail,
  localUrl,
}: {
  evidenceId: string
  filename: string
  /** Shown in the fallback, so the exhibit is still identified without a picture. */
  mediaType: string
  kind: 'image' | 'video' | 'none'
  /** Extra fact for the fallback's mono line, e.g. the stored size. */
  detail?: string
  localUrl?: string | null
}) {
  const { url, phase, error } = useEvidenceObjectUrl(kind === 'none' ? null : evidenceId, localUrl)

  if (kind !== 'none' && phase === 'ready' && url) {
    return kind === 'video' ? (
      <video src={url} controls />
    ) : (
      <img src={url} alt={filename} />
    )
  }

  const loading = kind !== 'none' && phase === 'loading'
  const failed = kind !== 'none' && phase === 'failed'
  const monoParts = [mediaType.toUpperCase()]
  if (detail) monoParts.push(detail)
  monoParts.push(loading ? 'LOADING STORED BYTES…' : failed ? 'PREVIEW UNAVAILABLE' : 'NO INLINE PREVIEW FOR THIS MEDIA TYPE')

  return (
    <div
      className="stack"
      style={{
        alignItems: 'center',
        gap: 8,
        padding: 'var(--space-6)',
        color: 'var(--text-muted)',
        textAlign: 'center',
      }}
    >
      <Icon
        name={loading ? 'refresh' : 'document'}
        size={40}
        style={{ color: loading ? 'var(--text-faint)' : 'var(--accent-bright)' }}
      />
      <span style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>{filename}</span>
      <span style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }}>{monoParts.join(' · ')}</span>
      {/* The seal is unaffected by a preview that would not load, and saying so
          here is the difference between "we cannot draw it" and "it is gone". */}
      {failed ? (
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)', maxWidth: 320 }}>
          {failureNote(error)} · the sealed record and its SHA-256 are unaffected
        </span>
      ) : null}
    </div>
  )
}
