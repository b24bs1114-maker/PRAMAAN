/**
 * Screen: Cases — Investigation Queue & Worklist.
 *
 * Primary question: "Which investigation should I open?"
 *
 * The worklist is now the real worklist. Two defects were removed:
 *
 *   - `getFlagshipDemoCases` reduced the queue to three hand-picked cases (one
 *     MANIPULATED, one AUTHENTIC, one multimodal) whenever no filter was active.
 *     A case queue that silently hides open cases is worse than no queue: the
 *     count read "Showing 3 of 11" while the control offering the rest was
 *     labelled "View Full Case Archive", as though the hidden eight were closed.
 *   - Untitled cases were displayed as "Circulating Media Evidence
 *     Investigation", inventing a subject for a case whose subject was never
 *     recorded.
 *
 * Structure:
 * 1. PAGE HEADER: "CASES" · "Manage and track investigations" · "+ New Case"
 * 2. SEARCH BAR: one free-text search over case number, title, examiner,
 *    description and complaint reference, evaluated server-side. The status /
 *    priority / date / assignee / verdict dropdowns were removed: the queue is
 *    small enough that search plus the newest-first ordering finds a case
 *    faster than five stacked selects, and each dropdown cost a facet query
 *    and a way to hide open cases behind a filter left set.
 * 3. MAIN CASE TABLE:
 *    - Priority | Case ID | Title / Subject | Status | Evidence | Verdict | Updated | Action
 *    - High-priority / urgent cases naturally rise visually with accent borders & badges
 *    - Primary row action: "Open Case →" continues into case workflow
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CaseDeleteResult, CaseRecord } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CaseDeleteDialog, DeleteCaseButton } from '../components/CaseDelete'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { deletionSummary, removeCase } from '../lib/casedelete'
import { NOT_MEASURED, caseStatusLabel, evidenceCountLabel, formatTimestampShort } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel, verdictPillTone } from '../lib/signals'
import { useCaseDeletion } from '../state/useCaseDeletion'
import type { Investigation } from '../state/useInvestigation'

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  if (!priority) return 'neutral'
  return 'warn'
}

function statusTone(status: string): PillTone {
  const s = status.toLowerCase()
  if (s.includes('closed') || s.includes('archived')) return 'neutral'
  if (s.includes('review') || s.includes('pending')) return 'warn'
  if (s.includes('complete') || s.includes('verified')) return 'ok'
  return 'accent'
}

/** Rows fetched per page; the backend applies limit/offset in SQL. */
const PAGE_SIZE = 25

export function ScreenCases({
  investigation,
  initialQuery = '',
  onNavigate,
  onSelectCase,
  onNewCase,
}: {
  investigation: Investigation
  initialQuery?: string
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string; q?: string }) => void
  onSelectCase: (caseId: string) => void
  /** Start a fresh case: clears prior case state before landing on intake. */
  onNewCase: () => void
}) {
  const { caseRecord: loadedCase, reset: resetInvestigation } = investigation
  /* The current page of the server-searched list, plus the server's total count
     of all rows matching the active search (NOT the page length). */
  const [cases, setCases] = useState<CaseRecord[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  /*
   * The last completed deletion, kept only so the queue can report what the
   * backend said it removed. Set from the `onDeleted` callback below, which only
   * runs after a 2xx -- so this can never describe a deletion that did not
   * happen. Cleared by the operator, not by a timer: the counts are the only
   * account of an irreversible action, and they should not vanish unread.
   */
  const [removed, setRemoved] = useState<{ result: CaseDeleteResult; target: CaseRecord } | null>(
    null,
  )

  const deletion = useCaseDeletion((result, target) => {
    // The backend has confirmed. Drop the row from the queue we are holding
    // rather than re-listing: a refetch here would hide a backend that answered
    // 200 while leaving the row in place.
    setCases((current) => removeCase(current, target.case_id))
    // The server's total is now one smaller; reflect that in "of Y" without a
    // round-trip. Never below zero.
    setTotal((n) => Math.max(0, n - 1))
    setRemoved({ result, target })
    // If the deleted case is the one loaded in the shared investigation
    // store, its analysis/provenance/report slices now describe evidence
    // that no longer exists. Reset so the sidebar cannot offer the deleted
    // case's reports or audit trail.
    if (loadedCase?.case_id === target.case_id) resetInvestigation()
  })

  // Free-text search, debounced so typing does not fire a request per keystroke.
  const [search, setSearch] = useState(initialQuery)
  const [debouncedSearch, setDebouncedSearch] = useState(initialQuery)

  // Server-side filters
  const [statusFilter, setStatusFilter] = useState('all')
  const [priorityFilter, setPriorityFilter] = useState('all')
  const [verdictFilter, setVerdictFilter] = useState('all')
  const [examiner, setExaminer] = useState('')
  const [debouncedExaminer, setDebouncedExaminer] = useState('')
  const [dateFilter, setDateFilter] = useState('all')

  // Server-side pagination cursor (rows to skip).
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedSearch(search)
      setOffset(0)
    }, 250)
    return () => window.clearTimeout(id)
  }, [search])

  useEffect(() => {
    const id = window.setTimeout(() => {
      setDebouncedExaminer(examiner)
      setOffset(0)
    }, 250)
    return () => window.clearTimeout(id)
  }, [examiner])

  // Keep the input in sync with the URL's `q` param
  useEffect(() => {
    setSearch(initialQuery)
  }, [initialQuery])

  // Compute ISO lower bound for date filter
  const createdAfter = useMemo(() => {
    if (dateFilter === '24h') {
      return new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
    }
    if (dateFilter === '7d') {
      return new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString()
    }
    if (dateFilter === '30d') {
      return new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString()
    }
    return undefined
  }, [dateFilter])

  // The authoritative table fetch with server-side query parameters.
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    api
      .listCases(
        {
          q: debouncedSearch.trim() || undefined,
          status: statusFilter !== 'all' ? statusFilter : undefined,
          priority: priorityFilter !== 'all' ? priorityFilter : undefined,
          verdict: verdictFilter !== 'all' ? verdictFilter : undefined,
          examiner: debouncedExaminer.trim() || undefined,
          created_after: createdAfter,
          limit: PAGE_SIZE,
          offset,
        },
        controller.signal,
      )
      .then((data) => {
        if (active) {
          setCases(data.cases)
          setTotal(data.count)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active && !controller.signal.aborted) {
          setError(err)
          setLoading(false)
        }
      })
    return () => {
      active = false
      controller.abort()
    }
  }, [debouncedSearch, statusFilter, priorityFilter, verdictFilter, debouncedExaminer, createdAfter, offset])

  const hasSearch = search.trim() !== ''
  const hasActiveFilters =
    hasSearch ||
    statusFilter !== 'all' ||
    priorityFilter !== 'all' ||
    verdictFilter !== 'all' ||
    examiner.trim() !== '' ||
    dateFilter !== 'all'

  const clearFilters = () => {
    setSearch('')
    setStatusFilter('all')
    setPriorityFilter('all')
    setVerdictFilter('all')
    setExaminer('')
    setDateFilter('all')
    setOffset(0)
  }

  const clearSearch = () => {
    setSearch('')
    setOffset(0)
  }

  /*
   * The table renders exactly what the server returned for the current page --
   * `cases` is already searched, filtered, ordered (newest first) and limited by the
   * backend.
   */
  const displayCases = cases

  // "Showing X of Y" and the pager both read from the server's numbers.
  const rangeStart = cases.length ? offset + 1 : 0
  const rangeEnd = offset + cases.length
  const canPrev = offset > 0
  const canNext = rangeEnd < total

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
          <p className="screen__lead">Investigation queue and active digital evidence worklist</p>
        </div>

        <button
          type="button"
          className="btn btn--primary"
          onClick={onNewCase}
          title="Open new case and ingest digital evidence"
          style={{ gap: 6, padding: '7px 16px', fontSize: 'var(--text-xs)', fontWeight: 700 }}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
          <span>New Case</span>
        </button>
      </div>

      {/* 2. SEARCH & SERVER-SIDE FILTER BAR */}
      <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 'var(--space-3)' }}>
        <div className="search-box" style={{ width: '100%' }}>
          <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
          <input
            id="cases-search"
            className="search-box__input"
            type="search"
            aria-label="Search the case queue by case number, subject, examiner or complaint reference"
            placeholder="Search cases, case numbers, subjects, examiners, complaints..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search ? (
            <button
              type="button"
              onClick={clearSearch}
              aria-label="Clear the case search"
              style={{
                display: 'flex',
                alignItems: 'center',
                background: 'none',
                border: 'none',
                color: 'var(--text-faint)',
                cursor: 'pointer',
                padding: 2,
              }}
              title="Clear search"
            >
              {/* The icon set, not a literal ✕: a Unicode glyph inherits the
                  reader's emoji font and lands at a different weight and
                  baseline from every other control on the row. */}
              <Icon name="close" size={13} />
            </button>
          ) : null}
        </div>

        {/* Filters Row: Status | Priority | Verdict | Analyst | Date | Clear */}
        <div className="row row--wrap" style={{ gap: 10, alignItems: 'center', fontSize: 'var(--text-xs)' }}>
          {/* Status */}
          <div className="row" style={{ gap: 5, alignItems: 'center' }}>
            <label htmlFor="filter-status" style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              Status:
            </label>
            <select
              id="filter-status"
              className="input input--sm"
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value)
                setOffset(0)
              }}
              style={{ padding: '3px 8px', fontSize: 'var(--text-xs)', height: '28px', background: 'var(--surface-2)' }}
            >
              <option value="all">All Statuses</option>
              <option value="active">Active</option>
              <option value="open">Open</option>
              <option value="review">Review</option>
              <option value="closed">Closed</option>
            </select>
          </div>

          {/* Priority */}
          <div className="row" style={{ gap: 5, alignItems: 'center' }}>
            <label htmlFor="filter-priority" style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              Priority:
            </label>
            <select
              id="filter-priority"
              className="input input--sm"
              value={priorityFilter}
              onChange={(e) => {
                setPriorityFilter(e.target.value)
                setOffset(0)
              }}
              style={{ padding: '3px 8px', fontSize: 'var(--text-xs)', height: '28px', background: 'var(--surface-2)' }}
            >
              <option value="all">All Priorities</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>

          {/* Verdict */}
          <div className="row" style={{ gap: 5, alignItems: 'center' }}>
            <label htmlFor="filter-verdict" style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              Verdict:
            </label>
            <select
              id="filter-verdict"
              className="input input--sm"
              value={verdictFilter}
              onChange={(e) => {
                setVerdictFilter(e.target.value)
                setOffset(0)
              }}
              style={{ padding: '3px 8px', fontSize: 'var(--text-xs)', height: '28px', background: 'var(--surface-2)' }}
            >
              <option value="all">All Verdicts</option>
              <option value="MANIPULATED">Manipulated</option>
              <option value="AUTHENTIC">Authentic</option>
              <option value="INSUFFICIENT_EVIDENCE">Insufficient Evidence</option>
              <option value="UNANALYSED">Not Yet Analysed</option>
            </select>
          </div>

          {/* Analyst */}
          <div className="row" style={{ gap: 5, alignItems: 'center' }}>
            <label htmlFor="filter-examiner" style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              Analyst:
            </label>
            <input
              id="filter-examiner"
              type="text"
              placeholder="Analyst name..."
              className="input input--sm"
              value={examiner}
              onChange={(e) => setExaminer(e.target.value)}
              style={{ width: 125, padding: '3px 8px', fontSize: 'var(--text-xs)', height: '28px', background: 'var(--surface-2)' }}
            />
          </div>

          {/* Date */}
          <div className="row" style={{ gap: 5, alignItems: 'center' }}>
            <label htmlFor="filter-date" style={{ color: 'var(--text-faint)', fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', fontWeight: 700 }}>
              Date:
            </label>
            <select
              id="filter-date"
              className="input input--sm"
              value={dateFilter}
              onChange={(e) => {
                setDateFilter(e.target.value)
                setOffset(0)
              }}
              style={{ padding: '3px 8px', fontSize: 'var(--text-xs)', height: '28px', background: 'var(--surface-2)' }}
            >
              <option value="all">All Dates</option>
              <option value="24h">Past 24 Hours</option>
              <option value="7d">Past 7 Days</option>
              <option value="30d">Past 30 Days</option>
            </select>
          </div>

          {/* Clear Button */}
          {hasActiveFilters ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={clearFilters}
              style={{ padding: '3px 8px', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}
            >
              Clear Filters
            </button>
          ) : null}
        </div>

        {/* Result count line */}
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <span>
              {total === 0 ? (
                <>No matching cases</>
              ) : (
                <>
                  Showing <strong>{rangeStart}–{rangeEnd}</strong> of <strong>{total}</strong> cases
                </>
              )}
            </span>
            {hasActiveFilters ? (
              <span className="pill pill--accent" style={{ fontSize: '9.5px', padding: '1px 6px' }}>
                FILTERED
              </span>
            ) : null}
          </div>

          {/* Server-side pager */}
          {total > PAGE_SIZE ? (
            <div className="row" style={{ gap: 6, alignItems: 'center' }}>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={!canPrev}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                style={{ padding: '3px 8px', fontSize: 'var(--text-2xs)' }}
              >
                ← Prev
              </button>
              <span style={{ fontFamily: 'var(--mono)' }}>
                Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                disabled={!canNext}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                style={{ padding: '3px 8px', fontSize: 'var(--text-2xs)' }}
              >
                Next →
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {/*
        The outcome of a completed deletion, in the backend's own numbers. Shown
        here rather than in the dialog because the dialog closes the moment the
        delete succeeds, and these counts are the only record the operator gets
        of what an irreversible action removed.
      */}
      {removed ? (
        <Banner
          tone="ok"
          title={`Case #${removed.result.case_number} deleted permanently.`}
          detail={
            <div className="stack" style={{ gap: 2 }}>
              {deletionSummary(removed.result).map((line) => (
                <span key={line}>{line}</span>
              ))}
            </div>
          }
          meta={
            <div className="row" style={{ gap: 12, alignItems: 'center' }}>
              <span style={{ fontFamily: 'var(--mono)' }}>
                Recorded at {formatTimestampShort(removed.result.deleted_at)}
              </span>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setRemoved(null)}
              >
                Dismiss
              </button>
            </div>
          }
        >
          {removed.result.warnings.length > 0 ? (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
              {removed.result.warnings.map((warning) => (
                <li key={warning} style={{ color: 'var(--warn)' }}>
                  {warning}
                </li>
              ))}
            </ul>
          ) : null}
        </Banner>
      ) : null}

      {/* 3. MAIN CASE TABLE */}
      {loading ? (
        <div className="card" style={{ padding: 'var(--space-6)' }}>
          <Spinner label="Loading investigation queue..." />
        </div>
      ) : error ? (
        <ErrorBanner context="Cases" error={error} />
      ) : displayCases.length === 0 ? (
        <Empty>
          {!hasSearch ? (
            <div className="stack" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
              <span>No investigations registered in the workspace yet.</span>
              <button type="button" className="btn btn--primary" onClick={onNewCase}>
                <Icon name="upload" size={14} />
                Create First Investigation
              </button>
            </div>
          ) : (
            <div className="stack" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
              <span>No cases match “{search.trim()}”.</span>
              <button type="button" className="btn btn--ghost" onClick={clearSearch}>
                Clear search
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
                <th style={{ width: 190, textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {displayCases.map((c) => {
                const isManipulated = c.latest_verdict?.includes('MANIPULATED')
                const isAuthentic = c.latest_verdict?.includes('AUTHENTIC')
                const isHighPriority = c.priority === 'high'
                const pTone = priorityTone(c.priority)

                return (
                  /*
                    A row, and it stays one. `role="button"` here told assistive
                    technology this `<tr>` was a button: the queue stopped being
                    a navigable table and every row was announced as a single
                    control named by concatenating all nine of its cells. The
                    click handler is kept as a mouse convenience; the keyboard
                    and screen-reader path is the real button on the case number
                    below.
                  */
                  <tr
                    key={c.case_id}
                    className="priority-case-tr"
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
                  >
                    {/* Priority */}
                    <td>
                      {/*
                        An unrecorded priority is not "NORMAL" -- that tier does
                        not exist -- and it is not styled as `medium` either.
                      */}
                      <span className={`badge-risk${c.priority ? ` badge-risk--${c.priority}` : ''}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        <span
                          style={{
                            width: 5,
                            height: 5,
                            borderRadius: '50%',
                            background:
                              pTone === 'error' ? 'var(--danger)'
                                : pTone === 'ok' ? 'var(--ok)'
                                  : pTone === 'neutral' ? 'var(--text-faint)'
                                    : 'var(--warn)',
                          }}
                        />
                        {c.priority ? c.priority.toUpperCase() : NOT_MEASURED}
                      </span>
                    </td>

                    {/* Case ID */}
                    <td>
                      <button
                        type="button"
                        className="case-open-btn"
                        onClick={(e) => {
                          // The row handles the click too; without this the case
                          // would be opened twice for one press.
                          e.stopPropagation()
                          openCase(c.case_id)
                        }}
                        title={`Open case ${c.case_number}${c.title ? ` — ${c.title}` : ''}`}
                      >
                        #{c.case_number}
                      </button>
                    </td>

                    {/* Title / Subject */}
                    <td>
                      <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                        {/*
                          Was `c.title || 'Circulating Media Evidence
                          Investigation'`, which put a plausible-looking subject
                          on every untitled case in the queue.
                        */}
                        <span
                          style={{
                            fontSize: 'var(--text-xs)',
                            color: c.title ? 'var(--text-strong)' : 'var(--text-muted)',
                            fontWeight: 600,
                            lineHeight: 1.3,
                            fontStyle: c.title ? undefined : 'italic',
                          }}
                        >
                          {c.title || 'Untitled investigation'}
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
                        {caseStatusLabel(c.status)}
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
                        {evidenceCountLabel(c.evidence_count)}
                      </span>
                    </td>

                    {/* Verdict */}
                    <td>
                      {c.latest_verdict ? (
                        /*
                         * The hedged band label, matching every other screen:
                         * the raw token "AUTHENTIC" overstates a finding that is
                         * only ever "no manipulation evidence found".
                         */
                        <Pill variant={verdictPillTone(c.latest_verdict)}>
                          {verdictBandLabel(c.latest_verdict)}
                        </Pill>
                      ) : (
                        <span style={{ color: 'var(--text-faint)', fontSize: 'var(--text-2xs)' }}>
                          NOT YET ANALYSED
                        </span>
                      )}
                    </td>

                    {/* Updated Timestamp */}
                    <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                      {formatTimestampShort(c.updated_at || c.created_at)}
                    </td>

                    {/* Row Actions: Open Case → · Delete */}
                    <td style={{ textAlign: 'right' }}>
                      <div
                        className="row"
                        style={{ gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}
                      >
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          style={{
                            fontSize: '11px',
                            padding: '3px 8px',
                            color: 'var(--accent-bright)',
                            fontWeight: 700,
                            letterSpacing: '0.03em',
                          }}
                          onClick={(e) => {
                            e.stopPropagation()
                            openCase(c.case_id)
                          }}
                        >
                          OPEN CASE →
                        </button>
                        {/*
                          The destructive control opens a dialog; it never deletes
                          on this click. `c` is the real row, so the dialog shows
                          the case number the backend issued.
                        */}
                        <DeleteCaseButton target={c} onClick={deletion.ask} />
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/*
        One dialog for the whole queue. It renders nothing until a row's delete
        button names a target, and its copy comes from `CaseDelete` so the queue
        and the dossier describe the same consequence identically.
      */}
      <CaseDeleteDialog
        state={deletion.state}
        onTyped={deletion.type}
        onCancel={deletion.dismiss}
        onConfirm={deletion.confirm}
      />
    </div>
  )
}
