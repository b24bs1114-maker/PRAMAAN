import type { ReactNode } from 'react'
import { ApiError } from '../api'
import { Icon, type IconName } from './Icon'

type BannerTone = 'info' | 'ok' | 'warn' | 'error'

// Shape carries the meaning; the tone class only tints it. A greyscale render
// still distinguishes a cross from a tick from a triangle.
const TONE_ICON: Record<BannerTone, IconName> = {
  info: 'info',
  ok: 'check',
  warn: 'alert',
  error: 'error',
}

/**
 * Inline notice. Used for every failure path, and for the states that are not
 * failures but must still be stated out loud (a duplicate upload, a detector
 * that is not installed).
 */
export function Banner({
  tone = 'info',
  title,
  detail,
  meta,
  children,
}: {
  tone?: BannerTone
  title: ReactNode
  detail?: ReactNode
  meta?: ReactNode
  children?: ReactNode
}) {
  return (
    <div
      className={`banner banner--${tone}`}
      // Errors and warnings are announced; informational notices are not, to
      // avoid interrupting a screen reader mid-task.
      role={tone === 'error' || tone === 'warn' ? 'alert' : 'status'}
    >
      <Icon name={TONE_ICON[tone]} className="banner__icon" size={17} />
      <div className="banner__body">
        <div className="banner__title">{title}</div>
        {detail ? <div className="banner__detail">{detail}</div> : null}
        {children}
        {meta ? <div className="banner__meta">{meta}</div> : null}
      </div>
    </div>
  )
}

/**
 * Render an ApiError.
 *
 * Always shows the backend's own message and, when present, the request id --
 * that is the string an operator quotes when reporting a fault. Nothing is
 * softened into a generic "something went wrong", and no fallback data is shown
 * in place of the failure.
 */
export function ErrorBanner({
  error,
  onRetry,
  context,
}: {
  error: unknown
  onRetry?: () => void
  /** What was being attempted, e.g. "Analysis". */
  context?: string
}) {
  const isApi = error instanceof ApiError
  const heading = context ? `${context} failed` : 'Request failed'
  const message = isApi
    ? error.userMessage
    : error instanceof Error
      ? error.message
      : 'An unexpected error occurred.'

  const metaParts: string[] = []
  if (isApi) {
    if (error.status) metaParts.push(`HTTP ${error.status}`)
    if (error.type) metaParts.push(error.type)
    if (error.requestId) metaParts.push(`request ${error.requestId}`)
  }

  return (
    <Banner
      tone="error"
      title={heading}
      detail={message}
      meta={metaParts.length ? metaParts.join(' · ') : undefined}
    >
      {isApi && error.isRetryable && onRetry ? (
        <div className="btn-row" style={{ marginTop: 10 }}>
          <button type="button" className="btn btn--ghost" onClick={onRetry}>
            <Icon name="refresh" size={15} />
            Retry
          </button>
        </div>
      ) : null}
    </Banner>
  )
}
