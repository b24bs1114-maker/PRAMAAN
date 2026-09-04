/** Class-name join. Falsy parts drop out, so conditionals stay inline. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
