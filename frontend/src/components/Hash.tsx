/**
 * SHA-256 presentation.
 *
 * A hash is the identity of a piece of evidence and the thing an investigator
 * will read out loud in court, so it gets a deliberate control rather than being
 * dropped into a table cell: monospace, truncated in the middle (both ends are
 * what people compare), copyable, and with the full value available on hover
 * and to a screen reader.
 *
 * The truncation is display-only. `title` and the clipboard always carry all 64
 * characters -- a shortened hash must never be the only copy on screen.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { cx } from '../lib/cx'
import { shortHash } from '../lib/format'

/** Clipboard write with a "copied" flag that resets itself. */
export function useCopy(): { copied: boolean; copy: (text: string) => void } {
  const [copied, setCopied] = useState(false)
  const timer = useRef<number | null>(null)

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current)
    },
    [],
  )

  const copy = useCallback((text: string) => {
    const finish = () => {
      setCopied(true)
      if (timer.current !== null) window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setCopied(false), 1400)
    }

    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(finish, () => {})
      return
    }
    // Fallback for insecure origins, where navigator.clipboard is undefined.
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    try {
      document.execCommand('copy')
      finish()
    } catch {
      /* nothing to report: the value is still visible on screen */
    }
    document.body.removeChild(area)
  }, [])

  return { copied, copy }
}

export function CopyButton({
  value,
  label = 'Copy',
  size = 13,
}: {
  value: string
  label?: string
  size?: number
}) {
  const { copied, copy } = useCopy()
  return (
    <button
      type="button"
      className={cx('hash__copy', copied && 'hash__copy--done')}
      onClick={() => copy(value)}
      aria-label={copied ? `${label} — copied` : label}
      title={copied ? 'Copied' : label}
    >
      <Icon name={copied ? 'check' : 'copy'} size={size} />
    </button>
  )
}

export function HashChip({
  value,
  algo = 'SHA-256',
  length = 16,
  block,
  className,
}: {
  value: string | null | undefined
  algo?: string | null
  /** Characters shown; the full value stays in `title` and on the clipboard. */
  length?: number
  block?: boolean
  className?: string
}) {
  if (!value) {
    return (
      <span className={cx('hash', block && 'hash--block', className)}>
        {algo ? <span className="hash__algo">{algo}</span> : null}
        <span className="hash__value unmeasured">Not recorded</span>
      </span>
    )
  }

  return (
    <span className={cx('hash', block && 'hash--block', className)} title={value}>
      {algo ? <span className="hash__algo">{algo}</span> : null}
      <span className="hash__value">{block ? value : shortHash(value, length)}</span>
      <CopyButton value={value} label={`Copy ${algo ?? 'hash'}`} />
    </span>
  )
}
