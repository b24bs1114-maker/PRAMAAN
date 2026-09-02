import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

/**
 * Copy-to-clipboard control.
 *
 * The workstation shows hashes and IDs in a shortened form for legibility; the
 * full value is never lost - it is one click away here. Briefly confirms the
 * copy so the operator knows the whole value (not the truncated display) landed
 * on the clipboard.
 */
export function CopyButton({
  value,
  label = 'Copy',
  title,
}: {
  value: string
  label?: string
  title?: string
}) {
  const [copied, setCopied] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be denied (insecure context / permissions).
      // Fail silently rather than interrupt the operator.
    }
  }

  return (
    <button
      type="button"
      className="btn btn--ghost btn--sm"
      onClick={copy}
      title={title ?? `Copy ${value}`}
      aria-label={title ?? `Copy full value`}
      style={copied ? { color: 'var(--ok-bright)', borderColor: 'var(--ok-line)', background: 'var(--ok-wash)' } : undefined}
    >
      <span style={copied ? { animation: 'popCheck 200ms cubic-bezier(0.16, 1, 0.3, 1)', display: 'inline-flex' } : undefined}>
        <Icon name={copied ? 'check' : 'document'} size={13} />
      </span>
      <span>{copied ? 'Copied' : label}</span>
    </button>
  )
}
