/**
 * Screen: Evidence Library.
 *
 * Two modes over one route, decided by whether a case is in scope:
 *
 * - **Scoped** (`#evidence?caseId=…`, which is where step 2 of the case workflow
 *   lands): the exhibits sealed into *that* case. The workflow bar above says
 *   "CASE #… · step 2 of 6", and this is the screen that makes that true. It used
 *   to show the whole catalogue here -- 269 exhibits from every investigation --
 *   under a bar announcing one case.
 * - **Global** (`#evidence`): the catalogue across investigations, which is a
 *   legitimate destination of its own and not a position in any case's workflow.
 *
 * Either way it shows: media previews, file metadata, SHA-256 seals with
 * quick-copy, perceptual index registration, and case lineage.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { EvidenceThumbnail } from '../components/EvidenceMedia'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { NOT_MEASURED, formatBytes, formatTimestampShort, shortHash } from '../lib/format'
import { isImageMedia } from '../lib/media'
import type { RoutePath } from '../lib/router'

/**
 * How many rows one library request asks for.
 *
 * The backend caps this at 500 (`MAX_LIST_LIMIT` in `app/api/cases.py`) and
 * defaults to 100. The default was the problem: the catalogue holds 269 exhibits
 * on this system, so a request that named no limit came back with a page and no
 * way for the screen to tell a page from the whole library.
 *
 * 500 is the server's ceiling, asked for explicitly. Beyond it the screen shows
 * what it has and says so against the real total, rather than pretending the
 * page is everything -- and because the count comes from the response's `total`
 * and not from the array, that sentence stays true whatever the cap is.
 */
const LIBRARY_PAGE_LIMIT = 500

/** Thumbnail with media type badge and fallback */
function EvidenceThumb({ ev }: { ev: Evidence }) {
  return (
    <div
      style={{
        width: 48,
        height: 48,
        flexShrink: 0,
        borderRadius: 'var(--radius-sm)',
        overflow: 'hidden',
        border: '1px solid var(--border-strong)',
        background: 'var(--surface-2)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative'
      }}
    >
      {isImageMedia(ev.media_type) ? (
        <EvidenceThumbnail evidenceId={ev.evidence_id} />
      ) : (
        <Icon name="document" size={20} style={{ color: 'var(--accent-bright)' }} />
      )}
    </div>
  )
}

export function ScreenEvidence({
  caseId,
  investigation,
  onNavigate,
  onSelectCase,
}: {
  /**
   * The case whose exhibits to show, from the URL, or null for the whole
   * catalogue. The URL rather than the store: a deep link into step 2 of a case
   * must scope to the case in the link, not to whichever case the store happens
   * to be holding.
   */
  caseId?: string | null
  investigation?: import('../state/useInvestigation').Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [items, setItems] = useState<Evidence[]>([])
  /*
   * How many exhibits match, per the backend -- which is not `items.length`.
   *
   * The request is capped (see `LIBRARY_PAGE_LIMIT`), so the rows in hand can be
   * a page of a longer list. Keeping the server's count separate is what lets the
   * screen say "showing 8 of 269" instead of "showing 8 of 100", which is what it
   * used to say: it reported the size of its own page as the size of the library,
   * and offered a "View All (100)" button over 269 stored exhibits.
   */
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  const [mediaType, setMediaType] = useState<string>('all')
  const [search, setSearch] = useState<string>('')

  const activeCase = investigation?.caseRecord ?? null
  const scopeCaseId = caseId ?? null
  const scoped = Boolean(scopeCaseId)
  /*
   * The case number for the heading, when the store has the row.
   *
   * Only used when it is the *same* case the URL scopes to. A stale store row
   * from a previous case would otherwise label this case's exhibits with the
   * other case's number. With no match the heading falls back to the id, which is
   * always correct if less readable -- it never names the wrong case.
   */
  const scopeCaseNumber =
    scopeCaseId && activeCase?.case_id === scopeCaseId ? activeCase.case_number : null

  // Debounce so typing in the search box does not fire a request per keystroke.
  useEffect(() => {
    let active = true
    const handle = setTimeout(() => {
      setLoading(true)
      api
        .listGlobalEvidence({
          media_type: mediaType !== 'all' ? mediaType : undefined,
          q: search.trim() || undefined,
          case_id: scopeCaseId ?? undefined,
          limit: LIBRARY_PAGE_LIMIT,
        })
        .then((data) => {
          if (active) {
            setItems(data.evidence)
            setTotal(data.total)
            setError(null)
            setLoading(false)
          }
        })
        .catch((err) => {
          if (active) {
            setError(err)
            setLoading(false)
          }
        })
    }, 250)
    return () => {
      active = false
      clearTimeout(handle)
    }
  }, [mediaType, search, scopeCaseId])

  const openCase = (rowCaseId: string | null) => {
    if (!rowCaseId) return
    onSelectCase(rowCaseId)
    onNavigate('case-detail', { caseId: rowCaseId })
  }

  const [showAll, setShowAll] = useState(false)
  /*
   * The catalogue is truncated to 8 rows until asked for more, because it spans
   * every investigation and the first screenful is a sample, not the list.
   *
   * A case's own exhibits are never truncated: an examiner reviewing step 2 has
   * to see every exhibit sealed into the case, and "showing 8 of 11" in a
   * forensic review is a way to miss one.
   */
  const displayItems =
    scoped || showAll || search.trim() || mediaType !== 'all' ? items : items.slice(0, 8)

  /*
   * More exhibits match than this screen was given.
   *
   * Only possible when the match set exceeds the server's cap, so on this system
   * it is off in every mode except an unfiltered catalogue of more than 500. It
   * exists so the count line can never overstate what is in hand: without it,
   * asking for 500 of 269 would look right today and start lying the moment the
   * corpus grows past the cap.
   */
  const loadedShortOfTotal = items.length < total

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <div>
          <h1 className="screen__title">
            {scoped ? 'Evidence in This Case' : 'Evidence Library & Catalog'}
          </h1>
          <p className="screen__lead">
            {scoped ? (
              <>
                Exhibits sealed into{' '}
                {scopeCaseNumber ? (
                  <strong>#{scopeCaseNumber}</strong>
                ) : (
                  'this case'
                )}
                , each with its SHA-256 seal and perceptual index status. Step 2 of the
                investigation workflow.
              </>
            ) : (
              'Ingested digital assets across active investigations, indexed for perceptual matching and forensic verification.'
            )}
          </p>
        </div>
        {scoped ? (
          <div className="btn-row">
            {/*
              Forward through the workflow, without running anything on the way.
              This button used to call `runAnalysis()` before navigating, which
              appends MATCH_SEARCHED and ANALYSIS_COMPLETED to the case's audit
              chain -- so clicking through step 2 wrote forensic history. The
              Analysis screen reads its own stored verdict and offers the run
              itself, which is where the decision to compute belongs.
            */}
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => onNavigate('analysis', { caseId: scopeCaseId! })}
            >
              Continue to Analysis
              <Icon name="arrow-right" size={14} />
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => onNavigate('intake', { caseId: scopeCaseId! })}
            >
              <Icon name="upload" size={14} />
              Ingest Evidence
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => onNavigate('evidence')}
              title="Every exhibit ingested across all investigations"
            >
              View Full Catalogue
            </button>
          </div>
        ) : (
          <div className="btn-row">
            {/* The catalogue is not inside a case, so the only case-scoped action
                offered here is the one that opens a case: intake. */}
            {activeCase ? (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => onNavigate('evidence', { caseId: activeCase.case_id })}
                title={`Show only the exhibits sealed into #${activeCase.case_number}`}
              >
                Only #{activeCase.case_number}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => onNavigate('intake')}
            >
              <Icon name="upload" size={14} />
              Ingest Evidence
            </button>
          </div>
        )}
      </div>

      {/* Filter and Search Controls.

          The two fields carry flexible bases rather than fixed minimums so the
          row can shrink under one column on narrow screens: `minWidth: 220`
          plus `minWidth: 160` could not fit a 390px viewport and pushed the
          whole filter row 94px past it. */}
      <div className="row row--wrap" style={{ gap: 'var(--space-3)', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div className="row row--wrap" style={{ gap: 'var(--space-3)', flex: '1 1 100%', minWidth: 0 }}>
          <div className="field" style={{ flex: '1 1 280px', minWidth: 0 }}>
            <label className="field__label" htmlFor="evidence-search">
              Search Evidence
            </label>
            <div className="search-box">
              <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
              <input
                id="evidence-search"
                className="search-box__input"
                type="search"
                placeholder="Filename, Evidence ID or SHA-256…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="field" style={{ flex: '0 1 160px', minWidth: 0 }}>
            <label className="field__label" htmlFor="evidence-type">
              Media Type
            </label>
            <select
              id="evidence-type"
              className="input"
              value={mediaType}
              onChange={(e) => setMediaType(e.target.value)}
            >
              <option value="all">All Media Types</option>
              <option value="image">Images</option>
              <option value="video">Videos</option>
              <option value="audio">Audio Files</option>
            </select>
          </div>
        </div>

        {/*
          Row accounting. Without it the table's length is unexplained: a
          truncated catalogue looks like a short one, and a filtered list looks
          like the whole library.

          Counted against the backend's `total`, never against the rows in hand.
          It used to compare the visible rows to `items.length`, so a capped
          response made the page's own size masquerade as the library's: 269
          stored exhibits were reported as "Showing 8 of 100" under a "View All
          (100)" button. `loadedShortOfTotal` is the remaining honest case -- more
          matches exist than one request returns -- and it is stated rather than
          rounded away.
        */}
        <div className="row" style={{ gap: 'var(--space-3)', alignItems: 'center' }}>
          {!loading && !error ? (
            <span className="muted" style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap' }}>
              {displayItems.length === total
                ? `${total} ${total === 1 ? 'exhibit' : 'exhibits'}`
                : `Showing ${displayItems.length} of ${total}`}
              {loadedShortOfTotal ? ` · ${items.length} loaded` : ''}
            </span>
          ) : null}

          {/* Truncation is a catalogue affordance only -- a case's exhibit list is
              never shortened, so there is nothing to expand.

              The label promises exactly what the click delivers: the rows this
              screen actually holds, which is the whole match set unless the
              backend capped the response. */}
          {!scoped && items.length > 8 && !search.trim() && mediaType === 'all' ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => setShowAll(!showAll)}
              title={
                loadedShortOfTotal
                  ? `${total} exhibits match; this screen holds the ${items.length} most recently ingested`
                  : undefined
              }
            >
              {showAll
                ? 'Show First 8 Only'
                : loadedShortOfTotal
                  ? `View Loaded (${items.length} of ${total})`
                  : `View All (${items.length})`}
            </button>
          ) : null}
        </div>
      </div>

      {loading ? (
        <Spinner label={scoped ? 'Loading this case’s exhibits…' : 'Querying evidence repository…'} />
      ) : error ? (
        <ErrorBanner context={scoped ? 'Case Evidence' : 'Evidence Library'} error={error} />
      ) : items.length === 0 ? (
        /*
         * What is empty, why, and what to do next -- three different situations
         * that used to share two sentences. "No evidence ingested yet" is in
         * particular a claim about the whole database, and printing it for a case
         * that simply has no exhibits, or for a filter that matched nothing, says
         * something untrue about everything else on the system.
         */
        <div className="stack" style={{ gap: 'var(--space-3)' }}>
          {search.trim() || mediaType !== 'all' ? (
            <>
              <Empty>
                No exhibit{scoped ? ' in this case' : ' in the catalogue'} matches the current
                search and media-type filter. The filter narrows what is listed; it does not
                change what is stored.
              </Empty>
              <div className="btn-row">
                <button
                  type="button"
                  className="btn btn--ghost"
                  onClick={() => {
                    setSearch('')
                    setMediaType('all')
                  }}
                >
                  Clear Filters
                </button>
              </div>
            </>
          ) : scoped ? (
            <>
              <Empty>
                No evidence has been sealed into{' '}
                {scopeCaseNumber ? `#${scopeCaseNumber}` : 'this case'} yet. The case exists,
                but step 2 of the workflow has not been completed, so there is nothing to
                analyse or trace.
              </Empty>
              <div className="btn-row">
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => onNavigate('intake', { caseId: scopeCaseId! })}
                >
                  <Icon name="upload" size={14} />
                  Ingest Evidence Into This Case
                </button>
              </div>
            </>
          ) : (
            <>
              <Empty>
                No evidence has been ingested on this system yet. Ingesting a file seals it
                with a SHA-256 hash, registers it for perceptual matching and opens a case
                around it.
              </Empty>
              <div className="btn-row">
                <button type="button" className="btn btn--primary" onClick={() => onNavigate('intake')}>
                  <Icon name="upload" size={14} />
                  Ingest First Evidence
                </button>
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="table-wrapper card">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 60 }}>Preview</th>
                <th>File &amp; Metadata</th>
                <th>Type</th>
                {/* Every row carries the same case in scoped mode: a column
                    repeating one value down the page is spent width. */}
                {scoped ? null : <th>Case Association</th>}
                <th>Ingested</th>
                <th>Cryptographic Seal (SHA-256)</th>
                <th>Perceptual Index</th>
              </tr>
            </thead>
            <tbody>
              {displayItems.map((ev) => {
                const typeTone = ev.media_type === 'image' ? 'accent' : ev.media_type === 'video' ? 'warn' : 'ok'
                return (
                  <tr
                    key={ev.evidence_id}
                    style={{ cursor: ev.case_id ? 'pointer' : 'default' }}
                    onClick={() => openCase(ev.case_id)}
                  >
                    <td>
                      <EvidenceThumb ev={ev} />
                    </td>
                    <td>
                      <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                        <span style={{ fontWeight: 700, fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>{ev.filename}</span>
                        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                          {ev.mime_type} · {formatBytes(ev.size_bytes)} {ev.width && ev.height ? `· ${ev.width}×${ev.height}` : ''}
                        </span>
                      </div>
                    </td>
                    <td>
                      <Pill variant={typeTone}>{ev.media_type.toUpperCase()}</Pill>
                    </td>
                    {scoped ? null : (
                      <td>
                        {ev.case_id ? (
                          <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--accent-bright)', fontWeight: 600 }}>
                            {ev.case_id}
                          </span>
                        ) : (
                          <span style={{ color: 'var(--text-faint)' }}>{NOT_MEASURED}</span>
                        )}
                      </td>
                    )}
                    <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                      {formatTimestampShort(ev.ingested_at)}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                        <code style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-2xs)' }}>
                          {shortHash(ev.sha256)}
                        </code>
                        <CopyButton value={ev.sha256} label="" title="Copy SHA-256 seal" />
                      </div>
                    </td>
                    <td>
                      <Pill variant={ev.indexed ? 'ok' : 'neutral'}>
                        {ev.indexed ? 'INDEXED' : 'PENDING'}
                      </Pill>
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
