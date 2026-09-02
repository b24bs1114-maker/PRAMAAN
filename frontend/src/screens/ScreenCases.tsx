/**
 * Screen: Cases — Investigation Queue & Worklist.
 *
 * Primary question: "Which investigation should I open?"
 *
 * Structure:
 * 1. PAGE HEADER: "CASES" · "Manage and track investigations" · "+ New Case"
 * 2. FILTER / SEARCH BAR:
 *    - Search (case number, title, examiner, description, complaint reference)
 *    - Status (dynamically derived from data)
 *    - Priority (High, Medium, Low)
 *    - Date (All time, 24h, 7d, 30d)
 *    - Assignment (Lead investigators)
 *    - Verdict (Manipulated, Authentic, Inconclusive, Pending)
 * 3. MAIN CASE TABLE:
 *    - Priority | Case ID | Title / Subject | Status | Evidence | Verdict | Updated | Action
 *    - High-priority / urgent cases naturally rise visually with accent borders & badges
 *    - Compact status & verdict indicators
 *    - Primary row action: "Open Case →" continues into case workflow
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

function verdictTone(verdict: string | undefined): PillTone {
  if (!verdict) return 'neutral'
  if (verdict.includes('MANIPULATED')) return 'error'
  if (verdict.includes('AUTHENTIC')) return 'ok'
  return 'warn'
}

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  return 'warn'
}

function statusTone(status: string): PillTone {
  const s = status.toLowerCase()
  if (s.includes('closed') || s.includes('archived')) return 'neutral'
  if (s.includes('review') || s.includes('pending')) return 'warn'
  if (s.includes('complete') || s.includes('verified')) return 'ok'
  return 'accent'
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
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string; q?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [cases, setCases] = useState<CaseRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  // Filters
  const [search, setSearch] = useState(initialQuery)
  const [status, setStatus] = useState(initialFilter)
  const [priority, setPriority] = useState('all')
  const [dateRange, setDateRange] = useState('all')
  const [assignment, setAssignment] = useState('all')
  const [verdictFilter, setVerdictFilter] = useState('all')
  const [showAll, setShowAll] = useState(false)

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

  // Keep controls in sync with URL search / filter params
  useEffect(() => {
    setSearch(initialQuery)
  }, [initialQuery])
  useEffect(() => {
    setStatus(initialFilter)
  }, [initialFilter])

  // Extract unique available values from data
  const statusOptions = useMemo(
    () => Array.from(new Set(cases.map((c) => c.status).filter(Boolean))).sort(),
    [cases],
  )
  const priorityOptions = useMemo(
    () => Array.from(new Set(cases.map((c) => c.priority).filter((p): p is string => Boolean(p)))).sort(),
    [cases],
  )
  const assignmentOptions = useMemo(
    () => Array.from(new Set(cases.map((c) => c.examiner).filter((e): e is string => Boolean(e)))).sort(),
    [cases],
  )

  const hasActiveFilters =
    search.trim() !== '' ||
    status !== 'all' ||
    priority !== 'all' ||
    dateRange !== 'all' ||
    assignment !== 'all' ||
    verdictFilter !== 'all'

  const resetFilters = () => {
    setSearch('')
    setStatus('all')
    setPriority('all')
    setDateRange('all')
    setAssignment('all')
    setVerdictFilter('all')
  }

  // Filter & naturally prioritize cases
  const filtered = useMemo(() => {
    const now = Date.now()
    return cases.filter((c) => {
      // Status filter
      if (status !== 'all' && c.status !== status) return false

      // Priority filter
      if (priority !== 'all' && (c.priority || 'normal') !== priority) return false

      // Assignment / Investigator filter
      if (assignment !== 'all' && c.examiner !== assignment) return false

      // Verdict filter
      if (verdictFilter !== 'all') {
        const v = c.latest_verdict?.toUpperCase() || ''
        if (verdictFilter === 'manipulated' && !v.includes('MANIPULATED')) return false
        if (verdictFilter === 'authentic' && !v.includes('AUTHENTIC')) return false
        if (verdictFilter === 'inconclusive' && !v.includes('INCONCLUSIVE') && !v.includes('INSUFFICIENT')) return false
        if (verdictFilter === 'pending' && v !== '') return false
      }

      // Date range filter
      if (dateRange !== 'all') {
        const updatedTime = new Date(c.updated_at || c.created_at).getTime()
        const diffHours = (now - updatedTime) / (1000 * 60 * 60)
        if (dateRange === '24h' && diffHours > 24) return false
        if (dateRange === '7d' && diffHours > 24 * 7) return false
        if (dateRange === '30d' && diffHours > 24 * 30) return false
      }

      // Search query (case ID, title, examiner, description, complaint reference)
      if (search.trim()) {
        const q = search.toLowerCase()
        const matchId = c.case_number.toLowerCase().includes(q)
        const matchTitle = (c.title || '').toLowerCase().includes(q)
        const matchExaminer = (c.examiner || '').toLowerCase().includes(q)
        const matchDesc = (c.description || '').toLowerCase().includes(q)
        const matchRef = (c.complaint_reference || '').toLowerCase().includes(q)
        return matchId || matchTitle || matchExaminer || matchDesc || matchRef
      }

      return true
    })
  }, [cases, status, priority, assignment, verdictFilter, dateRange, search])

  // Naturally sort so high-priority & urgent cases rise to top
  const sortedCases = useMemo(() => {
    return [...filtered].sort((a, b) => {
      const priorityWeight: Record<string, number> = { high: 3, medium: 2, low: 1 }
      const weightA = priorityWeight[a.priority || 'medium'] || 2
      const weightB = priorityWeight[b.priority || 'medium'] || 2
      if (weightB !== weightA) return weightB - weightA

      // Then by updated timestamp
      const timeA = new Date(a.updated_at || a.created_at).getTime()
      const timeB = new Date(b.updated_at || b.created_at).getTime()
      return timeB - timeA
    })
  }, [filtered])

  // Display selection (demo curated view if untampered, or all)
  const displayCases = useMemo(() => {
    if (showAll || hasActiveFilters || sortedCases.length <= 5) return sortedCases
    return getFlagshipDemoCases(sortedCases)
  }, [sortedCases, showAll, hasActiveFilters])

  const openCase = (caseId: string) => {
    onSelectCase(caseId)
    onNavigate('case-detail', { caseId })
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. PAGE HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">CASES</h1>
          <p className="screen__lead">Manage and track investigations</p>
        </div>

        <button
          type="button"
          className="btn-new-case"
          onClick={() => onNavigate('intake')}
          title="Open new case and ingest digital evidence"
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
          <span>New Case</span>
        </button>
      </div>

      {/* 2. FILTER / SEARCH CONTROLS CONTAINER */}
      <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 'var(--space-3)' }}>
        <div className="row row--wrap" style={{ gap: 'var(--space-3)', alignItems: 'center', justifyContent: 'space-between' }}>
          {/* Main Search Input */}
          <div className="search-box" style={{ flex: '1 1 300px', minWidth: 240, maxWidth: 540 }}>
            <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
            <input
              id="cases-search"
              className="search-box__input"
              type="search"
              placeholder="Search cases, case IDs, investigators, tags..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch('')}
                style={{ background: 'none', border: 'none', color: 'var(--text-faint)', cursor: 'pointer', padding: 2 }}
                title="Clear search"
              >
                ✕
              </button>
            ) : null}
          </div>

          {/* Filter Dropdowns Grid */}
          <div className="row row--wrap" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
            {/* Status Filter */}
            <select
              id="cases-status"
              className="input"
              style={{ fontSize: 'var(--text-xs)', height: 34, padding: '4px 8px', minWidth: 125 }}
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              aria-label="Filter by Status"
            >
              <option value="all">Status: All</option>
              {statusOptions.map((s) => (
                <option key={s} value={s}>
                  {humanise(s)}
                </option>
              ))}
            </select>

            {/* Priority Filter */}
            <select
              id="cases-priority"
              className="input"
              style={{ fontSize: 'var(--text-xs)', height: 34, padding: '4px 8px', minWidth: 125 }}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              aria-label="Filter by Priority"
            >
              <option value="all">Priority: All</option>
              {priorityOptions.map((p) => (
                <option key={p} value={p}>
                  {humanise(p)} Priority
                </option>
              ))}
            </select>

            {/* Date Filter */}
            <select
              id="cases-date"
              className="input"
              style={{ fontSize: 'var(--text-xs)', height: 34, padding: '4px 8px', minWidth: 125 }}
              value={dateRange}
              onChange={(e) => setDateRange(e.target.value)}
              aria-label="Filter by Date"
            >
              <option value="all">Date: All time</option>
              <option value="24h">Past 24 hours</option>
              <option value="7d">Past 7 days</option>
              <option value="30d">Past 30 days</option>
            </select>

            {/* Assignment Filter */}
            {assignmentOptions.length > 0 ? (
              <select
                id="cases-assignment"
                className="input"
                style={{ fontSize: 'var(--text-xs)', height: 34, padding: '4px 8px', minWidth: 135 }}
                value={assignment}
                onChange={(e) => setAssignment(e.target.value)}
                aria-label="Filter by Investigator"
              >
                <option value="all">Assignee: All</option>
                {assignmentOptions.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            ) : null}

            {/* Verdict Filter */}
            <select
              id="cases-verdict"
              className="input"
              style={{ fontSize: 'var(--text-xs)', height: 34, padding: '4px 8px', minWidth: 135 }}
              value={verdictFilter}
              onChange={(e) => setVerdictFilter(e.target.value)}
              aria-label="Filter by Verdict"
            >
              <option value="all">Verdict: All</option>
              <option value="manipulated">Manipulated</option>
              <option value="authentic">Authentic</option>
              <option value="inconclusive">Inconclusive</option>
              <option value="pending">Pending Analysis</option>
            </select>

            {/* Reset Filters CTA */}
            {hasActiveFilters ? (
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={resetFilters}
                style={{ color: 'var(--accent-bright)', padding: '4px 8px', fontSize: 'var(--text-xs)' }}
              >
                Reset Filters
              </button>
            ) : null}
          </div>
        </div>

        {/* Filter Summary & View Mode Line */}
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span>
              Showing <strong>{displayCases.length}</strong> of <strong>{cases.length}</strong> investigations
            </span>
            {hasActiveFilters ? (
              <span className="pill pill--accent" style={{ fontSize: '9.5px', padding: '1px 6px' }}>
                FILTERED
              </span>
            ) : null}
          </div>

          {cases.length > 5 && !hasActiveFilters ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ fontSize: 'var(--text-2xs)', padding: '2px 8px', height: 'auto' }}
              onClick={() => setShowAll(!showAll)}
            >
              {showAll ? 'Show Flagship Demo Queue (3 cases)' : `View Full Case Archive (${cases.length})`}
            </button>
          ) : null}
        </div>
      </div>

      {/* 3. MAIN CASE TABLE */}
      {loading ? (
        <div className="card" style={{ padding: 'var(--space-6)' }}>
          <Spinner label="Loading investigation queue..." />
        </div>
      ) : error ? (
        <ErrorBanner context="Cases" error={error} />
      ) : displayCases.length === 0 ? (
        <Empty>
          {cases.length === 0 ? (
            <div className="stack" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
              <span>No investigations registered in the workspace yet.</span>
              <button type="button" className="btn btn--primary" onClick={() => onNavigate('intake')}>
                <Icon name="upload" size={14} />
                Create First Investigation
              </button>
            </div>
          ) : (
            <div className="stack" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
              <span>No cases match your active filters or search term.</span>
              <button type="button" className="btn btn--ghost" onClick={resetFilters}>
                Reset all filters
              </button>
            </div>
          )}
        </Empty>
      ) : (
        <div className="table-wrapper card" style={{ boxShadow: 'var(--card-glow)' }}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 95 }}>Priority</th>
                <th style={{ width: 140 }}>Case ID</th>
                <th>Title / Subject</th>
                <th style={{ width: 120 }}>Status</th>
                <th style={{ width: 90 }}>Evidence</th>
                <th style={{ width: 145 }}>Verdict</th>
                <th style={{ width: 130 }}>Updated</th>
                <th style={{ width: 110, textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {displayCases.map((c) => {
                const isManipulated = c.latest_verdict?.includes('MANIPULATED')
                const isAuthentic = c.latest_verdict?.includes('AUTHENTIC')
                const isHighPriority = c.priority === 'high'
                const pTone = priorityTone(c.priority)

                return (
                  <tr
                    key={c.case_id}
                    className="priority-case-tr"
                    tabIndex={0}
                    role="button"
                    style={{
                      cursor: 'pointer',
                      borderLeft: isHighPriority
                        ? '3.5px solid var(--danger)'
                        : isManipulated
                        ? '3.5px solid var(--danger)'
                        : isAuthentic
                        ? '3.5px solid var(--ok)'
                        : '3.5px solid transparent',
                      background: isHighPriority ? 'var(--danger-wash)' : undefined,
                    }}
                    onClick={() => openCase(c.case_id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        openCase(c.case_id)
                      }
                    }}
                  >
                    {/* Priority */}
                    <td>
                      <span className={`badge-risk badge-risk--${c.priority || 'medium'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span
                          style={{
                            width: 5,
                            height: 5,
                            borderRadius: '50%',
                            background: pTone === 'error' ? 'var(--danger)' : pTone === 'ok' ? 'var(--ok)' : 'var(--warn)'
                          }}
                        />
                        {(c.priority || 'normal').toUpperCase()}
                      </span>
                    </td>

                    {/* Case ID */}
                    <td>
                      <span
                        style={{
                          fontFamily: 'var(--mono)',
                          fontWeight: 700,
                          fontSize: 'var(--text-xs)',
                          color: 'var(--text-strong)',
                          letterSpacing: '-0.01em',
                        }}
                      >
                        #{c.case_number}
                      </span>
                    </td>

                    {/* Title / Subject */}
                    <td>
                      <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                        <span
                          style={{
                            fontSize: 'var(--text-xs)',
                            color: 'var(--text-strong)',
                            fontWeight: 600,
                            lineHeight: 1.3,
                          }}
                        >
                          {c.title || 'Circulating Media Evidence Investigation'}
                        </span>
                        {c.examiner ? (
                          <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            Lead: {c.examiner}
                            {c.complaint_reference ? ` · Ref: ${c.complaint_reference}` : ''}
                          </span>
                        ) : null}
                      </div>
                    </td>

                    {/* Status */}
                    <td>
                      <Pill variant={statusTone(c.status)}>
                        {c.status.replace(/_/g, ' ').toUpperCase()}
                      </Pill>
                    </td>

                    {/* Evidence Count */}
                    <td>
                      <span
                        style={{
                          fontFamily: 'var(--mono)',
                          fontWeight: 600,
                          fontSize: 'var(--text-xs)',
                          color: 'var(--text-strong)',
                        }}
                      >
                        {c.evidence_count} {c.evidence_count === 1 ? 'item' : 'items'}
                      </span>
                    </td>

                    {/* Verdict */}
                    <td>
                      {c.latest_verdict ? (
                        <Pill variant={verdictTone(c.latest_verdict)}>
                          {c.latest_verdict.replace(/_/g, ' ')}
                        </Pill>
                      ) : (
                        <span style={{ color: 'var(--text-faint)', fontSize: 'var(--text-2xs)' }}>
                          PENDING ANALYSIS
                        </span>
                      )}
                    </td>

                    {/* Updated Timestamp */}
                    <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                      {formatTimestampShort(c.updated_at || c.created_at)}
                    </td>

                    {/* Row Action: Open Case → */}
                    <td style={{ textAlign: 'right' }}>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        style={{
                          fontSize: '11.5px',
                          padding: '3px 8px',
                          color: 'var(--accent-bright)',
                          fontWeight: 600,
                        }}
                        onClick={(e) => {
                          e.stopPropagation()
                          openCase(c.case_id)
                        }}
                      >
                        Open Case →
                      </button>
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
