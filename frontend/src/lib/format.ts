/**
 * Display formatting.
 *
 * One rule governs this module: a null from the backend means "not measured",
 * and it must never be rendered as a number. Every formatter here returns an
 * explicit placeholder rather than substituting a zero.
 */

/** Placeholder for a value the backend did not measure. */
export const NOT_MEASURED = '-'

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return NOT_MEASURED
  if (bytes < 1024) return `${bytes} B`
  const units = ['KiB', 'MiB', 'GiB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

/**
 * Render an ISO timestamp in Indian Standard Time (en-IN, Asia/Kolkata) with timezone.
 */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  try {
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      timeZoneName: 'short',
      timeZone: 'Asia/Kolkata',
    }).format(date)
  } catch {
    return date.toISOString()
  }
}

/** Short form for dense Indian forensic tables (en-IN). */
export function formatTimestampShort(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  try {
    return new Intl.DateTimeFormat('en-IN', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'Asia/Kolkata',
    }).format(date)
  } catch {
    return date.toISOString()
  }
}

/**
 * Format a fused or signal score.
 *
 * Deliberately not a percentage. The workflow specification forbids
 * percentage-style headline figures because they read as false precision.
 */
export function formatScore(score: number | null | undefined, digits = 3): string {
  if (score === null || score === undefined || !Number.isFinite(score)) return NOT_MEASURED
  return score.toFixed(digits)
}

/** Weights are configuration, not measurements, so a percentage is honest here. */
export function formatWeight(weight: number | null | undefined): string {
  if (weight === null || weight === undefined || !Number.isFinite(weight)) return NOT_MEASURED
  return `${(weight * 100).toFixed(0)}%`
}

/** Hamming distance with its bit budget, so 0 and 12 are interpretable. */
export function formatDistance(
  distance: number | null | undefined,
  bits: number | null = null,
): string {
  if (distance === null || distance === undefined || !Number.isFinite(distance)) return NOT_MEASURED
  return bits ? `${distance} / ${bits}` : String(distance)
}

export function formatSimilarity(similarity: number | null | undefined): string {
  if (similarity === null || similarity === undefined || !Number.isFinite(similarity))
    return NOT_MEASURED
  return similarity.toFixed(4)
}

export function shortHash(hash: string | null | undefined, len = 12): string {
  if (!hash) return NOT_MEASURED
  if (hash.length <= len) return hash
  return `${hash.slice(0, len / 2)}…${hash.slice(-len / 2)}`
}

export function orPlaceholder(val: string | number | null | undefined): string {
  if (val === null || val === undefined || val === '') return NOT_MEASURED
  return String(val)
}

/**
 * A case status token, as a label.
 *
 * The backend stores statuses as lowercase snake_case tokens (`open`,
 * `under_review`). Both the Cases queue and the Dashboard queue list the same
 * cases side by side, and they formatted this differently -- the Cases table
 * upper-cased and un-snaked it, the Dashboard printed the raw token -- so the same
 * case read `OPEN` on one screen and `open` on the other. That is a small thing
 * that makes a tool look like two tools.
 *
 * Unknown tokens pass through the same transformation rather than being mapped to
 * a known one; nothing here decides what a status means.
 */
export function caseStatusLabel(status: string | null | undefined): string {
  if (!status) return NOT_MEASURED
  return status.replace(/_/g, ' ').toUpperCase()
}

/**
 * An exhibit count with its noun agreeing.
 *
 * "1 items" is the kind of detail that reads as a prototype. Shared so the two
 * queues that print this count cannot disagree again.
 *
 * A null count is not zero: the field is absent when the endpoint did not count,
 * and "0 items" would state that the case holds no evidence.
 */
export function evidenceCountLabel(count: number | null | undefined): string {
  if (count === null || count === undefined) return NOT_MEASURED
  return `${count} ${count === 1 ? 'item' : 'items'}`
}
