/**
 * Tone mapping: the single place where a backend string becomes a colour.
 *
 * Previously each screen redefined its own verdict/priority/status helpers,
 * which is how a red "primary" button and a green "unverified" badge get into a
 * forensic tool. Semantic colour is declared once in tokens.css and assigned
 * once here.
 *
 * Note what is deliberately absent: confidence never maps to green. The backend
 * emits only none / low / moderate and states that no threshold here is
 * validated, so a confidence band must never be dressed as a pass.
 */

export type Tone = 'ok' | 'warn' | 'danger' | 'accent' | 'measure' | 'rare' | 'neutral'

const PILL: Record<Tone, string> = {
  ok: 'pill--ok',
  warn: 'pill--warn',
  danger: 'pill--danger',
  accent: 'pill--accent',
  measure: 'pill--measure',
  rare: 'pill--rare',
  neutral: '',
}

const DOT: Record<Tone, string> = {
  ok: 'dot--ok',
  warn: 'dot--warn',
  danger: 'dot--danger',
  accent: 'dot--accent',
  measure: 'dot--accent',
  rare: 'dot--accent',
  neutral: '',
}

export function pillClass(tone: Tone, extra?: string): string {
  return ['pill', PILL[tone], extra].filter(Boolean).join(' ')
}

export function dotClass(tone: Tone): string {
  return ['dot', DOT[tone]].filter(Boolean).join(' ')
}

/** CSS colour value for a tone, for the few places that need it inline (SVG). */
export function toneVar(tone: Tone): string {
  switch (tone) {
    case 'ok':
      return 'var(--ok)'
    case 'warn':
      return 'var(--warn)'
    case 'danger':
      return 'var(--danger)'
    case 'accent':
      return 'var(--accent)'
    case 'measure':
      return 'var(--accent-2)'
    case 'rare':
      return 'var(--rare)'
    default:
      return 'var(--neutral)'
  }
}

/** AUTHENTIC -> green, MANIPULATED -> red, INSUFFICIENT_EVIDENCE -> amber. */
export function verdictToneOf(band: string | null | undefined): Tone {
  switch (band) {
    case 'AUTHENTIC':
      return 'ok'
    case 'MANIPULATED':
      return 'danger'
    case 'INSUFFICIENT_EVIDENCE':
      return 'warn'
    default:
      return 'neutral'
  }
}

export function priorityToneOf(priority: string | null | undefined): Tone {
  switch ((priority ?? '').toLowerCase()) {
    case 'critical':
      return 'danger'
    case 'high':
      return 'warn'
    case 'medium':
      return 'accent'
    case 'low':
      return 'neutral'
    default:
      return 'neutral'
  }
}

export function priorityRowClass(priority: string | null | undefined): string {
  const p = (priority ?? '').toLowerCase()
  return ['critical', 'high', 'medium', 'low'].includes(p) ? `caserow--p-${p}` : ''
}

/**
 * Case status. "Closed" is green because the workflow finished, not because the
 * evidence was authentic -- those are different statements and the UI keeps
 * them in different places.
 */
export function statusToneOf(status: string | null | undefined): Tone {
  switch ((status ?? '').toLowerCase()) {
    case 'open':
    case 'active':
    case 'analysing':
    case 'analyzing':
      return 'accent'
    case 'pending':
    case 'pending_review':
    case 'in_review':
    case 'review':
      return 'warn'
    case 'closed':
    case 'complete':
    case 'completed':
    case 'archived':
      return 'ok'
    case 'escalated':
      return 'danger'
    default:
      return 'neutral'
  }
}

export function severityToneOf(severity: string | null | undefined): Tone {
  switch ((severity ?? '').toLowerCase()) {
    case 'critical':
    case 'high':
      return 'danger'
    case 'medium':
      return 'warn'
    case 'low':
      return 'accent'
    default:
      return 'neutral'
  }
}

/** Never 'ok': a confidence band is not a validation. */
export function confidenceToneOf(confidence: string | null | undefined): Tone {
  switch ((confidence ?? '').toLowerCase()) {
    case 'moderate':
      return 'accent'
    case 'low':
      return 'warn'
    case 'none':
      return 'neutral'
    default:
      return 'neutral'
  }
}

/** Signal status -> tone, for the status pill beside a signal name. */
export function signalStatusToneOf(status: string): Tone {
  switch (status) {
    case 'OK':
      return 'measure'
    case 'ERROR':
      return 'danger'
    case 'INCONCLUSIVE':
      return 'warn'
    default:
      return 'neutral'
  }
}

/** Human label for a case status token, without inventing a state. */
export function humanise(token: string | null | undefined): string {
  if (!token) return '—'
  return token.replace(/[_-]+/g, ' ').toUpperCase()
}
