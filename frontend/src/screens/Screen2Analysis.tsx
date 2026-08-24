/**
 * Screen: Analysis & verdict.
 *
 * The verdict leads and is read first; the media it concerns sits beside it. Then
 * the five forensic signals appear as compact rows — status, a one-line finding,
 * and a Details drawer that holds the arithmetic (score, weights, contribution)
 * and the raw signal basis for the examiner who needs it.
 *
 * Everything is the backend's output, shown as received. A signal that could not
 * run is excluded from the score, never counted as zero. Heavier material — file
 * metadata and the standalone detector — is folded behind disclosures so the
 * primary read stays: what did PRAMAAN find, and what do I do next.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { DetectorStatus, DetectResult, Evidence, Signal, Verdict } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Drawer } from '../components/Drawer'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { ProgressStrips } from '../components/ProgressStrips'
import { Section } from '../components/Section'
import { Tabs } from '../components/Tabs'
import { VerdictCard } from '../components/VerdictCard'
import { formatBytes, formatScore, formatWeight, shortHash } from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import { isExcluded, signalPillVariant, statusLabel } from '../lib/signals'
import type { Investigation } from '../state/useInvestigation'

/** First sentence of a longer explanation, for the one-line finding on a row. */
function firstSentence(text: string): string {
  const t = (text || '').trim()
  const m = t.match(/^(.*?[.!?])(\s|$)/)
  return m ? m[1] : t
}

/** Compact evidence preview; degrades to a labelled placeholder if the file can't load. */
function EvidencePreview({ evidence }: { evidence: Evidence }) {
  const [failed, setFailed] = useState(false)
  const canShowImage = isImageMedia(evidence.media_type) && !failed
  return (
    <div className="media-frame">
      {canShowImage ? (
        <img
          src={evidenceFileUrl(evidence.evidence_id)}
          alt={evidence.filename}
          className="media-frame__img"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="stack" style={{ alignItems: 'center', gap: 8, padding: 'var(--space-5)', color: 'var(--text-muted)' }}>
          <Icon name="document" size={32} />
          <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{evidence.filename}</span>
          <span style={{ fontSize: 'var(--text-xs)' }}>
            {evidence.media_type.toUpperCase()} · {formatBytes(evidence.size_bytes)}
          </span>
        </div>
      )}
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
  const { caseRecord, evidence, analysis, runAnalysis, metadata, loadMetadata } = investigation
  const [openSignal, setOpenSignal] = useState<Signal | null>(null)

  if (!caseRecord) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
        <div className="screen__head">
          <h1 className="screen__title">Analysis</h1>
          <p className="screen__lead">Assess a case's evidence across five forensic signals.</p>
        </div>
        <Empty>No case is open. Open a case or ingest a file to analyse.</Empty>
      </div>
    )
  }

  const running = analysis.phase === 'loading'
  const result = analysis.phase === 'ready' ? analysis.data : null
  const verdict = result?.verdict ?? null
  const signals: Signal[] = result?.signals ?? []
  const thresholds: Verdict['thresholds'] | null = verdict?.thresholds ?? null

  const available = signals.filter((s) => !isExcluded(s)).length
  const total = signals.length
  const primaryEvidence = result?.evidence?.[0] ?? evidence[0] ?? null

  // Exclusion reasons live on the verdict; index by signal for the Details drawer.
  const exclusionReason = new Map<string, string>(
    (verdict?.excluded_signals ?? []).map((e) => [e.signal_id, e.reason]),
  )

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Analysis</h1>
        <p className="screen__lead">
          Five signals assessed independently and fused into one advisory verdict. A signal that
          cannot run is excluded — never counted as zero.
        </p>
      </div>

      {running ? (
        <Section title="Assessing evidence">
          <ProgressStrips running signals={signals.length ? signals : null} thresholds={thresholds} />
          <Spinner label="Fusing available signals…" />
        </Section>
      ) : result ? (
        <>
          {/* Media beside the verdict — the two things read first. */}
          <div className="grid-asymmetric" style={{ gap: 'var(--space-5)' }}>
            <Section title="Evidence">
              {primaryEvidence ? (
                <div className="stack" style={{ gap: 'var(--space-2)' }}>
                  <EvidencePreview key={primaryEvidence.evidence_id} evidence={primaryEvidence} />
                  <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{primaryEvidence.filename}</span>
                    <Pill variant="accent">{primaryEvidence.media_type.toUpperCase()}</Pill>
                  </div>
                  {result.evidence.length > 1 ? (
                    <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                      +{result.evidence.length - 1} more evidence item
                      {result.evidence.length - 1 === 1 ? '' : 's'} in this analysis
                    </span>
                  ) : null}
                </div>
              ) : (
                <Empty>No evidence attached to this case.</Empty>
              )}
            </Section>

            <Section title="Verdict">
              <VerdictCard verdict={verdict} />

              {verdict ? (
                <div
                  className="card stack"
                  style={{
                    marginTop: 'var(--space-3)',
                    padding: 'var(--space-3)',
                    gap: 6,
                    background: 'var(--surface-2)',
                    borderLeft: verdict.verdict.includes('MANIPULATED')
                      ? '3px solid var(--danger)'
                      : verdict.verdict.includes('AUTHENTIC')
                      ? '3px solid var(--ok)'
                      : '3px solid var(--border-strong)',
                  }}
                >
                  <span className="label" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                    WHY THIS RESULT
                  </span>
                  <span style={{ fontSize: 'var(--text-xs)', fontWeight: 600, color: 'var(--text-strong)' }}>
                    {verdict.rationale || 'Independent forensic checks were assessed and weighted into a single advisory verdict.'}
                  </span>
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                    Fused score: {verdict.manipulation_score !== null ? verdict.manipulation_score.toFixed(4) : '—'} · Confidence: {typeof verdict.confidence === 'number' ? (verdict.confidence * 100).toFixed(0) + '%' : '—'} · Assessed: {available}/{total} signals
                  </span>
                </div>
              ) : null}
            </Section>
          </div>

          {/* Compact signal rows — status, one-line finding, Details. */}
          {signals.length ? (
            <Section
              title="Forensic Signals"
              aside={`${total} CHECKS · ${available} MEASURED · ${total - available} NOT PRESENT / NO MATCH`}
            >
              <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 'var(--space-3)', color: 'var(--text-muted)', fontSize: 'var(--text-xs)' }}>
                <Icon name="info" size={14} style={{ color: 'var(--accent)' }} />
                <span>Only measured signals contribute to the verdict. Unmeasured checks are excluded from both numerator and denominator.</span>
              </div>

              <div className="stack" style={{ gap: 'var(--space-2)' }}>
                {signals.map((s) => (
                  <div
                    key={s.signal_id}
                    className="card row"
                    style={{ justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3)' }}
                  >
                    <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                      <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{s.name}</span>
                      <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                        {firstSentence(s.explanation)}
                      </span>
                    </div>
                    <div className="row" style={{ gap: 8, alignItems: 'center', flexShrink: 0 }}>
                      <Pill variant={signalPillVariant(s, thresholds)} title={s.status}>
                        {statusLabel(s.status)}
                      </Pill>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setOpenSignal(s)}
                      >
                        Details
                        <Icon name="arrow-right" size={13} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>

              {result.warnings.length ? (
                <ul className="note-list" style={{ marginTop: 'var(--space-2)' }}>
                  {result.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              ) : null}
            </Section>
          ) : null}

          {/* What do I do next. */}
          <div className="btn-row">
            <button type="button" className="btn btn--primary" onClick={onPropagation}>
              <Icon name="external" size={14} />
              Trace provenance
            </button>
            <button type="button" className="btn btn--ghost" onClick={runAnalysis}>
              <Icon name="refresh" size={14} />
              Re-run analysis
            </button>
            <span className="faint" style={{ fontSize: 'var(--text-2xs)', alignSelf: 'center' }}>
              {result.refreshed ? 'Recomputed' : 'Served from stored results'} ·{' '}
              {result.processing_time_ms.toFixed(0)} ms · analysis {result.analysis_version}
            </span>
          </div>

          {/* File metadata — real, but secondary. Loaded on demand. */}
          <details className="disclosure">
            <summary>
              <Icon name="arrow-right" className="disclosure__chevron" size={13} />
              View file metadata
            </summary>
            <div className="disclosure__panel stack" style={{ gap: 'var(--space-3)' }}>
              <div className="btn-row">
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={loadMetadata}
                  disabled={metadata.phase === 'loading'}
                >
                  {metadata.phase === 'loading' ? <Spinner /> : null}
                  {metadata.phase === 'ready' ? 'Reload metadata' : 'Load metadata'}
                </button>
              </div>
              {metadata.phase === 'error' ? (
                <ErrorBanner error={metadata.error} context="Metadata extraction" onRetry={loadMetadata} />
              ) : null}
              {metadata.phase === 'ready' && metadata.data ? (
                <>
                  <p className="note">{metadata.data.interpretation}</p>
                  {metadata.data.items.map((item) => {
                    const entries = Object.entries(item.metadata ?? {})
                    return (
                      <div key={item.evidence_id} className="stack--tight">
                        <h3 className="label">{item.filename}</h3>
                        {entries.length ? (
                          <div className="table-wrapper">
                            <table className="table">
                              <thead>
                                <tr>
                                  <th scope="col">Field</th>
                                  <th scope="col">Value</th>
                                </tr>
                              </thead>
                              <tbody>
                                {entries.map(([key, value]) => (
                                  <tr key={key}>
                                    <td className="mono" style={{ fontSize: 'var(--text-2xs)' }}>{key}</td>
                                    <td className="break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                                      {typeof value === 'object' && value !== null
                                        ? JSON.stringify(value)
                                        : String(value)}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <Empty>
                            No metadata fields were recovered. Absence of metadata is not evidence of
                            manipulation — platforms strip it routinely during redistribution.
                          </Empty>
                        )}
                      </div>
                    )
                  })}
                  <p className="note">Extractor: {metadata.data.extractor}</p>
                </>
              ) : null}
            </div>
          </details>

          {/* Standalone detector — a direct-inference tool, folded away by default. */}
          <details className="disclosure">
            <summary>
              <Icon name="arrow-right" className="disclosure__chevron" size={13} />
              Run the detector directly on a file
            </summary>
            <div className="disclosure__panel">
              <DetectorPanel evidence={result.evidence.length ? result.evidence : evidence} />
            </div>
          </details>
        </>
      ) : analysis.phase === 'error' ? (
        <ErrorBanner error={analysis.error} context="Analysis" onRetry={runAnalysis} />
      ) : (
        <Section title="Verdict">
          <Empty>Not yet analysed. Run the five-signal analysis to produce a verdict.</Empty>
          <div className="btn-row">
            <button type="button" className="btn btn--primary btn--lg" onClick={runAnalysis}>
              Run analysis
              <Icon name="arrow-right" size={16} />
            </button>
          </div>
        </Section>
      )}

      {/* Per-signal detail: the arithmetic and raw basis, out of the primary view. */}
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

/** The full internals of one signal — shown in the Details drawer. */
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

      <p style={{ fontSize: 'var(--text-sm)', margin: 0 }}>{signal.explanation}</p>

      {excluded ? (
        <Banner
          tone="info"
          title="Excluded from the fused score"
          detail={
            exclusionReason ??
            'This signal could not be measured and is removed from both the numerator and the denominator — it counts as evidence in neither direction.'
          }
        />
      ) : null}

      <dl className="dl">
        <dt>Score</dt>
        <dd className="mono">
          {signal.score === null ? '— (not measured)' : formatScore(signal.score, 4)}
        </dd>
        <dt>Declared weight</dt>
        <dd className="mono">{formatWeight(signal.weight)}</dd>
        <dt>Effective weight</dt>
        <dd className="mono">{formatWeight(signal.effective_weight)}</dd>
        <dt>Contribution</dt>
        <dd className="mono">
          {signal.contribution === null ? '—' : formatScore(signal.contribution, 4)}
        </dd>
        <dt>In fused score</dt>
        <dd>{signal.included ? 'Yes' : 'No — excluded'}</dd>
      </dl>

      {basis.length ? (
        <div className="stack--tight">
          <span className="label">Signal basis (raw)</span>
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

/**
 * Standalone direct-inference detector (POST /api/detect), one modality at a time.
 * Independent of the case's fused verdict — a tool for spot-checking a file.
 */
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
        <span className="label">Detector</span>
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

      {status && !status.available ? (
        <p className="note">
          {status.reason ?? 'No detector is installed in this deployment.'} A direct run will abstain
          rather than guess — an abstention is not a finding.
        </p>
      ) : null}

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
          <label className="field__label">Local file</label>
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
            {file ? file.name : `Choose a ${mediaType} file`}
          </button>
        </div>

        <div className="field">
          <label className="field__label" htmlFor="detect-evidence">
            Or an ingested item
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
          {detecting ? <Spinner /> : <Icon name="refresh" size={13} />}
          Run detection
        </button>
      </div>

      {error ? (
        <ErrorBanner context="Detector" error={error} />
      ) : result ? (
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-2)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
              <span style={{ fontWeight: 600 }}>Detector output</span>
              <Pill variant={detectLabelTone(result)}>{result.label.replace(/_/g, ' ')}</Pill>
            </div>
            <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
              {result.latency_ms != null ? `${result.latency_ms} ms` : '—'}
            </span>
          </div>
          <dl className="dl">
            <dt>Manipulation score</dt>
            <dd className="mono">
              {result.manipulation_score === null
                ? result.abstained
                  ? '— (abstained)'
                  : '—'
                : formatScore(result.manipulation_score, 4)}
            </dd>
            <dt>Confidence</dt>
            <dd className="mono">
              {result.confidence === null ? '—' : `${(result.confidence * 100).toFixed(1)}%`}
            </dd>
            <dt>Model</dt>
            <dd>
              {result.model} <span className="muted">({result.model_version})</span>
            </dd>
            {result.regions && result.regions.length ? (
              <>
                <dt>Regions</dt>
                <dd>{result.regions.length} flagged</dd>
              </>
            ) : null}
            {result.timestamps && result.timestamps.length ? (
              <>
                <dt>Timestamps</dt>
                <dd>{result.timestamps.length} flagged</dd>
              </>
            ) : null}
            {result.weights_hash ? (
              <>
                <dt>Weights</dt>
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
