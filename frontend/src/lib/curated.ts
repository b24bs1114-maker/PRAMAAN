import type { CaseRecord } from '../api/types'

/**
 * Returns the exactly 3 curated flagship demo cases in consistent order:
 * 1. AI-generated image → MANIPULATED
 * 2. Authentic photograph → AUTHENTIC
 * 3. Video OR audio investigation → Multimodal detection
 */
export function getFlagshipDemoCases(cases: CaseRecord[]): CaseRecord[] {
  if (!cases.length) return []

  const manip = cases.find((c) => c.latest_verdict?.includes('MANIPULATED'))
  const auth = cases.find((c) => c.latest_verdict?.includes('AUTHENTIC'))
  const multi =
    cases.find(
      (c) =>
        c.case_id !== manip?.case_id &&
        c.case_id !== auth?.case_id &&
        (c.title?.toLowerCase().includes('video') ||
          c.title?.toLowerCase().includes('audio') ||
          c.evidence_count > 1),
    ) || cases.find((c) => c.case_id !== manip?.case_id && c.case_id !== auth?.case_id)

  const list = [manip, auth, multi].filter((c): c is CaseRecord => Boolean(c))
  return list.slice(0, 3)
}
