/**
 * Investigation state.
 *
 * One store for the whole case, for one reason: `matches` and `verdict` are
 * POST-only on the backend, so every screen that needs them must read a cached
 * result rather than re-issuing the call. Re-POSTing on navigation would write
 * fresh audit rows for a read.
 *
 * The store holds only what the backend returned. There is no derived forensic
 * state here -- no recomputed score, no locally decided verdict.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api } from '../api'
import type {
  AnalysisResponse,
  AuditVerification,
  CaseRecord,
  Evidence,
  MetadataResponse,
  PropagationResponse,
  ReportResponse,
  UploadResponse,
} from '../api/types'
import type { UploadProgress } from '../api/http'
import { createGenerationGate, type GenerationGate } from '../lib/newcase'

export type Phase = 'idle' | 'loading' | 'ready' | 'error'

export interface Slice<T> {
  phase: Phase
  data: T | null
  error: unknown
}

const idle = <T,>(): Slice<T> => ({ phase: 'idle', data: null, error: null })

/** True once a slice has something displayable. */
export const isReady = <T,>(slice: Slice<T>): slice is Slice<T> & { data: T } =>
  slice.phase === 'ready' && slice.data !== null

export type BackendHealth = 'unknown' | 'up' | 'down'

export interface Investigation {
  // Connectivity
  health: BackendHealth
  healthError: unknown
  recheckHealth: () => void

  // Stage 1-2: intake
  caseRecord: CaseRecord | null
  evidence: Evidence[]
  selectCase: (id: string) => void
  /**
   * Phase and error of the last `selectCase` call.
   *
   * Selecting a case is a network read that can fail (case deleted, backend
   * down, permission). It used to fail silently, which left whichever screen
   * asked for the case showing the *previous* case's data under the new id --
   * the worst possible outcome for a chain-of-custody tool. The failure is now
   * state the screens can render.
   */
  caseLoad: Slice<CaseRecord>
  upload: Slice<UploadResponse>
  uploadProgress: UploadProgress | null
  /**
   * Ingest a file, either into an existing case or into a new one.
   *
   * Pass `caseId` to attach the file to a case that already exists -- that is the
   * path behind "Ingest Evidence" on a case that is already open, and it is why
   * `title`/`description` are optional here: the backend only demands them when it
   * has to open a new case, and rejects the upload with a 422 naming whichever one
   * is missing if it does.
   *
   * No examiner is passed. The name recorded against the evidence is resolved by
   * the backend from the bearer token, so it cannot be set from here -- see
   * `UploadFields` in api/client.ts.
   */
  uploadFile: (
    file: File,
    fields: {
      caseId?: string
      title?: string
      description?: string
      acquisitionContext?: string
    },
  ) => void
  /**
   * Drop the last upload result, keeping the case and its evidence list.
   *
   * This is "seal another exhibit into the same case", as distinct from `reset`,
   * which discards the case entirely. Without it the intake screen could only
   * return to its form by unloading the case it had just sealed evidence into.
   */
  clearUpload: () => void
  reset: () => void

  // Stage 3-5: analysis (verdict, signals, matches all arrive together)
  analysis: Slice<AnalysisResponse>
  /**
   * Run the pipeline for the selected case.
   *
   * Default (no argument) replays the stored result, which is what opening a
   * screen should do -- a page load must not rewrite the analysis of record.
   * `{ refresh: true }` recomputes every stage, and is what an explicit
   * "Re-Run Analysis" has to send to be true to its label.
   */
  runAnalysis: (options?: { refresh?: boolean }) => void

  // Stage 4 detail
  metadata: Slice<MetadataResponse>
  loadMetadata: () => void

  // Stage 6: propagation, refreshable independently of a full re-analysis.
  //
  // Two actions, not one, because reading and computing are different acts:
  // `loadPropagation` reconstructs from what is on record and writes nothing,
  // `traceProvenance` runs the retrieval and is recorded in the audit chain.
  propagation: Slice<PropagationResponse>
  loadPropagation: () => void
  traceProvenance: () => void

  // Stage 7: audit verification
  auditVerification: Slice<AuditVerification>
  verifyAudit: () => void

  // Stage 8: report
  report: Slice<ReportResponse>
  generateReport: (examiner?: string) => Promise<ReportResponse | null>
}

export function useInvestigation(): Investigation {
  const [health, setHealth] = useState<BackendHealth>('unknown')
  const [healthError, setHealthError] = useState<unknown>(null)

  const [caseRecord, setCaseRecord] = useState<CaseRecord | null>(null)
  const [evidence, setEvidence] = useState<Evidence[]>([])
  const [caseLoad, setCaseLoad] = useState<Slice<CaseRecord>>(idle)

  const [upload, setUpload] = useState<Slice<UploadResponse>>(idle)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)

  const [analysis, setAnalysis] = useState<Slice<AnalysisResponse>>(idle)
  const [metadata, setMetadata] = useState<Slice<MetadataResponse>>(idle)
  const [propagation, setPropagation] = useState<Slice<PropagationResponse>>(idle)
  const [auditVerification, setAuditVerification] = useState<Slice<AuditVerification>>(idle)
  const [report, setReport] = useState<Slice<ReportResponse>>(idle)

  // Guards against a state write after unmount, and lets a reset invalidate
  // responses from calls that are still in flight. The generation rule itself
  // lives in lib/newcase so it can be asserted without a React renderer.
  const mounted = useRef(true)
  const gate = useRef<GenerationGate>(createGenerationGate())
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  /** True when a response may still be written: still mounted, still this case. */
  const live = useCallback((gen: number) => mounted.current && gate.current.accepts(gen), [])

  /**
   * The case the store's actions act on.
   *
   * Note the lag: this becomes the new case only once `GET /api/cases/{id}`
   * resolves, so between a route change and that response it still names the
   * case being left. Every action below is bound to it, which is correct -- an
   * action should act on the case whose record is actually loaded -- but it
   * means a screen must not fire one of them off the route's case id alone. The
   * Provenance screen did, and stored the previous case's propagation under the
   * new case's number. Screens that read on mount either wait for
   * `caseRecord.case_id` to match the route, or pass the route's id to `api`
   * directly.
   */
  const caseId = caseRecord?.case_id ?? null

  /** Wrap a call so its result is discarded if the case was reset meanwhile. */
  const guarded = useCallback(
    <T,>(setter: (s: Slice<T>) => void, call: () => Promise<T>) => {
      const gen = gate.current.snapshot()
      setter({ phase: 'loading', data: null, error: null })
      call().then(
        (data) => {
          if (!live(gen)) return
          setter({ phase: 'ready', data, error: null })
        },
        (error) => {
          if (!live(gen)) return
          setter({ phase: 'error', data: null, error })
          // A transport failure is also a statement about connectivity.
          if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
        },
      )
    },
    [live],
  )

  const recheckHealth = useCallback(() => {
    api.health().then(
      () => {
        if (!mounted.current) return
        setHealth('up')
        setHealthError(null)
      },
      (error) => {
        if (!mounted.current) return
        setHealth('down')
        setHealthError(error)
      },
    )
  }, [])

  // Probe on mount so the operator learns the backend is down before they pick
  // a file, rather than after waiting through an upload.
  useEffect(() => {
    recheckHealth()
  }, [recheckHealth])

  const reset = useCallback(() => {
    // Invalidate first: any response already on the wire for the case being
    // left must not be able to write into the slices cleared just below.
    gate.current.invalidate()
    setCaseRecord(null)
    setEvidence([])
    setCaseLoad(idle())
    setUpload(idle())
    setUploadProgress(null)
    setAnalysis(idle())
    setMetadata(idle())
    setPropagation(idle())
    setAuditVerification(idle())
    setReport(idle())
  }, [])

  /**
   * Forget the last upload without touching the case.
   *
   * Only the upload slice and its progress: the case record, its evidence list and
   * any analysis already run belong to the case, not to the individual exhibit, so
   * sealing a second file must not clear them. The generation gate is deliberately
   * left alone -- nothing is being abandoned, so nothing in flight is stale.
   */
  const clearUpload = useCallback(() => {
    setUpload(idle())
    setUploadProgress(null)
  }, [])

  const uploadFile = useCallback<Investigation['uploadFile']>(
    (file, fields) => {
      const gen = gate.current.snapshot()
      setUpload({ phase: 'loading', data: null, error: null })
      setUploadProgress({ loaded: 0, total: file.size, fraction: 0 })

      api
        .uploadEvidence(file, fields, {
          onProgress: (p) => {
            if (live(gen)) setUploadProgress(p)
          },
        })
        .then(
          (data) => {
            if (!live(gen)) return
            setUpload({ phase: 'ready', data, error: null })
            setCaseRecord(data.case)
            // The upload response carries one evidence item; accumulate so a
            // second file in the same case does not discard the first.
            setEvidence((prev) =>
              prev.some((e) => e.evidence_id === data.evidence.evidence_id)
                ? prev
                : [...prev, data.evidence],
            )
            setHealth('up')
          },
          (error) => {
            if (!live(gen)) return
            setUpload({ phase: 'error', data: null, error })
            setUploadProgress(null)
            if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
          },
        )
    },
    [live],
  )

  const runAnalysis = useCallback(
    (options: { refresh?: boolean } = {}) => {
      if (!caseId) return
      const gen = gate.current.snapshot()
      setAnalysis({ phase: 'loading', data: null, error: null })
      api.analyse(caseId, { refresh: options.refresh }).then(
        (data) => {
          if (!live(gen)) return
          setAnalysis({ phase: 'ready', data, error: null })
          setCaseRecord(data.case)
          setEvidence(data.evidence)
          // Analysis already returns propagation; seed the slice so Screen 3 has
          // data without a second round trip.
          setPropagation({ phase: 'ready', data: data.propagation, error: null })
        },
        (error) => {
          if (!live(gen)) return
          setAnalysis({ phase: 'error', data: null, error })
          if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
        },
      )
    },
    [caseId, live],
  )

  const loadMetadata = useCallback(() => {
    if (!caseId) return
    guarded<MetadataResponse>(setMetadata, () => api.metadata(caseId))
  }, [caseId, guarded])

  /**
   * Read the case's propagation reconstruction.
   *
   * A read, explicitly: this is what the Provenance screen calls on mount, and a
   * page load must not append to the case's audit chain. Running the trace is a
   * separate, deliberate act -- see `traceProvenance`.
   */
  const loadPropagation = useCallback(() => {
    if (!caseId) return
    guarded<PropagationResponse>(setPropagation, () =>
      api.propagation(caseId, { record: false }),
    )
  }, [caseId, guarded])

  /**
   * Run the provenance trace, and record that it ran.
   *
   * The operator asking for a trace *is* an act on the evidence: near-duplicate
   * retrieval executes against the corpus. It appends `MATCH_SEARCHED` and
   * `PROPAGATION_RECONSTRUCTED`, which is correct here and was the bug on mount.
   */
  const traceProvenance = useCallback(() => {
    if (!caseId) return
    guarded<PropagationResponse>(setPropagation, () =>
      api.propagation(caseId, { refresh: true }),
    )
  }, [caseId, guarded])

  const verifyAudit = useCallback(() => {
    if (!caseId) return
    guarded<AuditVerification>(setAuditVerification, () => api.verifyAudit(caseId))
  }, [caseId, guarded])

  const generateReport = useCallback(
    async (examiner?: string): Promise<ReportResponse | null> => {
      if (!caseId) return null
      const gen = gate.current.snapshot()
      setReport({ phase: 'loading', data: null, error: null })
      try {
        const data = await api.generateReport(caseId, { examiner })
        if (!live(gen)) return null
        setReport({ phase: 'ready', data, error: null })
        setHealth('up')
        return data
      } catch (error) {
        if (!live(gen)) return null
        setReport({ phase: 'error', data: null, error })
        if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
        return null
      }
    },
    [caseId, live],
  )

  const selectCase = useCallback((id: string) => {
    // Switching cases is the same chain-of-custody hazard as starting a new
    // one: the derived slices (analysis, propagation, audit verification,
    // report) belong to the case being left, and a late response issued for
    // it can still be on the wire. Invalidate the generation first -- every
    // outstanding snapshot stops being accepted -- then clear the derived
    // slices so Case B cannot render any of Case A's forensic results.
    // The case record and evidence list are replaced below from the fetch
    // for `id`, and are cleared outright on failure.
    gate.current.invalidate()
    setAnalysis(idle())
    setMetadata(idle())
    setPropagation(idle())
    setAuditVerification(idle())
    setReport(idle())
    setUpload(idle())
    setUploadProgress(null)

    const gen = gate.current.snapshot()
    setCaseLoad({ phase: 'loading', data: null, error: null })
    api.getCase(id).then(
      (c) => {
        if (!live(gen)) return
        setCaseRecord(c)
        setCaseLoad({ phase: 'ready', data: c, error: null })
        api.listEvidence(id).then(
          (ev) => {
            if (!live(gen)) return
            setEvidence(ev.evidence)
          },
          (error) => {
            // The case loaded but its evidence list did not. Surfacing this as a
            // case-level error is the honest outcome: an empty evidence table
            // beside a real case number would read as "this case has no
            // evidence", which is a different and false statement.
            if (!live(gen)) return
            setEvidence([])
            setCaseLoad({ phase: 'error', data: null, error })
            if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
          },
        )
      },
      (error) => {
        if (!live(gen)) return
        // Clear the stale case: showing the previous case's record under a new id
        // is a chain-of-custody error, not a graceful degradation.
        setCaseRecord(null)
        setEvidence([])
        setCaseLoad({ phase: 'error', data: null, error })
        if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
      },
    )
  }, [live])

  return useMemo(
    () => ({
      health,
      healthError,
      recheckHealth,
      caseRecord,
      evidence,
      selectCase,
      caseLoad,
      upload,
      uploadProgress,
      uploadFile,
      clearUpload,
      reset,
      analysis,
      runAnalysis,
      metadata,
      loadMetadata,
      propagation,
      loadPropagation,
      traceProvenance,
      auditVerification,
      verifyAudit,
      report,
      generateReport,
    }),
    [
      health,
      healthError,
      recheckHealth,
      caseRecord,
      evidence,
      selectCase,
      caseLoad,
      upload,
      uploadProgress,
      uploadFile,
      clearUpload,
      reset,
      analysis,
      runAnalysis,
      metadata,
      loadMetadata,
      propagation,
      loadPropagation,
      traceProvenance,
      auditVerification,
      verifyAudit,
      report,
      generateReport,
    ],
  )
}
