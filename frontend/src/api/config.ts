/**
 * API base URL resolution -- the single place the backend origin is defined.
 *
 * No component may import a URL string. Everything goes through here so that
 * moving between local development and a deployed backend is one environment
 * variable, never a code edit.
 */

/**
 * Backend origin, from VITE_API_URL.
 *
 * Falls back to the documented local development address so a fresh clone runs
 * with no .env file. The fallback is a localhost address by design -- it can
 * never accidentally point a developer's browser at production.
 */
const RAW_BASE: string =
  (import.meta.env.VITE_API_URL as string | undefined)?.trim() || '/api/backend'

/** Base URL with any trailing slash removed, so path joining is unambiguous. */
export const API_BASE_URL: string = RAW_BASE.replace(/\/+$/, '')

/** True when VITE_API_URL was explicitly provided rather than defaulted. */
export const API_BASE_URL_IS_EXPLICIT: boolean = Boolean(
  (import.meta.env.VITE_API_URL as string | undefined)?.trim(),
)

if (typeof window !== 'undefined') {
  console.info(
    '[PRAMAAN API] Configured Base URL:',
    API_BASE_URL,
    API_BASE_URL_IS_EXPLICIT ? '(from VITE_API_URL)' : '(default /api/backend)',
  )
}

/**
 * Join a backend-relative path onto the base URL.
 *
 * Accepts both "/api/..." and "api/...", and passes absolute URLs through
 * untouched -- the backend returns `download_url` as a relative path, and this
 * keeps that usable without special-casing at each call site.
 */
export function apiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path
  return `${API_BASE_URL}/${path.replace(/^\/+/, '')}`
}

/** Request timeout in ms. Analysis is the slowest call; keep headroom. */
export const REQUEST_TIMEOUT_MS = 120_000

/** Upload size ceiling, mirroring the backend's PRAMAAN_MAX_UPLOAD_BYTES default (64 MiB). */
export const MAX_UPLOAD_BYTES = 64 * 1024 * 1024
