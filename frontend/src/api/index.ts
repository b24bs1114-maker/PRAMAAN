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
export type { UploadProgress } from './http'
export type * from './types'
