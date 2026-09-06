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
import type { MatchCandidate, MatchesResponse, Origin, PropagationResponse, WebDiscoveryResponse } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { Empty, NoCaseSelected, Spinner } from '../components/Feedback'
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
  const { caseRecord, analysis, propagation, loadPropagation, traceProvenance } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  /*
   * Whether the in-flight propagation call is the operator's trace or the
   * screen's own read.
   *
   * The slice cannot tell them apart -- both are `phase: 'loading'` -- and the
   * two take very different amounts of time, so a shared label would either
   * overstate a read or leave a running corpus search looking like a page that
   * has simply not finished loading.
   */
  const [traceRequested, setTraceRequested] = useState(false)

  /*
   * Opening this screen reads; it does not trace.
   *
   * `loadPropagation` reconstructs from retrieval already on record and writes
   * nothing. It used to call the recording path, so merely arriving here ran
   * near-duplicate retrieval against the corpus and appended `MATCH_SEARCHED`
   * and `PROPAGATION_RECONSTRUCTED` to the case's chain -- the head hash moved
   * because somebody looked. Running the trace is the operator's act, and it has
   * its own button.
   */
  useEffect(() => {
    /*
     * The store's own case id lags the route's.
     *
     * `loadPropagation` is bound to `caseRecord.case_id`, which only becomes the
     * new case once `GET /api/cases/{id}` resolves, while this effect fires the
     * moment the route changes and the slice is reset to idle. Firing on the
     * route alone therefore issued the *previous* case's request and stored the
     * answer under this one: opening PRAMAAN-1003 showed PRAMAAN-1005's trace,
     * origin filename and NOT-MEASURED state, and vice versa -- one case's
     * forensic findings presented under another case's number. The generation
     * guard does not catch it, because the request is issued after the switch,
     * not before it.
     *
     * Waiting for the two to agree is what makes the read be about the case on
     * screen. The slice stays idle until then, so nothing stale is rendered in
     * the meantime.
     */
    const storeIsOnThisCase = caseRecord?.case_id === currentCaseId
    if (currentCaseId && storeIsOnThisCase && propagation.phase === 'idle') {
      // Also clears the trace flag: switching cases returns the slice to idle,
      // and the previous case's trace must not label the new case's read.
      setTraceRequested(false)
      loadPropagation()
    }
  }, [currentCaseId, caseRecord?.case_id, propagation.phase, loadPropagation])

  // On-demand candidate search
  const [liveMatches, setLiveMatches] = useState<MatchesResponse | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<unknown>(null)
  /*
   * Whether near-duplicate retrieval has ever run for this case.
   *
   * From the backend's audit trail, not inferred from an empty candidate list:
   * "nothing similar is indexed" and "nobody has looked" produce the same empty
   * table and only the first is a finding.
   */
  const [candidatesSearched, setCandidatesSearched] = useState(false)

  // Selected node in the lineage pipeline
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  // Public web discovery state
  const [webDiscovery, setWebDiscovery] = useState<WebDiscoveryResponse | null>(null)
  const [runningWebDiscovery, setRunningWebDiscovery] = useState(false)
  const [webDiscoveryError, setWebDiscoveryError] = useState<unknown>(null)
  const [customImageUrl, setCustomImageUrl] = useState('')
  /*
   * The availability probe, kept apart from its answer.
   *
   * `webDiscovery === null` used to mean three different things at once --
   * still asking, asked and told no, and the ask itself failed -- and the
   * summary badge rendered all three as "UNAVAILABLE / OPTIONAL". A stage whose
   * status could not be read is not a stage that is switched off, and saying so
   * is the same mistake this file already refuses to make about a corpus search
   * that nobody has run.
   */
  const [probe, setProbe] = useState<'loading' | 'loaded' | 'failed'>('loading')

  // Check public web discovery availability on mount
  useEffect(() => {
    let cancelled = false
    if (!currentCaseId) {
      setProbe('loading')
      return
    }
    setProbe('loading')
    api
      .getWebDiscovery(currentCaseId)
      .then((res) => {
        if (cancelled) return
        setWebDiscovery(res)
        setProbe('loaded')
      })
      .catch(() => {
        // Not surfaced as a banner: nothing the examiner did has failed, and the
        // rest of the screen is unaffected. It is recorded so the badge can say
        // "status unknown" instead of quietly asserting "unavailable".
        if (!cancelled) setProbe('failed')
      })
    return () => {
      cancelled = true
    }
  }, [currentCaseId])

  /**
   * Whether this deployment can actually run the stage.
   *
   * `null` while unknown -- which is why it is compared explicitly rather than
   * used as a boolean: a run button must be disabled when the answer is no, and
   * left enabled when the answer has not arrived, not disabled by the absence of
   * an answer.
   */
  const webDiscoveryAvailable: boolean | null =
    probe === 'loaded' && webDiscovery ? webDiscovery.available : null
  const webDiscoveryBlockedReason =
    webDiscoveryAvailable === false
      ? webDiscovery?.unavailable_reason ||
        'Public web discovery is not configured on this deployment.'
      : null

  const handleRunWebDiscovery = (overrideUrl?: string) => {
    if (!currentCaseId) return
    // Snapshot the case the run belongs to: a slow discovery for Case A must
    // not write its results into the screen rendered for Case B after a switch.
    const requestCaseId = currentCaseId
    setRunningWebDiscovery(true)
    setWebDiscoveryError(null)
    const imgUrl = overrideUrl !== undefined ? overrideUrl : customImageUrl.trim()
    api
      .runWebDiscovery(requestCaseId, {
        imageUrl: imgUrl || undefined,
        refresh: true,
      })
      .then((res) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setWebDiscovery(res)
      })
      .catch((err) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setWebDiscoveryError(err)
      })
      .finally(() => {
        setRunningWebDiscovery(false)
      })
  }


  if (!currentCaseId) {
    return <NoCaseSelected purpose="trace its provenance" onViewCases={() => onNavigate('cases')} />
  }

  const analysisData = isReady(analysis) ? analysis.data : null
  const propData: PropagationResponse | null = isReady(propagation) ? propagation.data : null

  /*
   * Whether the retrieval this reconstruction rests on has ever actually run.
   *
   * `NOT_RUN`, `STORED` and a search that matched nothing all produce the same
   * empty graph, and they do not mean the same thing: the first is an absence of
   * measurement, the second a measurement taken earlier, and only the third is a
   * finding. The screen used to render all of them as "NO MATCH FOUND -- trace
   * search completed", which reported never having looked as having looked and
   * found nothing.
   *
   * Older backends do not send the field. Treating that as `STORED` keeps the
   * previous wording for them rather than accusing them of not having searched.
   */
  const traceStatus = propData?.trace_status ?? 'STORED'
  const traceNotRun = traceStatus === 'NOT_RUN'
  const tracing = propagation.phase === 'loading'

  const runTrace = () => {
    setTraceRequested(true)
    traceProvenance()
  }

  const origin: Origin | null = propData?.origin ?? analysisData?.origin ?? null
  const graph = propData?.graph ?? null
  const nodes = graph?.nodes ?? []

  const subjectNode = nodes.find((n) => n.is_case_evidence) ?? null
  const earliestEvidenceId = origin?.evidence_id ?? null

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

  // Select subject node by default if none selected
  const activeSelectedNode =
    nodes.find((n) => n.evidence_id === (selectedNodeId || earliestEvidenceId || subjectNode?.evidence_id)) ||
    nodes[0] ||
    null

  const seededMatches = analysisData?.matches ?? null
  const effectiveMatches = liveMatches ?? seededMatches

  /**
   * Run near-duplicate retrieval, and record that it ran.
   *
   * A write: it replaces the case's stored match set and appends
   * `MATCH_SEARCHED`. Correct behind the operator's button, which is now the
   * only thing that calls it -- see `loadStoredCandidates` for the mount path.
   */
  const runCandidateSearch = () => {
    if (!currentCaseId) return
    // Snapshot: matches for Case A must not land in the list rendered for
    // Case B after a rapid switch. Last-issued wins, not last-resolved.
    const requestCaseId = currentCaseId
    setSearching(true)
    setSearchError(null)
    api.matches(requestCaseId).then(
      (data) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setLiveMatches(data)
        setCandidatesSearched(true)
        setSearching(false)
      },
      (err) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setSearchError(err)
        setSearching(false)
      },
    )
  }

  /**
   * Read the candidates already stored for the case. Retrieval is not run.
   *
   * This is what opening the screen is entitled to do. It used to call
   * `runCandidateSearch`, so arriving here POSTed to `/matches` and appended a
   * `MATCH_SEARCHED` row to the case's audit chain -- forensic history written
   * because somebody navigated. The GET on the same path returns exactly what
   * the last search stored, plus `searched`, which is the one thing the
   * candidate list cannot tell us: whether anybody has ever looked.
   */
  const loadStoredCandidates = () => {
    if (!currentCaseId) return
    const requestCaseId = currentCaseId
    setSearching(true)
    setSearchError(null)
    api.storedMatches(requestCaseId).then(
      (data) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setLiveMatches(data)
        setCandidatesSearched(data.searched)
        setSearching(false)
      },
      (err) => {
        if (requestCaseId !== (caseId || caseRecord?.case_id || null)) return
        setSearchError(err)
        setSearching(false)
      },
    )
  }

  // Read the stored candidates when the case changes. A read, never a search.
  useEffect(() => {
    if (currentCaseId) {
      loadStoredCandidates()
    }

  }, [currentCaseId])

  const candidatesList = useMemo(() => {
    if (!effectiveMatches?.queries) return []
    const seen = new Set<string>()
    const list: MatchCandidate[] = []
    for (const q of effectiveMatches.queries) {
      for (const c of q.candidates || []) {
        if (!seen.has(c.evidence_id)) {
          seen.add(c.evidence_id)
          list.push(c)
        }
      }
    }
    return list
  }, [effectiveMatches])

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* The case workflow stepper (Case → Evidence → … → Provenance → …) is
          owned by the app shell and rendered once, above this screen. It is
          not rendered here. */}

      {/* 2. PAGE HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">PROVENANCE &amp; SOURCE TRACE</h1>
          <p className="screen__lead">
            Trace this file to the earliest instance of it held in the indexed evidence corpus.
          </p>
        </div>
        {/*
          The trace is an act, so it needs a control.

          It had none: the screen ran the recording trace silently on mount and
          offered no way to ask for one, which is backwards on both counts --
          arriving somewhere wrote to a forensic record, and wanting the work
          done was not something the operator could express. The label says which
          of the two it is, because re-running against a corpus that has since
          grown is a different request from running for the first time.
        */}
        <button
          type="button"
          className="btn btn--primary"
          onClick={runTrace}
          // Double-submit prevention: each click runs retrieval across the
          // corpus and appends to the chain, so a second one mid-flight would
          // duplicate real work and real audit rows.
          disabled={tracing || !currentCaseId}
        >
          {tracing ? 'Tracing…' : traceNotRun ? 'Trace Provenance' : 'Re-Trace Provenance'}
        </button>
      </div>

      {/*
        Never searched is not the same as searched and found nothing, and only
        one of them is a result. Said once, at the top, because every panel below
        renders from the same empty graph.
      */}
      {propagation.phase === 'ready' && traceNotRun ? (
        <Banner
          tone="info"
          title="NOT MEASURED — NO PROVENANCE TRACE ON RECORD FOR THIS CASE"
          detail={
            propData?.trace_status_meaning ||
            'No near-duplicate retrieval has been run for this case. Nothing has been measured about copies of this evidence elsewhere in the corpus.'
          }
          meta="Run Trace Provenance to search the indexed corpus. The trace is recorded in this case's audit chain."
        />
      ) : null}

      {/*
        A failed or in-flight trace must not render as a finding. Without this,
        `propagation.phase === 'error'` fell through to "No prior instance found"
        and "No propagation nodes indexed for this case" -- presenting a network
        failure as a negative forensic result.
      */}
      {propagation.phase === 'error' ? (
        <ErrorBanner
          error={propagation.error}
          context="Provenance trace"
          // Retrying is a read, so the flag has to come back down or the next
          // spinner would claim a corpus search that is not running.
          onRetry={() => {
            setTraceRequested(false)
            loadPropagation()
          }}
        />
      ) : null}
      {/* Reading the reconstruction and running the retrieval are different
          waits; naming the one actually happening keeps the label honest. */}
      {propagation.phase === 'loading' ? (
        <Spinner
          label={
            traceRequested
              ? 'Running provenance trace against the indexed corpus…'
              : 'Loading provenance reconstruction…'
          }
        />
      ) : null}

      {/* 3. 2-COLUMN MAIN WORKSPACE (MATCHING PANEL 6 IN COLLAGE).

          `workspace-2col` collapses to one column under 960px. */}
      <div
        className="workspace-2col"
        style={{
          gridTemplateColumns: 'minmax(0, 1fr) 280px',
        }}
      >
        {/* LEFT MAIN COLUMN */}
        <div className="stack" style={{ gap: 'var(--space-4)' }}>
          {/* Box 1: EARLIEST KNOWN INSTANCE */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              EARLIEST KNOWN INSTANCE IN THE INDEXED EVIDENCE CORPUS
            </span>

            {origin ? (
              <>
                <div className="grid-3col" style={{ gap: 12, background: 'var(--surface-2)', padding: '12px 16px', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', fontFamily: 'var(--mono)', color: 'var(--text-faint)', fontWeight: 700 }}>
                      SOURCE DOMAIN / PLATFORM
                    </span>
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                      {origin.platform?.startsWith('web:') || origin.platform?.startsWith('http')
                        ? `PUBLIC WEB DISCOVERY (${origin.platform})`
                        : origin.platform
                        ? `INTERNAL EVIDENCE CORPUS (${origin.platform})`
                        : 'INTERNAL EVIDENCE CORPUS'}
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
              /*
                Two different absences share this slot, and the screen used to
                print the first one's wording over both: "NO MATCH FOUND / Trace
                search completed" appeared for cases where no trace had ever been
                run, reporting the absence of a search as the absence of copies.
              */
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: traceNotRun ? 'var(--text-muted)' : 'var(--text-strong)' }}>
                  {traceNotRun ? 'NOT MEASURED' : 'NO MATCH FOUND'}
                </span>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                  {traceNotRun
                    ? 'No provenance trace has been run for this case, so no earliest instance has been established. This is not a finding about the file: nothing has been measured.'
                    : 'Trace search completed against the indexed evidence corpus. No earlier or matching instance was identified. The absence of a prior match is not proof that the file is an original or authentic upload.'}
                </p>
              </div>
            ) : propagation.phase === 'error' ? (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--warn-bright)' }}>UNAVAILABLE</span>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                  The provenance trace service could not complete. This is not a finding of originality or of manipulation.
                </p>
              </div>
            ) : (
              <div style={{ padding: '12px 16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', border: '1px solid var(--border)' }}>
                <span style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-muted)' }}>NOT MEASURED</span>
                <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                  Provenance has not been computed for this case yet. Use "Trace Provenance" above to
                  search the indexed corpus.
                </p>
              </div>
            )}
          </div>

          {/* Box 2: HORIZONTAL LINEAGE NODE GRAPH */}
          <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              PROVENANCE TIMELINE
            </span>

            {nodes.length > 1 ? (
              <div
                className="row"
                style={{
                  gap: 8,
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  padding: '8px 12px',
                  background: 'var(--surface-2)',
                  borderRadius: 'var(--radius)',
                  border: '1px solid var(--border)',
                }}
              >
                <span
                  style={{
                    fontSize: '10px',
                    textTransform: 'uppercase',
                    color: 'var(--text-faint)',
                    fontFamily: 'var(--mono)',
                    fontWeight: 800,
                    letterSpacing: '0.06em',
                  }}
                >
                  SEQUENCE:
                </span>
                {nodes.map((n, i) => (
                  <span
                    key={n.evidence_id}
                    className="row"
                    style={{ gap: 6, alignItems: 'center', fontSize: 'var(--text-xs)', fontFamily: 'var(--mono)' }}
                  >
                    <span
                      style={{
                        fontWeight: 700,
                        color:
                          n.evidence_id === earliestEvidenceId
                            ? 'var(--info)'
                            : n.is_case_evidence
                            ? 'var(--danger-bright)'
                            : 'var(--text-strong)',
                      }}
                    >
                      {n.filename}
                    </span>
                    {i < nodes.length - 1 ? <span style={{ color: 'var(--text-faint)', fontWeight: 700 }}>→</span> : null}
                  </span>
                ))}
              </div>
            ) : null}

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

                  /*
                   * Node colours are semantic tokens, not literals, so they
                   * track the light/dark theme: the earliest instance reads as
                   * info, the case evidence as danger, intermediates as faint.
                   * The translucent fills use color-mix because a CSS variable
                   * cannot carry an appended hex alpha.
                   */
                  const nodeColor = isEarliest
                    ? 'var(--info)'
                    : isSubject
                    ? 'var(--danger-bright)'
                    : 'var(--text-faint)'

                  return (
                    <div key={node.evidence_id} style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1 }}>
                      {/*
                        A real button, not a div claiming to be one. It had
                        `role="button"` and `tabIndex={0}` but no key handler, so
                        a keyboard user could focus a lineage node and then had
                        no way to open it -- the one combination that is worse
                        than leaving it unfocusable, because it looks reachable.
                        `aria-pressed` carries the selected state that the border
                        and shadow show visually.
                      */}
                      <button
                        type="button"
                        onClick={() => setSelectedNodeId(node.evidence_id)}
                        aria-pressed={isSelected}
                        title={`Show the details recorded for this instance${
                          node.platform ? ` (${node.platform})` : ''
                        }`}
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
                          font: 'inherit',
                          boxShadow: isSelected ? `0 0 0 2px color-mix(in srgb, ${nodeColor} 20%, transparent)` : undefined,
                        }}
                      >
                        <div
                          style={{
                            width: 36,
                            height: 36,
                            borderRadius: '50%',
                            background: `color-mix(in srgb, ${nodeColor} 13%, transparent)`,
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
                      </button>

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
                TRACE SUMMARY
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

      {/* 3. RELATED / NEAR-DUPLICATE CANDIDATES */}
      <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
          <div className="stack" style={{ gap: 2 }}>
            <span className="label" style={{ color: 'var(--text-strong)' }}>
              RELATED / NEAR-DUPLICATE CANDIDATES ({candidatesList.length})
            </span>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              {effectiveMatches?.interpretation
                ? effectiveMatches.interpretation
                : 'Retrieved from the indexed evidence corpus; verification basis is listed per candidate.'}
            </span>
          </div>
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <Pill variant={candidatesList.length > 0 ? 'ok' : 'neutral'}>
              {candidatesList.length} {candidatesList.length === 1 ? 'candidate' : 'candidates'}
            </Pill>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={runCandidateSearch}
              disabled={searching}
            >
              {searching ? <Spinner label="Searching..." /> : <Icon name="search" size={13} />}
              Re-run Search
            </button>
          </div>
        </div>

        {candidatesList.length > 0 ? (
          <div className="table-wrapper" style={{ overflowX: 'auto' }}>
            <table className="table" style={{ width: '100%', fontSize: 'var(--text-xs)' }}>
              <thead>
                <tr>
                  <th>FILE / EVIDENCE ID</th>
                  <th>VISUAL SIMILARITY</th>
                  <th>PERCEPTUAL DISTANCE</th>
                  <th>MATCH BASIS</th>
                  <th>TIMESTAMP</th>
                  <th>CORPUS / PLATFORM</th>
                  <th>TRANSFORMATION</th>
                </tr>
              </thead>
              <tbody>
                {candidatesList.map((cand) => (
                  <tr key={cand.evidence_id}>
                    <td>
                      <div className="stack" style={{ gap: 2 }}>
                        <span style={{ fontWeight: 700, color: 'var(--text-strong)' }}>{cand.filename}</span>
                        <code style={{ fontSize: '10px', color: 'var(--text-faint)' }}>{cand.evidence_id}</code>
                      </div>
                    </td>
                    <td>
                      <span style={{ fontWeight: 700, color: cand.similarity >= 0.9 ? 'var(--ok-bright)' : 'var(--accent-bright)' }}>
                        {formatSimilarity(cand.similarity)}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontFamily: 'var(--mono)', color: 'var(--text-strong)' }}>
                        {formatDistance(cand.distance)}
                      </span>
                    </td>
                    <td>
                      {/*
                        The backend labels every candidate with a real
                        `match_basis`: "Exact SHA-256 byte match" only when the
                        bytes are identical, "Multi-hash verified (pHash dist N,
                        ...)" with the measured distances, or "Perceptual match".
                        The old fallback re-derived a claim from `distance === 0`
                        and printed "Exact SHA-256 byte match" for a perceptual
                        distance of zero -- which is not byte-identity -- or a
                        bare "Multi-hash verified" with no distances. Both stated
                        a verification the data had not performed, so the field is
                        shown as the backend sent it and left as a placeholder
                        when absent rather than invented.
                      */}
                      <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        {orPlaceholder(cand.match_basis)}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: '11px', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                        {formatTimestamp(cand.observed_at || cand.timestamp)}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: '11px', color: 'var(--text-strong)' }}>
                        {cand.platform?.startsWith('web:') || cand.platform?.startsWith('http')
                          ? `PUBLIC WEB DISCOVERY (${cand.platform})`
                          : cand.platform
                          ? `INTERNAL EVIDENCE CORPUS (${cand.platform})`
                          : 'INTERNAL EVIDENCE CORPUS'}
                      </span>
                    </td>
                    <td>
                      <Pill variant="neutral">
                        {orPlaceholder(cand.transformation)}
                      </Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          /*
            An empty candidate table has two causes and they are not
            interchangeable: retrieval ran and matched nothing, or retrieval has
            never run. This printed the first wording for both, so a case nobody
            had searched read as one searched without result.
          */
          <div style={{ padding: '16px', background: 'var(--surface-2)', borderRadius: 'var(--radius)', textAlign: 'center' }}>
            <span style={{ fontSize: 'var(--text-sm)', color: 'var(--text-muted)' }}>
              {searching
                ? 'Reading stored candidates…'
                : candidatesSearched
                  ? 'Near-duplicate retrieval ran and returned no candidates from the indexed evidence corpus. That is not proof that no other copies exist.'
                  : 'NOT MEASURED — no near-duplicate retrieval has been run for this case. Use "Run Candidate Search" below to search the indexed corpus; the search is recorded in the audit chain.'}
            </span>
          </div>
        )}
      </div>

      {/* ========================================================================= */}
      {/* 4. PUBLIC WEB DISCOVERY (Google Cloud Vision Web Detection)               */}
      {/* ========================================================================= */}
      <details className="disclosure card" open style={{ padding: 'var(--space-5)', border: '1px solid var(--border-subtle)', background: 'var(--surface-1)' }}>
        <summary className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap', fontWeight: 800 }}>
          <Icon name="arrow-right" size={13} className="disclosure__chevron" />
          <span style={{ fontSize: 'var(--text-base)', letterSpacing: '0.02em' }}>PUBLIC WEB DISCOVERY</span>
          <span className="badge badge--neutral mono" style={{ fontSize: '10px', padding: '2px 8px', letterSpacing: '0.05em' }}>
            GOOGLE CLOUD VISION
          </span>
          {/* Four states, not two: this deployment can run the stage, cannot run
              it, has not answered yet, or could not be asked. */}
          {probe === 'loading' ? (
            <span className="badge badge--muted" style={{ fontSize: '10px' }}>CHECKING…</span>
          ) : webDiscoveryAvailable === true ? (
            <span className="badge badge--success" style={{ fontSize: '10px' }}>AVAILABLE</span>
          ) : webDiscoveryAvailable === false ? (
            <span className="badge badge--muted" style={{ fontSize: '10px' }}>UNAVAILABLE / OPTIONAL</span>
          ) : (
            <span
              className="badge badge--muted"
              style={{ fontSize: '10px' }}
              title="The backend did not answer when this screen asked whether public web discovery is configured. That is not the same as the stage being switched off."
            >
              STATUS UNKNOWN
            </span>
          )}
        </summary>
        <div className="disclosure__panel stack" style={{ gap: 'var(--space-4)', marginTop: 'var(--space-3)' }}>
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            <p style={{ margin: 0, fontSize: 'var(--text-xs)', color: 'var(--text-muted)', maxWidth: '60ch' }}>
              Secondary forensic layer searching for publicly indexed external web occurrences. Independent of internal evidence corpus.
            </p>

            <div className="row" style={{ gap: 8, alignItems: 'center' }}>
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => handleRunWebDiscovery()}
                /* Disabled only when the backend has said outright that the
                   stage cannot run -- not while the answer is still in flight,
                   and not because the probe failed. An examiner should not be
                   invited to press a button whose only possible outcome is an
                   error banner, and should not be locked out of one that would
                   have worked. */
                disabled={runningWebDiscovery || webDiscoveryAvailable === false}
                title={
                  webDiscoveryBlockedReason
                    ? `Cannot run on this deployment: ${webDiscoveryBlockedReason}`
                    : 'Query Google Cloud Vision Web Detection for public copies of this exhibit'
                }
                style={{ fontWeight: 700 }}
              >
                {runningWebDiscovery ? <Spinner label="Querying Web..." /> : <Icon name="external" size={13} />}
                Run Public Web Discovery
              </button>
            </div>
          </div>

        {/* Optional Custom Public Image URL Input */}
        <div className="row" style={{ gap: 8, alignItems: 'center', background: 'var(--surface-2)', padding: 'var(--space-2) var(--space-3)', borderRadius: 'var(--radius-sm)' }}>
          {/* A real label rather than a span that merely sits next to the field:
              the caption is already on screen, so it costs nothing to make it
              the field's name and click target too. */}
          <label
            htmlFor="web-discovery-url"
            style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', fontWeight: 600 }}
          >
            Inspect Image URL:
          </label>
          <input
            id="web-discovery-url"
            type="url"
            value={customImageUrl}
            onChange={(e) => setCustomImageUrl(e.target.value)}
            placeholder="https://example.com/image.jpg (Optional public URL)"
            className="input"
            style={{ flex: 1, fontSize: 'var(--text-xs)', padding: '4px 8px', height: '28px' }}
          />
          {customImageUrl && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              style={{ height: '28px', fontSize: '11px', padding: '0 8px' }}
              onClick={() => handleRunWebDiscovery(customImageUrl)}
              disabled={runningWebDiscovery || webDiscoveryAvailable === false}
              title={
                webDiscoveryBlockedReason
                  ? `Cannot run on this deployment: ${webDiscoveryBlockedReason}`
                  : 'Run public web discovery against this URL instead of the case exhibit'
              }
            >
              Inspect This URL
            </button>
          )}
        </div>

        {webDiscoveryError ? (
          <ErrorBanner error={webDiscoveryError} context="Public web discovery" onRetry={() => handleRunWebDiscovery()} />
        ) : null}

        {/* State A: Unavailable */}
        {webDiscovery && !webDiscovery.available && (
          <Banner
            tone="info"
            title="Public web discovery unavailable."
            detail={
              webDiscovery.unavailable_reason ||
              'Google Cloud Vision Web Detection credentials (GOOGLE_APPLICATION_CREDENTIALS) are not configured or PRAMAAN_WEB_DISCOVERY_ENABLED is set to false.'
            }
            meta="Internal evidence corpus provenance and perceptual matching continue to operate independently of public web discovery."
          />
        )}

        {/* State B: Available and Executed with Results */}
        {webDiscovery && webDiscovery.available && webDiscovery.status === 'SUCCESS' && (
          <div className="stack" style={{ gap: 'var(--space-4)' }}>
            {/* Earliest Discovered Public Web Occurrence */}
            {webDiscovery.earliest_discovered_occurrence && (
              <div
                style={{
                  background: 'var(--info-wash)',
                  border: '1px solid var(--info-line)',
                  borderRadius: 'var(--radius-md)',
                  padding: 'var(--space-3) var(--space-4)',
                }}
              >
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: '11px', fontWeight: 800, textTransform: 'uppercase', color: 'var(--accent-bright)', letterSpacing: '0.06em' }}>
                    EARLIEST DISCOVERED PUBLIC WEB OCCURRENCE
                  </span>
                  <span className="badge badge--accent mono" style={{ fontSize: '10px' }}>
                    {webDiscovery.earliest_discovered_occurrence.match_type}
                  </span>
                </div>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <a
                      href={webDiscovery.earliest_discovered_occurrence.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ fontSize: 'var(--text-sm)', fontWeight: 700, color: 'var(--text-strong)', textDecoration: 'underline' }}
                    >
                      {webDiscovery.earliest_discovered_occurrence.page_title || webDiscovery.earliest_discovered_occurrence.url}
                    </a>
                    <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                      Source domain: {webDiscovery.earliest_discovered_occurrence.domain}
                    </span>
                  </div>
                  <div className="row" style={{ gap: 12, alignItems: 'center' }}>
                    {webDiscovery.earliest_discovered_occurrence.similarity !== null && (
                      <div className="stack" style={{ alignItems: 'flex-end', gap: 2 }}>
                        <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>SIMILARITY</span>
                        <strong className="mono" style={{ fontSize: 'var(--text-sm)', color: 'var(--accent-bright)' }}>
                          {(webDiscovery.earliest_discovered_occurrence.similarity * 100).toFixed(1)}%
                        </strong>
                      </div>
                    )}
                    <div className="stack" style={{ alignItems: 'flex-end', gap: 2 }}>
                      <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>TIMESTAMP</span>
                      <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                        {formatTimestamp(webDiscovery.earliest_discovered_occurrence.published_at || webDiscovery.earliest_discovered_occurrence.discovered_at)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Public Web Timeline */}
            {webDiscovery.timeline.length > 0 && (
              <div className="stack" style={{ gap: 8 }}>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: '11px', fontWeight: 800, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.05em' }}>
                    PUBLIC WEB TIMELINE
                  </span>
                  <span className="mono" style={{ fontSize: '11px', color: 'var(--text-faint)' }}>
                    {webDiscovery.timeline.length} public occurrences
                  </span>
                </div>
                <div className="row" style={{ gap: 8, overflowX: 'auto', paddingBottom: 6 }}>
                  {webDiscovery.timeline.map((node, idx) => (
                    <div
                      key={node.node_id}
                      className="card stack"
                      style={{
                        minWidth: 190,
                        maxWidth: 240,
                        padding: 'var(--space-2) var(--space-3)',
                        gap: 4,
                        fontSize: '11px',
                        background: node.is_earliest ? 'var(--info-wash)' : 'var(--surface-2)',
                        border: node.is_earliest ? '1px solid var(--accent-bright)' : '1px solid var(--border-subtle)',
                      }}
                    >
                      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                        <span style={{ fontWeight: 800, color: node.is_earliest ? 'var(--accent-bright)' : 'var(--text-muted)' }}>
                          Step {idx + 1}
                        </span>
                        <span className="badge badge--neutral mono" style={{ fontSize: '9px', padding: '1px 4px' }}>
                          {node.timestamp_type}
                        </span>
                      </div>
                      <span style={{ fontWeight: 600, color: 'var(--text-strong)', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }} title={node.label}>
                        {node.domain}
                      </span>
                      <span className="mono" style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                        {formatTimestampShort(node.timestamp)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Web Occurrences Table */}
            {webDiscovery.occurrences.length > 0 && (
              <div className="stack" style={{ gap: 8 }}>
                <span style={{ fontSize: '11px', fontWeight: 800, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.05em' }}>
                  DISCOVERED PUBLIC WEB OCCURRENCES ({webDiscovery.occurrences.length})
                </span>
                <div style={{ overflowX: 'auto', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-md)' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 'var(--text-xs)' }}>
                    <thead>
                      <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border-subtle)', textAlign: 'left' }}>
                        <th style={{ padding: '8px 12px' }}>Source / URL</th>
                        <th style={{ padding: '8px 12px' }}>Match Type</th>
                        <th style={{ padding: '8px 12px' }}>Similarity</th>
                        <th style={{ padding: '8px 12px' }}>Match Basis</th>
                        <th style={{ padding: '8px 12px' }}>Timestamp</th>
                      </tr>
                    </thead>
                    <tbody>
                      {webDiscovery.occurrences.map((occ) => (
                        <tr key={occ.occurrence_id} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                          <td style={{ padding: '8px 12px' }}>
                            <div className="stack" style={{ gap: 2, maxWidth: 300 }}>
                              <strong style={{ color: 'var(--text-strong)' }}>{occ.domain}</strong>
                              <a href={occ.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-bright)', fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                {occ.page_title || occ.url}
                              </a>
                            </div>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span className="badge badge--neutral mono" style={{ fontSize: '10.5px' }}>
                              {occ.match_type}
                            </span>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            {occ.similarity !== null ? (
                              <strong className="mono" style={{ color: 'var(--accent-bright)' }}>
                                {(occ.similarity * 100).toFixed(1)}%
                              </strong>
                            ) : (
                              <span style={{ color: 'var(--text-faint)' }}>—</span>
                            )}
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              {occ.match_basis}
                            </span>
                          </td>
                          <td style={{ padding: '8px 12px' }}>
                            <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              {occ.published_at ? `Pub: ${formatTimestampShort(occ.published_at)}` : `Disc: ${formatTimestampShort(occ.discovered_at)}`}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Web Entities */}
            {webDiscovery.web_entities.length > 0 && (
              <div className="stack" style={{ gap: 6 }}>
                <span style={{ fontSize: '11px', fontWeight: 800, textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.05em' }}>
                  DETECTED WEB ENTITIES
                </span>
                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                  {webDiscovery.web_entities.map((ent, idx) => (
                    <span key={idx} className="badge badge--neutral" style={{ fontSize: '11px', padding: '3px 8px' }}>
                      {ent.description} {ent.score ? `(${Math.round(ent.score * 100)}%)` : ''}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* State C: No Results */}
        {webDiscovery && webDiscovery.available && webDiscovery.status === 'NO_RESULTS' && (
          <div style={{ padding: 'var(--space-4)', background: 'var(--surface-2)', borderRadius: 'var(--radius-md)' }}>
            <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
              Google Cloud Vision Web Detection returned no matching web pages, full matches, or visually similar occurrences for this item.
            </span>
          </div>
        )}
        </div>
      </details>

      {/* 5. DISCLOSURE FOR PROPAGATION GRAPH & TECHNICAL DETAILS */}
      <details className="disclosure card" style={{ padding: 'var(--space-3) var(--space-4)' }}>
        <summary style={{ fontWeight: 700, fontSize: 'var(--text-sm)' }}>
          <Icon name="arrow-right" size={13} className="disclosure__chevron" />
          Propagation Graph &amp; Technical Details ({nodes.length} nodes, {propData?.graph?.edges?.length ?? 0} edges)
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
            <div className="stack" style={{ gap: 'var(--space-2)' }}>
              <span className="label" style={{ color: 'var(--text-strong)' }}>
                PROPAGATION GRAPH
              </span>
              <PropagationGraph graph={graph} earliestEvidenceId={earliestEvidenceId} />
            </div>
          ) : null}

          {searchError ? (
            <ErrorBanner
              error={searchError}
              context="Candidate search"
              // Retry the read, not the search: the failure being retried is a
              // failed load, and retrying it must not silently run retrieval.
              onRetry={loadStoredCandidates}
            />
          ) : null}

          <div className="btn-row">
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={runCandidateSearch}
              disabled={searching}
            >
              {searching ? <Spinner label="Searching..." /> : <Icon name="search" size={13} />}
              {/* "Re-run" was wrong on every case that had never been searched,
                  which was most of them: it implied a previous run. */}
              {candidatesSearched ? 'Re-run Candidate Search' : 'Run Candidate Search'}
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

        <div className="btn-row">
          <button
            type="button"
            className="btn btn--ghost"
            style={{ padding: '8px 16px', fontSize: 'var(--text-xs)' }}
            onClick={() => onNavigate('analysis', { caseId: currentCaseId })}
          >
            ← Back to Analysis
          </button>
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
    </div>
  )
}
