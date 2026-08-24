import { useEffect, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './Icon'

/**
 * Right-side slide-in panel for RAW / DETAILS content that would otherwise
 * clutter the primary view — signal internals, per-detector metadata. The
 * primary view answers "what did PRAMAAN find?"; the drawer holds the
 * "how do you know?" for the operator who asks.
 *
 * Renders into <body> so it escapes any transformed/overflow-clipped ancestor,
 * closes on backdrop click and Escape.
 */
export function Drawer({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  return createPortal(
    <div className="drawer__backdrop" onClick={onClose}>
      <div
        className="drawer__panel"
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === 'string' ? title : 'Details'}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="drawer__head">
          <span style={{ fontWeight: 600, fontSize: 'var(--text-md)' }}>{title}</span>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={onClose}
            aria-label="Close details"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
        <div className="drawer__body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
