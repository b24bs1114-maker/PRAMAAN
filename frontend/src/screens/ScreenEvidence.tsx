/**
 * Screen: Evidence.
 *
 * Every ingested media item across all cases, in one worklist. The columns are
 * the ones that help locate an item and see its state — a thumbnail, filename,
 * type, the case it belongs to, when it arrived, its SHA-256 seal, and whether
 * it is in the perceptual index.
 *
 * What this screen does NOT do is invent a verdict. The list endpoint carries no
 * per-item analysis result, so there is no "COMPLETED"/"VERIFIED" pill here —
 * that would be a fabricated finding. The honest, real signals are the SHA-256
 * seal (shown short, full value one click away) and the index flag. Row → the
 * case the evidence belongs to.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Evidence } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { formatBytes, formatTimestampShort, shortHash } from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import type { RoutePath } from '../lib/router'

/** Small thumbnail that degrades to a neutral glyph when the image can't load. */
function EvidenceThumb({ ev }: { ev: Evidence }) {
  const [failed, setFailed] = useState(false)
  const showImage = isImageMedia(ev.media_type) && !failed
  return (
    <div
      style={{
        width: 44,
        height: 44,
        flexShrink: 0,
        borderRadius: 'var(--radius, 6px)',
        overflow: 'hidden',
        border: '1px solid var(--border)',
        background: 'var(--surface-2)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
      }}
    >
      {showImage ? (
        <img
          src={evidenceFileUrl(ev.evidence_id)}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={() => setFailed(true)}
        />
      ) : (
        <Icon name="document" size={18} style={{ color: 'var(--text-faint)' }} />
      )}
    </div>
  )
}

export function ScreenEvidence({
  onNavigate,
  onSelectCase,
}: {
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [items, setItems] = useState<Evidence[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  const [mediaType, setMediaType] = useState<string>('all')
  const [search, setSearch] = useState<string>('')

  // Debounce so typing in the search box does not fire a request per keystroke.
  useEffect(() => {
    let active = true
    const handle = setTimeout(() => {
      setLoading(true)
      api
        .listGlobalEvidence({
          media_type: mediaType !== 'all' ? mediaType : undefined,
          q: search.trim() || undefined,
        })
        .then((data) => {
          if (active) {
            setItems(data.evidence)
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
  }, [mediaType, search])

  const openCase = (caseId: string | null) => {
    if (!caseId) return
    onSelectCase(caseId)
    onNavigate('case-detail', { caseId })
  }

  const [showAll, setShowAll] = useState(false)
  const displayItems = showAll || search.trim() || mediaType !== 'all' ? items : items.slice(0, 6)

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Evidence Library</h1>
        <p className="screen__lead">Ingested media items across active investigations.</p>
      </div>

      <div className="row row--wrap" style={{ gap: 'var(--space-3)', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div className="row row--wrap" style={{ gap: 'var(--space-3)', flex: '1 1 auto' }}>
          <div className="field" style={{ flex: '1 1 260px', minWidth: 220 }}>
            <label className="field__label" htmlFor="evidence-search">
              Search
            </label>
            <div className="search-box">
              <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
              <input
                id="evidence-search"
                className="search-box__input"
                type="search"
                placeholder="Filename, evidence ID or SHA-256…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
          </div>

          <div className="field" style={{ minWidth: 160 }}>
            <label className="field__label" htmlFor="evidence-type">
              Media type
            </label>
            <select
              id="evidence-type"
              className="input"
              value={mediaType}
              onChange={(e) => setMediaType(e.target.value)}
            >
              <option value="all">All media types</option>
              <option value="image">Image</option>
              <option value="video">Video</option>
              <option value="audio">Audio</option>
            </select>
          </div>
        </div>

        {items.length > 6 && !search.trim() && mediaType === 'all' ? (
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => setShowAll(!showAll)}
          >
            {showAll ? 'Show curated evidence' : `View all evidence (${items.length})`}
          </button>
        ) : null}
      </div>

      {loading ? (
        <Spinner label="Loading evidence items…" />
      ) : error ? (
        <ErrorBanner context="Evidence" error={error} />
      ) : items.length === 0 ? (
        <Empty>
          {search.trim() || mediaType !== 'all'
            ? 'No evidence matches the current search and filter.'
            : 'No evidence ingested yet. Open a case to upload media.'}
        </Empty>
      ) : (
        <div className="table-wrapper card">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 56 }} aria-label="Preview" />
                <th>File</th>
                <th>Type</th>
                <th>Case</th>
                <th>Received</th>
                <th>SHA-256</th>
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
                        <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{ev.filename}</span>
                        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                          {formatBytes(ev.size_bytes)}
                        </span>
                      </div>
                    </td>
                    <td>
                      <Pill variant={typeTone}>{ev.media_type.toUpperCase()}</Pill>
                    </td>
                    <td>
                      {ev.case_id ? (
                        <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                          {ev.case_id.slice(0, 8)}…
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-faint)' }}>—</span>
                      )}
                    </td>
                    <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', color: 'var(--text-muted)' }}>
                      {formatTimestampShort(ev.ingested_at)}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                        <code style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-2xs)' }}>
                          {shortHash(ev.sha256)}
                        </code>
                        <CopyButton value={ev.sha256} label="" title="Copy SHA-256 digest" />
                      </div>
                    </td>
                    <td>
                      <Pill variant={ev.indexed ? 'accent' : 'neutral'}>
                        {ev.indexed ? 'INDEXED' : 'NOT INDEXED'}
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
