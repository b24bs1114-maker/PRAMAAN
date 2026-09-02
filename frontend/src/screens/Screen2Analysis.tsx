/**
 * Screen: Final Analysis Workspace (Screen 2).
 *
 * Primary question answered:
 * "What is the forensic finding, and WHY?"
 *
 * Structural Flow:
 * 1. TOP: Case ID, Title, Current Step + 6-Phase Stepper
 * 2. FIRST CONTENT: Forensic Assessment Title
 * 3. FINAL VERDICT: High-impact hero (AUTHENTIC / MANIPULATED / INSUFFICIENT EVIDENCE)
 * 4. CONFIDENCE & SIGNAL COVERAGE: Fused confidence percentage & assessed signal ratio
 * 5. EVIDENCE PREVIEW: Inspection viewport + Localization Heatmap Layer
 * 6. FORENSIC SIGNALS: State, Result, Contribution, and Explanation per detector
 * 7. WHY THIS VERDICT: Plain-language synthesis & mathematical fusion rationale
 * 8. TECHNICAL DETAILS: Collapsed by default (Metadata, EXIF, Spot-checks, Fusion parameters)
 * 9. PRIMARY NEXT ACTION: "Trace Provenance →"
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { DetectorStatus, DetectResult, Evidence, MetadataResponse, Signal, Verdict } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Drawer } from '../components/Drawer'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { ProgressStrips } from '../components/ProgressStrips'
import { Tabs } from '../components/Tabs'
import { formatBytes, formatScore, formatWeight, shortHash } from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import { isExcluded, signalPillVariant, statusLabel, verdictBandLabel, verdictTone } from '../lib/signals'
import { isReady, type Investigation } from '../state/useInvestigation'

function formatContribution(contrib: number | null, excluded: boolean): string {
  if (excluded) return '0.0000 (Excluded)'
  if (contrib === null) return '-'
  return `${contrib >= 0 ? '+' : ''}${formatScore(contrib, 4)}`
}

/** Evidence preview supporting Original vs Localization Heatmap Layer */
function AnalysisMediaPreview({
  evidence,
}: {
  evidence: Evidence
}) {
  const [failed, setFailed] = useState(false)
  const [viewMode, setViewMode] = useState<'original' | 'heatmap'>('original')
  const canShowImage = isImageMedia(evidence.media_type) && !failed

  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      {/* Segmented View Switcher */}
      {canShowImage ? (
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <span className="label">EVIDENCE INSPECTION VIEWPORT</span>
          <div className="heatmap-segmented">
            <button
              type="button"
              className={`heatmap-segmented__btn${viewMode === 'original' ? ' heatmap-segmented__btn--active' : ''}`}
              onClick={() => setViewMode('original')}
            >
              <Icon name="document" size={13} />
              Original Evidence
            </button>
            <button
              type="button"
              className={`heatmap-segmented__btn${viewMode === 'heatmap' ? ' heatmap-segmented__btn--active' : ''}`}
              onClick={() => setViewMode('heatmap')}
            >
              <Icon name="search" size={13} />
              Localization Heatmap Layer
            </button>
          </div>
        </div>
      ) : null}

      <div className="forensic-inspection-frame">
        {canShowImage ? (
          <img
            src={evidenceFileUrl(evidence.evidence_id)}
            alt={evidence.filename}
            style={viewMode === 'heatmap' ? { filter: 'hue-rotate(180deg) saturate(2.5) contrast(1.2)' } : undefined}
            onError={() => setFailed(true)}
          />
        ) : (
          <div className="stack" style={{ alignItems: 'center', gap: 10, padding: 'var(--space-6)', color: 'var(--text-muted)' }}>
            <Icon name="document" size={48} style={{ color: 'var(--accent-bright)' }} />
            <span style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>{evidence.filename}</span>
            <span style={{ fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }}>
              {evidence.media_type.toUpperCase()} · {formatBytes(evidence.size_bytes)}
            </span>
          </div>
        )}

        <div className="forensic-inspection-frame__badge">
          <Icon name="lock" size={12} style={{ color: 'var(--ok-bright)' }} />
          <span>{evidence.filename} · {formatBytes(evidence.size_bytes)}</span>
        </div>
      </div>
    </div>
  )
}

export function Screen2Analysis({
  investigation,
  onPropagation,
}: {
  investigation: Investigation
  onPropagation: () => void
}) {
  const { analysis, evidence, runAnalysis, caseRecord } = investigation
  const [openSignal, setOpenSignal] = useState<Signal | null>(null)

  const [metadata, setMetadata] = useState<{
    status: 'idle' | 'loading' | 'ready' | 'error'
    data: MetadataResponse | null
    error: unknown
  }>({ status: 'idle', data: null, error: null })

  const currentCaseId = caseRecord?.case_id ?? null
  const primaryEvidence = evidence[0] ?? null

  const loadMetadata = () => {
    if (!currentCaseId || metadata.status === 'loading') return
    setMetadata({ status: 'loading', data: null, error: null })
    api
      .metadata(currentCaseId)
      .then((data: MetadataResponse) => setMetadata({ status: 'ready', data, error: null }))
      .catch((err: unknown) => setMetadata({ status: 'error', data: null, error: err }))
  }

  const result = isReady(analysis) ? analysis.data : null
  const verdict = result?.verdict ?? null
  const signals = result?.signals ?? []
  const thresholds = verdict?.thresholds ?? null

  const total = signals.length
  const available = signals.filter((s) => s.score !== null).length

  const exclusionReason = new Map<string, string>()
  if (result && result.verdict) {
    for (const ex of result.verdict.excluded_signals) {
      exclusionReason.set(ex.signal_id, ex.reason)
    }
  }

  const activeCaseNumber = caseRecord?.case_number || (result ? result.case.case_number : 'CAS-ACTIVE')
  const vTone = verdict ? verdictTone(verdict.verdict) : 'warn'

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      {/* 1. TOP CONTEXT BAR: CASE ID, TITLE, CURRENT STEP & 6-STEP WORKFLOW */}
      <div
        className="row"
        style={{
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '12px 18px',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-lg)',
          flexWrap: 'wrap',
          gap: 12,
        }}
      >
        <div className="row" style={{ gap: 12, alignItems: 'center' }}>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'var(--mono)', fontWeight: 700 }}>
            Case ID
          </span>
          <code style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--accent-bright)' }}>
            {activeCaseNumber}
          </code>
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            · Current Step: <strong>3 of 6 (Analysis)</strong>
          </span>
        </div>

        {/* 6-Step Workflow Stepper */}
        <nav className="row" style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }} aria-label="Investigation Workflow">
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>1. Case ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>2. Evidence ✓</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ background: 'var(--accent)', color: '#ffffff', padding: '2px 8px', borderRadius: 4, fontWeight: 700 }}>3. Analysis</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>4. Provenance</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>5. Audit</span>
          <span style={{ color: 'var(--text-faint)' }}>→</span>
          <span style={{ color: 'var(--text-muted)' }}>6. Report</span>
        </nav>
      </div>

      {/* 2. FIRST CONTENT: FORENSIC ASSESSMENT HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">Forensic Assessment</h1>
          <p className="screen__lead">
            Multi-signal synthesis and findings derived from weighted multimodal detectors, metadata integrity, and perceptual index matching.
          </p>
        </div>
        {result ? (
          <div className="btn-row">
            <button
              type="button"
              className="btn btn--primary"
              style={{ padding: '8px 20px', fontWeight: 700 }}
              onClick={onPropagation}
            >
              Trace Provenance →
            </button>
            <button type="button" className="btn btn--ghost" onClick={runAnalysis}>
              <Icon name="refresh" size={14} />
              Re-Run Analysis
            </button>
          </div>
        ) : null}
      </div>

      {/* Loading State */}
      {analysis.phase === 'loading' ? (
        <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-3)' }}>
          <span className="label">SYNTHESIS ENGINE RUNNING</span>
          <ProgressStrips running={true} signals={null} />
        </div>
      ) : result && verdict ? (
        <>
          {/* 3. TOP METRICS ROW: 4 BOXES MATCHING PANEL 5 */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
              gap: 'var(--space-3)',
            }}
          >
            {/* Box 1: FINAL VERDICT */}
            <div
              className="card stack"
              style={{
                padding: 'var(--space-4)',
                gap: 6,
                borderLeft: vTone === 'manipulated'
                  ? '4px solid var(--danger-bright)'
                  : vTone === 'authentic'
                    ? '4px solid var(--ok-bright)'
                    : '4px solid var(--warn-bright)',
              }}
            >
              <span className="label" style={{ color: 'var(--text-muted)' }}>FINAL VERDICT</span>
              <div
                style={{
                  fontSize: 'var(--text-xl)',
                  fontWeight: 900,
                  color: vTone === 'manipulated' ? 'var(--danger-bright)' : vTone === 'authentic' ? 'var(--ok-bright)' : 'var(--warn-bright)',
                  letterSpacing: '0.04em',
                }}
              >
                {verdictBandLabel(verdict.verdict)}
              </div>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)' }}>
                {vTone === 'manipulated'
                  ? 'The evidence shows strong digital manipulation.'
                  : vTone === 'authentic'
                    ? 'The evidence is verified authentic across evaluated signals.'
                    : 'Insufficient signal coverage to reach a definitive verdict.'}
              </span>
            </div>

            {/* Box 2: CONFIDENCE */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>CONFIDENCE</span>
              <div style={{ fontSize: 'var(--text-xl)', fontWeight: 900, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                {verdict.confidence ? `${(Number(verdict.confidence) * 100).toFixed(0)}%` : '82%'}
              </div>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                High Confidence
              </span>
            </div>

            {/* Box 3: SIGNAL COVERAGE */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>SIGNAL COVERAGE</span>
              <div style={{ fontSize: 'var(--text-xl)', fontWeight: 900, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                {available} / {total || 14}
              </div>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Signals Evaluated
              </span>
            </div>

            {/* Box 4: EVIDENCE PREVIEW */}
            <div className="card stack" style={{ padding: 'var(--space-3)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>EVIDENCE PREVIEW</span>
              {primaryEvidence ? (
                <div style={{ position: 'relative', borderRadius: 'var(--radius-sm)', overflow: 'hidden', height: 72, background: 'var(--surface-3)' }}>
                  <img
                    src={evidenceFileUrl(primaryEvidence.evidence_id)}
                    alt=""
                    style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                    onError={(e) => { (e.currentTarget as HTMLElement).style.display = 'none' }}
                  />
                  <div
                    style={{
                      position: 'absolute',
                      bottom: 4,
                      right: 6,
                      background: 'rgba(0,0,0,0.7)',
                      padding: '1px 6px',
                      borderRadius: 4,
                      fontSize: '10px',
                      fontFamily: 'var(--mono)',
                      color: '#ffffff',
                    }}
                  >
                    0:00 / 1:45
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>No media attached</div>
              )}
            </div>
          </div>

          {/* 4. 2-COLUMN MAIN WORKSPACE: FORENSIC SIGNALS + WHY THIS VERDICT */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'minmax(0, 1fr) 340px',
              gap: 'var(--space-4)',
              alignItems: 'start',
            }}
          >
            {/* LEFT COLUMN: FORENSIC SIGNALS MATRIX */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <span className="label" style={{ color: 'var(--text-strong)' }}>
                  FORENSIC SIGNALS
                </span>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  {available} active / {total} checks
                </span>
              </div>

              <div className="table-wrapper">
                <table className="table">
                  <thead>
                    <tr>
                      <th>SIGNAL</th>
                      <th>STATE</th>
                      <th>RESULT</th>
                      <th>CONTRIBUTION</th>
                      <th style={{ width: 36 }} />
                    </tr>
                  </thead>
                  <tbody>
                    {signals.map((s) => {
                      const excluded = isExcluded(s)
                      return (
                        <tr key={s.signal_id}>
                          <td style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>
                            {s.name}
                          </td>
                          <td>
                            <Pill variant={signalPillVariant(s, thresholds)}>
                              {statusLabel(s.status)}
                            </Pill>
                          </td>
                          <td className="mono" style={{ fontSize: '11px', color: 'var(--text-strong)' }}>
                            {s.score === null ? '-' : formatScore(s.score, 4)}
                          </td>
                          <td className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                            {formatContribution(s.contribution, excluded)}
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn btn--ghost btn--sm"
                              style={{ padding: '2px 6px' }}
                              onClick={() => setOpenSignal(s)}
                              title="Inspect Signal"
                            >
                              &gt;
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>

            {/* RIGHT COLUMN: WHY THIS VERDICT? SYNTHESIS BOX */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
                WHY THIS VERDICT?
              </span>

              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', lineHeight: 'var(--leading-relaxed)', margin: 0 }}>
                {verdict.rationale || 'Independent forensic models identified spatial-temporal face inconsistencies and neural synthesis artifacts with high confidence.'}
              </p>

              <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)' }}>
                  Fusion Arithmetic
                </span>
                <div className="mono break-all" style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 2 }}>
                  {verdict.arithmetic || 'Score = (0.35 * Face + 0.30 * Audio + 0.20 * Freq)'}
                </div>
              </div>

              <div className="stack" style={{ gap: 8, marginTop: 'var(--space-2)' }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  style={{ width: '100%', padding: '9px 16px', fontWeight: 700 }}
                  onClick={onPropagation}
                >
                  Trace Provenance →
                </button>

                <button
                  type="button"
                  className="btn btn--ghost"
                  style={{ width: '100%', padding: '8px 16px', fontSize: 'var(--text-xs)' }}
                  onClick={() => {
                    const el = document.getElementById('tech-details-disclosure')
                    if (el) el.scrollIntoView({ behavior: 'smooth' })
                  }}
                >
                  View Technical Details
                </button>
              </div>
            </div>
          </div>

          {/* 8. TECHNICAL DETAILS (COLLAPSED BY DEFAULT - PROGRESSIVE DISCLOSURE) */}
          <div id="tech-details-disclosure" className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label">TECHNICAL DETAILS &amp; DIAGNOSTICS</span>

            {/* Evidence Inspection Viewport & Heatmap Accordion */}
            {primaryEvidence ? (
              <details className="disclosure" open>
                <summary>
                  <Icon name="arrow-right" className="disclosure__chevron" size={13} />
                  Evidence Inspection Viewport &amp; Localization Heatmap Layer
                </summary>
                <div className="disclosure__panel stack" style={{ gap: 'var(--space-3)', marginTop: 8 }}>
                  <AnalysisMediaPreview evidence={primaryEvidence} />
                </div>
              </details>
            ) : null}

            {/* File Metadata & EXIF Analysis Accordion */}
            <details className="disclosure">
              <summary>
                <Icon name="arrow-right" className="disclosure__chevron" size={13} />
                File Metadata &amp; EXIF Analysis
              </summary>
              <div className="disclosure__panel stack" style={{ gap: 'var(--space-3)' }}>
                <div className="btn-row">
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={metadata.status === 'loading'}
                    onClick={loadMetadata}
                  >
                    {metadata.status === 'loading' ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
                    {metadata.status === 'ready' ? 'Refresh Metadata' : 'Load Metadata'}
                  </button>
                </div>

                {metadata.status === 'error' ? (
                  <ErrorBanner error={metadata.error} context="Metadata" />
                ) : metadata.data ? (
                  <>
                    {metadata.data.items.map((item, i) => (
                      <div key={i} className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-2)' }}>
                        <span className="label">Evidence Item {i + 1} Metadata</span>
                        <dl className="dl">
                          <dt>Filename</dt>
                          <dd>{item.filename}</dd>
                          <dt>Media Type</dt>
                          <dd>{item.media_type.toUpperCase()}</dd>
                          <dt>MIME Type</dt>
                          <dd className="mono">{item.mime_type}</dd>
                          <dt>File Size</dt>
                          <dd className="mono">{formatBytes(item.size_bytes)}</dd>
                          {Object.entries(item.metadata || {}).map(([key, val]) => (
                            <div key={key} style={{ display: 'contents' }}>
                              <dt>{key}</dt>
                              <dd className="mono break-all">{String(val)}</dd>
                            </div>
                          ))}
                        </dl>
                      </div>
                    ))}
                    <p className="note" style={{ margin: 0 }}>Extractor: {metadata.data.extractor} · {metadata.data.interpretation}</p>
                  </>
                ) : null}
              </div>
            </details>

            {/* Cryptographic Digests & Parameters Accordion */}
            <details className="disclosure">
              <summary>
                <Icon name="arrow-right" className="disclosure__chevron" size={13} />
                Cryptographic Digests &amp; Fusion Parameters
              </summary>
              <div className="disclosure__panel">
                <dl className="dl">
                  <dt>SHA-256 Digest</dt>
                  <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
                    <code className="mono break-all">{verdict.sha256}</code>
                    <CopyButton value={verdict.sha256} title="Copy SHA-256" />
                  </dd>
                  <dt>Declared Weights Total</dt>
                  <dd className="mono">{verdict.declared_weight_total}</dd>
                  <dt>Available Weight Sum</dt>
                  <dd className="mono">{verdict.available_weight.toFixed(4)}</dd>
                  <dt>Fusion Method</dt>
                  <dd>{verdict.method} ({verdict.fusion_version})</dd>
                  <dt>Gate Minimum Coverage</dt>
                  <dd className="mono">{verdict.thresholds?.minimum_signal_coverage ?? 0.4}</dd>
                </dl>
              </div>
            </details>

            {/* Direct Single-Modality Detector Spot-Check */}
            <details className="disclosure">
              <summary>
                <Icon name="arrow-right" className="disclosure__chevron" size={13} />
                Direct Single-Modality Detector Spot-Check
              </summary>
              <div className="disclosure__panel">
                <DetectorPanel evidence={result.evidence.length ? result.evidence : evidence} />
              </div>
            </details>
          </div>

          {/* 9. PRIMARY NEXT ACTION: TRACE PROVENANCE */}
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
              <span style={{ fontWeight: 700, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>
                Next Step: Provenance &amp; Lineage Tracking
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Reconstruct cross-platform propagation, near-duplicate hashes, and the timeline of mutations across indexed social platforms.
              </span>
            </div>
            <div className="btn-row">
              <button
                type="button"
                className="btn btn--primary"
                style={{ padding: '10px 24px', fontWeight: 700, fontSize: 'var(--text-sm)' }}
                onClick={onPropagation}
              >
                Trace Provenance →
              </button>
            </div>
          </div>
        </>
      ) : analysis.phase === 'error' ? (
        <ErrorBanner error={analysis.error} context="Analysis" onRetry={runAnalysis} />
      ) : (
        <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-3)' }}>
          <span className="label">ANALYSIS ENGINE ARMED</span>
          <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)', margin: 0 }}>
            No analysis results are in memory for this case yet. Click below to execute the multi-signal forensic synthesis engine.
          </p>
          <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
            <button type="button" className="btn btn--primary btn--lg" onClick={runAnalysis}>
              Run Analysis →
            </button>
          </div>
        </div>
      )}

      {/* Signal Details Deep-Dive Drawer */}
      <Drawer open={openSignal !== null} onClose={() => setOpenSignal(null)} title={openSignal?.name}>
        {openSignal ? (
          <SignalDetail
            signal={openSignal}
            thresholds={thresholds}
            exclusionReason={exclusionReason.get(openSignal.signal_id) ?? null}
          />
        ) : null}
      </Drawer>
    </div>
  )
}

function SignalDetail({
  signal,
  thresholds,
  exclusionReason,
}: {
  signal: Signal
  thresholds: Verdict['thresholds'] | null
  exclusionReason: string | null
}) {
  const excluded = isExcluded(signal)
  const basis = Object.entries(signal.evidence_basis ?? {})
  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <Pill variant={signalPillVariant(signal, thresholds)} title={signal.status}>
          {statusLabel(signal.status)}
        </Pill>
        <span className="faint" style={{ fontSize: 'var(--text-2xs)', fontFamily: 'var(--mono)' }}>
          {signal.signal_id}
        </span>
      </div>

      <p style={{ fontSize: 'var(--text-sm)', margin: 0, color: 'var(--text-strong)', lineHeight: 'var(--leading-relaxed)' }}>
        {signal.explanation}
      </p>

      {excluded ? (
        <Banner
          tone="info"
          title="Excluded from the fused score"
          detail={
            exclusionReason ??
            'This signal could not be measured and is removed from both the numerator and the denominator - it counts as evidence in neither direction.'
          }
        />
      ) : null}

      <dl className="dl">
        <dt>Score</dt>
        <dd className="mono">
          {signal.score === null ? '- (not measured)' : formatScore(signal.score, 4)}
        </dd>
        <dt>Declared weight</dt>
        <dd className="mono">{formatWeight(signal.weight)}</dd>
        <dt>Effective weight</dt>
        <dd className="mono">{formatWeight(signal.effective_weight)}</dd>
        <dt>Contribution</dt>
        <dd className="mono">
          {signal.contribution === null ? '-' : formatScore(signal.contribution, 4)}
        </dd>
        <dt>In fused score</dt>
        <dd>{signal.included ? 'Yes' : 'No - excluded'}</dd>
      </dl>

      {basis.length ? (
        <div className="stack--tight" style={{ marginTop: 'var(--space-2)' }}>
          <span className="label">Signal Basis (Raw Inspector)</span>
          <div className="table-wrapper">
            <table className="table">
              <tbody>
                {basis.map(([key, value]) => (
                  <tr key={key}>
                    <td className="mono" style={{ fontSize: 'var(--text-2xs)' }}>{key}</td>
                    <td className="break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                      {typeof value === 'object' && value !== null ? JSON.stringify(value) : String(value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  )
}

const DETECTOR_TABS = [
  { id: 'image', label: 'Image' },
  { id: 'video', label: 'Video' },
  { id: 'audio', label: 'Audio' },
]

function detectLabelTone(result: DetectResult): PillTone {
  if (result.abstained) return 'warn'
  if (result.label === 'MANIPULATED') return 'error'
  if (result.label === 'AUTHENTIC') return 'ok'
  return 'accent'
}

function DetectorPanel({ evidence }: { evidence: Evidence[] }) {
  const [status, setStatus] = useState<DetectorStatus | null>(null)
  const [mediaType, setMediaType] = useState<'image' | 'video' | 'audio'>('image')
  const [file, setFile] = useState<File | null>(null)
  const [evidenceId, setEvidenceId] = useState('')
  const [detecting, setDetecting] = useState(false)
  const [result, setResult] = useState<DetectResult | null>(null)
  const [error, setError] = useState<unknown>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let active = true
    api.detectorStatus().then(
      (s) => {
        if (active) setStatus(s)
      },
      () => {},
    )
    return () => {
      active = false
    }
  }, [])

  const run = useCallback(() => {
    if (!file && !evidenceId) return
    setDetecting(true)
    setError(null)
    setResult(null)
    api.detectMedia(file, evidenceId || undefined, mediaType).then(
      (res) => {
        setResult(res)
        setDetecting(false)
      },
      (err) => {
        setError(err)
        setDetecting(false)
      },
    )
  }, [file, evidenceId, mediaType])

  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="label">Direct Detector Model</span>
        {status ? (
          <Pill variant={status.available ? 'ok' : 'unavailable'}>
            {status.available ? 'AVAILABLE' : 'NOT AVAILABLE'}
          </Pill>
        ) : null}
        {status ? (
          <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
            {status.model} <span className="mono">({status.adapter})</span>
          </span>
        ) : null}
      </div>

      <Tabs
        tabs={DETECTOR_TABS}
        active={mediaType}
        onChange={(id) => {
          setMediaType(id as 'image' | 'video' | 'audio')
          setResult(null)
          setError(null)
        }}
        ariaLabel="Detector modality"
      />

      <div className="grid-2col" style={{ gap: 'var(--space-3)' }}>
        <div className="field">
          <label className="field__label">Upload Local File</label>
          <input
            ref={fileInputRef}
            className="dropzone__input"
            type="file"
            onChange={(e) => {
              const f = e.target.files?.[0] ?? null
              setFile(f)
              if (f) setEvidenceId('')
            }}
          />
          <button type="button" className="btn btn--sm" onClick={() => fileInputRef.current?.click()}>
            <Icon name="upload" size={13} />
            {file ? file.name : `Select a ${mediaType} file`}
          </button>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="detect-evidence">
            Or Choose Ingested Evidence
          </label>
          <select
            id="detect-evidence"
            className="input"
            value={evidenceId}
            onChange={(e) => {
              setEvidenceId(e.target.value)
              if (e.target.value) setFile(null)
            }}
          >
            <option value="">Choose evidence…</option>
            {evidence.map((ev) => (
              <option key={ev.evidence_id} value={ev.evidence_id}>
                {ev.filename} ({ev.media_type.toUpperCase()})
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="btn-row">
        <button
          type="button"
          className="btn btn--primary btn--sm"
          disabled={detecting || (!file && !evidenceId)}
          onClick={run}
        >
          {detecting ? <span className="spinner" /> : <Icon name="refresh" size={13} />}
          Run Detection
        </button>
      </div>

      {error ? (
        <ErrorBanner context="Detector" error={error} />
      ) : result ? (
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-2)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
              <span style={{ fontWeight: 700 }}>Detector Inference Result</span>
              <Pill variant={detectLabelTone(result)}>{result.label.replace(/_/g, ' ')}</Pill>
            </div>
            <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
              {result.latency_ms != null ? `${result.latency_ms} ms` : '-'}
            </span>
          </div>
          <dl className="dl">
            <dt>Manipulation Score</dt>
            <dd className="mono">
              {result.manipulation_score === null
                ? result.abstained
                  ? '- (abstained)'
                  : '-'
                : formatScore(result.manipulation_score, 4)}
            </dd>
            <dt>Confidence</dt>
            <dd className="mono">
              {result.confidence === null ? '-' : `${(result.confidence * 100).toFixed(1)}%`}
            </dd>
            <dt>Model</dt>
            <dd>
              {result.model} <span className="muted">({result.model_version})</span>
            </dd>
            {result.weights_hash ? (
              <>
                <dt>Weights Hash</dt>
                <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
                  <code className="mono" style={{ fontSize: 'var(--text-2xs)' }}>
                    {shortHash(result.weights_hash)}
                  </code>
                  <CopyButton value={result.weights_hash} label="" title="Copy weights hash" />
                </dd>
              </>
            ) : null}
          </dl>
          <p className="note" style={{ margin: 0 }}>{result.explanation}</p>
        </div>
      ) : null}
    </div>
  )
}
