/**
 * Screen: Cases.
 *
 * The investigator's worklist. Controls are the four the workflow calls for —
 * Search, Status, Priority, New Case — and the table shows only what helps
 * choose a case to open: Case, Status, Priority, Evidence, Verdict, Updated.
 *
 * Filter option lists are derived from the data actually returned, so the
 * dropdowns never offer a status or priority the backend does not use, and never
 * invent one it does. Row → Case Detail.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CaseRecord } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { formatTimestampShort } from '../lib/format'
import { getFlagshipDemoCases } from '../lib/curated'
import type { RoutePath } from '../lib/router'
import type { Investigation } from '../state/useInvestigation'

function verdictTone(verdict: string): PillTone {
  if (verdict.includes('MANIPULATED')) return 'error'
  if (verdict.includes('AUTHENTIC')) return 'ok'
  return 'warn'
}

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  return 'warn'
}

/** "pending_review" → "Pending review" for a human-facing option label. */
function humanise(value: string): string {
  const spaced = value.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}

export function ScreenCases({
  initialFilter = 'all',
  initialQuery = '',
  onNavigate,
  onSelectCase,
}: {
  investigation: Investigation
  initialFilter?: string
  initialQuery?: string
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [cases, setCases] = useState<CaseRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [status, setStatus] = useState(initialFilter)
  const [priority, setPriority] = useState('all')
  const [search, setSearch] = useState(initialQuery)

  useEffect(() => {
    let active = true
    setLoading(true)
    api
      .listCases()
      .then((data) => {
        if (active) {
          setCases(data.cases)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setError(err)
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [])

  // Keep the controls in step with the URL when navigation supplies a query or
  // filter after mount (e.g. the header search while already on this screen).
  useEffect(() => {
    setSearch(initialQuery)
  }, [initialQuery])
  useEffect(() => {
    setStatus(initialFilter)
  }, [initialFilter])

  const statusOptions = useMemo(
    () => Array.from(new Set(cases.map((c) => c.status).filter(Boolean))).sort(),
    [cases],
  )
  const priorityOptions = useMemo(
    () => Array.from(new Set(cases.map((c) => c.priority).filter((p): p is string => Boolean(p)))).sort(),
    [cases],
  )

  const [showAll, setShowAll] = useState(false)

  const filtered = useMemo(() => {
    return cases.filter((c) => {
      if (status !== 'all' && c.status !== status) return false
      if (priority !== 'all' && c.priority !== priority) return false
      if (search.trim()) {
        const q = search.toLowerCase()
        return (
          c.case_number.toLowerCase().includes(q) ||
          (c.title || '').toLowerCase().includes(q) ||
          (c.examiner || '').toLowerCase().includes(q)
        )
      }
      return true
    })
  }, [cases, status, priority, search])

  // Curated demo view picks exactly the 3 flagship demo cases
  const curatedSelection = useMemo(() => {
    if (showAll || search.trim() || status !== 'all' || priority !== 'all') return filtered
    return getFlagshipDemoCases(filtered)
  }, [filtered, showAll, search, status, priority])

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Cases</h1>
        <p className="screen__lead">Search and filter your investigations.</p>
      </div>

      <div className="row row--wrap" style={{ gap: 'var(--space-3)', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div className="row row--wrap" style={{ gap: 'var(--space-3)', flex: '1 1 auto' }}>
          <div className="field" style={{ flex: '1 1 260px', minWidth: 220 }}>
            <label className="field__label" htmlFor="cases-search">
              Search
            </label>
            <div className="search-box">
              <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
              <input
                id="cases-search"
                className="search-box__input"
                type="search"
                placeholder="Case number, title or examiner…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="field" style={{ minWidth: 160 }}>
            <label className="field__label" htmlFor="cases-status">
              Status
            </label>
            <select
              id="cases-status"
              className="input"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="all">All statuses</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {humanise(s)}
                </option>
              ))}
            </select>
          </div>

          <div className="field" style={{ minWidth: 150 }}>
            <label className="field__label" htmlFor="cases-priority">
              Priority
            </label>
            <select
              id="cases-priority"
              className="input"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            >
              <option value="all">All priorities</option>
              {priorityOptions.map((p) => (
                <option key={p} value={p}>
                  {humanise(p)}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          {cases.length > 5 && !search.trim() && status === 'all' && priority === 'all' ? (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? 'Show curated demo cases' : `View all cases (${cases.length})`}
            </button>
          ) : null}
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('intake')}>
            <Icon name="upload" size={14} />
            New case
          </button>
        </div>
      </div>

      {loading ? (
        <Spinner label="Loading cases…" />
      ) : error ? (
        <ErrorBanner context="Cases" error={error} />
      ) : filtered.length === 0 ? (
        <Empty>
          {cases.length === 0
            ? 'No cases yet. Create a case to start an investigation.'
            : 'No cases match the current search and filters.'}
        </Empty>
      ) : (
        <div className="table-wrapper card">
          <table className="table">
            <thead>
              <tr>
                <th>Case</th>
                <th>Status</th>
                <th>Priority</th>
                <th className="table__num">Evidence</th>
                <th>Verdict</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {curatedSelection.map((c) => {
                const isManipulated = c.latest_verdict?.includes('MANIPULATED')
                const isAuthentic = c.latest_verdict?.includes('AUTHENTIC')
                const isHighPriority = c.priority === 'high'

                return (
                  <tr
                    key={c.case_id}
                    style={{
                      cursor: 'pointer',
                      borderLeft: isHighPriority
                        ? '3px solid var(--danger)'
                        : isManipulated
                        ? '3px solid var(--danger)'
                        : isAuthentic
                        ? '3px solid var(--ok)'
                        : '3px solid transparent',
                      background: isHighPriority
                        ? 'var(--danger-wash)'
                        : undefined
                    }}
                    onClick={() => {
                      onSelectCase(c.case_id)
                      onNavigate('case-detail', { caseId: c.case_id })
                    }}
                  >
                    <td>
                      <div className="stack" style={{ gap: 2 }}>
                        <span style={{ fontFamily: 'var(--mono)', fontWeight: 700, fontSize: 'var(--text-xs)' }}>
                          {c.case_number}
                        </span>
                        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 500 }}>
                          {c.title || 'Circulating Media Evidence Investigation'}
                        </span>
                      </div>
                    </td>
                    <td>
                      <Pill variant="accent">{c.status.toUpperCase()}</Pill>
                    </td>
                    <td>
                      {c.priority ? (
                        <Pill variant={priorityTone(c.priority)}>{c.priority.toUpperCase()}</Pill>
                      ) : (
                        <span style={{ color: 'var(--text-faint)' }}>—</span>
                      )}
                    </td>
                    <td className="table__num" style={{ fontWeight: 600 }}>{c.evidence_count}</td>
                    <td>
                      {c.latest_verdict ? (
                        <Pill variant={verdictTone(c.latest_verdict)}>
                          {c.latest_verdict.replace(/_/g, ' ')}
                        </Pill>
                      ) : (
                        <span style={{ color: 'var(--text-faint)' }}>—</span>
                      )}
                    </td>
                    <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                      {formatTimestampShort(c.updated_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
