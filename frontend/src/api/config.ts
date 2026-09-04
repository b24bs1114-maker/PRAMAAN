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
 * The fallback is a SAME-ORIGIN PATH, not a localhost address, and not a no-op:
 * `/api/backend/*` is rewritten to the deployed backend by `vercel.json`, and
 * proxied to `VITE_API_URL || http://127.0.0.1:8000` by the Vite dev server. So
 * a fresh clone works with no .env file, and a reviewer whose environment blocks
 * cross-origin requests can run the app entirely against one origin.
 *
 * What that means, and what the previous comment here claimed the opposite of:
 * leaving VITE_API_URL unset on Vercel does NOT keep the browser local. It
 * follows the rewrite to whatever host `vercel.json` names -- the production
 * backend. Set VITE_API_URL explicitly when you need to be certain which
 * backend you are talking to; API_BASE_URL_IS_EXPLICIT below reports whether
 * anyone did.
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
