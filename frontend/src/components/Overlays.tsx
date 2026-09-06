/**
 * Modal overlay.
 *
 * Behaviour contract an investigator learns once: Escape closes, the backdrop
 * closes, focus moves in on open and is trapped until close, focus returns to
 * whatever opened the overlay, and the page behind does not scroll.
 *
 * Exit is animated rather than instantaneous -- `usePresence` holds the node
 * mounted for one duration step while the `--leaving` class runs. Under
 * `prefers-reduced-motion` that step is skipped entirely by the hook, so nobody
 * waits on choreography they asked not to see.
 */

import { useEffect, useId, useRef, type ReactNode } from 'react'
import { cx } from '../lib/cx'
import { usePresence } from '../lib/motion'

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])'

/**
 * Escape, scroll lock, focus trap and focus restoration for one overlay.
 *
 * Returns the ref to put on the panel. Anything inside the panel marked
 * `data-autofocus` receives focus on open; otherwise the panel itself does.
 */
function useOverlay(active: boolean, onClose: () => void) {
  const panel = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!active) return
    const previous = document.activeElement as HTMLElement | null
    return () => {
      if (previous && document.contains(previous)) previous.focus()
    }
  }, [active])

  // Depth-counted so closing an inner overlay does not unlock the page while an
  // outer one is still open.
  useEffect(() => {
    if (!active) return
    const body = document.body
    body.dataset.overlayDepth = String(Number(body.dataset.overlayDepth ?? '0') + 1)
    body.style.overflow = 'hidden'
    return () => {
      const next = Number(body.dataset.overlayDepth ?? '1') - 1
      if (next > 0) {
        body.dataset.overlayDepth = String(next)
      } else {
        delete body.dataset.overlayDepth
        body.style.overflow = ''
      }
    }
  }, [active])

  useEffect(() => {
    if (!active) return
    const node = panel.current
    if (!node) return

    const auto = node.querySelector<HTMLElement>('[data-autofocus]')
    ;(auto ?? node).focus()

    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') return

      const stops = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      )
      if (stops.length === 0) {
        event.preventDefault()
        node.focus()
        return
      }
      const first = stops[0]
      const last = stops[stops.length - 1]
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
        event.preventDefault()
        last.focus()
      }
    }

    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [active, onClose])

  return panel
}

/** Centred dialog for a short, focused decision or a small form. */
export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  subtitle,
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  eyebrow?: string
  subtitle?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  const { mounted, leaving } = usePresence(open, 180)
  const panel = useOverlay(mounted && !leaving, onClose)
  const titleId = useId()

  if (!mounted) return null

  return (
    <div className="modal">
      <div className={cx('scrim', leaving && 'scrim--leaving')} onClick={onClose} />
      <div
        ref={panel}
        className={cx('modal__panel', leaving && 'modal__panel--leaving')}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal__head">
          {eyebrow ? <div className="modal__eyebrow">{eyebrow}</div> : null}
          <h2 className="modal__title" id={titleId}>
            {title}
          </h2>
          {subtitle ? <div className="modal__sub">{subtitle}</div> : null}
        </div>
        <div className="modal__body">{children}</div>
        {footer ? <div className="modal__foot">{footer}</div> : null}
      </div>
    </div>
  )
}
