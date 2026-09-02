/**
 * Error model for the API layer.
 *
 * Every failure -- HTTP status, network drop, timeout, malformed body -- is
 * normalised into a single ApiError so the UI has exactly one thing to catch
 * and one place that decides what an officer is told.
 *
 * The guiding rule from the brief: the UI must never silently show fake
 * successful data. An ApiError is always surfaced, never swallowed.
 */

export type ApiErrorKind =
  | 'bad_request' // 400 -- rejected file, malformed input
  | 'not_found' // 404 -- unknown case or evidence
  | 'payload_too_large' // 413 -- oversized upload
  | 'validation' // 422 -- FastAPI request validation
  | 'server' // 500+ -- backend fault
  | 'network' // fetch rejected: backend down, DNS, CORS, TLS
  | 'timeout' // aborted by us
  | 'unavailable' // a capability is absent (detector, analysis, index)
  | 'unknown'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  /** HTTP status, or 0 for transport-level failures. */
  readonly status: number
  /** Backend error envelope `error.type`, when present. */
  readonly type: string | null
  /** Backend request id -- quote this when reporting a fault. */
  readonly requestId: string | null
  /** Field-level detail from a 422. */
  readonly details: Array<{ location: unknown[]; message: string; type: string }> | null
  readonly url: string

  constructor(init: {
    kind: ApiErrorKind
    status: number
    message: string
    url: string
    type?: string | null
    requestId?: string | null
    details?: Array<{ location: unknown[]; message: string; type: string }> | null
  }) {
    super(init.message)
    this.name = 'ApiError'
    this.kind = init.kind
    this.status = init.status
    this.url = init.url
    this.type = init.type ?? null
    this.requestId = init.requestId ?? null
    this.details = init.details ?? null
  }

  /** True when the backend could not be reached at all. */
  get isBackendUnreachable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout'
  }

  /** True when retrying unchanged could plausibly succeed. */
  get isRetryable(): boolean {
    return this.kind === 'network' || this.kind === 'timeout' || this.kind === 'server'
  }

  /**
   * Operator-facing sentence.
   *
   * Deliberately specific per kind: the workflow spec requires the interface to
   * say *why* something is unavailable rather than "Analysis failed".
   */
  get userMessage(): string {
    switch (this.kind) {
      case 'network':
        return `Cannot reach the PRAMAAN backend. Confirm it is running and that this origin is listed in PRAMAAN_CORS_ALLOW_ORIGINS.`
      case 'timeout':
        return `The backend did not respond within ${Math.round(
          120_000 / 1000,
        )} seconds. The request was aborted; nothing was recorded.`
      case 'payload_too_large':
        return this.message || 'That file exceeds the maximum upload size.'
      case 'bad_request':
        return this.message || 'The backend rejected that request.'
      case 'not_found':
        return this.message || 'That case or evidence item does not exist on the backend.'
      case 'validation':
        return this.details?.length
          ? `Request rejected: ${this.details
              .map((d) => `${d.location.slice(-1).join('') || 'field'} - ${d.message}`)
              .join('; ')}`
          : 'The backend rejected the request as invalid.'
      case 'server':
        return `${
          this.message || 'The backend encountered an internal error.'
        }${this.requestId ? ` (request id ${this.requestId})` : ''}`
      case 'unavailable':
        return this.message
      default:
        return this.message || 'An unexpected error occurred.'
    }
  }
}

/** Map an HTTP status onto an error kind. */
export function kindForStatus(status: number): ApiErrorKind {
  if (status === 400) return 'bad_request'
  if (status === 404) return 'not_found'
  if (status === 413) return 'payload_too_large'
  if (status === 422) return 'validation'
  if (status >= 500) return 'server'
  return 'unknown'
}
