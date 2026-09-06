/**
 * Which evidence can be shown inline.
 *
 * The bytes themselves are fetched, not linked: `GET /api/evidence/{id}/file`
 * requires the operator's bearer token, and a browser-initiated `<img src>`
 * cannot carry an Authorization header. `components/EvidenceMedia` owns that
 * fetch and the object-URL lifetime; this module only answers the question the
 * screens ask before mounting a preview.
 */

/** Only images can be shown inline in an <img>; video/audio fall back to an icon. */
export function isImageMedia(mediaType: string | null | undefined): boolean {
  return mediaType === 'image'
}
