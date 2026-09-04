/**
 * Screen: Provenance Workspace.
 *
 * Where the earliest known instance of the subject media is reported, together
 * with the propagation graph that connects it to the case evidence.
 *
 * This screen carried the largest concentration of invented provenance in the
 * build, which matters more here than anywhere else: provenance claims are the
 * ones that get attributed to a person.
 *
 *   - The SOURCE field read `Telegram Channel: Political Hub @political_hub`
 *     when no platform was recorded, and prefixed any real platform with
 *     "Telegram Channel:" -- so a file whose recorded platform was "WhatsApp"
 *     was displayed as a Telegram channel.
 *   - The earliest node was labelled **Original Upload**. The backend's own
 *     wording is "earliest known instance in the indexed evidence corpus", and
 *     `Origin.is_absolute_origin` exists precisely because earlier copies can
 *     exist outside the corpus. "Original upload" asserts the one thing the
 *     data cannot support.
 *   - CONFIDENCE was a hardcoded green `HIGH` pill, in two places. Nothing in
 *     the propagation response grades confidence at all.
 *   - Similarity fell back to `98.41%`, the node hash to
 *     `a1b2c3d4e5f6...7890abcdef`, propagation time to `1 day 0 hr 29 mins`, and
 *     the variant count to `2`. Each of those is a forensic measurement, and
 *     each was a literal.
 *   - Middle nodes were labelled `Variant N` by index and their platform
 *     defaulted to `Telegram`.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { MatchesResponse, Origin, PropagationResponse } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { PropagationGraph } from '../components/PropagationGraph'
import {
  NOT_MEASURED,
  formatDistance,
  formatSimilarity,
  formatTimestamp,
  formatTimestampShort,
  orPlaceholder,
  shortHash,
} from '../lib/format'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

/**
 * Elapsed time between the earliest and latest dated instance.
 *
 * Returns `null` unless at least two instances carry a timestamp -- a span needs
 * two ends. Undated instances are common (platforms strip metadata), and the
 * previous build papered over that with the fixed string "1 day 0 hr 29 mins".
 */
function propagationSpan(timestamps: (string | null)[]): string | null {
  const times = timestamps
    .filter((t): t is string => Boolean(t))
    .map((t) => Date.parse(t))
    .filter((n) => Number.isFinite(n))
  if (times.length < 2) return null
  const ms = Math.max(...times) - Math.min(...times)
  const days = Math.floor(ms / 86_400_000)
  const hours = Math.floor((ms % 86_400_000) / 3_600_000)
  const mins = Math.floor((ms % 3_600_000) / 60_000)
  const parts: string[] = []
  if (days) parts.push(`${days}d`)
  if (hours) parts.push(`${hours}h`)
  parts.push(`${mins}m`)
  return parts.join(' ')
}

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

  // On-demand candidate search
  const [liveMatches, setLiveMatches] = useState<MatchesResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)

  // Selected node in the lineage pipeline
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
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

  const origin: Origin | null = propData?.origin ?? analysisData?.origin ?? null
  const graph = propData?.graph ?? null
  const nodes = graph?.nodes ?? []

  const subjectNode = nodes.find((n) => n.is_case_evidence) ?? null
  const earliestEvidenceId = origin?.evidence_id ?? null

  // No synthesised case number: an internal UUID is shown as an internal UUID.
  const activeCaseNumber = caseRecord?.case_number || null

  /** Elapsed span across the real node timestamps, or null when undatable. */
  const span = useMemo(() => propagationSpan(nodes.map((n) => n.timestamp)), [nodes])

  /**
   * Instances that are neither the earliest known one nor the case evidence.
   *
   * Previously `nodes.length > 2 ? nodes.length - 2 : 2`, which reported two
   * intermediate variants for a two-node graph that has none.
   */
  const intermediateCount = useMemo(
    () => nodes.filter((n) => !n.is_case_evidence && n.evidence_id !== earliestEvidenceId).length,
    [nodes, earliestEvidenceId],
  )

  /** Workflow position, derived from real state. Nothing is pre-ticked. */
  const steps = useMemo(
    () => [
      { label: 'Case', done: Boolean(caseRecord) },
      { label: 'Evidence', done: evidence.length > 0 },
      { label: 'Analysis', done: isReady(analysis) || Boolean(caseRecord?.latest_verdict) },
    ],
    [caseRecord, evidence.length, analysis],
  )

  // Select subject node by default if none selected
  const activeSelectedNode =
    nodes.find((n) => n.evidence_id === (selectedNodeId || earliestEvidenceId || subjectNode?.evidence_id)) ||
    nodes[0] ||
    null

  const seededMatches = analysisData?.matches ?? null
  const effectiveMatches = liveMatches ?? seededMatches

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
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. TOP: CASE CONTEXT & 6-PHASE STEPPER */}
      <div
        className="row"
        style={{
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '10px 18px',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius)',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div className="row" style={{ gap: 10, alignItems: 'center' }}>
          <span style={{ fontSize: '10px', color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'var(--mono)', fontWeight: 700 }}>
            {activeCaseNumber ? 'CASE NUMBER' : 'INTERNAL CASE ID'}
          </span>
          <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
            {activeCaseNumber ? `#${activeCaseNumber}` : shortHash(currentCaseId, 12)}
          </code>
          {caseRecord?.title ? (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              · {caseRecord.title}
            </span>
          ) : null}
        </div>

        {/* Workflow stepper. Ticks reflect real state; nothing is pre-ticked. */}
        <nav className="row" style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }} aria-label="Investigation Workflow">
          {steps.map((step, idx) => (
            <span key={step.label} className="row" style={{ gap: 6, alignItems: 'center' }}>
              <span
                style={{
                  color: step.done ? 'var(--ok-bright)' : 'var(--text-faint)',
                  fontWeight: step.done ? 600 : 400,
                }}
              >
                {idx + 1}. {step.label} {step.done ? '✓' : '·'}
              </span>
              <span style={{ color: 'var(--text-faint)' }}>→</span>
            </span>
          ))}
          <span style={{ background: 'var(--accent)', color: '#ffffff', padding: '2px 8px', borderRadius: 4, fontWeight: 700 }}>4. Provenance</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>5. Audit</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>6. Report</span>
        </nav>
      </div>

      {/* 2. PAGE HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">PROVENANCE</h1>
          <p className="screen__lead">
            Trace this file to the earliest instance of it held in the indexed evidence corpus.
          </p>
        </div>
      </div>

      {/*
        A failed or in-flight trace must not render as a finding. Without this,
        `propagation.phase === 'error'` fell through to "No prior instance found"
        and "No propagation nodes indexed for this case" -- presenting a network
        failure as a negative forensic result.
      */}
      {propagation.phase === 'error' ? (
        <ErrorBanner error={propagation.error} context="Provenance trace" onRetry={loadPropagation} />
      ) : null}
      {propagation.phase === 'loading' ? <Spinner label="Tracing provenance..." /> : null}

      {/* 3. 2-COLUMN MAIN WORKSPACE (MATCHING PANEL 6 IN COLLAGE) */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 280px',
          gap: 'var(--space-4)',
          alignItems: 'start',
        }}
      >
        {/* LEFT MAIN COLUMN */}
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          {/* Box 1: EARLIEST KNOWN INSTANCE */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              EARLIEST KNOWN INSTANCE
            </span>

            {origin ? (
              <>
                <div className="grid-3col" style={{ gap: 12, background: 'var(--surface-2)', padding: '12px 16px', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      RECORDED PLATFORM
                    </span>
                    {/*
                      The platform is whatever was recorded at ingest, verbatim.
                      It is not inferred, and there is no default: an unrecorded
                      platform reads "Not recorded", never a named service.
                    */}
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: origin.platform ? 'var(--text-strong)' : 'var(--text-muted)' }}>
                      {origin.platform || 'Not recorded'}
                    </span>
                  </div>

                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      TIMESTAMP
                    </span>
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {formatTimestamp(origin.timestamp)}
                    </span>
                    <span style={{ fontSize: '10px', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                      Source: {orPlaceholder(origin.timestamp_source)}
                    </span>
                  </div>

                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      SCOPE OF THIS FINDING
                    </span>
                    {/*
                      This slot used to hold a hardcoded green `HIGH` pill. The
                      propagation response grades no confidence anywhere; what it
                      does publish is `is_absolute_origin`, which is the more
                      important distinction and the one that was being hidden.
                    */}
                    <div>
                      <Pill variant={origin.is_absolute_origin ? 'neutral' : 'unavailable'}>
                        {origin.is_absolute_origin ? 'NO EARLIER COPY IN CORPUS' : 'CORPUS-LIMITED'}
                      </Pill>
                    </div>
                  </div>
                </div>

                <div className="grid-3col" style={{ gap: 12 }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      FILE
                    </span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontFamily: 'var(--mono)', wordBreak: 'break-all' }}>
                      {orPlaceholder(origin.filename)}
                    </span>
                  </div>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      ROLE
                    </span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {orPlaceholder(origin.role)}
                    </span>
                  </div>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      DISTANCE TO CASE EVIDENCE
                    </span>
                    <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {origin.distance_to_case_evidence === null
                        ? NOT_MEASURED
                        : formatDistance(origin.distance_to_case_evidence)}
                    </span>
                  </div>
                </div>

                {origin.is_synthetic ? (
                  <Pill variant="unavailable">SYNTHETIC DEMO DATA</Pill>
                ) : null}

                {/* The backend's own caveat, rendered rather than dropped. */}
                {origin.caveat ? (
                  <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
                    {origin.caveat}
                  </p>
                ) : null}
              </>
            ) : propagation.phase === 'ready' ? (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-strong)' }}>No prior instance found</span>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                  No earlier instance of this file was identified in the indexed evidence corpus. The absence of a prior match is not a finding that the file is authentic.
                </p>
              </div>
            ) : (
              <Empty>
                {propagation.phase === 'error'
                  ? 'The provenance trace did not complete, so no earliest instance can be reported.'
                  : 'Provenance has not been traced for this case yet.'}
              </Empty>
            )}
          </div>

          {/* Box 2: HORIZONTAL LINEAGE NODE GRAPH */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              LINEAGE PROPAGATION TIMELINE
            </span>

            {nodes.length > 0 ? (
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: 12,
                  overflowX: 'auto',
                  padding: '16px 8px',
                }}
              >
                {nodes.map((node, idx, arr) => {
                  /*
                   * Roles come from the node, never from its position. The old
                   * code treated `idx === 0` as the earliest instance and
                   * `idx === arr.length - 1` as the case evidence, so a graph
                   * returned in any other order was mislabelled -- and every
                   * middle node was tinted by index.
                   */
                  const isEarliest = earliestEvidenceId !== null && node.evidence_id === earliestEvidenceId
                  const isSubject = node.is_case_evidence
                  const isSelected = activeSelectedNode?.evidence_id === node.evidence_id

                  const nodeColor = isEarliest ? '#38bdf8' : isSubject ? '#ef4444' : '#94a3b8'

                  return (
                    <div key={node.evidence_id} style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                      <div
                        onClick={() => setSelectedNodeId(node.evidence_id)}
                        role="button"
                        tabIndex={0}
                        style={{
                          background: isSelected ? 'var(--surface-3)' : 'var(--surface-2)',
                          border: `1px solid ${isSelected ? nodeColor : 'var(--border)'}`,
                          borderRadius: 'var(--radius)',
                          padding: '12px 14px',
                          display: 'flex',
                          flexDirection: 'column',
                          alignItems: 'center',
                          gap: 6,
                          textAlign: 'center',
                          cursor: 'pointer',
                          minWidth: 140,
                          flex: 1,
                          boxShadow: isSelected ? `0 0 0 2px ${nodeColor}33` : undefined,
                        }}
                      >
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: '50%',
                            background: `${nodeColor}22`,
                            border: `2px solid ${nodeColor}`,
                            color: nodeColor,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            fontWeight: 800,
                            fontSize: '12px',
                          }}
                        >
                          <Icon name={isEarliest ? 'diamond' : isSubject ? 'square' : 'dot'} size={14} />
                        </div>
                        <span style={{ fontSize: '10.5px', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                          {formatTimestampShort(node.timestamp)}
                        </span>
                        {/*
                          "Original Upload" was the label here. It is the one
                          claim provenance cannot make: the corpus only bounds
                          what PRAMAAN has indexed, so the honest ceiling is
                          "earliest known instance". Other nodes carry the
                          backend's own `role` instead of an invented
                          "Variant N".
                        */}
                        <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                          {isEarliest
                            ? 'Earliest known instance'
                            : isSubject
                              ? 'Case evidence'
                              : orPlaceholder(node.role)}
                        </span>
                        <span style={{ fontSize: '10px', color: node.platform ? 'var(--text-faint)' : 'var(--text-muted)' }}>
                          {node.platform || 'Platform not recorded'}
                        </span>
                      </div>

                      {idx < arr.length - 1 ? (
                        <span style={{ color: 'var(--text-faint)', fontSize: 16 }}>→</span>
                      ) : null}
                    </div>
                  )
                })}
              </div>
            ) : (
              <Empty>
                {propagation.phase === 'error'
                  ? 'The provenance trace did not complete. No lineage can be shown.'
                  : propagation.phase === 'ready'
                    ? 'No propagation nodes indexed for this case.'
                    : 'Provenance has not been traced for this case yet.'}
              </Empty>
            )}
          </div>

          {/* Box 3: DUAL SUMMARY BOXES (MATCH INFORMATION + TIMELINE SUMMARY) */}
          <div className="grid-2col" style={{ gap: 'var(--space-4)' }}>
            <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 6 }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                MATCH INFORMATION
              </span>
              {/*
                Was: `Found high match (98.41%) with the earliest instance.` --
                a fixed percentage and an unconditional "high match" claim, shown
                even for a node with no measured similarity at all. Similarity is
                reported when the backend measured it and withheld when it did
                not; "high" is not a band this API defines, so it is not used.
              */}
              {activeSelectedNode ? (
                <>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Similarity to case evidence:</span>
                    <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {activeSelectedNode.similarity_to_case_evidence === null
                        ? 'Not measured'
                        : formatSimilarity(activeSelectedNode.similarity_to_case_evidence)}
                    </span>
                  </div>
                  <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                    <span style={{ color: 'var(--text-muted)' }}>Perceptual distance:</span>
                    <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {activeSelectedNode.distance_to_case_evidence === null
                        ? 'Not measured'
                        : formatDistance(activeSelectedNode.distance_to_case_evidence)}
                    </span>
                  </div>
                  <div className="row" style={{ gap: 6, alignItems: 'center', marginTop: 4 }}>
                    <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>SHA-256:</span>
                    <code className="mono" style={{ fontSize: '10.5px', color: 'var(--text-strong)' }}>
                      {activeSelectedNode.sha256 ? shortHash(activeSelectedNode.sha256, 20) : NOT_MEASURED}
                    </code>
                  </div>
                </>
              ) : (
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  No instance selected.
                </span>
              )}
            </div>

            <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 6 }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                TIMELINE SUMMARY
              </span>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Span across dated instances:</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                  {span ?? 'Not computable'}
                </span>
              </div>
              {span === null ? (
                <span style={{ fontSize: '10.5px', color: 'var(--text-faint)' }}>
                  Fewer than two instances carry a timestamp. A span needs two ends.
                </span>
              ) : null}
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Indexed instances:</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>{nodes.length}</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Intermediate instances:</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>{intermediateCount}</span>
              </div>
            </div>
          </div>
        </div>

        {/* RIGHT SIDE COLUMN: COMPACT NODE DETAILS PANEL */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
          <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
            NODE DETAILS
          </span>

          {activeSelectedNode ? (
            <div className="stack" style={{ gap: 12, fontSize: 'var(--text-xs)' }}>
              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>ROLE</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>
                  {activeSelectedNode.is_case_evidence
                    ? 'Case evidence'
                    : activeSelectedNode.evidence_id === earliestEvidenceId
                      ? 'Earliest known instance'
                      : orPlaceholder(activeSelectedNode.role)}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>FILE</span>
                <span style={{ fontWeight: 600, color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                  {orPlaceholder(activeSelectedNode.filename)}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>HASH (SHA-256)</span>
                <code className="mono" style={{ fontSize: '10px', color: 'var(--accent-bright)', wordBreak: 'break-all' }}>
                  {activeSelectedNode.sha256 ? shortHash(activeSelectedNode.sha256, 24) : NOT_MEASURED}
                </code>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                  SIMILARITY TO CASE EVIDENCE
                </span>
                {/*
                  `?? '98.41%'` lived here. Note the guard is `=== null`, not
                  truthiness: a genuine similarity of 0 is a measurement, and the
                  old `? :` test reported the literal for it.
                */}
                <span style={{ fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)', fontSize: 'var(--text-sm)' }}>
                  {activeSelectedNode.similarity_to_case_evidence === null
                    ? 'Not measured'
                    : formatSimilarity(activeSelectedNode.similarity_to_case_evidence)}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>LINK BASIS</span>
                {/*
                  Replaces a hardcoded `HIGH` confidence pill. How the link was
                  established is a fact the response carries; a confidence grade
                  for it is not.
                */}
                <span style={{ fontWeight: 600, color: 'var(--text-strong)' }}>
                  {orPlaceholder(activeSelectedNode.discovered_by)}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>RECORDED PLATFORM</span>
                <span style={{ fontWeight: 600, color: activeSelectedNode.platform ? 'var(--text-strong)' : 'var(--text-muted)' }}>
                  {activeSelectedNode.platform || 'Not recorded'}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>TRANSFORMATION</span>
                <span style={{ fontWeight: 600, color: 'var(--text-strong)' }}>
                  {orPlaceholder(activeSelectedNode.transformation)}
                </span>
              </div>

              {activeSelectedNode.is_synthetic ? (
                <Pill variant="unavailable">SYNTHETIC DEMO DATA</Pill>
              ) : null}
            </div>
          ) : (
            <Empty>Select a node in the graph to view details.</Empty>
          )}
        </div>
      </div>

      {/* 4. DISCLOSURE FOR NEAR-DUPLICATE TABLE & TECHNICAL ARTEFACTS */}
      <details className="disclosure card" style={{ padding: 'var(--space-3) var(--space-4)' }}>
        <summary style={{ fontWeight: 700, fontSize: 'var(--text-sm)' }}>
          <Icon name="arrow-right" size={13} className="disclosure__chevron" />
          Near-Duplicate Candidates &amp; Topological Graph ({effectiveMatches?.total_candidates ?? 0} candidates)
        </summary>
        <div className="disclosure__panel stack" style={{ gap: 'var(--space-4)', marginTop: 'var(--space-3)' }}>
          {/*
            The propagation response ships its own method statement,
            interpretation, notes and caveats. None of them were rendered, which
            is how the screen came to read more confidently than the data does.
          */}
          {propData ? (
            <div className="stack" style={{ gap: 6, fontSize: 'var(--text-xs)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>Method</span>
                <code className="mono" style={{ fontSize: '10.5px', color: 'var(--text-strong)' }}>
                  {orPlaceholder(propData.method)}
                </code>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-muted)' }}>Indexed instances / matched candidates</span>
                <code className="mono" style={{ fontSize: '10.5px', color: 'var(--text-strong)' }}>
                  {propData.instance_count} / {propData.matched_candidate_count}
                </code>
              </div>
              {propData.interpretation ? (
                <p style={{ color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>{propData.interpretation}</p>
              ) : null}
              {propData.truncated ? (
                <Pill variant="unavailable">RESULT TRUNCATED — NOT THE COMPLETE SET</Pill>
              ) : null}
              {propData.caveats.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-muted)', lineHeight: 1.6 }}>
                  {propData.caveats.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              ) : null}
              {propData.notes.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-faint)', lineHeight: 1.6 }}>
                  {propData.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          {graph && graph.nodes.length > 0 ? (
            <PropagationGraph graph={graph} earliestEvidenceId={earliestEvidenceId} />
          ) : null}

          {searchError ? (
            <ErrorBanner error={searchError} context="Candidate search" onRetry={runCandidateSearch} />
          ) : null}

          <div className="btn-row">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={runCandidateSearch}
              disabled={searching}
            >
              {searching ? <Spinner label="Searching..." /> : <Icon name="search" size={13} />}
              Re-run Candidate Search
            </button>
          </div>
        </div>
      </details>

      {/* 5. BOTTOM ACTION BAR: VERIFY AUDIT */}
      <div
        className="card row"
        style={{
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 'var(--space-3)',
          padding: 'var(--space-4)',
          background: 'var(--surface-2)',
          border: '1px solid var(--border-accent)',
        }}
      >
        <div className="stack" style={{ gap: 2 }}>
          <span style={{ fontWeight: 800, fontSize: '11px', textTransform: 'uppercase', color: 'var(--accent-bright)', fontFamily: 'var(--mono)', letterSpacing: '0.06em' }}>
            NEXT ACTION
          </span>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
            Verify the recorded custody chain for this case before compiling the report.
          </span>
        </div>

        <button
          type="button"
          className="btn btn--primary"
          style={{ padding: '8px 22px', fontWeight: 700 }}
          onClick={() => onNavigate('audit', { caseId: currentCaseId })}
        >
          Verify Audit →
        </button>
      </div>
    </div>
  )
}
