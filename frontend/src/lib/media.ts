/**
 * Evidence media URLs.
 *
 * The backend streams the stored bytes of one evidence item from
 * `GET /api/evidence/{id}/file` (append `?download=true` to force a download
 * rather than an inline preview). This is the only correct source for a preview
 * - never the JSON list endpoint. Reads here are plain GETs and write no audit
 * rows.
 */
import { apiUrl } from '../api'

export function evidenceFileUrl(evidenceId: string, opts: { download?: boolean } = {}): string {
  const query = opts.download ? '?download=true' : ''
  return apiUrl(`/api/evidence/${encodeURIComponent(evidenceId)}/file${query}`)
}

/** Only images can be shown inline in an <img>; video/audio fall back to an icon. */
export function isImageMedia(mediaType: string | null | undefined): boolean {
  return mediaType === 'image'
}
