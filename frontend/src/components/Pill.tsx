import type { ReactNode } from 'react'
import type { PillVariant } from '../lib/signals'

/** Every variant a Pill accepts: the six signal states plus neutral, ok, error, warn tones. */
export type PillTone = PillVariant | 'info' | 'accent' | 'ok' | 'error' | 'warn'

/**
 * Monospace status chip, matching the `.st-*` treatment in the specification.
 *
 * The `unavailable` variant is dashed and unfilled by design: an absent signal
 * must not look like a measured one.
 */
export function Pill({
  variant,
  children,
  title,
}: {
  variant: PillTone
  children: ReactNode
  title?: string
}) {
  return (
    <span className={`pill pill--${variant}`} title={title}>
      {children}
    </span>
  )
}
