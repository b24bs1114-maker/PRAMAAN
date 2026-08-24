/**
 * The single fetch wrapper. No component calls fetch() directly.
 *
 * Responsibilities:
 *   - prefix every path with the configured base URL
 *   - enforce a timeout via AbortController
 *   - translate every non-2xx status and every transport failure into ApiError
 *   - read the backend's uniform { error, request_id } envelope
 *
 * Upload uses XMLHttpRequest rather than fetch, purely because fetch cannot
 * report upload progress. Everything else uses fetch.
 */

import { apiUrl, REQUEST_TIMEOUT_MS } from './config'
import { ApiError, kindForStatus } from './errors'
import type { ApiErrorEnvelope } from './types'

/** Pull the message/type/details out of the backend's error envelope. */
function readEnvelope(body: unknown): {
  message: string | null
  type: string | null
  requestId: string | null
  details: ApiErrorEnvelope['error']['details'] | null
} {
  if (!body || typeof body !== 'object') {
    return { message: null, type: null, requestId: null, details: null }
  }
  const env = body as Partial<ApiErrorEnvelope>
  return {
    message: env.error?.message ?? null,
    type: env.error?.type ?? null,
    requestId: env.request_id ?? null,
    details: env.error?.details ?? null,
  }
}

async function toApiError(response: Response, url: string): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await response.json()
  } catch {
    // Non-JSON error body (e.g. a proxy's HTML 502). Fall through to status text.
  }
  const { message, type, requestId, details } = readEnvelope(body)
  return new ApiError({
    kind: kindForStatus(response.status),
    status: response.status,
    message: message || response.statusText || `HTTP ${response.status}`,
    url,
    type,
    requestId: requestId ?? response.headers.get('X-Request-ID'),
    details,
  })
}

/** Wrap a transport-level failure (backend down, CORS, DNS, TLS, abort). */
function toTransportError(cause: unknown, url: string, aborted: boolean): ApiError {
  if (aborted) {
    return new ApiError({
      kind: 'timeout',
      status: 0,
      message: 'Request timed out.',
      url,
    })
  }
  return new ApiError({
    kind: 'network',
    status: 0,
    message: cause instanceof Error ? cause.message : 'Network request failed.',
    url,
  })
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'
  /** JSON-serialisable request body. */
  json?: unknown
  /** Pre-built body (FormData). Takes precedence over `json`. */
  body?: BodyInit
  signal?: AbortSignal
  timeoutMs?: number
  headers?: Record<string, string>
}

/**
 * Perform a request and parse a JSON response.
 *
 * Throws ApiError on any failure. Never returns a partial or fabricated value.
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const url = apiUrl(path)
  const controller = new AbortController()
  const timeoutMs = options.timeoutMs ?? REQUEST_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  // Honour a caller-supplied signal alongside our timeout.
  const onExternalAbort = () => controller.abort()
  options.signal?.addEventListener('abort', onExternalAbort)

  const headers: Record<string, string> = { Accept: 'application/json', ...options.headers }
  let body: BodyInit | undefined = options.body
  if (body === undefined && options.json !== undefined) {
    body = JSON.stringify(options.json)
    headers['Content-Type'] = 'application/json'
  }

  let response: Response
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      headers,
      body,
      signal: controller.signal,
      // No cookies are sent: the backend runs with
      // PRAMAAN_CORS_ALLOW_CREDENTIALS=false by default, and requesting
      // credentials would make a wildcard CORS origin illegal.
      credentials: 'omit',
      mode: 'cors',
    })
  } catch (cause) {
    throw toTransportError(cause, url, controller.signal.aborted)
  } finally {
    clearTimeout(timer)
    options.signal?.removeEventListener('abort', onExternalAbort)
  }

  if (!response.ok) throw await toApiError(response, url)

  // 204 and other empty bodies.
  if (response.status === 204) return undefined as T
  const text = await response.text()
  if (!text) return undefined as T
  try {
    return JSON.parse(text) as T
  } catch {
    throw new ApiError({
      kind: 'unknown',
      status: response.status,
      message: 'The backend returned a response that could not be parsed as JSON.',
      url,
    })
  }
}

/** Fetch a binary body (the report PDF) as a Blob. */
export async function requestBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const url = apiUrl(path)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), options.timeoutMs ?? REQUEST_TIMEOUT_MS)
  let response: Response
  try {
    response = await fetch(url, {
      method: options.method ?? 'GET',
      headers: options.headers,
      signal: controller.signal,
      credentials: 'omit',
      mode: 'cors',
    })
  } catch (cause) {
    throw toTransportError(cause, url, controller.signal.aborted)
  } finally {
    clearTimeout(timer)
  }
  if (!response.ok) throw await toApiError(response, url)
  return response.blob()
}

export interface UploadProgress {
  loaded: number
  total: number
  /** 0..1, or null when the browser cannot determine the total. */
  fraction: number | null
}

/**
 * multipart/form-data POST with upload progress.
 *
 * Uses XMLHttpRequest because the fetch API exposes no upload progress event.
 * Error translation matches `request()` so callers catch the same ApiError.
 */
export function upload<T>(
  path: string,
  form: FormData,
  opts: { onProgress?: (p: UploadProgress) => void; signal?: AbortSignal } = {},
): Promise<T> {
  const url = apiUrl(path)
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', url, true)
    xhr.responseType = 'text'
    xhr.timeout = REQUEST_TIMEOUT_MS
    xhr.setRequestHeader('Accept', 'application/json')

    xhr.upload.onprogress = (event) => {
      opts.onProgress?.({
        loaded: event.loaded,
        total: event.total,
        fraction: event.lengthComputable && event.total > 0 ? event.loaded / event.total : null,
      })
    }

    const failTransport = (kind: 'network' | 'timeout', message: string) =>
      reject(new ApiError({ kind, status: 0, message, url }))

    xhr.onerror = () =>
      failTransport(
        'network',
        'Network request failed. The backend may be unreachable or blocking this origin (CORS).',
      )
    xhr.ontimeout = () => failTransport('timeout', 'Upload timed out.')
    xhr.onabort = () => failTransport('timeout', 'Upload was cancelled.')

    xhr.onload = () => {
      let parsed: unknown = null
      try {
        parsed = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        // fall through
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(parsed as T)
        return
      }
      const { message, type, requestId, details } = readEnvelope(parsed)
      reject(
        new ApiError({
          kind: kindForStatus(xhr.status),
          status: xhr.status,
          message: message || xhr.statusText || `HTTP ${xhr.status}`,
          url,
          type,
          requestId: requestId ?? xhr.getResponseHeader('X-Request-ID'),
          details,
        }),
      )
    }

    opts.signal?.addEventListener('abort', () => xhr.abort())
    xhr.send(form)
  })
}
