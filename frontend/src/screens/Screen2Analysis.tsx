/**
 * Screen: Final Analysis Workspace (Screen 2).
 *
 * Primary question answered:
 * "What is the forensic finding, and WHY?"
 *
 * Structural Flow:
 * 1. The app shell above this screen renders the case workflow stepper once.
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
import type {
  AssessmentState,
  DetectorStatus,
  DetectResult,
  Evidence,
  ExecutionStatus,
  MetadataResponse,
  Signal,
  StoredVerdictResponse,
  Verdict,
} from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Drawer } from '../components/Drawer'
import { EvidencePreview, EvidenceThumbnail } from '../components/EvidenceMedia'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { ProgressStrips } from '../components/ProgressStrips'
import { Tabs } from '../components/Tabs'
import { NoCaseSelected, Spinner } from '../components/Feedback'
import { NOT_MEASURED, formatBytes, formatScore, formatWeight, shortHash } from '../lib/format'
import { isImageMedia } from '../lib/media'
import type { RoutePath } from '../lib/router'
import {
  assessmentStateLabel,
  assessmentStateTone,
  confidenceBandLabel,
  confidenceBandNote,
  executionStatusLabel,
  isExcluded,
  signalPillVariant,
  statusLabel,
  verdictBandLabel,
  verdictPillTone,
  verdictTone,
  type VerdictTone,
} from '../lib/signals'
import { isReady, type Investigation } from '../state/useInvestigation'

/**
 * Contribution cell text.
 *
 * An excluded signal reads `- (excluded)`, never `0.0000 (Excluded)`. A
 * four-decimal zero is a measurement, and printing one for a signal that was
 * never measured is the single most common way a forensic UI lies: it puts a
 * number in the evidence column that no detector produced.
 */
function formatContribution(contrib: number | null, excluded: boolean): string {
  if (excluded) return `${NOT_MEASURED} (excluded)`
  if (contrib === null) return NOT_MEASURED
  return `${contrib >= 0 ? '+' : ''}${formatScore(contrib, 4)}`
}

/**
 * Evidence preview.
 *
 * Shows the stored bytes as they are. There is no "Localization Heatmap Layer"
 * toggle: this build has no endpoint that serves a heatmap. The previous version
 * simulated one with `filter: hue-rotate(180deg) saturate(2.5)`, which recolours
 * the whole frame uniformly and localizes nothing -- a false claim that a model
 * had identified which regions were manipulated. The detector package can
 * produce a real Grad-CAM (`ImageDetector.get_heatmap`), but until the backend
 * exposes it there is nothing honest to display.
 */
function AnalysisMediaPreview({
  evidence,
}: {
  evidence: Evidence
}) {
  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <span className="label">EVIDENCE INSPECTION VIEWPORT</span>
        <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
          STORED BYTES · NO LOCALIZATION OVERLAY AVAILABLE
        </span>
      </div>

      <div className="forensic-inspection-frame">
        <EvidencePreview
          evidenceId={evidence.evidence_id}
          filename={evidence.filename}
          mediaType={evidence.media_type}
          kind={isImageMedia(evidence.media_type) ? 'image' : 'none'}
          detail={formatBytes(evidence.size_bytes)}
        />

        <div className="forensic-inspection-frame__badge">
          <Icon name="lock" size={12} style={{ color: 'var(--ok-bright)' }} />
          <span>{evidence.filename} · {formatBytes(evidence.size_bytes)}</span>
        </div>
      </div>

      <p className="note" style={{ margin: 0 }}>
        This build serves the original stored file only. It does not render a manipulation
        localization map, so no region of this image is being marked as altered.
      </p>
    </div>
  )
}

interface AssessmentEvidence {
  state: AssessmentState
  stateLabel: string
  stateTone: VerdictTone
  conclusive: boolean
  executionStatus: ExecutionStatus
  executionStatusLabel: string
  taskScope: string
  policyId: string
  policyVersion: string
  stateNote: string
  reasonCodes: string[]
  limitations: string[]
  leading: { name: string; detail: string; score: number | null; contribution: number | null } | null
  supporting: Array<{ name: string; detail: string; score: number | null; contribution: number | null }>
  conflicting: Array<{ name: string; detail: string; score: number | null; contribution: number | null }>
  unavailable: Array<{ name: string; reason: string; status: string; isAbstained: boolean }>
  observations: Array<{ name: string; detail: string; score: number | null; role: string }>
}

function resolveAssessmentView(
  verdict: Verdict,
  signals: Signal[],
  exclusionReasons: Map<string, string>,
): AssessmentEvidence {
  const backend = verdict.assessment

  if (backend) {
    const state = backend.state
    const contributing = backend.contributing_checks || []
    const unavailableList = backend.unavailable_checks || []
    const observationsList = backend.descriptive_observations || []

    let leading: AssessmentEvidence['leading'] = null
    const supporting: AssessmentEvidence['supporting'] = []
    const conflicting: AssessmentEvidence['conflicting'] = []

    if (contributing.length > 0) {
      // Find leading contributor
      const sorted = [...contributing].sort(
        (a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0)
      )
      const top = sorted[0]
      leading = {
        name: top.name,
        detail: top.explanation || top.basis?.detail || (top.reason_code ? `Status: ${top.reason_code}` : ''),
        score: top.score ?? null,
        contribution: top.contribution ?? null,
      }

      for (let i = 1; i < sorted.length; i++) {
        const c = sorted[i]
        const isConflicting =
          (state === 'INDICATORS_DETECTED' && c.check_state === 'NO_INDICATORS_DETECTED') ||
          (state === 'NO_INDICATORS_DETECTED' && c.check_state === 'INDICATORS_DETECTED')

        const item = {
          name: c.name,
          detail: c.explanation || c.basis?.detail || (c.reason_code ? `Status: ${c.reason_code}` : ''),
          score: c.score ?? null,
          contribution: c.contribution ?? null,
        }

        if (isConflicting) {
          conflicting.push(item)
        } else {
          supporting.push(item)
        }
      }
    }

    const unavailable: AssessmentEvidence['unavailable'] = unavailableList.map((u) => ({
      name: u.name,
      reason: u.reason_code ? `${u.reason_code}: ${u.detail || u.reason_code}` : u.detail || 'Unavailable check',
      status: u.execution_status,
      isAbstained: u.execution_status === 'ABSTAINED',
    }))

    const observations: AssessmentEvidence['observations'] = observationsList.map((o) => ({
      name: o.name,
      detail: o.detail || '',
      score: o.score ?? null,
      role: o.role || 'DESCRIPTIVE',
    }))

    return {
      state,
      stateLabel: assessmentStateLabel(state),
      stateTone: assessmentStateTone(state),
      conclusive: Boolean(backend.conclusive),
      executionStatus: backend.execution_status || 'COMPLETED',
      executionStatusLabel: executionStatusLabel(backend.execution_status),
      taskScope: backend.scope || 'synthetic_media_indicators',
      policyId: backend.policy_id || 'pramaan.synthetic_media_indicators',
      policyVersion: backend.policy_version || '1.0',
      stateNote: backend.state_note || '',
      reasonCodes: backend.reason_codes || [],
      limitations: backend.limitations || [],
      leading,
      supporting,
      conflicting,
      unavailable,
      observations,
    }
  }

  // Legacy fallback when backend verdict has no assessment object
  const vBand = verdict.verdict
  const fallbackState: AssessmentState =
    vBand === 'MANIPULATED'
      ? 'INDICATORS_DETECTED'
      : vBand === 'AUTHENTIC'
        ? 'NO_INDICATORS_DETECTED'
        : 'INCONCLUSIVE'

  const included = signals.filter((s) => s.included && s.score !== null)
  const excluded = signals.filter((s) => !s.included || s.score === null)

  const sortedIncluded = [...included].sort(
    (a, b) => Math.abs(b.contribution ?? 0) - Math.abs(a.contribution ?? 0)
  )

  let leading: AssessmentEvidence['leading'] = null
  const supporting: AssessmentEvidence['supporting'] = []

  if (sortedIncluded.length > 0) {
    const top = sortedIncluded[0]
    leading = {
      name: top.name,
      detail: top.explanation,
      score: top.score,
      contribution: top.contribution,
    }
    for (let i = 1; i < sortedIncluded.length; i++) {
      const s = sortedIncluded[i]
      supporting.push({
        name: s.name,
        detail: s.explanation,
        score: s.score,
        contribution: s.contribution,
      })
    }
  }

  const unavailable: AssessmentEvidence['unavailable'] = excluded.map((s) => {
    const isAbstained =
      s.status === 'INCONCLUSIVE' ||
      s.evidence_basis?.availability === 'ran_and_declined' ||
      s.evidence_basis?.abstained === true ||
      String(s.explanation || '').toLowerCase().includes('abstained')

    const reason =
      exclusionReasons.get(s.signal_id) ||
      (isAbstained
        ? 'Model ran but prediction fell inside abstention band; excluded from fusion.'
        : s.explanation || 'Signal not available or excluded from fusion calculation.')

    return {
      name: s.name,
      reason,
      status: s.status,
      isAbstained,
    }
  })

  return {
    state: fallbackState,
    stateLabel: assessmentStateLabel(fallbackState),
    stateTone: assessmentStateTone(fallbackState),
    conclusive: vBand !== 'INCONCLUSIVE',
    executionStatus: 'COMPLETED',
    executionStatusLabel: 'COMPLETED',
    taskScope: 'synthetic_media_indicators',
    policyId: 'pramaan.synthetic_media_indicators.legacy',
    policyVersion: verdict.fusion_version || 'legacy',
    stateNote: '',
    reasonCodes: [],
    limitations: [],
    leading,
    supporting,
    conflicting: [],
    unavailable,
    observations: [],
  }
}

export function Screen2Analysis({
  caseId,
  investigation,
  onNavigate,
  onPropagation,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
  onPropagation: () => void
}) {
  const { analysis, evidence, runAnalysis, caseRecord } = investigation
  const [openSignal, setOpenSignal] = useState<Signal | null>(null)

  const [metadata, setMetadata] = useState<{
    status: 'idle' | 'loading' | 'ready' | 'error'
    data: MetadataResponse | null
    error: unknown
  }>({ status: 'idle', data: null, error: null })

  // Backend truth for which signals apply to which media type. The screen
  // renders its signal matrix from this response; applicability is never
  // hardcoded here -- the fusion engine defines it and this screen obeys.
  const [applicability, setApplicability] = useState<Record<
    string,
    Array<{ signal_id: string; name: string }>
  > | null>(null)

  const currentCaseId = caseId || caseRecord?.case_id || null
  const primaryEvidence = evidence[0] ?? null
  // The evidence media type is decided by the backend from the file's own
  // bytes at ingestion; the UI never guesses it from a filename.
  const evidenceMediaType = primaryEvidence?.media_type ?? null

  /*
   * The verdict the case already carries, read on arrival.
   *
   * An examiner who opens an analysed case by deep link or from the queue has an
   * empty `analysis` slice -- it is only filled by running the pipeline in this
   * session. Without this read the screen announced "No analysis results are in
   * memory for this case" beside a stepper that (correctly) showed Analysis
   * complete, and offered to run the pipeline again on a case that had already
   * been analysed.
   *
   * It deliberately does NOT call `runAnalysis()`. `POST /analyse` re-runs
   * near-duplicate retrieval and appends MATCH_SEARCHED and ANALYSIS_COMPLETED to
   * the audit chain even with `refresh` false, so auto-running it would make
   * opening a page a recorded forensic act. `GET /verdict` computes nothing and
   * writes nothing.
   */
  const [stored, setStored] = useState<{
    status: 'idle' | 'loading' | 'ready' | 'error'
    data: StoredVerdictResponse | null
    error: unknown
  }>({ status: 'idle', data: null, error: null })

  useEffect(() => {
    if (!currentCaseId) {
      setStored({ status: 'idle', data: null, error: null })
      return
    }
    let active = true
    setStored({ status: 'loading', data: null, error: null })
    api.storedVerdict(currentCaseId).then(
      (data) => {
        if (active) setStored({ status: 'ready', data, error: null })
      },
      (error: unknown) => {
        if (active) setStored({ status: 'error', data: null, error })
      },
    )
    return () => {
      active = false
    }
  }, [currentCaseId])

  useEffect(() => {
    let active = true
    api.signalApplicability().then(
      (data) => {
        if (active) setApplicability(data.applicability)
      },
      () => {},
    )
    return () => {
      active = false
    }
  }, [])

  const loadMetadata = () => {
    if (!currentCaseId || metadata.status === 'loading') return
    setMetadata({ status: 'loading', data: null, error: null })
    api
      .metadata(currentCaseId)
      .then((data: MetadataResponse) => setMetadata({ status: 'ready', data, error: null }))
      .catch((err: unknown) => setMetadata({ status: 'error', data: null, error: err }))
  }

  // A case-id guard on the shared analysis slice: after a case switch the
  // store's slice belongs to the new case only once its own analysis has been
  // run; without this check Case A's verdict renders under Case B's number.
  const result =
    isReady(analysis) && (!currentCaseId || analysis.data.case.case_id === currentCaseId)
      ? analysis.data
      : null
  const verdict = result?.verdict ?? null
  const thresholds = verdict?.thresholds ?? null

  /*
   * Media-aware signal rendering. The backend builds and returns only the
   * signals APPLICABLE to the evidence's media type, and `signals` below is
   * exactly that list -- an inapplicable signal (image-only perceptual indexing
   * or compression forensics on video/audio evidence) is never rendered as a
   * row, never treated as failed, and never enters a coverage count. The
   * applicable-set ids from the verdict itself are the belt-and-braces filter;
   * the applicability endpoint supplies the labels while analysis is in flight.
   */
  const signals = result?.signals ?? []
  const applicableIds = new Set(
    (verdict?.applicable_signals ?? []).map((s) => s.signal_id),
  )
  const visibleSignals = applicableIds.size > 0 ? signals.filter((s) => applicableIds.has(s.signal_id)) : signals
  const pendingLabels = applicability && evidenceMediaType
    ? (applicability[evidenceMediaType] ?? []).map((s) => ({ id: s.signal_id, label: s.name }))
    : undefined

  /*
   * Coverage counts come straight from the backend verdict, never recomputed
   * here, and are scoped to the APPLICABLE set for this media type:
   * `signals_total` is the applicable count, `signals_evaluated` how many of
   * them ran, `signals_available` how many contributed to the fused score. A
   * client-side `score !== null` tally would diverge from the backend's own
   * `included` count -- a signal can carry a score yet be excluded -- and then
   * the ratio shown here would disagree with the verdict it sits beside. These
   * are only ever displayed inside the `verdict` branch below.
   */
  const total = verdict ? verdict.signals_total : visibleSignals.length
  const available = verdict ? verdict.signals_available : 0
  const evaluated = verdict ? verdict.signals_evaluated : 0

  const exclusionReason = new Map<string, string>()
  if (result && result.verdict) {
    for (const ex of result.verdict.excluded_signals) {
      exclusionReason.set(ex.signal_id, ex.reason)
    }
  }

  const assessment = verdict ? resolveAssessmentView(verdict, visibleSignals, exclusionReason) : null

  const vTone = assessment ? assessment.stateTone : (verdict ? verdictTone(verdict.verdict) : 'warn')

  /*
   * No case, no analysis. The same guard every other case-scoped screen has.
   *
   * This screen used to render its idle body here instead -- "No analysis results
   * are in memory for this case yet" with an enabled `Run Analysis →` -- for a
   * case that did not exist. There was no "this case", and the button called
   * `runAnalysis()`, which returns immediately when the store holds no case id,
   * so it was a primary action that did nothing and said nothing.
   */
  if (!currentCaseId) {
    return (
      <NoCaseSelected
        purpose="examine its evidence and view the forensic assessment"
        onViewCases={() => onNavigate('cases')}
      />
    )
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      {/* The case workflow stepper (Case → Evidence → Analysis → …) is owned
          by the app shell and rendered once, above this screen. It is not
          rendered here. */}

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
            {/*
              This has to send refresh=true. POST /analyse replays the stored
              result by default, so a "Re-Run" that omitted it would return the
              same verdict and read as a control that does nothing -- exactly
              wrong on a screen whose job is to be trustworthy. The button is
              only rendered when a result is on screen, and the re-run replaces
              that result with the WORKING state, so it cannot be double-fired.
            */}
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => runAnalysis({ refresh: true })}
            >
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
          <ProgressStrips running={true} signals={null} pendingLabels={pendingLabels} />
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
            {/* Box 1: FORENSIC ASSESSMENT & VERDICT */}
            <div
              className="card stack"
              style={{
                padding: 'var(--space-4)',
                gap: 8,
                borderLeft: vTone === 'manipulated'
                  ? '4px solid var(--danger-bright)'
                  : vTone === 'authentic'
                    ? '4px solid var(--ok-bright)'
                    : '4px solid var(--warn-bright)',
              }}
            >
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
                <span className="label" style={{ color: 'var(--text-muted)' }}>FORENSIC ASSESSMENT</span>
                {assessment ? (
                  <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                    <Pill variant={assessment.conclusive ? 'ok' : 'warn'}>
                      {assessment.conclusive ? 'CONCLUSIVE' : 'INCONCLUSIVE'}
                    </Pill>
                    <Pill variant={assessment.executionStatus === 'COMPLETED' ? 'neutral' : assessment.executionStatus === 'FAILED' ? 'error' : 'warn'}>
                      {assessment.executionStatusLabel}
                    </Pill>
                  </div>
                ) : null}
              </div>

              {/* The verdict is the hero: an examiner scanning this panel should
                  read the outcome (LIKELY MANIPULATED / AUTHENTIC / INCONCLUSIVE)
                  first, in the band colour. The task-qualified assessment state
                  ("Indicators detected") is the honest qualifier and sits just
                  under it -- it is what the verdict is derived from, not a weaker
                  synonym for it. */}
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

              {/* Assessment state and scope: the qualifier behind the verdict. */}
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', fontSize: '11px', color: 'var(--text-muted)', flexWrap: 'wrap', gap: 4 }}>
                {assessment ? (
                  <span className="mono">Assessment: <strong>{assessment.stateLabel}</strong></span>
                ) : null}
                {assessment?.taskScope ? (
                  <span className="mono" style={{ fontSize: '10px' }}>Scope: {assessment.taskScope}</span>
                ) : null}
              </div>

              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)' }}>
                {assessment?.stateNote || (
                  vTone === 'manipulated'
                    ? 'The assessed signals support manipulation. Decision aid, not a legal conclusion.'
                    : vTone === 'authentic'
                      ? 'The assessed signals did not support manipulation. This is not a verification of authenticity.'
                      : 'Insufficient signal coverage to conclude. Not a finding of authenticity or of manipulation.'
                )}
              </span>
            </div>

            {/* Box 2: CONFIDENCE BAND (a word from the backend, never a percentage) */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>CONFIDENCE BAND</span>
              <div style={{ fontSize: 'var(--text-xl)', fontWeight: 900, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                {confidenceBandLabel(verdict.confidence)}
              </div>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 'var(--leading-normal)' }}>
                {confidenceBandNote(verdict.confidence)}
              </span>
            </div>

            {/* Box 3: SIGNAL COVERAGE -- media-aware, all values from backend truth.
                Applicable / Evaluated / Contributing, scoped to the signals that
                can apply to this evidence's media type. Inapplicable signals are
                hidden, not failed and not zero, and are in no count here. */}
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>SIGNAL COVERAGE</span>
              <div style={{ fontSize: 'var(--text-xl)', fontWeight: 900, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                {available} / {total}
              </div>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                Applicable: {total} · Evaluated: {evaluated} · Contributing: {available}
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                Contributing to the fused score, of the signals applicable to{' '}
                {evidenceMediaType ? evidenceMediaType.toUpperCase() : 'this media type'}
              </span>
            </div>

            {/* Box 4: EVIDENCE PREVIEW */}
            <div className="card stack" style={{ padding: 'var(--space-3)', gap: 6, background: 'var(--surface-2)' }}>
              <span className="label" style={{ color: 'var(--text-muted)' }}>EVIDENCE PREVIEW</span>
              {primaryEvidence ? (
                <div style={{ position: 'relative', borderRadius: 'var(--radius-sm)', overflow: 'hidden', height: 72, background: 'var(--surface-3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  {isImageMedia(primaryEvidence.media_type) ? (
                    <EvidenceThumbnail evidenceId={primaryEvidence.evidence_id} iconSize={24} />
                  ) : (
                    <Icon name="document" size={24} style={{ color: 'var(--accent-bright)' }} />
                  )}
                  {/*
                    The badge carries the media type and stored size, both from
                    the evidence record. It used to read "0:00 / 1:45" -- a
                    playback duration for a file whose duration this build never
                    measured, on an image as readily as on a video.
                  */}
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
                    {primaryEvidence.media_type.toUpperCase()} · {formatBytes(primaryEvidence.size_bytes)}
                  </div>
                </div>
              ) : (
                <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>No media attached</div>
              )}
            </div>
          </div>

          {/* 4. FORENSIC SIGNALS — content-driven sections, stacked in the
              order the operator reads them. This used to be a rigid
              `minmax(0, 1fr) 340px` two-column grid pairing the signals
              table with the synthesis panel; with a short signal list the
              right column was three times taller than the left, so the grid
              row reserved ~700px of empty space under FORENSIC SIGNALS
              before TECHNICAL DETAILS could begin. Stacked sections take
              exactly the height of their own content. */}

          {/* 4a. FORENSIC SIGNALS MATRIX */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                FORENSIC SIGNALS
              </span>
              <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>
                Applicable: {total} · Evaluated: {evaluated} · Contributing: {available}
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
                  {/*
                    Only the signals the backend fused -- the set applicable to
                    this media type. An inapplicable signal (image-only
                    perceptual or compression techniques on video/audio) is
                    HIDDEN, not rendered as a failed or zero row.
                  */}
                  {visibleSignals.map((s) => {
                    const excluded = isExcluded(s)
                    const isAbstained =
                      s.status === 'INCONCLUSIVE' ||
                      s.evidence_basis?.availability === 'ran_and_declined' ||
                      s.evidence_basis?.abstained === true ||
                      String(s.explanation || '').toLowerCase().includes('abstained')
                    const statusText = statusLabel(s.status, s)
                    return (
                      <tr key={s.signal_id}>
                        <td style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>
                          <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                            <span>{s.name}</span>
                            {isAbstained && (
                              <span className="mono" style={{ fontSize: '10px', color: 'var(--warn-bright)' }}>
                                (ABSTAINED)
                              </span>
                            )}
                          </div>
                        </td>
                        <td>
                          <Pill variant={isAbstained ? 'warn' : signalPillVariant(s, thresholds)}>
                            {statusText}
                          </Pill>
                        </td>
                        <td className="mono" style={{ fontSize: '11px', color: 'var(--text-strong)' }}>
                          {s.score === null ? (isAbstained ? 'ABSTAINED' : '-') : formatScore(s.score, 4)}
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

          {/* 4b. WHY THIS ASSESSMENT? — the synthesis panel, directly under the
              signal rows it explains. */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
            <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 6 }}>
              <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
                WHY THIS ASSESSMENT?
              </span>
              <div className="row" style={{ gap: 8, alignItems: 'center' }}>
                {assessment?.policyId ? (
                  <span className="mono" style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                    Policy: {assessment.policyId} (v{assessment.policyVersion})
                  </span>
                ) : null}
                <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                  {available} of {total} signals
                </span>
              </div>
            </div>

            {verdict.rationale ? (
              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)', lineHeight: 'var(--leading-relaxed)', margin: 0 }}>
                {verdict.rationale}
              </p>
            ) : (
              <p className="note" style={{ margin: 0 }}>
                Assessment derived deterministically from weighted forensic detector signals.
              </p>
            )}

            {/* Reason codes banner if present */}
            {assessment && assessment.reasonCodes.length > 0 ? (
              <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <span className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>REASON CODES:</span>
                {assessment.reasonCodes.map((rc) => (
                  <Pill key={rc} variant="neutral">
                    <span className="mono" style={{ fontSize: '10px' }}>{rc}</span>
                  </Pill>
                ))}
              </div>
            ) : null}

            {/* Structured evidence categories -- side-by-side where they fit,
                wrapping to as many rows as the content needs. */}
            {assessment ? (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                  gap: 'var(--space-2)',
                }}
              >
                {/* Leading evidence */}
                <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                  <div className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                    LEADING EVIDENCE
                  </div>
                  {assessment.leading ? (
                    <div style={{ marginTop: 4 }}>
                      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                        <span style={{ fontWeight: 700, fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>
                          {assessment.leading.name}
                        </span>
                        {assessment.leading.score !== null && (
                          <span className="mono" style={{ fontSize: '11px', color: 'var(--accent-bright)' }}>
                            score: {formatScore(assessment.leading.score, 4)}
                          </span>
                        )}
                      </div>
                      <p style={{ fontSize: '11px', color: 'var(--text-muted)', margin: '4px 0 0', lineHeight: 1.4 }}>
                        {assessment.leading.detail}
                      </p>
                    </div>
                  ) : (
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      No single leading signal (insufficient signal coverage).
                    </span>
                  )}
                </div>

                {/* Supporting evidence */}
                {assessment.supporting.length > 0 && (
                  <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                    <div className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                      SUPPORTING EVIDENCE ({assessment.supporting.length})
                    </div>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {assessment.supporting.map((s, idx) => (
                        <li key={idx}>
                          <strong style={{ color: 'var(--text-strong)' }}>{s.name}</strong>
                          {s.score !== null ? ` (score: ${formatScore(s.score, 4)})` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Conflicting evidence */}
                {assessment.conflicting.length > 0 && (
                  <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                    <div className="label" style={{ fontSize: '10px', color: 'var(--warn-bright)' }}>
                      CONFLICTING EVIDENCE ({assessment.conflicting.length})
                    </div>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: '11px', color: 'var(--warn-bright)', lineHeight: 1.4 }}>
                      {assessment.conflicting.map((s, idx) => (
                        <li key={idx}>
                          <strong>{s.name}</strong>
                          {s.score !== null ? ` (score: ${formatScore(s.score, 4)})` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Unavailable evidence */}
                {assessment.unavailable.length > 0 && (
                  <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                    <div className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                      UNAVAILABLE / EXCLUDED EVIDENCE ({assessment.unavailable.length})
                    </div>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {assessment.unavailable.map((u, idx) => (
                        <li key={idx}>
                          <strong style={{ color: 'var(--text-strong)' }}>{u.name}</strong>: {u.reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

                {/* Descriptive Observations */}
                {assessment.observations.length > 0 && (
                  <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                    <div className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                      DESCRIPTIVE OBSERVATIONS ({assessment.observations.length})
                    </div>
                    <span style={{ fontSize: '10px', color: 'var(--text-faint)', display: 'block', margin: '2px 0 4px' }}>
                      Reported for examiner review; does not move synthetic-media assessment state.
                    </span>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 16, fontSize: '11px', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {assessment.observations.map((o, idx) => (
                        <li key={idx}>
                          <strong style={{ color: 'var(--text-strong)' }}>{o.name}</strong>
                          {o.score !== null ? ` (score: ${formatScore(o.score, 4)})` : ''}
                          {o.detail ? `: ${o.detail}` : ''}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : null}

            {/* Assessment Limitations if present */}
            {assessment && assessment.limitations.length > 0 ? (
              <div style={{ background: 'var(--surface-3)', padding: '8px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)', fontSize: '11px', color: 'var(--text-muted)' }}>
                <span className="label" style={{ fontSize: '10px', color: 'var(--text-faint)', display: 'block', marginBottom: 2 }}>ASSESSMENT LIMITATIONS</span>
                <ul style={{ margin: 0, paddingLeft: 16, lineHeight: 1.4 }}>
                  {assessment.limitations.map((lim, idx) => (
                    <li key={idx}>{lim}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* Fusion Coverage + Fusion Arithmetic side by side where they fit */}
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                gap: 'var(--space-2)',
              }}
            >
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', background: 'var(--surface-3)', padding: '8px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                <span className="label" style={{ fontSize: '10px', color: 'var(--text-faint)' }}>FUSION COVERAGE</span>
                <span className="mono" style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-strong)' }}>
                  {available} of {total} applicable signals
                </span>
              </div>

              <div style={{ background: 'var(--surface-3)', padding: '10px 12px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)' }}>
                  Fusion Arithmetic
                </span>
                <div className="mono break-all" style={{ fontSize: '11px', color: 'var(--text-muted)', marginTop: 2 }}>
                  {verdict.arithmetic || 'Not reported by the backend for this verdict.'}
                </div>
              </div>
            </div>

            {verdict.score_semantics ? (
              <p className="note" style={{ margin: 0 }}>{verdict.score_semantics}</p>
            ) : null}
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
                  <dt>Fused Manipulation Score</dt>
                  <dd className="mono">
                    {verdict.manipulation_score === null
                      ? `${NOT_MEASURED} (no signal could be scored)`
                      : `${formatScore(verdict.manipulation_score, 4)} on a 0-1 scale`}
                  </dd>
                  <dt>Confidence Band</dt>
                  <dd className="mono">{confidenceBandLabel(verdict.confidence)}</dd>
                  <dt>Declared Weights Total</dt>
                  <dd className="mono">{verdict.declared_weight_total}</dd>
                  <dt>Available Weight Sum</dt>
                  <dd className="mono">{verdict.available_weight.toFixed(4)}</dd>
                  <dt>Signal Coverage by Weight</dt>
                  <dd className="mono">
                    {typeof verdict.signal_coverage === 'number'
                      ? verdict.signal_coverage.toFixed(4)
                      : NOT_MEASURED}
                  </dd>
                  <dt>Primary Signal Available</dt>
                  <dd>{verdict.primary_signal_available ? 'Yes' : 'No'}</dd>
                  <dt>Fusion Method</dt>
                  <dd>{verdict.method} ({verdict.fusion_version})</dd>
                  {/*
                    The gate is printed only when the backend published it. The
                    previous `?? 0.4` fallback stated a coverage gate this system
                    does not use -- the configured minimum is 0.30 -- so a reader
                    checking the arithmetic against the printed gate would have
                    reached the wrong conclusion about whether the verdict passed.
                  */}
                  <dt>Gate Minimum Coverage</dt>
                  <dd className="mono">
                    {typeof verdict.thresholds?.minimum_signal_coverage === 'number'
                      ? verdict.thresholds.minimum_signal_coverage
                      : `${NOT_MEASURED} (not published by the backend)`}
                  </dd>
                </dl>

                {verdict.caveat ? (
                  <p className="note" style={{ marginTop: 'var(--space-2)', marginBottom: 0 }}>
                    {verdict.caveat}
                  </p>
                ) : null}
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
        <ErrorBanner error={analysis.error} context="Analysis" onRetry={() => runAnalysis()} />
      ) : (
        <AnalysisEntryState
          stored={stored}
          evidenceCount={evidence.length}
          onRun={() => runAnalysis()}
          onReRun={() => runAnalysis({ refresh: true })}
          onIngest={() => onNavigate('intake', { caseId: currentCaseId })}
        />
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

/**
 * What the Analysis screen shows for a case whose pipeline has not been run in
 * this session -- which is every case reached from the queue or a deep link.
 *
 * There is one thing this must get right: whether the case has already been
 * analysed. The read-only `GET /verdict` answers it from the case file, and the
 * four states below are exactly the four the backend can distinguish. None of
 * them is a placeholder for another:
 *
 *   loading  -- the case file has not answered yet. No claim either way.
 *   error    -- the read failed. Say so; do not fall through to "not analysed",
 *               which would report an unreachable backend as a forensic finding.
 *   analysed -- `analysed_count` items carry a fused verdict. The verdict of
 *               record is named here; loading the full workspace is an action the
 *               examiner takes, because it re-runs retrieval and is audited.
 *   none     -- no fusion on record. Now "Run Analysis" is the honest primary.
 *
 * A case with no evidence gets neither: there is nothing to fuse, so the next
 * step is intake, not analysis.
 *
 * Exported for the contract suite: these five branches encode forensic-honesty
 * rules (no premature claim, no verdict invented for an unfused exhibit, a null
 * score printed as NOT MEASURED) that are worth asserting directly rather than
 * through the whole screen.
 */
export function AnalysisEntryState({
  stored,
  evidenceCount,
  onRun,
  onReRun,
  onIngest,
}: {
  stored: {
    status: 'idle' | 'loading' | 'ready' | 'error'
    data: StoredVerdictResponse | null
    error: unknown
  }
  evidenceCount: number
  onRun: () => void
  onReRun: () => void
  onIngest: () => void
}) {
  if (stored.status === 'loading' || stored.status === 'idle') {
    return (
      <div className="card" style={{ padding: 'var(--space-5)' }}>
        <Spinner label="Reading the fused verdict on record for this case…" />
      </div>
    )
  }

  if (stored.status === 'error') {
    return (
      <ErrorBanner
        error={stored.error}
        context="Stored verdict"
      />
    )
  }

  const data = stored.data
  const analysed = data?.analysed_count ?? 0
  const onRecord = data?.items ?? []
  const pending = data?.pending_evidence ?? []
  /*
   * How many exhibits this case holds, according to the case file.
   *
   * Not `evidence.length`: that slice is cleared on a case switch and is only
   * filled by loading a case in this session, so on a deep link it is 0 while the
   * case in fact holds exhibits. Counting from it would print "0 exhibits" for a
   * case with five. The backend's `evidence_count` is the count of record; the
   * store's length is the fallback for a payload that predates it.
   */
  const exhibits = data?.evidence_count ?? evidenceCount

  // No evidence: the pipeline has nothing to weigh, so offer intake instead of a
  // Run button that could only produce an empty result.
  if (exhibits === 0) {
    return (
      <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-3)' }}>
        <span className="label">NO EVIDENCE IN THIS CASE</span>
        <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)', margin: 0 }}>
          This case holds no evidence, so there is nothing to analyse. Seal a file into
          the case first; the assessment is computed from the ingested bytes.
        </p>
        <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
          <button type="button" className="btn btn--primary btn--lg" onClick={onIngest}>
            Ingest Evidence →
          </button>
        </div>
      </div>
    )
  }

  if (analysed > 0) {
    return (
      <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-4)' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
          <span className="label">ANALYSIS ON RECORD</span>
          <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)' }}>
            {analysed} of {exhibits} exhibit(s) fused
          </span>
        </div>

        <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)', margin: 0, lineHeight: 'var(--leading-relaxed)' }}>
          This case has already been analysed. The verdict below is the one stored in
          the case file, read without re-running fusion. Load the full workspace to see
          the signal matrix, fusion arithmetic and near-duplicate retrieval behind it.
        </p>

        {/* The verdicts of record, named -- not summarised into a count. Each row
            is a stored fusion payload, so the band, score and time are the ones
            fusion wrote, and a null score prints as NOT MEASURED, never 0. */}
        <ul className="stack" style={{ listStyle: 'none', margin: 0, padding: 0, gap: 'var(--space-2)' }}>
          {onRecord.map((v) => (
            <li
              key={v.evidence_id}
              className="row"
              style={{
                justifyContent: 'space-between',
                alignItems: 'center',
                flexWrap: 'wrap',
                gap: 'var(--space-2)',
                background: 'var(--surface-3)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                padding: '10px 12px',
              }}
            >
              <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', wordBreak: 'break-all' }}>
                  {v.filename}
                </span>
                <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)' }}>
                  {v.fused_at ? `fused ${v.fused_at}` : 'fusion time not recorded'}
                </span>
              </div>
              <div className="row" style={{ gap: 'var(--space-2)', alignItems: 'center' }}>
                <span className="mono" style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                  score {v.manipulation_score === null ? NOT_MEASURED : formatScore(v.manipulation_score, 4)}
                </span>
                <Pill variant={verdictPillTone(v.verdict)} title={v.verdict}>
                  {verdictBandLabel(v.verdict)}
                </Pill>
              </div>
            </li>
          ))}
        </ul>

        {/* Unfused exhibits are stated, because a case-level verdict pill beside a
            partially analysed case would otherwise read as covering all of it. */}
        {pending.length > 0 ? (
          <p className="note" style={{ margin: 0 }}>
            {pending.length} further exhibit(s) in this case have no stored verdict. They
            have not been analysed -- no verdict has been withheld or implied for them.
            Re-running the analysis fuses them.
          </p>
        ) : null}

        <div className="btn-row" style={{ marginTop: 'var(--space-1)' }}>
          <button type="button" className="btn btn--primary btn--lg" onClick={onRun}>
            Load Full Analysis →
          </button>
          <button type="button" className="btn btn--ghost" onClick={onReRun}>
            <Icon name="refresh" size={14} />
            Re-Run Analysis
          </button>
        </div>
        <p className="note" style={{ margin: 0 }}>
          Loading replays the stored assessment and refreshes near-duplicate retrieval.
          Re-running recomputes every signal from the evidence bytes. Both are recorded
          in the audit chain.
        </p>
      </div>
    )
  }

  return (
    <div className="card stack" style={{ padding: 'var(--space-5)', gap: 'var(--space-3)' }}>
      <span className="label">NOT YET ANALYSED</span>
      <p style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)', margin: 0, lineHeight: 'var(--leading-relaxed)' }}>
        No fused verdict is stored for this case&apos;s {exhibits === 1 ? 'exhibit' : `${exhibits} exhibits`}.
        Running the analysis extracts metadata, runs the available detectors, inspects
        C2PA provenance, retrieves near-duplicates and fuses whichever signals could be
        measured into a verdict.
      </p>
      <div className="btn-row" style={{ marginTop: 'var(--space-2)' }}>
        <button type="button" className="btn btn--primary btn--lg" onClick={onRun}>
          Run Analysis →
        </button>
      </div>
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
  const isAbstained =
    signal.status === 'INCONCLUSIVE' ||
    signal.evidence_basis?.availability === 'ran_and_declined' ||
    signal.evidence_basis?.abstained === true ||
    String(signal.explanation || '').toLowerCase().includes('abstained')
  const basis = Object.entries(signal.evidence_basis ?? {})
  return (
    <div className="stack" style={{ gap: 'var(--space-3)' }}>
      <div className="row" style={{ gap: 8, alignItems: 'center' }}>
        <Pill variant={isAbstained ? 'warn' : signalPillVariant(signal, thresholds)} title={signal.status}>
          {statusLabel(signal.status, signal)}
        </Pill>
        <span className="faint" style={{ fontSize: 'var(--text-2xs)', fontFamily: 'var(--mono)' }}>
          {signal.signal_id}
        </span>
      </div>

      <p style={{ fontSize: 'var(--text-sm)', margin: 0, color: 'var(--text-strong)', lineHeight: 'var(--leading-relaxed)' }}>
        {signal.explanation}
      </p>

      {isAbstained ? (
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 4, background: 'var(--surface-3)', border: '1px solid var(--border)' }}>
          <span style={{ fontSize: '10px', fontWeight: 700, color: 'var(--warn-bright)', letterSpacing: '0.06em' }}>
            MODEL STATUS: RAN — ABSTAINED
          </span>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>
            <strong>Reason:</strong> Prediction inside model abstention band ({exclusionReason ?? 'prediction fell within abstention range'}).
          </div>
          <div style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            <strong>Contribution:</strong> Excluded from fusion (weight redistributed proportionally).
          </div>
        </div>
      ) : excluded ? (
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
          <label className="field__label" htmlFor="adhoc-detector-file">
            Upload Local File
          </label>
          <input
            ref={fileInputRef}
            id="adhoc-detector-file"
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
                  ? `${NOT_MEASURED} (model ran and abstained)`
                  : `${NOT_MEASURED} (no score returned)`
                : formatScore(result.manipulation_score, 4)}
            </dd>
            {/*
              Confidence is shown only when the MODEL reported one. These
              detectors do not: a confidence derived from the score carries no
              extra information, and none of them ship with a calibration set.
            */}
            <dt>Model-reported Confidence</dt>
            <dd className="mono">
              {result.confidence === null
                ? `${NOT_MEASURED} (this model reports no calibrated confidence)`
                : `${(result.confidence * 100).toFixed(1)}%`}
            </dd>
            <dt>Status</dt>
            <dd className="mono">{result.status}</dd>
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
