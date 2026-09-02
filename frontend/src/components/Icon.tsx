import type { CSSProperties } from 'react'

/**
 * The icon set.
 *
 * Every glyph is authored here as an SVG on a 24-unit grid with a single stroke
 * weight, so the interface has one consistent icon voice. This deliberately
 * replaces the Unicode characters (✓ ⚠ ◐ ✕ ℹ ◆ ■ ●) the console used to lean
 * on: those inherit the reader's emoji font and render at a different weight,
 * baseline and colour on every platform, which is exactly the "assembled, not
 * built" tell a forensic tool cannot afford.
 *
 * Icons take colour from `currentColor`, so a caller sets the hue by setting
 * `color` on the icon or its container. Shape - never colour alone - carries
 * the meaning, so a greyscale print or a colour-blind reader loses nothing.
 */

export type IconName =
  | 'check'
  | 'alert'
  | 'inconclusive'
  | 'info'
  | 'error'
  | 'close'
  | 'shield'
  | 'upload'
  | 'download'
  | 'arrow-right'
  | 'arrow-left'
  | 'refresh'
  | 'external'
  | 'lock'
  | 'clock'
  | 'document'
  | 'search'
  | 'settings'
  | 'diamond'
  | 'square'
  | 'dot'

// Line paths for the stroked icons. Filled shape icons are handled separately.
const PATHS: Partial<Record<IconName, string>> = {
  check: 'M20 6 9 17l-5-5',
  alert: 'M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0zM12 9v4M12 17h.01',
  info: 'M12 16v-4M12 8h.01',
  error: 'M15 9l-6 6M9 9l6 6',
  close: 'M18 6 6 18M6 6l12 12',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
  download: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3',
  'arrow-right': 'M5 12h14M13 6l6 6-6 6',
  'arrow-left': 'M19 12H5M11 18l-6-6 6-6',
  refresh: 'M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6',
  external: 'M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6',
  lock: 'M7 10V7a5 5 0 0 1 10 0v3M12 15v2',
  clock: 'M12 7v5l3 2',
  document: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6',
  search: 'M18 11a7 7 0 1 1-14 0 7 7 0 0 1 14 0M21 21l-4.3-4.3',
  settings: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6',
}

export function Icon({
  name,
  size = 18,
  strokeWidth = 1.75,
  className,
  style,
  title,
}: {
  name: IconName
  size?: number
  strokeWidth?: number
  className?: string
  style?: CSSProperties
  title?: string
}) {
  const common = {
    width: size,
    height: size,
    viewBox: '0 0 24 24',
    className,
    style: { flex: 'none', ...style },
    'aria-hidden': title ? undefined : true,
    role: title ? 'img' : undefined,
    'aria-label': title,
  } as const

  // Filled semantic marks (shape carries meaning without a stroke).
  if (name === 'diamond') {
    return (
      <svg {...common} fill="currentColor" stroke="none">
        {title ? <title>{title}</title> : null}
        <path d="M12 3l9 9-9 9-9-9 9-9z" />
      </svg>
    )
  }
  if (name === 'square') {
    return (
      <svg {...common} fill="currentColor" stroke="none">
        {title ? <title>{title}</title> : null}
        <rect x="4" y="4" width="16" height="16" rx="2" />
      </svg>
    )
  }
  if (name === 'dot') {
    return (
      <svg {...common} fill="currentColor" stroke="none">
        {title ? <title>{title}</title> : null}
        <circle cx="12" cy="12" r="7" />
      </svg>
    )
  }

  // The inconclusive mark: an outlined circle with its left half filled - the
  // half-tone reads as "partial / undecided" in any colour or in greyscale.
  if (name === 'inconclusive') {
    return (
      <svg {...common} fill="none" stroke="currentColor" strokeWidth={strokeWidth}>
        {title ? <title>{title}</title> : null}
        <circle cx="12" cy="12" r="9" />
        <path d="M12 3a9 9 0 0 0 0 18z" fill="currentColor" stroke="none" />
      </svg>
    )
  }

  // Marks that pair a ringed enclosure with an inner glyph.
  const ringed = name === 'info' || name === 'error'
  const shield = name === 'shield'

  return (
    <svg
      {...common}
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {title ? <title>{title}</title> : null}
      {ringed ? <circle cx="12" cy="12" r="9" /> : null}
      {shield ? (
        <>
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
          <path d="M9 12l2 2 4-4" />
        </>
      ) : (
        <path d={PATHS[name]} />
      )}
    </svg>
  )
}
