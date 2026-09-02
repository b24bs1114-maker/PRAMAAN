/**
 * Screen: Evidence Library.
 *
 * Comprehensive digital evidence repository across investigations:
 * - High-fidelity media previews (images, video, audio)
 * - File metadata (size, MIME type, dimensions, ingestion date)
 * - SHA-256 cryptographic seals with quick-copy
 * - Perceptual index registration indicators
 * - Case lineage and association links
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

/** Thumbnail with media type badge and fallback */
function EvidenceThumb({ ev }: { ev: Evidence }) {
  const [failed, setFailed] = useState(false)
  const showImage = isImageMedia(ev.media_type) && !failed

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
      {showImage ? (
        <img
          src={evidenceFileUrl(ev.evidence_id)}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={() => setFailed(true)}
        />
      ) : (
        <Icon name="document" size={20} style={{ color: 'var(--accent-bright)' }} />
      )}
    </div>
  )
}

export function ScreenEvidence({
  investigation,
  onNavigate,
  onSelectCase,
}: {
  investigation?: import('../state/useInvestigation').Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onSelectCase: (caseId: string) => void
}) {
  const [items, setItems] = useState<Evidence[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  const [mediaType, setMediaType] = useState<string>('all')
  const [search, setSearch] = useState<string>('')

  const activeCase = investigation?.caseRecord ?? null

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
  const displayItems = showAll || search.trim() || mediaType !== 'all' ? items : items.slice(0, 8)

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <div>
          <h1 className="screen__title">Evidence Library & Catalog</h1>
          <p className="screen__lead">
            Ingested digital assets across active investigations, indexed for perceptual matching and forensic verification.
          </p>
        </div>
        {activeCase ? (
          <div className="btn-row">
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => {
                investigation?.runAnalysis()
                onNavigate('analysis', { caseId: activeCase.case_id })
              }}
            >
              Run Analysis
              <Icon name="arrow-right" size={14} />
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => onNavigate('intake')}
            >
              <Icon name="upload" size={14} />
              Ingest Evidence
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onNavigate('intake')}
          >
            <Icon name="upload" size={14} />
            Ingest Evidence
          </button>
        )}
      </div>

      {/* Filter and Search Controls */}
      <div className="row row--wrap" style={{ gap: 'var(--space-3)', alignItems: 'flex-end', justifyContent: 'space-between' }}>
        <div className="row row--wrap" style={{ gap: 'var(--space-3)', flex: '1 1 auto' }}>
          <div className="field" style={{ flex: '1 1 280px', minWidth: 220 }}>
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

          <div className="field" style={{ minWidth: 160 }}>
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

        {items.length > 8 && !search.trim() && mediaType === 'all' ? (
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => setShowAll(!showAll)}
          >
            {showAll ? 'Show Curated Items' : `View All (${items.length})`}
          </button>
        ) : null}
      </div>

      {loading ? (
        <Spinner label="Querying evidence repository…" />
      ) : error ? (
        <ErrorBanner context="Evidence Library" error={error} />
      ) : items.length === 0 ? (
        <Empty>
          {search.trim() || mediaType !== 'all'
            ? 'No evidence items match the current query and filters.'
            : 'No evidence ingested yet. Open a case to ingest files.'}
        </Empty>
      ) : (
        <div className="table-wrapper card">
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 60 }}>Preview</th>
                <th>File & Metadata</th>
                <th>Type</th>
                <th>Case Association</th>
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
                    <td>
                      {ev.case_id ? (
                        <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--accent-bright)', fontWeight: 600 }}>
                          {ev.case_id}
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-faint)' }}>-</span>
                      )}
                    </td>
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
