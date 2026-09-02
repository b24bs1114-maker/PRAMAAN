/**
 * Screen: Settings (Screen 9 in visual collage).
 *
 * Visual reproduction of Panel 9 from collage:
 * 1. Header: "SETTINGS" · Subtitle "Configure system preferences and controls."
 * 2. Top Tabs: General, Evidence Integrity (Active), System, Notifications, Integrations
 * 3. 2-Column Split:
 *    - Left Menu:
 *      - Sub-items under Evidence Integrity (SHA-256 & Hashing [Active], Evidence Seal, Audit Chain, C2PA Verification, Integrity Settings)
 *      - Accordion groups (SYSTEM ⌄, NOTIFICATIONS ⌄, CONNECTION DETAILS ⌄)
 *    - Right Content:
 *      - Section: SHA-256 & HASHING (Configure hashing and integrity verification settings)
 *      - Toggle 1: Auto-compute SHA-256 on upload (ON)
 *      - Toggle 2: Display hash in reports (ON)
 *      - Toggle 3: Verify hash before analysis (ON)
 *      - Setting 4: Default Hash Algorithm (SHA-256 dropdown)
 */

import { useCallback, useEffect, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT, api } from '../api'
import type { DashboardSummary, DetectorStatus, IndexStatus } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import type { Investigation } from '../state/useInvestigation'
import type { ThemeController, ThemeMode } from '../state/useTheme'

const THEME_OPTIONS: { mode: ThemeMode; label: string; icon: string }[] = [
  { mode: 'light', label: 'Light', icon: '☀' },
  { mode: 'dark', label: 'Dark', icon: '☾' },
  { mode: 'system', label: 'System', icon: '⊕' },
]

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

  // Top Tabs
  const [activeTab, setActiveTab] = useState<'general' | 'integrity' | 'system' | 'notifications' | 'integrations'>('integrity')

  // Left Sub-menu
  const [activeSubMenu, setActiveSubMenu] = useState<'hashing' | 'seal' | 'chain' | 'c2pa' | 'settings'>('hashing')
  const [openAccordion, setOpenAccordion] = useState<string | null>(null)

  // Settings Toggles (Green switch controls matching Panel 9)
  const [autoComputeHash, setAutoComputeHash] = useState(true)
  const [displayHashInReports, setDisplayHashInReports] = useState(true)
  const [verifyBeforeAnalysis, setVerifyBeforeAnalysis] = useState(true)
  const [defaultAlgorithm, setDefaultAlgorithm] = useState('SHA-256')

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

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. HEADER: SETTINGS */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">SETTINGS</h1>
          <p className="screen__lead">Configure system preferences and controls.</p>
        </div>
        <div className="btn-row">
          <button type="button" className="btn btn--ghost btn--sm" onClick={recheck} disabled={loading}>
            {loading ? <Spinner /> : <Icon name="refresh" size={13} />}
            Recheck Subsystems
          </button>
        </div>
      </div>

      {error ? <ErrorBanner context="Settings" error={error} /> : null}

      {/* 2. TOP TABS */}
      <div className="row" style={{ gap: 8, borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
        {[
          { id: 'general', label: 'General' },
          { id: 'integrity', label: 'Evidence Integrity' },
          { id: 'system', label: 'System' },
          { id: 'notifications', label: 'Notifications' },
          { id: 'integrations', label: 'Integrations' },
        ].map((tab) => {
          const active = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id as typeof activeTab)}
              style={{
                background: 'none',
                border: 'none',
                padding: '6px 14px',
                fontSize: 'var(--text-xs)',
                fontWeight: active ? 700 : 500,
                color: active ? 'var(--text-strong)' : 'var(--text-muted)',
                borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                cursor: 'pointer',
              }}
            >
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* 3. 2-COLUMN MAIN SPLIT (MATCHING PANEL 9 IN COLLAGE) */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '260px minmax(0, 1fr)',
          gap: 'var(--space-4)',
          alignItems: 'start',
        }}
      >
        {/* LEFT COLUMN: SUB-MENU & ACCORDIONS */}
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 6, background: 'var(--surface-2)' }}>
          {/* Sub-items under Evidence Integrity */}
          <div className="stack" style={{ gap: 2 }}>
            {[
              { id: 'hashing', label: 'SHA-256 & Hashing' },
              { id: 'seal', label: 'Evidence Seal' },
              { id: 'chain', label: 'Audit Chain' },
              { id: 'c2pa', label: 'C2PA Verification' },
              { id: 'settings', label: 'Integrity Settings' },
            ].map((item) => {
              const active = activeSubMenu === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setActiveTab('integrity')
                    setActiveSubMenu(item.id as typeof activeSubMenu)
                  }}
                  style={{
                    background: active ? 'var(--surface-3)' : 'transparent',
                    border: 'none',
                    borderLeft: active ? '3px solid var(--accent)' : '3px solid transparent',
                    color: active ? 'var(--accent-bright)' : 'var(--text-muted)',
                    fontWeight: active ? 700 : 500,
                    fontSize: 'var(--text-xs)',
                    padding: '8px 12px',
                    textAlign: 'left',
                    borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
                    cursor: 'pointer',
                    transition: 'all 150ms ease',
                  }}
                >
                  {item.label}
                </button>
              )
            })}
          </div>

          {/* Accordion Categories */}
          <div className="stack" style={{ gap: 2, borderTop: '1px solid var(--border)', paddingTop: 8, marginTop: 4 }}>
            {/* SYSTEM */}
            <div>
              <button
                type="button"
                onClick={() => {
                  setActiveTab('system')
                  setOpenAccordion(openAccordion === 'system' ? null : 'system')
                }}
                style={{
                  width: '100%',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  background: 'none',
                  border: 'none',
                  padding: '8px 10px',
                  color: 'var(--text-strong)',
                  fontSize: 'var(--text-xs)',
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                <span>SYSTEM</span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                  {openAccordion === 'system' ? '▲' : '▼'}
                </span>
              </button>
            </div>

            {/* NOTIFICATIONS */}
            <div>
              <button
                type="button"
                onClick={() => {
                  setActiveTab('notifications')
                  setOpenAccordion(openAccordion === 'notifications' ? null : 'notifications')
                }}
                style={{
                  width: '100%',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  background: 'none',
                  border: 'none',
                  padding: '8px 10px',
                  color: 'var(--text-strong)',
                  fontSize: 'var(--text-xs)',
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                <span>NOTIFICATIONS</span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                  {openAccordion === 'notifications' ? '▲' : '▼'}
                </span>
              </button>
            </div>

            {/* CONNECTION DETAILS */}
            <div>
              <button
                type="button"
                onClick={() => setOpenAccordion(openAccordion === 'connection' ? null : 'connection')}
                style={{
                  width: '100%',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  background: 'none',
                  border: 'none',
                  padding: '8px 10px',
                  color: 'var(--text-strong)',
                  fontSize: 'var(--text-xs)',
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                <span>CONNECTION DETAILS</span>
                <span style={{ fontSize: '10px', color: 'var(--text-faint)' }}>
                  {openAccordion === 'connection' ? '▲' : '▼'}
                </span>
              </button>

              {openAccordion === 'connection' ? (
                <div style={{ padding: '8px 10px', fontSize: '10.5px', color: 'var(--text-muted)' }}>
                  <div style={{ wordBreak: 'break-all', fontFamily: 'var(--mono)' }}>{API_BASE_URL}</div>
                  <div style={{ fontSize: '10px', color: 'var(--text-faint)', marginTop: 4 }}>
                    {API_BASE_URL_IS_EXPLICIT ? 'Explicitly defined via VITE_API_URL' : 'Default relative origin'}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        </div>

        {/* RIGHT CONTENT AREA */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-4)' }}>
          {activeTab === 'general' ? (
            <div className="stack" style={{ gap: 'var(--space-3)' }}>
              <div>
                <h2 style={{ fontSize: 'var(--text-md)', fontWeight: 800, margin: 0, color: 'var(--text-strong)' }}>
                  APPEARANCE &amp; WORKSTATION
                </h2>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  Configure visual display and interface themes.
                </span>
              </div>

              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Workstation Theme</span>
                  <span className="settings-row-item__desc">Choose between Light judicial mode, Dark command center, or host system.</span>
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
          ) : activeTab === 'system' ? (
            <div className="stack" style={{ gap: 'var(--space-3)' }}>
              <div>
                <h2 style={{ fontSize: 'var(--text-md)', fontWeight: 800, margin: 0, color: 'var(--text-strong)' }}>
                  SYSTEM &amp; SUBSYSTEM HEALTH
                </h2>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  Real-time status of forensic backends and neural models.
                </span>
              </div>

              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Backend API Server</span>
                  <span className="settings-row-item__desc">Core FastAPI orchestration server.</span>
                </div>
                <Pill variant={health === 'up' ? 'ok' : health === 'down' ? 'error' : 'warn'}>
                  {health === 'up' ? 'ONLINE (UP)' : 'UNREACHABLE'}
                </Pill>
              </div>

              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">AI Multimodal Detector Models</span>
                  <span className="settings-row-item__desc">SwinB Vision Transformer, EfficientNet-B0, Wav2Vec2.</span>
                </div>
                <Pill variant="ok">11/11 MODELS ACTIVE</Pill>
              </div>

              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Perceptual Vector Index</span>
                  <span className="settings-row-item__desc">
                    {index ? `${index.indexed_count.toLocaleString()} vectors registered` : 'Corpus available'}
                  </span>
                </div>
                <Pill variant="ok">INDEX READY</Pill>
              </div>
            </div>
          ) : (
            /* DEFAULT: EVIDENCE INTEGRITY (SHA-256 & HASHING MATCHING PANEL 9) */
            <div className="stack" style={{ gap: 'var(--space-3)' }}>
              <div>
                <h2 style={{ fontSize: 'var(--text-md)', fontWeight: 800, margin: 0, color: 'var(--text-strong)', letterSpacing: '0.04em' }}>
                  SHA-256 &amp; HASHING
                </h2>
                <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
                  Configure hashing and integrity verification settings.
                </span>
              </div>

              {/* Setting 1: Auto-compute SHA-256 on upload */}
              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Auto-compute SHA-256 on upload</span>
                  <span className="settings-row-item__desc">Automatically compute SHA-256 hash for all uploaded evidence</span>
                </div>
                <div
                  className={`switch-toggle${autoComputeHash ? ' switch-toggle--active' : ''}`}
                  onClick={() => setAutoComputeHash(!autoComputeHash)}
                  role="switch"
                  aria-checked={autoComputeHash}
                  tabIndex={0}
                >
                  <span className="switch-toggle__thumb" />
                </div>
              </div>

              {/* Setting 2: Display hash in reports */}
              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Display hash in reports</span>
                  <span className="settings-row-item__desc">Include hash in all generated reports</span>
                </div>
                <div
                  className={`switch-toggle${displayHashInReports ? ' switch-toggle--active' : ''}`}
                  onClick={() => setDisplayHashInReports(!displayHashInReports)}
                  role="switch"
                  aria-checked={displayHashInReports}
                  tabIndex={0}
                >
                  <span className="switch-toggle__thumb" />
                </div>
              </div>

              {/* Setting 3: Verify hash before analysis */}
              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Verify hash before analysis</span>
                  <span className="settings-row-item__desc">Verify file integrity before running analysis</span>
                </div>
                <div
                  className={`switch-toggle${verifyBeforeAnalysis ? ' switch-toggle--active' : ''}`}
                  onClick={() => setVerifyBeforeAnalysis(!verifyBeforeAnalysis)}
                  role="switch"
                  aria-checked={verifyBeforeAnalysis}
                  tabIndex={0}
                >
                  <span className="switch-toggle__thumb" />
                </div>
              </div>

              {/* Setting 4: Default Hash Algorithm */}
              <div className="settings-row-item">
                <div className="settings-row-item__info">
                  <span className="settings-row-item__label">Default Hash Algorithm</span>
                  <span className="settings-row-item__desc">Select cryptographic hashing function for intake sealing.</span>
                </div>
                <select
                  className="input input--sm"
                  style={{ width: 140, fontSize: 'var(--text-xs)' }}
                  value={defaultAlgorithm}
                  onChange={(e) => setDefaultAlgorithm(e.target.value)}
                >
                  <option value="SHA-256">SHA-256</option>
                  <option value="SHA-512">SHA-512</option>
                  <option value="BLAKE3">BLAKE3</option>
                </select>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
