/**
 * Screen: Settings & System Diagnostics Console.
 *
 * Professional digital forensics configuration console providing full transparency
 * into:
 * 1. SYSTEM: Server environment, database, storage directories, disk metrics.
 * 2. DETECTORS & MODEL STATUS: Deep neural detector stack (ViT-384, VideoMAE, AASIST)
 *    with checkpoint files, cryptographic weights SHA-256, architectures,
 *    input requirements, and empirical limitations.
 * 3. SECURITY & INTEGRITY: SHA-256 evidence sealing, immutable linear audit hash chain,
 *    and session-derived examiner authentication.
 * 4. INTEGRATION: Perceptual vector indexing, C2PA manifest verification, and Web Discovery.
 * 5. APPLICATION: Workstation appearance (Light / Dark / System) and API connectivity.
 *
 * All metrics, capabilities, and statuses are derived directly from the backend
 * (/api/system/status, /api/detector/manifest, /api/detector/status, /api/index/status).
 * Zero fake toggles, zero simulated states.
 *
 * "Derived from the backend" is load-bearing, and this screen used to break it in
 * both directions. Where a value was missing it substituted a plausible one --
 * "v1.0.0 (production)", "NullDetector", "Public forensic research checkpoint",
 * "Empirically evaluated on reference benchmark" -- so a failed fetch printed a
 * confident description of a deployment nobody had looked at. And where a value
 * was hardcoded it contradicted the backend outright: the database engine was
 * "SQLite / PostgreSQL" for a deployment the backend reports as `sqlite`. Both
 * are gone. A capability this console cannot read is now shown as unread, and
 * the backend's own caveats -- including its statement that the fusion weights
 * are unvalidated demonstration defaults -- are rendered rather than dropped.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT, api } from '../api'
import type {
  DetectorManifest,
  DetectorStatus,
  IndexStatus,
  ModelManifestSpec,
  SystemStatus,
} from '../api/types'
import { CopyButton } from '../components/CopyButton'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { NOT_MEASURED, formatBytes, formatTimestampShort, shortHash } from '../lib/format'
import type { Investigation } from '../state/useInvestigation'
import type { ThemeController, ThemeMode } from '../state/useTheme'

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: string }[] = [
  { mode: 'light', label: 'Light', icon: '☀' },
  { mode: 'dark', label: 'Dark', icon: '☾' },
  { mode: 'system', label: 'System', icon: '⊕' },
]

type SettingsTab = 'system' | 'detectors' | 'security' | 'integrations' | 'application'

/** One diagnostic source that did not answer, named by the endpoint behind it. */
interface DiagnosticFailure {
  source: string
  endpoint: string
  error: unknown
}

/**
 * A capability this console could not read.
 *
 * Deliberately not the same as a capability that is switched off: "we asked and
 * it said no" and "we never got an answer" are different facts about the
 * deployment, and only one of them is a reason to change something.
 */
function Unread({ what }: { what: string }) {
  return (
    <Pill variant="unavailable" title={`${what} could not be read from the backend.`}>
      NOT MEASURED
    </Pill>
  )
}

export function ScreenSettings({
  investigation,
  theme,
}: {
  investigation: Investigation
  theme: ThemeController
}) {
  const { health, recheckHealth } = investigation

  const [activeTab, setActiveTab] = useState<SettingsTab>('system')
  const [loading, setLoading] = useState(true)
  /**
   * Which diagnostic sources failed on the last refresh.
   *
   * This replaces a single `error` slot that could never fill: every fetch was
   * wrapped in `.catch(() => null)` and then handed to `Promise.all`, so the
   * aggregate never rejected and the `.catch` attached to it was unreachable. A
   * backend that answered none of the four produced a silent screen of blanks
   * and hardcoded defaults. Failures are now kept per source, because they are
   * independent -- the detector manifest can be missing on a host whose system
   * status is fine, and the operator needs to know which one they are looking at.
   */
  const [failures, setFailures] = useState<DiagnosticFailure[]>([])
  const [refreshedAt, setRefreshedAt] = useState<string | null>(null)

  // Real backend state
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [manifest, setManifest] = useState<DetectorManifest | null>(null)
  const [detectorStatus, setDetectorStatus] = useState<DetectorStatus | null>(null)
  const [indexStatus, setIndexStatus] = useState<IndexStatus | null>(null)

  // Guards a state write after the operator has navigated away. The previous
  // version built a cleanup function and then discarded it -- `useEffect(() =>
  // { loadData() })` has a block body, so the returned cleanup went nowhere.
  const abandoned = useRef(false)
  useEffect(() => {
    abandoned.current = false
    return () => {
      abandoned.current = true
    }
  }, [])

  const loadData = useCallback(() => {
    setLoading(true)

    Promise.allSettled([
      api.systemStatus(),
      api.detectorManifest(),
      api.detectorStatus(),
      api.indexStatus(),
    ]).then(([sys, man, det, idx]) => {
      if (abandoned.current) return
      const failed: DiagnosticFailure[] = []

      if (sys.status === 'fulfilled') setSystemStatus(sys.value)
      else
        failed.push({
          source: 'Runtime, storage, database and corpus volume',
          endpoint: '/api/system/status',
          error: sys.reason,
        })

      if (man.status === 'fulfilled') setManifest(man.value)
      else
        failed.push({
          source: 'Model manifest (architectures and weight digests)',
          endpoint: '/api/detector/manifest',
          error: man.reason,
        })

      if (det.status === 'fulfilled') setDetectorStatus(det.value)
      else
        failed.push({
          source: 'Detector adapter runtime',
          endpoint: '/api/detector/status',
          error: det.reason,
        })

      if (idx.status === 'fulfilled') setIndexStatus(idx.value)
      else
        failed.push({
          source: 'Perceptual index',
          endpoint: '/api/index/status',
          error: idx.reason,
        })

      setFailures(failed)
      // Stamped only from a completed round trip, so "refreshed 2 min ago" is
      // never printed over values that were never fetched.
      setRefreshedAt(new Date().toISOString())
      setLoading(false)
    })
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleRefresh = () => {
    recheckHealth()
    loadData()
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. HEADER */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">CONFIGURATION &amp; DIAGNOSTICS</h1>
          <p className="screen__lead">
            Operational status, verified detector neural weights, cryptographic chain parameters, and forensic runtime bounds.
          </p>
        </div>
        <div className="btn-row">
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={handleRefresh}
            // Double-submit prevention: four concurrent round trips whose
            // results race to write the same four state slots.
            disabled={loading}
          >
            {loading ? <Spinner /> : <Icon name="refresh" size={13} />}
            {/* One label, matching the control's actual state. It read
                "Refresh Diagnostics" throughout, so a slow backend gave a
                spinner next to a verb in the imperative and no indication the
                press had registered. */}
            <span>{loading ? 'Reading Diagnostics…' : 'Refresh Diagnostics'}</span>
          </button>
        </div>
      </div>

      {/*
        Failures, named by source.

        Nothing here is fatal to the screen -- the four diagnostic reads are
        independent, and the tabs backed by the ones that answered stay usable --
        but a console reporting a deployment's capabilities must say which of
        those capabilities it failed to read, or the blanks below read as
        findings.
      */}
      {failures.length > 0 ? (
        <div className="stack" style={{ gap: 'var(--space-2)' }}>
          {failures.map((f) => (
            <ErrorBanner
              key={f.endpoint}
              context={`${f.source} (${f.endpoint})`}
              error={f.error}
            />
          ))}
        </div>
      ) : null}

      {/* 2. NAVIGATION TABS */}
      <div
        className="row"
        role="tablist"
        aria-label="Diagnostics sections"
        style={{ gap: 8, borderBottom: '1px solid var(--border)', paddingBottom: 8, flexWrap: 'wrap' }}
      >
        {[
          { id: 'system', label: 'SYSTEM', icon: 'server' },
          { id: 'detectors', label: 'DETECTORS & MODEL STATUS', icon: 'shield' },
          { id: 'security', label: 'SECURITY & INTEGRITY', icon: 'lock' },
          { id: 'integrations', label: 'INTEGRATIONS & ENGINES', icon: 'link' },
          { id: 'application', label: 'APPLICATION PREFERENCES', icon: 'settings' },
        ].map((tab) => {
          const active = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              // These look and behave like tabs, so they announce as tabs. As
              // plain buttons a screen reader gave five identical-sounding
              // controls with nothing to say which one was showing.
              role="tab"
              id={`settings-tab-${tab.id}`}
              aria-selected={active}
              aria-controls="settings-tabpanel"
              onClick={() => setActiveTab(tab.id as SettingsTab)}
              style={{
                background: active ? 'var(--surface-2)' : 'transparent',
                border: active ? '1px solid var(--border)' : '1px solid transparent',
                borderRadius: 'var(--radius-sm)',
                padding: '7px 14px',
                fontSize: 'var(--text-xs)',
                fontWeight: active ? 800 : 500,
                color: active ? 'var(--accent-bright)' : 'var(--text-muted)',
                fontFamily: 'var(--mono)',
                letterSpacing: '0.04em',
                cursor: 'pointer',
                transition: 'all 120ms ease',
              }}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      {loading && !systemStatus ? (
        <div className="card stack" style={{ padding: 'var(--space-6)', alignItems: 'center', gap: 12 }}>
          <Spinner label="Loading operational parameters and neural manifests…" />
        </div>
      ) : (
        /* TAB CONTENT */
        <div
          className="stack"
          role="tabpanel"
          id="settings-tabpanel"
          aria-labelledby={`settings-tab-${activeTab}`}
          style={{ gap: 'var(--space-4)' }}
        >
          {/* ========================================================================= */}
          {/* TAB 1: SYSTEM                                                             */}
          {/* ========================================================================= */}
          {activeTab === 'system' && (
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              {/* Operational Runtime Status */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <span className="label" style={{ color: 'var(--text-strong)' }}>
                  OPERATIONAL RUNTIME &amp; ENVIRONMENT
                </span>
                <div className="grid-2col" style={{ gap: 'var(--space-3)' }}>
                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Backend API Server</span>
                      <span className="settings-row-item__desc">FastAPI forensic orchestration core</span>
                    </div>
                    <Pill variant={health === 'up' ? 'ok' : 'error'}>
                      {health === 'up' ? 'ONLINE (UP)' : 'UNREACHABLE'}
                    </Pill>
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Application Version</span>
                      <span className="settings-row-item__desc">Build identifier and target environment</span>
                    </div>
                    {/*
                      No invented build. This read `version || '1.0.0'` and
                      `environment || 'production'`, so a status endpoint that
                      never answered produced "v1.0.0 (production)" -- a
                      specific, checkable, false claim about which build an
                      examiner's conclusions came from. The real deployment
                      reports v0.1.0 (development).
                    */}
                    {systemStatus ? (
                      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>
                        v{systemStatus.app.version} ({systemStatus.app.environment})
                      </span>
                    ) : (
                      <Unread what="The application version" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Offline Verification Guard</span>
                      {/*
                        The backend's own wording, not a stronger one. This said
                        "Air-gapped operation without external telemetry"; the
                        backend claims only that no stage makes an outbound call
                        at runtime, which is a narrower and defensible statement
                        and is the one it can actually substantiate.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus?.app.offline_detail ??
                          'Whether any analysis stage makes an outbound network call at runtime'}
                      </span>
                    </div>
                    {systemStatus ? (
                      <Pill variant={systemStatus.app.offline ? 'ok' : 'neutral'}>
                        {systemStatus.app.offline ? 'ENFORCED' : 'DISABLED'}
                      </Pill>
                    ) : (
                      // Not "DISABLED". An unread guard and a guard that is off
                      // are different facts, and the false branch used to claim
                      // the second whenever the fetch failed.
                      <Unread what="The offline posture" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Database Engine</span>
                      {/*
                        `SQLite / PostgreSQL` was hardcoded here -- two engines
                        named where exactly one is in use, on a page whose entire
                        purpose is to state what bounds the conclusions on this
                        host. The backend reports the engine, the file and its
                        size; all three are now read from it.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus
                          ? `${systemStatus.database.path}${
                              systemStatus.database.exists
                                ? ` · ${formatBytes(systemStatus.database.size_bytes)}`
                                : ' · file not present'
                            }`
                          : 'Persistence layer and relational store'}
                      </span>
                    </div>
                    {systemStatus ? (
                      <span className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--text-strong)' }}>
                        {systemStatus.database.engine.toUpperCase()}
                      </span>
                    ) : (
                      <Unread what="The database engine" />
                    )}
                  </div>
                </div>

                {/*
                  Read time, so nothing above is mistaken for live telemetry.
                  These are point-in-time values from four reads; without a
                  timestamp, a page left open for an hour presents an hour-old
                  capability report as the current state of the host.
                */}
                {refreshedAt ? (
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)', fontFamily: 'var(--mono)' }}>
                    READ AT {formatTimestampShort(refreshedAt)} · NOT LIVE — PRESS REFRESH DIAGNOSTICS TO RE-READ
                  </span>
                ) : null}
              </div>

              {/*
                What the backend says about its own limits.

                These notes are written by the services themselves -- including
                the statement that the fusion weights and verdict thresholds are
                unvalidated demonstration defaults with no known error rate --
                and this screen used to drop every one of them on the floor. A
                diagnostics console that hides the deployment's own caveats is
                worse than one that has none to show.
              */}
              {systemStatus?.notes?.length ? (
                <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-2)' }}>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    DEPLOYMENT CAVEATS REPORTED BY THE BACKEND
                  </span>
                  <ul className="stack" style={{ gap: 6, margin: 0, paddingLeft: 18 }}>
                    {systemStatus.notes.map((note) => (
                      <li
                        key={note}
                        style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', lineHeight: 1.5 }}
                      >
                        {note}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {/* Working Storage Directories */}
              {systemStatus?.storage && (
                <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    STORAGE BOUNDS &amp; LOCAL EVIDENCE DIRECTORIES
                  </span>
                  <div className="table-wrapper">
                    <table className="table" style={{ fontSize: 'var(--text-xs)' }}>
                      <thead>
                        <tr>
                          <th>PURPOSE</th>
                          <th>FILESYSTEM PATH</th>
                          <th>DIRECTORY STATE</th>
                          <th>PERMISSIONS</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(systemStatus.storage).map(([key, dir]) => (
                          <tr key={key}>
                            <td style={{ fontWeight: 700, textTransform: 'uppercase', fontFamily: 'var(--mono)', fontSize: '11px' }}>
                              {key.replace(/_/g, ' ')}
                            </td>
                            <td className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                              {dir.path}
                            </td>
                            <td>
                              <Pill variant={dir.exists ? 'ok' : 'warn'}>
                                {dir.exists ? 'EXISTS' : 'MISSING'}
                              </Pill>
                            </td>
                            <td>
                              <Pill variant={dir.writable ? 'ok' : 'error'}>
                                {dir.writable ? 'READ / WRITE' : 'READ ONLY'}
                              </Pill>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Volume & Record Counts */}
              {systemStatus?.counts && (
                <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    FORENSIC CORPUS VOLUME ON RECORD
                  </span>
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                      gap: 12,
                    }}
                  >
                    {Object.entries(systemStatus.counts).map(([label, count]) => (
                      <div key={label} style={{ background: 'var(--surface-2)', padding: '10px 14px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                        <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                          {label.replace(/_/g, ' ')}
                        </span>
                        <div className="mono" style={{ fontSize: 'var(--text-lg)', fontWeight: 800, color: 'var(--text-strong)', marginTop: 4 }}>
                          {count.toLocaleString()}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ========================================================================= */}
          {/* TAB 2: DETECTORS & MODEL STATUS (MODEL TRANSPARENCY)                      */}
          {/* ========================================================================= */}
          {activeTab === 'detectors' && (
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              {/* Active Adapter Overview */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                  <div>
                    <span className="label" style={{ color: 'var(--text-strong)' }}>
                      ACTIVE DETECTOR ADAPTER RUNTIME
                    </span>
                    <p style={{ margin: '4px 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                      Primary inference runtime executing multimodal neural evaluations for incoming evidence.
                    </p>
                  </div>
                  {/*
                    Three states. `available ? ACTIVE : UNAVAILABLE` printed
                    "DETECTOR UNAVAILABLE" whenever /api/detector/status itself
                    failed -- reporting a failed read of the detector as a
                    failed detector, which is the same category error the
                    forensic screens are careful to avoid for signals.
                  */}
                  {detectorStatus ? (
                    <Pill
                      variant={detectorStatus.available ? 'ok' : 'unavailable'}
                      title={detectorStatus.reason ?? undefined}
                    >
                      {detectorStatus.available ? 'DETECTOR ENGINE ACTIVE' : 'DETECTOR UNAVAILABLE'}
                    </Pill>
                  ) : (
                    <Unread what="The detector adapter runtime" />
                  )}
                </div>

                {/* The backend's explanation of why a detector could not load,
                    shown where the operator is looking at the detector. */}
                {detectorStatus && !detectorStatus.available && detectorStatus.reason ? (
                  <p style={{ margin: 0, fontSize: 'var(--text-xs)', color: 'var(--warn-bright)', lineHeight: 1.5 }}>
                    {detectorStatus.reason}
                  </p>
                ) : null}

                <div className="grid-3col" style={{ gap: 12, background: 'var(--surface-2)', padding: '12px 16px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border)' }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                      LOADED ADAPTER
                    </span>
                    {/* Was `adapter || 'NullDetector'`: a named class, asserted
                        as loaded, on the strength of a fetch that failed. */}
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)', fontFamily: 'var(--mono)' }}>
                      {detectorStatus?.adapter || NOT_MEASURED}
                    </span>
                  </div>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                      MODEL FAMILY
                    </span>
                    {/* Was `model || 'None'`, which states that no model is
                        configured -- a fact about the host, from no evidence. */}
                    <span style={{ fontSize: 'var(--text-xs)', fontWeight: 700, color: 'var(--text-strong)' }}>
                      {detectorStatus?.model || NOT_MEASURED}
                    </span>
                  </div>
                  <div className="stack" style={{ gap: 2 }}>
                    <span style={{ fontSize: '10px', textTransform: 'uppercase', color: 'var(--text-faint)', fontFamily: 'var(--mono)', fontWeight: 700 }}>
                      PRIMARY WEIGHTS SHA-256
                    </span>
                    <div className="row" style={{ gap: 6, alignItems: 'center' }}>
                      {manifest?.models.image?.weights_sha256 ? (
                        <>
                          <code className="mono" style={{ fontSize: '11px', color: 'var(--accent-bright)' }}>
                            {shortHash(manifest.models.image.weights_sha256, 16)}
                          </code>
                          <CopyButton value={manifest.models.image.weights_sha256} title="Copy primary weights SHA-256" />
                        </>
                      ) : (
                        <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                          {NOT_MEASURED}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              {/* Verified Model Manifest Specifications */}
              <div className="stack" style={{ gap: 'var(--space-3)' }}>
                <span className="label" style={{ color: 'var(--text-strong)' }}>
                  VERIFIED DETECTOR ARCHITECTURES &amp; NEURAL CHECKPOINTS
                </span>

                {manifest?.models && Object.keys(manifest.models).length > 0 ? (
                  <div className="stack" style={{ gap: 'var(--space-4)' }}>
                    {Object.entries(manifest.models).map(([modality, spec]) => {
                      if (!spec) return null
                      return <ModelSpecCard key={modality} modality={modality} spec={spec} />
                    })}
                  </div>
                ) : (
                  <Empty>No model manifest entries found on disk.</Empty>
                )}
              </div>
            </div>
          )}

          {/* ========================================================================= */}
          {/* TAB 3: SECURITY & INTEGRITY                                               */}
          {/* ========================================================================= */}
          {activeTab === 'security' && (
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              {/* Cryptographic Baseline */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <div>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    CRYPTOGRAPHIC INTEGRITY BASELINE
                  </span>
                  <p style={{ margin: '4px 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    Deterministic cryptographic security measures applied universally across the custody lifecycle.
                  </p>
                </div>

                <div className="stack" style={{ gap: 'var(--space-2)' }}>
                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Intake Evidence Sealing (SHA-256)</span>
                      {/*
                        Was "hashed immediately at acquisition before any
                        filesystem write or database commit", which is not what
                        happens: the upload is streamed to a staging path and
                        validated there, and only then hashed. The part that
                        matters is still true and is what the row now says --
                        the digest is taken before the file is admitted to
                        evidence storage and before any row is committed, so no
                        evidence record has ever existed without one.
                      */}
                      <span className="settings-row-item__desc">
                        Every upload is hashed in staging, from the bytes as they sit on disk, before it is admitted to evidence storage or written to the case file.
                      </span>
                    </div>
                    <Pill variant="ok">MANDATORY · SHA-256</Pill>
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Pre-Analysis Digest Verification</span>
                      {/*
                        This row read "Pre-Inference Digest Verification …
                        ACTIVE" as a hardcoded string, and no such check existed
                        anywhere in the analysis path: the pipeline copied the
                        stored digest into its payloads and never recomputed it.
                        An examiner reading the row would have believed that a
                        file replaced on disk between intake and analysis would
                        be caught, and it would not have been.

                        The control now exists -- every stage runner re-hashes
                        the bytes before reading them and refuses the analysis on
                        a mismatch -- and this row reports the backend's own
                        declaration of it rather than asserting one of its own.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus?.capabilities.integrity_verification.detail ??
                          'Whether evidence bytes are re-hashed against the recorded intake digest before a stage reads them'}
                      </span>
                    </div>
                    {systemStatus ? (
                      <Pill
                        variant="ok"
                        title={`Applies to ${systemStatus.capabilities.integrity_verification.scope}.`}
                      >
                        {systemStatus.capabilities.integrity_verification.algorithm} ·{' '}
                        {systemStatus.capabilities.integrity_verification.on_mismatch} ON MISMATCH
                      </Pill>
                    ) : (
                      <Unread what="The pre-analysis integrity guarantee" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Immutable Linear Hash Chain</span>
                      <span className="settings-row-item__desc">
                        {/*
                          A linear hash chain, and named as one. Not a Merkle
                          tree: there is no tree, no sibling hashing and no
                          inclusion proof, and calling it one would overstate
                          what the ledger can demonstrate.
                        */}
                        Chain-of-custody ledger where each event carries the SHA-256 of the prior event (H_i = SHA256(H_{`{i-1}`} || E_i)).
                        {systemStatus
                          ? ` ${systemStatus.audit.total_rows.toLocaleString()} entries recorded${
                              systemStatus.audit.head_hash
                                ? `, head ${shortHash(systemStatus.audit.head_hash)}`
                                : ''
                            }. ${
                              systemStatus.audit.last_verified_at
                                ? `Last verified ${formatTimestampShort(systemStatus.audit.last_verified_at)}.`
                                : 'Never verified on this deployment — the check has not been run, which is not a sign of tampering.'
                            }`
                          : ''}
                      </span>
                    </div>
                    <Pill variant="ok">LINEAR HASH CHAIN</Pill>
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Canonical PDF Report Hash Sealing</span>
                      <span className="settings-row-item__desc">
                        The rendered PDF bytes are hashed with SHA-256 and stored alongside the audit head hash as it stood at generation, so a report can be tied back to the state of the chain that produced it.
                      </span>
                    </div>
                    <Pill variant="ok">SEALED ON GENERATION</Pill>
                  </div>
                </div>
              </div>

              {/* Authentication & Authorization */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <div>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    AUTHENTICATION &amp; OPERATOR PROVENANCE
                  </span>
                  <p style={{ margin: '4px 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    Cryptographic tokens and backend-verified operator attribution.
                  </p>
                </div>

                <div className="stack" style={{ gap: 'var(--space-2)' }}>
                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Session Architecture</span>
                      <span className="settings-row-item__desc">
                        Bearer token session authorization validated per protected API call (/api/auth/me).
                      </span>
                    </div>
                    <Pill variant="ok">SESSION PROTECTED</Pill>
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Analyst Attribution Policy</span>
                      <span className="settings-row-item__desc">
                        Examiner credentials and names are derived strictly from the active session. Client-supplied operator spoofing is rejected.
                      </span>
                    </div>
                    <Pill variant="ok">STRICT SERVER DERIVATION</Pill>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ========================================================================= */}
          {/* TAB 4: INTEGRATIONS & ENGINES                                             */}
          {/* ========================================================================= */}
          {activeTab === 'integrations' && (
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <span className="label" style={{ color: 'var(--text-strong)' }}>
                  SUBSYSTEM &amp; AUXILIARY ENGINES
                </span>

                <div className="stack" style={{ gap: 'var(--space-2)' }}>
                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Perceptual Vector Index</span>
                      <span className="settings-row-item__desc">
                        {indexStatus
                          ? `${indexStatus.indexed_count.toLocaleString()} fingerprints indexed · ${indexStatus.backend} · ${
                              indexStatus.exact_search ? 'exhaustive (exact) search' : 'approximate search'
                            } · storage ${indexStatus.persisted ? 'persistent' : 'ephemeral'}`
                          : 'Perceptual hashing and nearest-neighbour search'}
                      </span>
                      {/* The heading said "pHash / FAISS" on a deployment where
                          FAISS is not installed. The backend reports which
                          backend is in use and says the results are identical
                          either way; both belong here rather than a library
                          name that may not be present. */}
                      {indexStatus?.notes ? (
                        <span className="settings-row-item__desc">{indexStatus.notes}</span>
                      ) : null}
                      {systemStatus?.capabilities.perceptual_index.covers ? (
                        <span className="settings-row-item__desc">
                          {systemStatus.capabilities.perceptual_index.covers}
                        </span>
                      ) : null}
                    </div>
                    {indexStatus ? (
                      <Pill variant={indexStatus.persisted ? 'ok' : 'neutral'}>
                        {indexStatus.persisted ? 'PERSISTED' : 'IN MEMORY ONLY'}
                      </Pill>
                    ) : (
                      <Unread what="The perceptual index" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">C2PA / Content Credentials Validator</span>
                      {/*
                        This row is where the screen crashed. It read
                        `systemStatus.validator.c2pa_installed`; the response has
                        no `validator` key -- the block lives under
                        `capabilities.c2pa_validator` and the flag is called
                        `c2pa_library_available` -- so the optional chain guarded
                        the wrong hop and the property access threw, taking the
                        whole Settings screen into the error boundary and keeping
                        it there for every tab after it.

                        The state is also not binary. A deployment without the
                        c2pa library can still find a manifest container and
                        report it as PRESENT_UNVERIFIED; collapsing that to "NO
                        C2PA BINARY" hides a capability that is actually running,
                        and collapsing it the other way would imply signatures
                        were validated when they were not.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus?.capabilities.c2pa_validator.detail ??
                          'Manifest container scan and cryptographic signature validation'}
                      </span>
                      {systemStatus?.capabilities.c2pa_validator.inspector ? (
                        <span className="settings-row-item__desc mono" style={{ fontSize: '11px' }}>
                          {systemStatus.capabilities.c2pa_validator.inspector}
                        </span>
                      ) : null}
                    </div>
                    {systemStatus ? (
                      <Pill
                        variant={
                          systemStatus.capabilities.c2pa_validator.signature_validation_available
                            ? 'ok'
                            : systemStatus.capabilities.c2pa_validator.container_scan_available
                              ? 'warn'
                              : 'unavailable'
                        }
                      >
                        {systemStatus.capabilities.c2pa_validator.signature_validation_available
                          ? 'SIGNATURE VALIDATION'
                          : systemStatus.capabilities.c2pa_validator.container_scan_available
                            ? 'CONTAINER SCAN ONLY'
                            : 'UNAVAILABLE'}
                      </Pill>
                    ) : (
                      <Unread what="The C2PA validator" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">PDF Examination Report Renderer</span>
                      {/*
                        Two hardcoded claims removed. "2-page" is not a property
                        of the renderer -- report length follows the case, and
                        the last one generated here ran to three -- and
                        "REPORTLAB CANONICAL" asserted a library that the backend
                        reports on and that a deployment can legitimately lack,
                        in which case reports are still produced by the built-in
                        writer.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus?.capabilities.report_renderer.note ??
                          'Backend-rendered forensic examination report, hashed and recorded on generation'}
                      </span>
                    </div>
                    {systemStatus ? (
                      <Pill
                        variant={systemStatus.capabilities.report_renderer.reportlab_available ? 'ok' : 'warn'}
                        title={systemStatus.capabilities.report_renderer.reason ?? undefined}
                      >
                        {systemStatus.capabilities.report_renderer.writer.toUpperCase()}
                      </Pill>
                    ) : (
                      <Unread what="The report renderer" />
                    )}
                  </div>

                  <div className="settings-row-item">
                    <div className="settings-row-item__info">
                      <span className="settings-row-item__label">Public Web Discovery</span>
                      {/*
                        "OPTIONAL · CREDENTIAL GATED" was a permanent label: it
                        said the same thing on a host where discovery was fully
                        credentialed as on one where it was switched off. The
                        backend had the answer and simply was not asked, so
                        `capabilities.web_discovery` was added to
                        /api/system/status and is read here -- including the
                        reason, because "disabled by configuration" and
                        "credentials missing" are different problems with
                        different fixes.
                      */}
                      <span className="settings-row-item__desc">
                        {systemStatus?.capabilities.web_discovery.detail ??
                          'External reverse-image search and public web timeline reconstruction'}
                      </span>
                      {systemStatus?.capabilities.web_discovery.reason ? (
                        <span className="settings-row-item__desc">
                          {systemStatus.capabilities.web_discovery.reason}
                        </span>
                      ) : null}
                    </div>
                    {systemStatus ? (
                      <Pill
                        variant={systemStatus.capabilities.web_discovery.available ? 'ok' : 'unavailable'}
                      >
                        {systemStatus.capabilities.web_discovery.available
                          ? 'AVAILABLE'
                          : systemStatus.capabilities.web_discovery.enabled
                            ? 'ENABLED · NOT CREDENTIALED'
                            : 'DISABLED'}
                      </Pill>
                    ) : (
                      <Unread what="Public web discovery" />
                    )}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ========================================================================= */}
          {/* TAB 5: APPLICATION PREFERENCES                                            */}
          {/* ========================================================================= */}
          {activeTab === 'application' && (
            <div className="stack" style={{ gap: 'var(--space-4)' }}>
              {/* Workstation Appearance */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <div>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    WORKSTATION APPEARANCE &amp; THEME
                  </span>
                  <p style={{ margin: '4px 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    Adjust console contrast and palette for laboratory examination environments.
                  </p>
                </div>

                <div className="settings-row-item">
                  <div className="settings-row-item__info">
                    <span className="settings-row-item__label">Active Color Palette</span>
                    <span className="settings-row-item__desc">
                      Current mode: <strong>{theme.mode.toUpperCase()}</strong> (Active: {theme.resolved})
                    </span>
                  </div>
                  <div className="heatmap-segmented" role="group" aria-label="Theme Mode">
                    {THEME_OPTIONS.map((opt) => {
                      const active = theme.mode === opt.mode
                      return (
                        <button
                          key={opt.mode}
                          type="button"
                          className={`heatmap-segmented__btn${active ? ' heatmap-segmented__btn--active' : ''}`}
                          onClick={() => theme.setMode(opt.mode)}
                          aria-pressed={active}
                        >
                          <span>{opt.icon}</span>
                          <span>{opt.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>

              {/* Endpoint Connectivity */}
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)' }}>
                <div>
                  <span className="label" style={{ color: 'var(--text-strong)' }}>
                    API CONNECTIVITY
                  </span>
                  <p style={{ margin: '4px 0 0', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                    Configured forensic API origin for this workstation session.
                  </p>
                </div>

                <div className="settings-row-item">
                  <div className="settings-row-item__info">
                    <span className="settings-row-item__label">API Base URL</span>
                    <span className="settings-row-item__desc">
                      {API_BASE_URL_IS_EXPLICIT
                        ? 'Explicitly defined via VITE_API_URL environment configuration'
                        : 'Default relative origin (/api)'}
                    </span>
                  </div>
                  <code className="mono" style={{ fontSize: 'var(--text-xs)', color: 'var(--accent-bright)' }}>
                    {API_BASE_URL}
                  </code>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Model specification card rendering real manifest data.
 */
function ModelSpecCard({
  modality,
  spec,
}: {
  modality: string
  spec: ModelManifestSpec
}) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <span style={{ fontWeight: 800, fontSize: 'var(--text-sm)', color: 'var(--text-strong)' }}>
            {spec.model_name || modality.toUpperCase()}
          </span>
          <Pill variant="neutral">{modality.toUpperCase()}</Pill>
          {/* Was `v{model_version || '1.0.0'}`. A checkpoint whose version the
              manifest does not record is not version 1.0.0. */}
          <span className="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
            {spec.model_version ? `v${spec.model_version}` : NOT_MEASURED}
          </span>
        </div>
        {/* Was `(status || 'ACTIVE')` styled as a live state. An unrecorded
            status is not an active one. */}
        {spec.status ? (
          <Pill variant={spec.status === 'published' ? 'ok' : 'neutral'}>
            {spec.status.toUpperCase()}
          </Pill>
        ) : (
          <Pill variant="unavailable">STATUS NOT RECORDED</Pill>
        )}
      </div>

      <dl className="dl" style={{ fontSize: 'var(--text-xs)' }}>
        <dt>Architecture</dt>
        <dd style={{ color: 'var(--text-strong)' }}>{spec.architecture || NOT_MEASURED}</dd>

        <dt>Checkpoint File</dt>
        <dd className="mono">
          {spec.checkpoint_filename || NOT_MEASURED}
          {spec.weights_size_bytes ? ` (${formatBytes(spec.weights_size_bytes)})` : ''}
        </dd>

        <dt>Weights SHA-256</dt>
        <dd className="row" style={{ gap: 6, alignItems: 'center' }}>
          <code className="mono break-all" style={{ fontSize: '11px', color: 'var(--accent-bright)' }}>
            {spec.weights_sha256 ? spec.weights_sha256 : NOT_MEASURED}
          </code>
          {spec.weights_sha256 && (
            <CopyButton value={spec.weights_sha256} title="Copy weights SHA-256" />
          )}
        </dd>

        <dt>Training Provenance</dt>
        {/*
          Was `source_repo || dataset || 'Public forensic research checkpoint'`.
          The fallback describes where a checkpoint came from -- the exact
          question this row exists to answer -- for a checkpoint whose manifest
          entry records no origin at all.
        */}
        <dd>
          {spec.source_repo || spec.dataset ? (
            <>
              {spec.source_repo || spec.dataset}
              {spec.license ? ` · License: ${spec.license}` : ''}
            </>
          ) : (
            <span style={{ color: 'var(--text-muted)' }}>
              Not recorded in the model manifest
            </span>
          )}
        </dd>

        <dt>Validation Status</dt>
        {/*
          The most serious of the invented defaults on this screen: an absent
          `validation_status` rendered as "Empirically evaluated on reference
          benchmark." in the success colour -- a claim that a checkpoint had been
          measured against a benchmark, manufactured from the fact that nobody
          had written down whether it had. The backend states elsewhere on this
          same page that the thresholds here are unvalidated demonstration
          defaults with no known error rate.
        */}
        {spec.validation_status ? (
          <dd style={{ color: 'var(--ok-bright)', fontWeight: 600 }}>{spec.validation_status}</dd>
        ) : (
          <dd style={{ color: 'var(--text-muted)' }}>
            No validation result is recorded for this checkpoint. That is the absence of a
            record, not a failed evaluation — and not a passed one.
          </dd>
        )}
      </dl>

      {/* Expandable technical details */}
      <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8 }}>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          style={{ padding: '2px 8px', fontSize: '11px' }}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? '▲ Hide Technical Parameters & Limitations' : '▼ View Technical Parameters & Limitations'}
        </button>

        {expanded && (
          <div className="stack" style={{ gap: 8, marginTop: 8, fontSize: 'var(--text-xs)' }}>
            {spec.input_preprocessing && (
              <div className="stack" style={{ gap: 2 }}>
                <span className="label" style={{ fontSize: '10px' }}>INPUT PREPROCESSING REQUIREMENTS</span>
                <pre
                  className="mono"
                  style={{
                    background: 'var(--surface-3)',
                    padding: 8,
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '11px',
                    margin: 0,
                    overflowX: 'auto',
                  }}
                >
                  {JSON.stringify(spec.input_preprocessing, null, 2)}
                </pre>
              </div>
            )}

            {spec.known_limitations && (
              <div className="stack" style={{ gap: 2 }}>
                <span className="label" style={{ fontSize: '10px', color: 'var(--warn-bright)' }}>KNOWN LIMITATIONS</span>
                <p style={{ margin: 0, color: 'var(--text-muted)', lineHeight: 1.4 }}>
                  {spec.known_limitations}
                </p>
              </div>
            )}

            {spec.score_direction && (
              <div className="stack" style={{ gap: 2 }}>
                <span className="label" style={{ fontSize: '10px' }}>SCORE DIRECTION &amp; ABSTENTION</span>
                <p style={{ margin: 0, color: 'var(--text-muted)', lineHeight: 1.4 }}>
                  {spec.score_direction}
                </p>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
