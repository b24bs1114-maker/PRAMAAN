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
  uploadFile: (file: File, fields: { title?: string; description?: string; examiner?: string }) => void
  reset: () => void

  // Stage 3-5: analysis (verdict, signals, matches all arrive together)
  analysis: Slice<AnalysisResponse>
  runAnalysis: () => void

  // Stage 4 detail
  metadata: Slice<MetadataResponse>
  loadMetadata: () => void

  // Stage 6: propagation, refreshable independently of a full re-analysis
  propagation: Slice<PropagationResponse>
  loadPropagation: () => void

  // Stage 7: audit verification
  auditVerification: Slice<AuditVerification>
  verifyAudit: () => void

  // Stage 8: report
  report: Slice<ReportResponse>
  generateReport: (examiner?: string) => void
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
  // responses from calls that are still in flight.
  const mounted = useRef(true)
  const generation = useRef(0)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const caseId = caseRecord?.case_id ?? null

  /** Wrap a call so its result is discarded if the case was reset meanwhile. */
  const guarded = useCallback(
    <T,>(setter: (s: Slice<T>) => void, call: () => Promise<T>) => {
      const gen = generation.current
      setter({ phase: 'loading', data: null, error: null })
      call().then(
        (data) => {
          if (!mounted.current || gen !== generation.current) return
          setter({ phase: 'ready', data, error: null })
        },
        (error) => {
          if (!mounted.current || gen !== generation.current) return
          setter({ phase: 'error', data: null, error })
          // A transport failure is also a statement about connectivity.
          if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
        },
      )
    },
    [],
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
    generation.current += 1
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

  const uploadFile = useCallback<Investigation['uploadFile']>(
    (file, fields) => {
      const gen = generation.current
      setUpload({ phase: 'loading', data: null, error: null })
      setUploadProgress({ loaded: 0, total: file.size, fraction: 0 })

      api
        .uploadEvidence(file, fields, {
          onProgress: (p) => {
            if (mounted.current && gen === generation.current) setUploadProgress(p)
          },
        })
        .then(
          (data) => {
            if (!mounted.current || gen !== generation.current) return
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
            if (!mounted.current || gen !== generation.current) return
            setUpload({ phase: 'error', data: null, error })
            setUploadProgress(null)
            if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
          },
        )
    },
    [],
  )

  const runAnalysis = useCallback(() => {
    if (!caseId) return
    const gen = generation.current
    setAnalysis({ phase: 'loading', data: null, error: null })
    api.analyse(caseId).then(
      (data) => {
        if (!mounted.current || gen !== generation.current) return
        setAnalysis({ phase: 'ready', data, error: null })
        setCaseRecord(data.case)
        setEvidence(data.evidence)
        // Analysis already returns propagation; seed the slice so Screen 3 has
        // data without a second round trip.
        setPropagation({ phase: 'ready', data: data.propagation, error: null })
      },
      (error) => {
        if (!mounted.current || gen !== generation.current) return
        setAnalysis({ phase: 'error', data: null, error })
        if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
      },
    )
  }, [caseId])

  const loadMetadata = useCallback(() => {
    if (!caseId) return
    guarded<MetadataResponse>(setMetadata, () => api.metadata(caseId))
  }, [caseId, guarded])

  const loadPropagation = useCallback(() => {
    if (!caseId) return
    guarded<PropagationResponse>(setPropagation, () => api.propagation(caseId))
  }, [caseId, guarded])

  const verifyAudit = useCallback(() => {
    if (!caseId) return
    guarded<AuditVerification>(setAuditVerification, () => api.verifyAudit(caseId))
  }, [caseId, guarded])

  const generateReport = useCallback(
    (examiner?: string) => {
      if (!caseId) return
      guarded<ReportResponse>(setReport, () => api.generateReport(caseId, { examiner }))
    },
    [caseId, guarded],
  )

  const selectCase = useCallback((id: string) => {
    const gen = generation.current
    setCaseLoad({ phase: 'loading', data: null, error: null })
    api.getCase(id).then(
      (c) => {
        if (!mounted.current || gen !== generation.current) return
        setCaseRecord(c)
        setCaseLoad({ phase: 'ready', data: c, error: null })
        api.listEvidence(id).then(
          (ev) => {
            if (!mounted.current || gen !== generation.current) return
            setEvidence(ev.evidence)
          },
          (error) => {
            // The case loaded but its evidence list did not. Surfacing this as a
            // case-level error is the honest outcome: an empty evidence table
            // beside a real case number would read as "this case has no
            // evidence", which is a different and false statement.
            if (!mounted.current || gen !== generation.current) return
            setEvidence([])
            setCaseLoad({ phase: 'error', data: null, error })
            if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
          },
        )
      },
      (error) => {
        if (!mounted.current || gen !== generation.current) return
        // Clear the stale case: showing the previous case's record under a new id
        // is a chain-of-custody error, not a graceful degradation.
        setCaseRecord(null)
        setEvidence([])
        setCaseLoad({ phase: 'error', data: null, error })
        if (error instanceof ApiError && error.isBackendUnreachable) setHealth('down')
      },
    )
  }, [])

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
      reset,
      analysis,
      runAnalysis,
      metadata,
      loadMetadata,
      propagation,
      loadPropagation,
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
      reset,
      analysis,
      runAnalysis,
      metadata,
      loadMetadata,
      propagation,
      loadPropagation,
      auditVerification,
      verifyAudit,
      report,
      generateReport,
    ],
  )
}
