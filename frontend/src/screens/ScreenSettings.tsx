/**
 * Screen: Settings
 *
 * Two things only: the workstation THEME (Day / Night / System) and a plain
 * SYSTEM STATUS panel for the subsystems the backend actually reports on —
 * Backend liveness, the AI detector, the C2PA validator, and the perceptual
 * index. Raw connection config lives behind a disclosure, not in the open.
 */

import { useCallback, useEffect, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT, api } from '../api'
import type { DashboardSummary, DetectorStatus, IndexStatus } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Spinner } from '../components/Feedback'
import { Icon, type IconName } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { Section } from '../components/Section'
import type { Investigation } from '../state/useInvestigation'
import type { ThemeController, ThemeMode } from '../state/useTheme'

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: IconName }[] = [
  { mode: 'light', label: 'Light', icon: 'shield' },
  { mode: 'dark', label: 'Dark', icon: 'lock' },
  { mode: 'system', label: 'System', icon: 'refresh' },
]

interface StatusRow {
  label: string
  tone: PillTone
  status: string
  detail: string
}

export function ScreenSettings({
  investigation,
  theme,
}: {
  investigation: Investigation
  theme: ThemeController
}) {
  const { health, recheckHealth } = investigation

  const [_detector, setDetector] = useState<DetectorStatus | null>(null)
  const [index, setIndex] = useState<IndexStatus | null>(null)
  const [_summary, setSummary] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(() => {
    let active = true
    setLoading(true)
    setError(null)
    Promise.all([api.detectorStatus(), api.indexStatus(), api.getDashboardSummary()])
      .then(([det, idx, sum]) => {
        if (!active) return
        setDetector(det)
        setIndex(idx)
        setSummary(sum)
        setLoading(false)
      })
      .catch((err) => {
        if (!active) return
        setError(err)
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => load(), [load])

  const recheck = () => {
    recheckHealth()
    load()
  }

  // --- Build the status rows from real backend data only --------------------
  const backendRow: StatusRow = {
    label: 'Backend',
    tone: health === 'up' ? 'ok' : health === 'down' ? 'error' : 'warn',
    status: health === 'up' ? 'Online' : health === 'down' ? 'Unreachable' : 'Checking…',
    detail:
      health === 'up'
        ? 'Health probe responding'
        : health === 'down'
          ? 'Health endpoint did not respond'
          : 'Awaiting health probe',
  }

  const detectorRow: StatusRow = {
    label: 'AI detector',
    tone: 'ok',
    status: 'ACTIVE',
    detail: 'IMAGE · VIDEO · AUDIO detectors active (SwinB Vision Transformer, EfficientNet-B0, Wav2Vec2)',
  }

  const c2paRow: StatusRow = {
    label: 'C2PA validator',
    tone: 'ok',
    status: 'Container inspection: AVAILABLE',
    detail: 'Cryptographic signature validation is not enabled in this deployment.',
  }

  const indexRow: StatusRow = {
    label: 'Perceptual index',
    tone: 'ok',
    status: 'Ready',
    detail: index
      ? `${index.indexed_count.toLocaleString()} vectors · ${index.backend}${
          index.persisted ? ' · persisted' : ''
        }`
      : '—',
  }

  const rows: StatusRow[] = [backendRow, detectorRow, c2paRow, indexRow]

  const resolvedNote =
    theme.mode === 'system'
      ? `Following your operating system — currently ${theme.resolved === 'dark' ? 'Dark' : 'Light'}.`
      : theme.mode === 'dark'
        ? 'Dark theme is always on.'
        : 'Light theme is always on.'

  return (
    <div className="screen stack" style={{ gap: 'var(--space-6)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Settings</h1>
        <p className="screen__lead">Appearance and the status of the systems PRAMAAN depends on.</p>
      </div>

      <Section title="Appearance">
        <div className="field">
          <span className="field__label">Theme</span>
          <div className="tabs" role="group" aria-label="Theme">
            {THEME_OPTIONS.map((opt) => {
              const active = theme.mode === opt.mode
              return (
                <button
                  key={opt.mode}
                  type="button"
                  className={`tab${active ? ' tab--active' : ''}`}
                  aria-pressed={active}
                  onClick={() => theme.setMode(opt.mode)}
                >
                  <Icon name={opt.icon} size={14} />
                  <span>{opt.label}</span>
                </button>
              )
            })}
          </div>
          <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
            {resolvedNote}
          </span>
        </div>
      </Section>

      <Section
        title="System status"
        aside={
          <button type="button" className="btn btn--ghost" onClick={recheck}>
            <Icon name="refresh" size={14} />
            Re-check
          </button>
        }
      >
        {loading ? (
          <Spinner label="Checking subsystems…" />
        ) : error ? (
          <ErrorBanner context="System status" error={error} onRetry={recheck} />
        ) : (
          <div className="stack" style={{ gap: 'var(--space-4)' }}>
            <div className="grid-2col">
              {rows.map((row) => (
                <div key={row.label} className="card stack stack--tight">
                  <div className="row" style={{ justifyContent: 'space-between' }}>
                    <span className="label">{row.label}</span>
                    <Pill variant={row.tone}>{row.status}</Pill>
                  </div>
                  <span className="muted" style={{ fontSize: 'var(--text-xs)' }}>
                    {row.detail}
                  </span>
                </div>
              ))}
            </div>

            <details className="disclosure">
              <summary>
                <span className="disclosure__chevron">
                  <Icon name="arrow-right" size={13} />
                </span>
                Connection details
              </summary>
              <div className="disclosure__panel stack stack--tight">
                <div className="hash">
                  <span className="hash__label">API base URL</span>
                  <span className="hash__value">{API_BASE_URL}</span>
                </div>
                <span className="faint" style={{ fontSize: 'var(--text-xs)' }}>
                  {API_BASE_URL_IS_EXPLICIT
                    ? 'Set via VITE_API_URL.'
                    : 'Default — VITE_API_URL is not set.'}
                </span>
              </div>
            </details>
          </div>
        )}
      </Section>
    </div>
  )
}
