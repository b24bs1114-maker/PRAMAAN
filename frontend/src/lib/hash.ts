/**
 * Local integrity hashing.
 *
 * The workflow specification requires the file's hash to appear the moment it
 * lands on the intake panel, before any analysis runs. That hash is computed
 * here, in the browser, from the bytes the operator selected.
 *
 * This is a real measurement, not a preview of the backend's answer. Showing
 * both and comparing them is the point: if the locally computed digest matches
 * the one the backend independently computed on receipt, the bytes survived the
 * network unchanged.
 */

/** Web Crypto needs a secure context. localhost and 127.0.0.1 both qualify. */
export function canHashLocally(): boolean {
  return typeof crypto !== 'undefined' && typeof crypto.subtle?.digest === 'function'
}

export async function sha256Hex(file: Blob): Promise<string> {
  const buffer = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buffer)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}
