import type { ReactNode } from 'react'

/** Indeterminate activity indicator. Always paired with visible label text. */
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="spinner" aria-hidden="true" />
      {label ? (
        <span className="muted" style={{ fontSize: 13 }}>
          {label}
        </span>
      ) : null}
      <span className="visually-hidden" role="status">
        {label ?? 'Working'}
      </span>
    </span>
  )
}

/**
 * Placeholder for a section with nothing to show.
 *
 * The caller supplies the sentence, because "empty" is never self-explanatory
 * in this product: no matches found means "no prior instance found in the
 * indexed corpus", not "this file is original".
 */
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}
