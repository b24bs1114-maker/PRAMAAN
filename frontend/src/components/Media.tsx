/**
 * Media presentation: the evidence viewer, thumbnails and the intake dropzone.
 *
 * The viewer is an inspection surface, not a gallery. Three constraints:
 *
 *  1. **Regions are drawn only from stored coordinates.** If a detector recorded
 *     a bounding box, it is drawn to scale over the media. If it recorded none,
 *     nothing is drawn -- the viewer never invents a box to make a verdict look
 *     substantiated.
 *  2. **Video and audio are played, not analysed here.** The browser renders the
 *     bytes the backend served; every figure beside the player comes from the
 *     backend's stored metadata.
 *  3. **A file the browser cannot decode says so.** A silent blank frame reads as
 *     "nothing to see"; a failed decode is a fact about the file.
 *
 * `evidenceFileUrl` is a plain GET that writes no audit row, so previewing
 * evidence does not alter the case record.
 */

import { useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'
import { Icon, mediaIcon } from './Icon'
import { Button, Empty, StatusPill } from './Primitives'
import { cx } from '../lib/cx'
import { formatBytes } from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import { humanise } from '../lib/tone'

/** A bounding box a detector actually stored, in image-relative units (0..1). */
export interface Region {
  x: number
  y: number
  width: number
  height: number
  label?: string | null
}

const ZOOMS = [1, 1.5, 2, 3] as const

/**
 * The evidence viewer.
 *
 * Images get zoom and, when supplied, detector regions overlaid to scale. Video
 * and audio get native controls. Anything else gets a stated absence of preview
 * rather than a broken frame -- the file is still evidence, it just cannot be
 * rendered in a browser.
 */
export function MediaViewer({
  evidenceId,
  mediaType,
  filename,
  regions,
  tag,
  children,
}: {
  evidenceId: string
  mediaType: string
  filename: string
  /** Only ever coordinates a detector stored. Omit when none were recorded. */
  regions?: Region[]
  /** Small overlay label, e.g. the media type or a stage name. */
  tag?: ReactNode
  /** Footer content under the media, e.g. dimensions from stored metadata. */
  children?: ReactNode
}) {
  const [zoom, setZoom] = useState(0)
  const [failed, setFailed] = useState(false)
  const url = evidenceFileUrl(evidenceId)

  // A new evidence item is a new decode attempt.
  useEffect(() => {
    setFailed(false)
    setZoom(0)
  }, [evidenceId])

  const isImage = isImageMedia(mediaType)
  const isVideo = mediaType === 'video'
  const isAudio = mediaType === 'audio'
  const scale = ZOOMS[zoom]

  if (failed) {
    return (
      <div className="viewer viewer--flat">
        <Empty
          icon={mediaIcon(mediaType)}
          title="This browser could not decode the file"
          detail={`The backend served bytes for ${filename}, but the browser could not render them. The file is unchanged on disk; only the preview is unavailable.`}
        />
      </div>
    )
  }

  return (
    <div className="viewer-wrap">
      <div className={cx('viewer', isAudio && 'viewer--flat')}>
        {tag ? <div className="viewer__tag">{tag}</div> : null}

        {isImage ? (
          <div className="viewer__stage" style={{ transform: `scale(${scale})` }}>
            <img
              className="viewer__media"
              src={url}
              alt={`Evidence ${filename}`}
              onError={() => setFailed(true)}
            />
            {regions?.map((region, i) => (
              <div
                key={i}
                className="viewer__region"
                style={{
                  left: `${region.x * 100}%`,
                  top: `${region.y * 100}%`,
                  width: `${region.width * 100}%`,
                  height: `${region.height * 100}%`,
                }}
              >
                {region.label ? <span className="viewer__region-label">{region.label}</span> : null}
              </div>
            ))}
          </div>
        ) : isVideo ? (
          <video
            className="viewer__media"
            src={url}
            controls
            preload="metadata"
            onError={() => setFailed(true)}
          />
        ) : isAudio ? (
          <div className="viewer__audio">
            <Icon name="audio" size={28} />
            <audio className="viewer__player" src={url} controls preload="metadata" onError={() => setFailed(true)} />
            <span className="viewer__audio-note">
              Waveform rendering is not performed here. Audio signals come from the backend's stored
              analysis, not from this player.
            </span>
          </div>
        ) : (
          <Empty
            tight
            icon={mediaIcon(mediaType)}
            title="No in-browser preview for this media type"
            detail={`${humanise(mediaType)} evidence is stored and hashed, but cannot be rendered inline. Download the file to inspect it.`}
          />
        )}

        <div className="viewer__frame" aria-hidden="true">
          <span className="viewer__reticle viewer__reticle--tl" />
          <span className="viewer__reticle viewer__reticle--tr" />
          <span className="viewer__reticle viewer__reticle--bl" />
          <span className="viewer__reticle viewer__reticle--br" />
        </div>

        {isImage ? (
          <div className="viewer__zoom">
            <Button
              size="sm"
              variant="bare"
              icon="zoom-out"
              iconOnly
              disabled={zoom === 0}
              onClick={() => setZoom((z) => Math.max(0, z - 1))}
            >
              Zoom out
            </Button>
            <span className="viewer__zoom-val">{scale}×</span>
            <Button
              size="sm"
              variant="bare"
              icon="zoom-in"
              iconOnly
              disabled={zoom === ZOOMS.length - 1}
              onClick={() => setZoom((z) => Math.min(ZOOMS.length - 1, z + 1))}
            >
              Zoom in
            </Button>
          </div>
        ) : null}
      </div>

      {regions && regions.length > 0 ? (
        <p className="viewer__note">
          {regions.length} {regions.length === 1 ? 'region' : 'regions'} drawn from coordinates the
          detector stored for this file. No region is inferred by this interface.
        </p>
      ) : null}

      {children}
    </div>
  )
}

/**
 * Evidence thumbnail.
 *
 * Only image evidence has a renderable thumbnail. Video and audio get their
 * media glyph and a type badge -- a generic film-strip picture pretending to be
 * a frame would be a fabricated preview.
 */
export function Thumb({
  evidenceId,
  mediaType,
  filename,
  size = 'md',
}: {
  evidenceId: string
  mediaType: string
  filename?: string
  size?: 'sm' | 'md' | 'full'
}) {
  const [failed, setFailed] = useState(false)
  const showImage = isImageMedia(mediaType) && !failed

  return (
    <span className={cx('thumb', size === 'sm' && 'thumb--sm', size === 'md' && 'thumb--md')}>
      {showImage ? (
        <img
          className="thumb__img"
          src={evidenceFileUrl(evidenceId)}
          alt={filename ? `Thumbnail of ${filename}` : 'Evidence thumbnail'}
          loading="lazy"
          onError={() => setFailed(true)}
        />
      ) : (
        <Icon name={mediaIcon(mediaType)} size={size === 'sm' ? 16 : 20} />
      )}
      <span className="thumb__badge">{mediaType || 'file'}</span>
    </span>
  )
}

/**
 * The intake dropzone.
 *
 * `accept` is derived from what the deployment says it accepts, not from a
 * hardcoded list: the previous build advertised audio in its copy while its
 * `accept` attribute allowed only `image/*,video/*`, so an officer with a .wav
 * saw a file picker that refused it. When the deployment has not published its
 * extension list, the picker falls back to the three media families the backend
 * supports and the copy says the exact list is unpublished.
 */
export function Dropzone({
  onFiles,
  busy,
  allowed,
  maxBytes,
  title = 'Drop evidence here',
  hint,
  disabled,
}: {
  onFiles: (files: File[]) => void
  busy?: boolean
  /** `SystemStatus.ingestion.allowed_extensions`, when the backend published it. */
  allowed?: { image?: string[]; video?: string[]; audio?: string[] } | null
  maxBytes?: number | null
  title?: string
  hint?: ReactNode
  disabled?: boolean
}) {
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement | null>(null)

  const families = [
    { key: 'image', label: 'Image', list: allowed?.image },
    { key: 'video', label: 'Video', list: allowed?.video },
    { key: 'audio', label: 'Audio', list: allowed?.audio },
  ] as const

  // Extensions when published; otherwise the three families by MIME wildcard.
  const published = families.some((f) => (f.list?.length ?? 0) > 0)
  const accept = published
    ? families
        .flatMap((f) => (f.list ?? []).map((ext) => `.${ext.replace(/^\./, '')}`))
        .join(',')
    : 'image/*,video/*,audio/*'

  const take = (list: FileList | null) => {
    if (!list || list.length === 0) return
    onFiles(Array.from(list))
  }

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    if (disabled || busy) return
    take(event.dataTransfer?.files ?? null)
  }

  return (
    <div
      className={cx('dropzone', over && 'dropzone--active', busy && 'dropzone--busy')}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled && !busy) setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
      onClick={() => {
        if (!disabled && !busy) input.current?.click()
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          if (!disabled && !busy) input.current?.click()
        }
      }}
      role="button"
      tabIndex={disabled ? -1 : 0}
      aria-disabled={disabled || busy || undefined}
      aria-label={`${title}. Opens a file picker.`}
    >
      <span className="dropzone__reticle dropzone__reticle--tl" />
      <span className="dropzone__reticle dropzone__reticle--tr" />
      <span className="dropzone__reticle dropzone__reticle--bl" />
      <span className="dropzone__reticle dropzone__reticle--br" />

      <span className="dropzone__mark">
        {busy ? <span className="spinner spinner--lg" /> : <Icon name="upload" size={24} />}
      </span>

      <span className="dropzone__title">{busy ? 'Ingesting…' : title}</span>

      <span className="dropzone__sub">
        {hint ?? (
          <>
            The file is hashed with SHA-256 at ingest and stored unmodified, so the digest continues to
            describe the bytes on disk. The media type is decided by sniffing the file's own bytes; a
            declared MIME type never overrides it.
          </>
        )}
      </span>

      <span className="dropzone__formats">
        {published ? (
          families
            .filter((f) => (f.list?.length ?? 0) > 0)
            .map((f) => (
              <StatusPill key={f.key} tone="neutral" title={(f.list ?? []).join(', ')}>
                {f.label}: {(f.list ?? []).slice(0, 4).join(' · ')}
                {(f.list?.length ?? 0) > 4 ? ` +${(f.list?.length ?? 0) - 4}` : ''}
              </StatusPill>
            ))
        ) : (
          <StatusPill tone="neutral">Image, video and audio · exact extension list unpublished</StatusPill>
        )}
        {typeof maxBytes === 'number' ? (
          <StatusPill tone="neutral">Max {formatBytes(maxBytes)}</StatusPill>
        ) : null}
      </span>

      <input
        ref={input}
        type="file"
        className="sr-only"
        accept={accept}
        multiple
        disabled={disabled || busy}
        onChange={(e) => {
          take(e.target.files)
          e.target.value = ''
        }}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
      />
    </div>
  )
}

