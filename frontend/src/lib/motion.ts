/**
 * Motion layer.
 *
 * Framer Motion is not installed here and cannot be: the npm registry is
 * blocked in this environment. Rather than drop the motion specification, it is
 * implemented with CSS keyframes (base.css) plus the small amount of state that
 * genuinely needs JavaScript -- exit transitions and staggering.
 *
 * Rules this layer enforces:
 *  - Durations come from the token scale (150-250ms for interface movement),
 *    which `prefers-reduced-motion` collapses to 1ms in CSS. Nothing here needs
 *    to branch on the media query except where a timer is involved.
 *  - Motion never invents a value. There is deliberately no count-up helper:
 *    animating a KPI from 0 to its real figure would display numbers the
 *    backend never returned.
 */

import { useEffect, useRef, useState, type CSSProperties } from 'react'

/** Entrance animation classes, mirroring the keyframes in base.css. */
export const ENTER = {
  fade: 'm-fade',
  rise: 'm-rise',
  riseSm: 'm-rise-sm',
  slideLeft: 'm-slide-left',
  scaleIn: 'm-scale-in',
} as const

export type EnterVariant = keyof typeof ENTER

/** True when the user has asked the OS to reduce motion. Live-updating. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return reduced
}

/**
 * Per-item entrance delay for lists. Capped so a long evidence list never
 * makes the investigator wait on choreography.
 */
export function stagger(index: number, stepMs = 26, maxItems = 10): CSSProperties {
  const capped = Math.min(Math.max(index, 0), maxItems)
  return { animationDelay: `${capped * stepMs}ms` }
}

/**
 * Keeps a component mounted while it animates out.
 *
 * Returns `mounted` (render or not) and `leaving` (apply the exit class). When
 * motion is reduced the exit is immediate, so no one waits on a timer they
 * asked not to see.
 */
export function usePresence(open: boolean, exitMs = 180): { mounted: boolean; leaving: boolean } {
  const reduced = usePrefersReducedMotion()
  const [mounted, setMounted] = useState(open)
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    if (open) {
      setMounted(true)
      setLeaving(false)
      return
    }

    if (!mounted) return

    if (reduced) {
      setMounted(false)
      return
    }

    setLeaving(true)
    const id = window.setTimeout(() => {
      setMounted(false)
      setLeaving(false)
    }, exitMs)
    return () => window.clearTimeout(id)
  }, [open, mounted, reduced, exitMs])

  return { mounted, leaving }
}

/**
 * Returns a key that changes whenever `token` changes, so an entrance
 * animation replays when the underlying data is genuinely new. Used for the
 * verdict reveal and the provenance graph.
 */
export function useReplayKey(token: unknown): number {
  const [key, setKey] = useState(0)
  const previous = useRef(token)

  useEffect(() => {
    if (previous.current !== token) {
      previous.current = token
      setKey((k) => k + 1)
    }
  }, [token])

  return key
}
