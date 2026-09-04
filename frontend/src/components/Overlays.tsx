/**
 * Overlay layer: drawer, modal, command palette.
 *
 * All three share one behaviour contract, because an investigator who learns it
 * once should not have to relearn it: Escape closes, the backdrop closes, focus
 * moves in on open and is trapped until close, focus returns to whatever opened
 * the overlay, and the page behind does not scroll.
 *
 * Exit is animated rather than instantaneous -- `usePresence` holds the node
 * mounted for one duration step while the `--leaving` class runs. Under
 * `prefers-reduced-motion` that step is skipped entirely by the hook, so nobody
 * waits on choreography they asked not to see.
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { Icon, type IconName } from './Icon'
import { cx } from '../lib/cx'
import { usePresence } from '../lib/motion'
import { Button, Empty, Kbd, StatusPill } from './Primitives'
import type { Tone } from '../lib/tone'

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

/**
 * Right-hand sheet for a detail view that must not lose the list behind it --
 * evidence inspection from a case, a signal's full explanation, an audit row.
 */
export function Drawer({
  open,
  onClose,
  title,
  eyebrow,
  subtitle,
  wide,
  footer,
  children,
}: {
  open: boolean
  onClose: () => void
  title: string
  eyebrow?: string
  subtitle?: ReactNode
  wide?: boolean
  footer?: ReactNode
  children: ReactNode
}) {
  const { mounted, leaving } = usePresence(open, 200)
  const panel = useOverlay(mounted && !leaving, onClose)
  const titleId = useId()

  if (!mounted) return null

  return (
    <div className="drawer">
      <div className={cx('scrim', leaving && 'scrim--leaving')} onClick={onClose} />
      <div
        ref={panel}
        className={cx(
          'drawer__panel',
          wide && 'drawer__panel--wide',
          leaving && 'drawer__panel--leaving',
        )}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="drawer__head">
          <div className="drawer__titles">
            {eyebrow ? <div className="drawer__eyebrow">{eyebrow}</div> : null}
            <h2 className="drawer__title" id={titleId}>
              {title}
            </h2>
            {subtitle ? <div className="drawer__sub">{subtitle}</div> : null}
          </div>
          <Button variant="bare" icon="close" iconOnly aria-label="Close" onClick={onClose} />
        </div>
        <div className="drawer__body">{children}</div>
        {footer ? <div className="drawer__foot">{footer}</div> : null}
      </div>
    </div>
  )
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

/**
 * One command-palette row.
 *
 * `href` and `run` are exclusive: a row that navigates renders an anchor, so
 * cmd-click and "copy link" work; a row that performs an action renders a
 * button. `sub` is where the honest qualifier goes -- a case row says how many
 * evidence items it holds, not what the verdict "probably" is.
 */
export interface PaletteItem {
  id: string
  group: string
  title: string
  sub?: string | null
  tag?: { label: string; tone: Tone } | null
  icon?: IconName
  href?: string
  run?: () => void
  /** Matched by the query but never displayed -- case numbers, hashes, ids. */
  keywords?: string
}

/** Substring match over title, subtitle and keywords. No fuzzy guessing. */
function matches(item: PaletteItem, needle: string): boolean {
  if (!needle) return true
  const haystack = `${item.title} ${item.sub ?? ''} ${item.keywords ?? ''}`.toLowerCase()
  return needle
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => haystack.includes(term))
}

/**
 * Cmd/Ctrl-K palette.
 *
 * It searches only the rows the caller passes in, which are only ever rows the
 * backend has already returned in this session. It does not query the server on
 * keystroke and it never offers a case or an evidence item it has not seen, so
 * an empty result means "not in what is loaded", which the footer states.
 */
export function CommandPalette({
  open,
  onClose,
  items,
  placeholder = 'Search screens, cases and evidence…',
  note,
}: {
  open: boolean
  onClose: () => void
  items: PaletteItem[]
  placeholder?: string
  /** What this palette can and cannot see. Shown in the footer, verbatim. */
  note?: string
}) {
  const { mounted, leaving } = usePresence(open, 150)
  const panel = useOverlay(mounted && !leaving, onClose)
  const listId = useId()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const list = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setActive(0)
    }
  }, [open])

  const filtered = useMemo(() => items.filter((item) => matches(item, query)), [items, query])
  const index = filtered.length === 0 ? -1 : Math.min(active, filtered.length - 1)

  useEffect(() => {
    list.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [index, query])

  const activate = useCallback(
    (item: PaletteItem | undefined) => {
      if (!item) return
      onClose()
      if (item.run) item.run()
      else if (item.href) window.location.hash = item.href.replace(/^#/, '')
    },
    [onClose],
  )

  const onKey = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((n) => (filtered.length === 0 ? 0 : (Math.min(n, filtered.length - 1) + 1) % filtered.length))
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((n) =>
        filtered.length === 0 ? 0 : (Math.min(n, filtered.length - 1) - 1 + filtered.length) % filtered.length,
      )
    } else if (event.key === 'Home') {
      event.preventDefault()
      setActive(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      setActive(Math.max(filtered.length - 1, 0))
    } else if (event.key === 'Enter') {
      event.preventDefault()
      activate(filtered[index])
    }
  }

  if (!mounted) return null

  const body = (item: PaletteItem) => (
    <>
      <Icon name={item.icon ?? 'arrow-right'} size={15} className="palette__item-icon" />
      <span className="palette__item-main">
        <span className="palette__item-title">{item.title}</span>
        {item.sub ? <span className="palette__item-sub">{item.sub}</span> : null}
      </span>
      {item.tag ? (
        <StatusPill tone={item.tag.tone} className="palette__item-tag">
          {item.tag.label}
        </StatusPill>
      ) : null}
    </>
  )

  return (
    <div className="palette-root">
      <div className={cx('scrim', leaving && 'scrim--leaving')} onClick={onClose} />
      <div
        ref={panel}
        className={cx('palette', leaving && 'palette--leaving')}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        tabIndex={-1}
      >
        <div className="palette__field">
          <Icon name="search" size={17} style={{ color: 'var(--text-faint)' }} />
          <input
            className="palette__input"
            data-autofocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            onKeyDown={onKey}
            placeholder={placeholder}
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={index >= 0 ? `${listId}-${index}` : undefined}
            spellCheck={false}
            autoComplete="off"
          />
          <Kbd>ESC</Kbd>
        </div>

        <div className="palette__list" id={listId} role="listbox" ref={list} aria-label="Results">
          {filtered.length === 0 ? (
            <Empty
              tight
              icon="search"
              title="Nothing loaded matches that"
              detail={
                query
                  ? `No screen, case or evidence item currently in this session matches "${query}".`
                  : 'Nothing is loaded to search yet.'
              }
            />
          ) : (
            filtered.map((item, i) => (
              <Fragment key={item.id}>
                {i === 0 || filtered[i - 1].group !== item.group ? (
                  <div className="palette__group-label">{item.group}</div>
                ) : null}
                {item.href ? (
                  <a
                    id={`${listId}-${i}`}
                    className={cx('palette__item', i === index && 'palette__item--active')}
                    href={item.href}
                    role="option"
                    aria-selected={i === index}
                    data-active={i === index}
                    onMouseMove={() => setActive(i)}
                    onClick={onClose}
                  >
                    {body(item)}
                  </a>
                ) : (
                  <button
                    id={`${listId}-${i}`}
                    type="button"
                    className={cx('palette__item', i === index && 'palette__item--active')}
                    role="option"
                    aria-selected={i === index}
                    data-active={i === index}
                    onMouseMove={() => setActive(i)}
                    onClick={() => activate(item)}
                  >
                    {body(item)}
                  </button>
                )}
              </Fragment>
            ))
          )}
        </div>

        <div className="palette__foot">
          <span className="palette__hint">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> move
          </span>
          <span className="palette__hint">
            <Kbd>↵</Kbd> open
          </span>
          {note ? <span className="palette__hint">{note}</span> : null}
        </div>
      </div>
    </div>
  )
}
