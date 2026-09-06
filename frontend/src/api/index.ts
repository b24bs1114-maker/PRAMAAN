/**
 * Public surface of the API layer.
 *
 * Components import from `../api` only. This keeps the base URL, transport and
 * error model in one place, per the integration brief's requirement that
 * fetch() calls are not scattered through React components.
 */

export * as api from './client'
export { API_BASE_URL, API_BASE_URL_IS_EXPLICIT, MAX_UPLOAD_BYTES, apiUrl } from './config'
export { ApiError, type ApiErrorKind } from './errors'
// The token seam. Only `useAuth` should call these: it owns persistence and is the
// one place that decides who is signed in. Everything else reads the operator from
// that hook, never from the transport.
export { getAuthToken, setAuthToken, setUnauthorizedHandler } from './http'
export type { UploadProgress } from './http'
export type * from './types'
