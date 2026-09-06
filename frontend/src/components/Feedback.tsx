import type { ReactNode } from 'react'

/**
 * Indeterminate activity indicator. Always paired with visible label text.
 *
 * The label is announced exactly once. It used to be rendered twice -- visibly,
 * and again inside a `role="status"` live region -- so a screen reader read
 * "Querying evidence repository… Querying evidence repository…", and a DOM or
 * accessibility-tree reading of any loading screen showed the doubled string. The
 * visible text *is* the live region now; the hidden span is the fallback for the
 * unlabelled case, where there would otherwise be nothing to announce at all.
 */
export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="spinner" aria-hidden="true" />
      {label ? (
        <span className="muted" style={{ fontSize: 13 }} role="status">
          {label}
        </span>
      ) : (
        <span className="visually-hidden" role="status">
          Working
        </span>
      )}
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

/**
 * What a case-scoped screen shows when no case is open.
 *
 * Four screens -- Analysis, Provenance, Audit and Case Detail -- can only
 * describe one case, and each had grown its own version of this: four wordings,
 * two spellings of the button ("View Cases" and "View cases") and two different
 * stack gaps. Reached in sequence they read as four different tools. There is one
 * of them now, and the only thing a caller varies is the clause naming what this
 * particular screen would have done with a case, because that is the only part
 * that legitimately differs.
 *
 * The button is the primary action because it is the *only* action: there is
 * nothing else to do on this screen until a case is chosen.
 */
export function NoCaseSelected({
  /** Completes "Open an active investigation to ..." -- e.g. "trace its provenance". */
  purpose,
  onViewCases,
}: {
  purpose: string
  onViewCases: () => void
}) {
  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <Empty>No case is selected. Open an active investigation to {purpose}.</Empty>
      <div className="btn-row">
        <button type="button" className="btn btn--primary" onClick={onViewCases}>
          View Cases
        </button>
      </div>
    </div>
  )
}
