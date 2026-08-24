/**
 * Screen: Provenance — "Where did it come from?"
 *
 * The signature page. It answers one question in one downward-reading spine:
 *   CURRENT FILE ↓ MATCHES ↓ TRANSFORMATIONS ↓ REPOSTS ↓ EARLIEST KNOWN INSTANCE
 * built entirely from what the backend actually returned — never invented.
 *
 * Two forensic guarantees shape the design:
 *
 *  1. never-run ≠ no-result. Until propagation is traced, the stages below the
 *     current file are not shown as empty findings ("no matches") — they are
 *     shown as not-yet-traced, with a button to trace. Once traced, an empty
 *     stage is an honest negative result.
 *
 *  2. earliest known instance ≠ absolute origin. The origin is stated in the
 *     backend's own words — "earliest known instance in the indexed evidence
 *     corpus" — and its caveat is shown verbatim. A near-duplicate is a
 *     candidate, not proof of where the file was born.
 *
 * READ-ONLY DEFAULT: opening this page issues no POST. It reads the propagation
 * trace seeded by analysis (a GET) and the matches already returned by a prior
 * analysis. Running a fresh candidate search (POST /matches, which writes an
 * audit row) is an explicit, opt-in action. The perceptual-index internals,
 * the propagation graph, the timeline and the raw match arithmetic all live
 * behind "Technical details".
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MatchesResponse, Origin, PropagationResponse, TimelineEvent } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon, type IconName } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { PropagationGraph } from '../components/PropagationGraph'
import { Section } from '../components/Section'
import {
  formatDistance,
  formatSimilarity,
  formatTimestamp,
  formatTimestampShort,
  orPlaceholder,
  shortHash,
} from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

/** The backend's exact wording for the origin. Used in more than one place. */
const CORPUS_PHRASE = 'earliest known instance in the indexed evidence corpus'

export function ScreenProvenance({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, analysis, propagation, loadPropagation } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  // Auto-trace provenance on mount if idle
  useEffect(() => {
    if (currentCaseId && propagation.phase === 'idle') {
      loadPropagation()
    }
  }, [currentCaseId, propagation.phase, loadPropagation])

  // On-demand candidate search. POST /matches writes an audit row, so it never
  // fires on mount — only when the operator asks for it.
  const [liveMatches, setLiveMatches] = useState<MatchesResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
        <div className="screen__head">
          <h1 className="screen__title">Where did it come from?</h1>
          <p className="screen__lead">
            Provenance reconstructs where near-duplicates of a file appear in the indexed evidence
            corpus, and the earliest instance among them.
          </p>
        </div>
        <Empty>No case is selected. Open a case to trace its provenance.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View cases
          </button>
        </div>
      </div>
    )
  }

  const analysisData = isReady(analysis) ? analysis.data : null
  const propData: PropagationResponse | null = isReady(propagation) ? propagation.data : null
  const traced = propData !== null

  const origin: Origin | null = propData?.origin ?? analysisData?.origin ?? null
  const timeline: TimelineEvent[] = propData?.timeline ?? analysisData?.timeline ?? []
  const graph = propData?.graph ?? null
  const nodes = graph?.nodes ?? []

  const subjectNode = nodes.find((n) => n.is_case_evidence) ?? null
  const subjectEvidenceId =
    subjectNode?.evidence_id ??
    analysisData?.verdict_evidence_id ??
    analysisData?.verdict?.evidence_id ??
    evidence[0]?.evidence_id ??
    null
  const subjectFilename =
    subjectNode?.filename ??
    analysisData?.verdict?.filename ??
    evidence[0]?.filename ??
    '—'

  // Real lineage counts — every one drawn from the trace, none fabricated.
  const otherNodes = nodes.filter((n) => !n.is_case_evidence)
  const matchedCount = propData?.matched_candidate_count ?? otherNodes.length
  const transformations = Array.from(
    new Set(
      [
        ...nodes.map((n) => n.transformation),
        ...(graph?.edges.map((e) => e.transformation) ?? []),
      ].filter((t): t is string => Boolean(t) && t !== 'none'),
    ),
  )
  const platforms = (propData?.platforms ?? []).filter(Boolean)

  const seededMatches = analysisData?.matches ?? null
  const effectiveMatches = liveMatches ?? seededMatches

  const mediaTypeOf = (evidenceId: string | null): string | null =>
    evidenceId ? evidence.find((e) => e.evidence_id === evidenceId)?.media_type ?? null : null

  const runCandidateSearch = () => {
    if (!currentCaseId) return
    setSearching(true)
    setSearchError(null)
    api.matches(currentCaseId).then(
      (data) => {
        setLiveMatches(data)
        setSearching(false)
      },
      (err) => {
        setSearchError(err)
        setSearching(false)
      },
    )
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-6)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Where did it come from?</h1>
        <p className="screen__lead">
          The spine below reads top to bottom, from the file under examination to the earliest
          instance of it found in the indexed evidence corpus. Matches are near-duplicate
          candidates, not proof of absolute origin.
        </p>
      </div>

      {/* KEY FINDING — only once we have actually traced. */}
      {traced ? <KeyFinding origin={origin} matchedCount={matchedCount} /> : null}

      {/* THE SPINE — the signature visual. */}
      <Section title="Lineage">
        {propagation.phase === 'loading' ? (
          <Spinner label="Tracing propagation…" />
        ) : propagation.phase === 'error' ? (
          <ErrorBanner error={propagation.error} context="Propagation" onRetry={loadPropagation} />
        ) : (
          <div className="stack" style={{ gap: 0 }}>
            <FlowStage
              label="Current file"
              icon="document"
              thumbEvidenceId={subjectEvidenceId}
              thumbMediaType={mediaTypeOf(subjectEvidenceId)}
            >
              <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{subjectFilename}</span>
              <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                The evidence under examination
              </span>
            </FlowStage>

            {!traced ? (
              <>
                <FlowArrow />
                <div
                  className="card stack"
                  style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}
                >
                  <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                    Propagation has not been traced for this case yet. Tracing reconstructs the
                    matches, transformations, reposts and earliest known instance from the indexed
                    corpus. It is a read-only lookup and writes no findings.
                  </span>
                  <div className="btn-row">
                    <button
                      type="button"
                      className="btn btn--primary"
                      onClick={loadPropagation}
                    >
                      <Icon name="search" size={14} />
                      Trace propagation
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => onNavigate('analysis', { caseId: currentCaseId })}
                    >
                      Run full analysis
                      <Icon name="arrow-right" size={14} />
                    </button>
                  </div>
                </div>
              </>
            ) : (
              <>
                <FlowArrow />
                <FlowStage
                  label="Near-duplicate matches"
                  icon="search"
                  empty={matchedCount === 0}
                >
                  {matchedCount > 0 ? (
                    <span style={{ fontSize: 'var(--text-sm)' }}>
                      {matchedCount} candidate {matchedCount === 1 ? 'instance' : 'instances'} in the
                      indexed corpus
                    </span>
                  ) : (
                    <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                      No near-duplicate of this file was found in the indexed corpus. That is not a
                      finding that the file is original.
                    </span>
                  )}
                </FlowStage>

                <FlowArrow />
                <FlowStage
                  label="Transformations"
                  icon="refresh"
                  empty={transformations.length === 0}
                >
                  {transformations.length > 0 ? (
                    <div className="row row--wrap" style={{ gap: 6 }}>
                      {transformations.map((t) => (
                        <Pill key={t} variant="neutral">
                          {t}
                        </Pill>
                      ))}
                    </div>
                  ) : (
                    <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                      No re-encoding or edits were recorded between instances.
                    </span>
                  )}
                </FlowStage>

                <FlowArrow />
                <FlowStage label="Reposts" icon="external" empty={platforms.length === 0}>
                  {platforms.length > 0 ? (
                    <div className="row row--wrap" style={{ gap: 6 }}>
                      {platforms.map((p) => (
                        <Pill key={p} variant="accent">
                          {p}
                        </Pill>
                      ))}
                    </div>
                  ) : (
                    <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                      No platform reposts were recorded for any instance.
                    </span>
                  )}
                </FlowStage>

                <FlowArrow />
                <FlowStage
                  label="Earliest known instance"
                  icon="diamond"
                  accent={Boolean(origin)}
                  thumbEvidenceId={origin?.evidence_id ?? null}
                  thumbMediaType={mediaTypeOf(origin?.evidence_id ?? null)}
                  empty={!origin}
                >
                  {origin ? (
                    <>
                      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>
                          {origin.filename}
                        </span>
                        {origin.is_absolute_origin ? null : (
                          <Pill variant="unavailable">NOT ABSOLUTE ORIGIN</Pill>
                        )}
                      </div>
                      <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                        {formatTimestampShort(origin.timestamp)} · {orPlaceholder(origin.platform)}
                      </span>
                      <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                        {CORPUS_PHRASE}
                      </span>
                    </>
                  ) : (
                    <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                      No earlier instance is indexed. This is not a finding that the file is
                      original — only that no earlier copy exists in this index.
                    </span>
                  )}
                </FlowStage>
              </>
            )}
          </div>
        )}

        {traced ? (
          <div className="btn-row">
            <button
              type="button"
              className="btn"
              onClick={loadPropagation}
              disabled={propagation.phase === 'loading'}
            >
              <Icon name="refresh" size={15} />
              Refresh trace
            </button>
          </div>
        ) : null}
      </Section>

      {/* NEAR-DUPLICATE CANDIDATES — compact table, read-only default. */}
      <Section
        title="Near-duplicate candidates"
        aside={
          effectiveMatches ? (
            <>
              {effectiveMatches.total_candidates} candidate
              {effectiveMatches.total_candidates === 1 ? '' : 's'}
              {liveMatches ? ' · live search' : ' · from analysis'}
            </>
          ) : null
        }
      >
        {searchError ? (
          <ErrorBanner error={searchError} context="Candidate search" onRetry={runCandidateSearch} />
        ) : null}

        {effectiveMatches ? (
          <CandidateTable matches={effectiveMatches} mediaTypeOf={mediaTypeOf} />
        ) : (
          <Empty>
            No candidate matches loaded. Run a candidate search to look up near-duplicates in the
            perceptual index — this writes an audit row.
          </Empty>
        )}

        <div className="btn-row">
          <button
            type="button"
            className="btn btn--primary"
            onClick={runCandidateSearch}
            disabled={searching}
          >
            {searching ? <Spinner label="Searching index…" /> : <Icon name="search" size={14} />}
            {effectiveMatches ? 'Re-run candidate search' : 'Run candidate search'}
          </button>
        </div>
      </Section>

      {/* TECHNICAL DETAILS — graph, timeline, method and raw match internals. */}
      {traced || effectiveMatches ? (
        <details className="disclosure">
          <summary>
            <Icon name="arrow-right" size={14} className="disclosure__chevron" />
            Technical details
          </summary>
          <div className="disclosure__panel stack" style={{ gap: 'var(--space-5)' }}>
            {graph && graph.nodes.length ? (
              <div className="stack" style={{ gap: 'var(--space-3)' }}>
                <h3 className="label">Propagation graph</h3>
                <PropagationGraph graph={graph} earliestEvidenceId={origin?.evidence_id ?? null} />
                <InstanceTable
                  nodes={graph.nodes}
                  earliestEvidenceId={origin?.evidence_id ?? null}
                />
              </div>
            ) : null}

            {timeline.length ? (
              <div className="stack" style={{ gap: 'var(--space-3)' }}>
                <h3 className="label">Timeline</h3>
                <TimelineList timeline={timeline} earliestEvidenceId={origin?.evidence_id ?? null} />
              </div>
            ) : null}

            {propData ? <MethodAndLimits propagation={propData} /> : null}

            {origin ? (
              <div className="stack" style={{ gap: 'var(--space-2)' }}>
                <h3 className="label">Origin detail</h3>
                <dl className="dl">
                  <dt>Label</dt>
                  <dd>{origin.label}</dd>
                  <dt>Evidence ID</dt>
                  <dd className="mono break-all">{origin.evidence_id}</dd>
                  <dt>Timestamp</dt>
                  <dd>
                    {formatTimestamp(origin.timestamp)}
                    {origin.timestamp_source ? (
                      <span className="faint"> · source: {origin.timestamp_source}</span>
                    ) : null}
                  </dd>
                  <dt>Discovered by</dt>
                  <dd>{orPlaceholder(origin.discovered_by)}</dd>
                  <dt>Distance to case evidence</dt>
                  <dd className="mono">{formatDistance(origin.distance_to_case_evidence, 64)}</dd>
                  <dt>Absolute origin</dt>
                  <dd>
                    {origin.is_absolute_origin ? (
                      <Pill variant="weak-authentic">ESTABLISHED</Pill>
                    ) : (
                      <Pill variant="unavailable">NOT ESTABLISHED</Pill>
                    )}
                  </dd>
                </dl>
                <p className="verdict__caveat">{origin.caveat}</p>
              </div>
            ) : null}

            {effectiveMatches ? <MatchInternals matches={effectiveMatches} /> : null}
          </div>
        </details>
      ) : null}
    </div>
  )
}

// --- The spine ---------------------------------------------------------------

/** One stage in the lineage spine: a media/icon box beside a labelled body. */
function FlowStage({
  label,
  icon,
  children,
  thumbEvidenceId = null,
  thumbMediaType = null,
  accent = false,
  empty = false,
}: {
  label: string
  icon: IconName
  children: React.ReactNode
  thumbEvidenceId?: string | null
  thumbMediaType?: string | null
  accent?: boolean
  empty?: boolean
}) {
  return (
    <div
      className="card"
      style={{
        padding: 'var(--space-4)',
        display: 'flex',
        gap: 12,
        alignItems: 'flex-start',
        borderLeft: accent ? '3px solid var(--accent)' : undefined,
        opacity: empty ? 0.72 : 1,
      }}
    >
      <LineageThumb evidenceId={thumbEvidenceId} mediaType={thumbMediaType} icon={icon} />
      <div className="stack" style={{ gap: 3, minWidth: 0, flex: 1 }}>
        <span className="label">{label}</span>
        {children}
      </div>
    </div>
  )
}

/** Downward connector between stages. */
function FlowArrow() {
  return (
    <div
      aria-hidden="true"
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        color: 'var(--text-faint)',
      }}
    >
      <span style={{ width: 2, height: 12, background: 'var(--border)' }} />
      <Icon name="arrow-right" size={16} style={{ transform: 'rotate(90deg)' }} />
    </div>
  )
}

/** 44px preview that degrades to a glyph for non-images or load failures. */
function LineageThumb({
  evidenceId,
  mediaType,
  icon,
}: {
  evidenceId: string | null
  mediaType: string | null
  icon: IconName
}) {
  const [failed, setFailed] = useState(false)
  // Try the image whenever we have an id and the type is image or unknown.
  const showImage =
    Boolean(evidenceId) && (mediaType === null || isImageMedia(mediaType)) && !failed
  return (
    <div
      style={{
        width: 44,
        height: 44,
        flexShrink: 0,
        borderRadius: 'var(--radius)',
        overflow: 'hidden',
        border: '1px solid var(--border)',
        background: 'var(--surface-2)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-faint)',
      }}
    >
      {showImage && evidenceId ? (
        <img
          src={evidenceFileUrl(evidenceId)}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          onError={() => setFailed(true)}
        />
      ) : (
        <Icon name={icon} size={18} />
      )}
    </div>
  )
}

// --- Key finding -------------------------------------------------------------

function KeyFinding({ origin, matchedCount }: { origin: Origin | null; matchedCount: number }) {
  if (origin) {
    const when = formatTimestampShort(origin.timestamp)
    const where = origin.platform ? ` on ${origin.platform}` : ''
    return (
      <Banner
        tone="info"
        title="Key finding"
        detail={`The ${CORPUS_PHRASE} is ${origin.filename}${
          when !== '—' ? `, observed ${when}` : ''
        }${where}. ${
          origin.is_absolute_origin
            ? ''
            : 'This is the earliest instance visible in the local index, not a determination of real-world origin.'
        }`}
      />
    )
  }
  return (
    <Banner
      tone="info"
      title="Key finding"
      detail={`No earlier instance of this file was found in the ${CORPUS_PHRASE}${
        matchedCount > 0
          ? ` among the ${matchedCount} near-duplicate candidate(s).`
          : '. No near-duplicate was found at all.'
      } The absence of an earlier copy is not a finding that the file is original.`}
    />
  )
}

// --- Candidate table ---------------------------------------------------------

function bandTone(band: string): PillTone {
  const b = band.toLowerCase()
  if (b.includes('strong')) return 'error'
  if (b.includes('near') || b.includes('medium') || b.includes('moderate')) return 'warn'
  if (b.includes('weak') || b.includes('low')) return 'neutral'
  return 'accent'
}

function CandidateTable({
  matches,
  mediaTypeOf,
}: {
  matches: MatchesResponse
  mediaTypeOf: (evidenceId: string | null) => string | null
}) {
  const multiQuery = matches.queries.length > 1
  const anyCandidate = matches.queries.some((q) => q.candidates.length > 0)

  if (!anyCandidate) {
    return (
      <Empty>
        No near-duplicate candidates within the Hamming-distance threshold. No earlier or repeated
        instance of this file exists in the indexed corpus.
      </Empty>
    )
  }

  return (
    <div className="stack" style={{ gap: 'var(--space-4)' }}>
      {matches.queries.map((q) => (
        <div key={q.evidence_id} className="stack" style={{ gap: 'var(--space-2)' }}>
          {multiQuery ? (
            <span className="label">
              Query: {q.filename} · {q.candidates.length} candidate
              {q.candidates.length === 1 ? '' : 's'}
            </span>
          ) : null}
          {q.candidates.length === 0 ? (
            <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
              No near-duplicate candidates for this query.
            </span>
          ) : (
            <div className="table-wrapper">
              <table className="table">
                <thead>
                  <tr>
                    <th style={{ width: 44 }}>#</th>
                    <th>Candidate</th>
                    <th className="table__num">Distance</th>
                    <th className="table__num">Similarity</th>
                    <th>Platform</th>
                    <th>Observed</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {q.candidates.map((c) => (
                    <tr key={c.evidence_id}>
                      <td className="table__num" style={{ fontWeight: 700 }}>
                        {c.rank}
                      </td>
                      <td>
                        <div className="row" style={{ gap: 8, minWidth: 0 }}>
                          <LineageThumb
                            evidenceId={c.evidence_id}
                            mediaType={mediaTypeOf(c.evidence_id)}
                            icon="document"
                          />
                          <div className="stack" style={{ gap: 1, minWidth: 0 }}>
                            <span style={{ fontSize: 'var(--text-xs)', fontWeight: 600 }}>
                              {c.filename}
                            </span>
                            {c.is_synthetic ? (
                              <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                                synthetic demo data
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </td>
                      <td className="table__num">{formatDistance(c.distance)}</td>
                      <td className="table__num">{formatSimilarity(c.similarity)}</td>
                      <td style={{ fontSize: 'var(--text-xs)' }}>
                        {c.platform || 'Indexed corpus'}
                      </td>
                      <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap' }}>
                        {formatTimestampShort(c.observed_at ?? c.ingested_at)}
                      </td>
                      <td>
                        <Pill variant={bandTone(c.confidence_band)}>
                          {c.confidence_band.toUpperCase()}
                        </Pill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
      <p className="note">{matches.interpretation}</p>
    </div>
  )
}

// --- Technical details -------------------------------------------------------

function InstanceTable({
  nodes,
  earliestEvidenceId,
}: {
  nodes: PropagationResponse['graph']['nodes']
  earliestEvidenceId: string | null
}) {
  if (!nodes.length) return <Empty>No instances in the graph.</Empty>
  return (
    <div className="table-wrapper">
      <table className="table">
        <caption className="visually-hidden">
          Instances in the propagation graph — the accessible equivalent of the graph
        </caption>
        <thead>
          <tr>
            <th>Role</th>
            <th>Instance</th>
            <th>Platform</th>
            <th className="table__num">Gen.</th>
            <th>Observed</th>
            <th className="table__num">Dist.</th>
            <th className="table__num">Sim.</th>
          </tr>
        </thead>
        <tbody>
          {nodes.map((node) => (
            <tr key={node.evidence_id}>
              <td>
                {node.is_case_evidence ? (
                  <Pill variant="info">SUBJECT</Pill>
                ) : node.evidence_id === earliestEvidenceId ? (
                  <Pill variant="accent">EARLIEST</Pill>
                ) : (
                  <Pill variant="neutral">{node.role.toUpperCase()}</Pill>
                )}
              </td>
              <td>
                <div style={{ fontSize: 'var(--text-xs)' }}>{node.filename}</div>
                <div className="faint mono" style={{ fontSize: 'var(--text-2xs)' }}>
                  {shortHash(node.evidence_id, 8)}
                </div>
                {node.transformation && node.transformation !== 'none' ? (
                  <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                    {node.transformation}
                  </div>
                ) : null}
              </td>
              <td style={{ fontSize: 'var(--text-xs)' }}>{orPlaceholder(node.platform)}</td>
              <td className="table__num">{orPlaceholder(node.generation)}</td>
              <td className="table__num" style={{ whiteSpace: 'nowrap' }}>
                {formatTimestampShort(node.timestamp)}
              </td>
              <td className="table__num">{formatDistance(node.distance_to_case_evidence)}</td>
              <td className="table__num">
                {formatSimilarity(node.similarity_to_case_evidence)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TimelineList({
  timeline,
  earliestEvidenceId,
}: {
  timeline: TimelineEvent[]
  earliestEvidenceId: string | null
}) {
  if (!timeline.length) {
    return (
      <Empty>
        No dated instances to place on a timeline. Where a copy carries no recorded observation
        time, the backend does not substitute one.
      </Empty>
    )
  }
  return (
    <ol className="timeline__list">
      {timeline.map((event, i) => (
        <li
          key={`${event.evidence_id}-${event.event_type}-${i}`}
          className={`timeline__item${
            event.evidence_id === earliestEvidenceId ? ' timeline__item--earliest' : ''
          }`}
        >
          <div className="timeline__when">
            {formatTimestamp(event.occurred_at)}
            {event.timestamp_source ? ` · ${event.timestamp_source}` : ''}
          </div>
          <div style={{ fontSize: 'var(--text-sm)' }}>{event.description}</div>
          <div className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
            {event.event_type}
            {event.platform ? ` · ${event.platform}` : ''}
            {event.generation !== null ? ` · gen ${event.generation}` : ''}
            {event.is_synthetic ? ' · synthetic demo data' : ''}
          </div>
        </li>
      ))}
    </ol>
  )
}

function MethodAndLimits({ propagation }: { propagation: PropagationResponse }) {
  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }}>
      <h3 className="label">Method &amp; limits</h3>
      <dl className="dl">
        <dt>Method</dt>
        <dd>{propagation.method}</dd>
        <dt>Instances</dt>
        <dd>
          {propagation.instance_count} in the graph · {propagation.matched_candidate_count} reached
          by perceptual match
        </dd>
        <dt>Platforms</dt>
        <dd>{propagation.platforms.length ? propagation.platforms.join(', ') : '—'}</dd>
        <dt>Generations</dt>
        <dd>{propagation.generations.length ? propagation.generations.join(', ') : '—'}</dd>
      </dl>
      <p className="note">{propagation.interpretation}</p>
      {propagation.caveats.length ? (
        <div className="stack--tight">
          <h4 className="label">Limits of this reconstruction</h4>
          <ul className="note-list">
            {propagation.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {propagation.notes.length ? (
        <ul className="note-list">
          {propagation.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function MatchInternals({ matches }: { matches: MatchesResponse }) {
  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <h3 className="label">Perceptual match internals</h3>
      <dl className="dl">
        <dt>Strong-candidate max distance</dt>
        <dd className="mono">{matches.thresholds.strong_candidate_max_distance}</dd>
        <dt>Near-duplicate max distance</dt>
        <dd className="mono">{matches.thresholds.near_duplicate_max_distance}</dd>
        <dt>Hash bits</dt>
        <dd className="mono">{matches.thresholds.hash_bits}</dd>
        <dt>Basis</dt>
        <dd>{matches.thresholds.basis}</dd>
      </dl>
      {matches.queries.map((q) => (
        <dl className="dl" key={q.evidence_id}>
          <dt>Query</dt>
          <dd className="break-all">{q.filename}</dd>
          <dt>pHash</dt>
          <dd>
            <span className="row" style={{ gap: 6 }}>
              <code className="mono" style={{ fontSize: 'var(--text-2xs)' }}>
                {q.phash ? shortHash(q.phash) : '—'}
              </code>
              {q.phash ? <CopyButton value={q.phash} label="" title="Copy pHash" /> : null}
            </span>
          </dd>
          <dt>Index backend</dt>
          <dd className="mono">
            {q.index_backend} · v{q.index_version} · {q.indexed_count} indexed
          </dd>
          <dt>Algorithm</dt>
          <dd className="mono">{q.algorithm}</dd>
        </dl>
      ))}
    </div>
  )
}
