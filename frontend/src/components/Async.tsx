/**
 * Async state rendering.
 *
 * Every remote call in this app lands in a `Slice<T>` with four phases, and each
 * phase has exactly one honest presentation:
 *
 *   idle     the call has not been made. Show what would make it happen -- never
 *            a spinner, which would claim work is underway.
 *   loading  work is underway. Show what is being computed.
 *   error    the call failed. Show the backend's own reason, the request id if
 *            there is one, and a retry only when retrying could plausibly work.
 *   ready    render the data.
 *
 * The failure mode this file exists to prevent: an error rendered as an empty
 * result. "No propagation links found" and "the propagation request failed" are
 * different forensic statements and must never share a presentation.
 */

import type { ReactNode } from 'react'
import { ApiError } from '../api'
import type { Slice } from '../state/useInvestigation'
import { Banner, Button, Empty, LoadingRow } from './Primitives'

/** The operator-facing sentence for any thrown value. */
export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.userMessage
  if (error instanceof Error && error.message) return error.message
  return 'An unexpected error occurred.'
}

/** The backend request id, when the failure carried one. */
export function errorRequestId(error: unknown): string | null {
  return error instanceof ApiError ? error.requestId : null
}

/** Whether retrying the identical request could plausibly succeed. */
export function canRetry(error: unknown): boolean {
  return error instanceof ApiError ? error.isRetryable : false
}

/** True when nothing reached the backend at all -- a different fix for the operator. */
export function isUnreachable(error: unknown): boolean {
  return error instanceof ApiError ? error.isBackendUnreachable : false
}

/**
 * A failed call, stated as a failure.
 *
 * `title` names the operation that failed, so the operator knows what is missing
 * from the case rather than just that "something went wrong".
 */
export function ErrorState({
  error,
  title,
  onRetry,
  retryLabel = 'Retry',
}: {
  error: unknown
  title: string
  onRetry?: () => void
  retryLabel?: string
}) {
  const requestId = errorRequestId(error)
  const retryable = canRetry(error)
  return (
    <Banner
      kind="error"
      title={title}
      meta={
        requestId ? (
          <>
            Quote request id <span className="mono">{requestId}</span> when reporting this.
          </>
        ) : null
      }
      actions={
        onRetry && retryable ? (
          <Button size="sm" icon="refresh" onClick={onRetry}>
            {retryLabel}
          </Button>
        ) : null
      }
    >
      {errorText(error)}
    </Banner>
  )
}

/**
 * Renders one `Slice<T>` across all four phases.
 *
 * `children` only ever runs with data that actually arrived, so no screen needs
 * a `data!` or a `?? 0` to satisfy the type checker -- which is how null values
 * used to get coerced into zeros further down.
 */
export function SliceView<T>({
  slice,
  what,
  idle,
  loading,
  onRetry,
  children,
}: {
  slice: Slice<T>
  /** Names the operation, used in the loading line and the error title. */
  what: string
  /** Shown before the call is made -- normally the button that would make it. */
  idle?: ReactNode
  /** Overrides the default loading line, e.g. with skeletons of the real shape. */
  loading?: ReactNode
  onRetry?: () => void
  children: (data: T) => ReactNode
}) {
  if (slice.phase === 'loading') {
    return <>{loading ?? <LoadingRow label={`${what}…`} />}</>
  }
  if (slice.phase === 'error') {
    return <ErrorState error={slice.error} title={`${what} failed`} onRetry={onRetry} />
  }
  if (slice.phase === 'ready' && slice.data !== null) {
    return <>{children(slice.data)}</>
  }
  return <>{idle ?? <Empty tight icon="clock" title="Not run yet" detail={`${what} has not been requested for this case.`} />}</>
}
