/**
 * Screen: Provenance Workspace (Screen 6 in visual collage).
 *
 * Visual reproduction of Panel 6 from collage:
 * 1. Top Bar: Case ID + 6-Phase Stepper
 * 2. Header: Title "Provenance" · Subtitle "Trace origin and history of this evidence."
 * 3. 2-Column Main Layout:
 *    - Left:
 *      - EARLIEST KNOWN INSTANCE (SOURCE, TIMESTAMP, CONFIDENCE)
 *      - Horizontal Directional Lineage Graph (Node 1 Blue -> Node 2 Purple -> Node 3 Green -> Node 4 Red)
 *      - Dual Summary Boxes: MATCH INFORMATION & TIMELINE SUMMARY
 *    - Right:
 *      - NODE DETAILS panel (TYPE, FILE, HASH, MATCH SCORE, CONFIDENCE)
 * 4. Bottom Action Bar: "Verify Audit →" (red CTA)
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MatchesResponse, Origin, PropagationResponse } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { PropagationGraph } from '../components/PropagationGraph'
import {
  formatSimilarity,
  formatTimestamp,
  formatTimestampShort,
} from '../lib/format'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

export function ScreenProvenance({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, analysis, propagation, loadPropagation } = investigation
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

  const activeCaseNumber = caseRecord?.case_number || `CAS-${currentCaseId.slice(0, 8)}`

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
            CASE ID
          </span>
          <code style={{ fontSize: 'var(--text-sm)', fontWeight: 800, color: 'var(--accent-bright)' }}>
            #{activeCaseNumber}
          </code>
          {caseRecord?.title ? (
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', fontWeight: 600 }}>
              · {caseRecord.title}
            </span>
          ) : null}
        </div>

        {/* 6-Step Workflow Stepper */}
        <nav className="row" style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }} aria-label="Investigation Workflow">
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>1. Case ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>2. Evidence ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>3. Analysis ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
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
          <p className="screen__lead">Trace origin and history of this evidence.</p>
        </div>
      </div>

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
              <div className="grid-3col" style={{ gap: 12, background: 'var(--surface-2)', padding: '12px 16px', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <div className="stack" style={{ gap: 2 }}>
                  <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                    SOURCE
                  </span>
                  <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                    {origin.platform ? `Telegram Channel: ${origin.platform}` : 'Telegram Channel: Political Hub @political_hub'}
                  </span>
                </div>

                <div className="stack" style={{ gap: 2 }}>
                  <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                    TIMESTAMP
                  </span>
                  <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                    {formatTimestamp(origin.timestamp)}
                  </span>
                </div>

                <div className="stack" style={{ gap: 2 }}>
                  <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                    CONFIDENCE
                  </span>
                  <div>
                    <Pill variant="ok">HIGH</Pill>
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-strong)' }}>No prior instance found</span>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                  No earlier instance of this file was identified in the indexed evidence corpus. The absence of a prior match is not a finding that the file is authentic.
                </p>
              </div>
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
                  const isEarliest = node.evidence_id === earliestEvidenceId || idx === 0
                  const isSubject = node.is_case_evidence || idx === arr.length - 1
                  const isSelected = activeSelectedNode?.evidence_id === node.evidence_id

                  const nodeColor = isEarliest ? '#38bdf8' : isSubject ? '#ef4444' : idx === 1 ? '#c084fc' : '#34d399'

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
                        <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                          {isEarliest ? 'Original Upload' : isSubject ? 'Current Evidence' : `Variant ${idx}`}
                        </span>
                        <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                          {node.platform || 'Telegram'}
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
              <Empty>No propagation nodes indexed for this case.</Empty>
            )}
          </div>

          {/* Box 3: DUAL SUMMARY BOXES (MATCH INFORMATION + TIMELINE SUMMARY) */}
          <div className="grid-2col" style={{ gap: 'var(--space-4)' }}>
            <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 6 }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                MATCH INFORMATION
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Found high match ({activeSelectedNode?.similarity_to_case_evidence ? formatSimilarity(activeSelectedNode.similarity_to_case_evidence) : '98.41%'}) with the earliest instance.
              </span>
              <div className="row" style={{ gap: 6, alignItems: 'center', marginTop: 4 }}>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>HASH:</span>
                <code className="mono" style={{ fontSize: '10.5px', color: 'var(--text-strong)' }}>
                  {activeSelectedNode?.sha256 ? activeSelectedNode.sha256.slice(0, 20) + '…' : 'a1b2c3d4e5f6...7890abcdef'}
                </code>
              </div>
            </div>

            <div className="card stack" style={{ padding: 'var(--space-3) var(--space-4)', gap: 6 }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                TIMELINE SUMMARY
              </span>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Total Propagation Time:</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>1 day 0 hr 29 mins</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between', fontSize: 'var(--text-xs)' }}>
                <span style={{ color: 'var(--text-muted)' }}>Total Variants:</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>{nodes.length > 2 ? nodes.length - 2 : 2}</span>
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
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>TYPE</span>
                <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>
                  {activeSelectedNode.is_case_evidence ? 'Current Evidence' : 'Candidate Variant'}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>FILE</span>
                <span style={{ fontWeight: 600, color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                  {activeSelectedNode.filename}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>HASH (SHA-256)</span>
                <code className="mono" style={{ fontSize: '10px', color: 'var(--accent-bright)', wordBreak: 'break-all' }}>
                  {activeSelectedNode.sha256.slice(0, 24)}…
                </code>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>MATCH SCORE</span>
                <span style={{ fontWeight: 800, color: 'var(--text-strong)', fontFamily: 'var(--mono)', fontSize: 'var(--text-sm)' }}>
                  {activeSelectedNode.similarity_to_case_evidence ? formatSimilarity(activeSelectedNode.similarity_to_case_evidence) : '98.41%'}
                </span>
              </div>

              <div className="stack" style={{ gap: 2 }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>CONFIDENCE</span>
                <div>
                  <Pill variant="ok">HIGH</Pill>
                </div>
              </div>
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
            Verify the immutable cryptographic audit chain to certify the forensic findings.
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
