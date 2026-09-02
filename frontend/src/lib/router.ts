/**
 * Lightweight hash-based client router hook.
 *
 * Supports URL hash navigation, parameter passing, and browser back/forward buttons.
 * Available route paths (sidebar destinations plus the two flows reached from
 * within Cases):
 *   - dashboard
 *   - cases
 *   - case-detail   (opened from Cases)
 *   - evidence
 *   - intake        (New Case / upload flow)
 *   - analysis
 *   - provenance    (folds in propagation)
 *   - reports
 *   - audit
 *   - settings
 *
 * Propagation, Multimodal and Alerts are no longer top-level routes - they now
 * live inside Provenance, Analysis and the Dashboard respectively.
 */

import { useCallback, useEffect, useState } from 'react'

export type RoutePath =
  | 'dashboard'
  | 'cases'
  | 'case-detail'
  | 'evidence'
  | 'intake'
  | 'analysis'
  | 'provenance'
  | 'reports'
  | 'audit'
  | 'settings'

export interface RouteState {
  path: RoutePath
  caseId: string | null
  evidenceId: string | null
  filter: string | null
  q: string | null
}

export function parseHash(hash: string): RouteState {
  const clean = hash.replace(/^#\/?/, '')
  const [routePart, queryPart] = clean.split('?')
  const params = new URLSearchParams(queryPart || '')

  const validPaths: RoutePath[] = [
    'dashboard',
    'cases',
    'case-detail',
    'evidence',
    'intake',
    'analysis',
    'provenance',
    'reports',
    'audit',
    'settings',
  ]

  let path: RoutePath = 'dashboard'
  if (validPaths.includes(routePart as RoutePath)) {
    path = routePart as RoutePath
  }

  return {
    path,
    caseId: params.get('caseId'),
    evidenceId: params.get('evidenceId'),
    filter: params.get('filter'),
    q: params.get('q'),
  }
}

export function useRouter() {
  const [route, setRoute] = useState<RouteState>(() => parseHash(window.location.hash))

  useEffect(() => {
    const handleHashChange = () => {
      setRoute(parseHash(window.location.hash))
    }
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  const navigate = useCallback(
    (
      path: RoutePath,
      params?: { caseId?: string | null; evidenceId?: string | null; filter?: string | null; q?: string | null },
    ) => {
      const query = new URLSearchParams()
      if (params?.caseId) query.set('caseId', params.caseId)
      if (params?.evidenceId) query.set('evidenceId', params.evidenceId)
      if (params?.filter) query.set('filter', params.filter)
      if (params?.q) query.set('q', params.q)

      const qstr = query.toString() ? `?${query.toString()}` : ''
      window.location.hash = `#${path}${qstr}`
    },
    [],
  )

  return { route, navigate }
}
