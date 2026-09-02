/**
 * Application Shell - PRAMAAN Digital Forensics Platform.
 *
 * Four primary global destinations (Dashboard, Cases, Reports, Settings)
 * reached from the left sidebar. Case-specific workflow navigation (Case, Evidence,
 * Analysis, Provenance, Audit, Report) is anchored directly in the persistent case header.
 * Direct URL-hash navigation and browser back/forward are supported.
 */

import { useEffect, useMemo, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT } from './api'
import { Banner } from './components/Banner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Icon } from './components/Icon'
import { SidebarNav, type NavSection } from './components/SidebarNav'
import { useRouter } from './lib/router'
import { Screen1Intake } from './screens/Screen1Intake'
import { Screen2Analysis } from './screens/Screen2Analysis'
import { ScreenAudit } from './screens/ScreenAudit'
import { ScreenCaseDetail } from './screens/ScreenCaseDetail'
import { ScreenCases } from './screens/ScreenCases'
import { ScreenDashboard } from './screens/ScreenDashboard'
import { ScreenEvidence } from './screens/ScreenEvidence'
import { ScreenProvenance } from './screens/ScreenProvenance'
import { ScreenReports } from './screens/ScreenReports'
import { ScreenSettings } from './screens/ScreenSettings'
import { useInvestigation } from './state/useInvestigation'
import { useTheme } from './state/useTheme'

export function App() {
  const investigation = useInvestigation()
  const { route, navigate } = useRouter()
  const [searchQuery, setSearchQuery] = useState('')

  const theme = useTheme()
  const { caseRecord, health, healthError, recheckHealth, selectCase, runAnalysis } = investigation

  // Live clock
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(id)
  }, [])

  const dateStr = useMemo(() => now.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }), [now])
  const timeStr = useMemo(() => now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true, timeZoneName: 'short' }).replace(':00 ', ' ').toUpperCase(), [now])

  useEffect(() => {
    if (route.caseId && (!caseRecord || caseRecord.case_id !== route.caseId)) {
      selectCase(route.caseId)
    }
  }, [route.caseId, caseRecord, selectCase])

  const getActiveNavSection = (): NavSection => {
    switch (route.path) {
      case 'dashboard':
        return 'dashboard'
      case 'cases':
      case 'case-detail':
      case 'intake':
      case 'evidence':
      case 'analysis':
      case 'provenance':
      case 'audit':
        return 'cases'
      case 'reports':
        return 'reports'
      case 'settings':
        return 'settings'
      default:
        return 'dashboard'
    }
  }

  const handleNavSelect = (section: NavSection) => {
    switch (section) {
      case 'dashboard':
        navigate('dashboard')
        break
      case 'cases':
        navigate('cases')
        break
      case 'reports':
        navigate('reports', { caseId: caseRecord?.case_id })
        break
      case 'settings':
        navigate('settings')
        break
    }
  }

  const handleGlobalSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && searchQuery.trim()) {
      navigate('cases', { q: searchQuery.trim() })
    }
  }

  return (
    <div className="app">
      {/* Top Header Bar */}
      <header className="workstation-bar">
        <div
          className="workstation-bar__brand"
          onClick={() => navigate('dashboard')}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') navigate('dashboard')
          }}
          title="PRAMAAN | प्रमाण - Return to Dashboard"
        >
          <picture className="workstation-bar__brand-picture">
            <source
              media="(max-width: 768px)"
              srcSet="/assets/pramaan-emblem.png"
            />
            <img
              src={theme.resolved === 'light' ? '/assets/pramaan-logo-light.png' : '/assets/pramaan-logo-dark.png'}
              alt="PRAMAAN | प्रमाण - Digital Evidence Examination & Provenance"
              className="workstation-bar__brand-img"
            />
          </picture>
        </div>

        <div className="search-box">
          <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
          <input
            className="search-box__input"
            type="search"
            placeholder="Search cases, evidence, hashes, platforms..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleGlobalSearchKeyDown}
          />
          <span className="search-box__shortcut">⌘K</span>
        </div>

        <div className="workstation-bar__right">
          {/* Notification Bell with Badge */}
          <div className="workstation-bar__bell">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
            <span className="workstation-bar__bell-badge">7</span>
          </div>

          {/* Live Datetime Stamp */}
          <div className="workstation-bar__datetime">
            <div className="workstation-bar__date">{dateStr}</div>
            <div className="workstation-bar__time">{timeStr}</div>
          </div>
        </div>
      </header>

      {/* Main Body Layout */}
      <div className="app__body">
        <SidebarNav activeSection={getActiveNavSection()} onSelectSection={handleNavSelect} theme={theme} />

        <main className="app__main">
          {health === 'down' ? (
            <Banner
              tone="error"
              title="Backend not reachable"
              detail={
                healthError instanceof Error
                  ? healthError.message
                  : 'The health endpoint did not respond.'
              }
              meta={`Configured base URL: ${API_BASE_URL}${
                API_BASE_URL_IS_EXPLICIT
                  ? ' (from VITE_API_URL)'
                  : ' (default - VITE_API_URL not set)'
              }`}
            >
              <div className="btn-row" style={{ marginTop: 10 }}>
                <button type="button" className="btn btn--ghost" onClick={recheckHealth}>
                  <Icon name="refresh" size={15} />
                  Retry connection
                </button>
              </div>
            </Banner>
          ) : null}

          {/* Render Active Route Screen */}
          <ErrorBoundary key={`${route.path}:${route.caseId ?? ''}`}>
            {route.path === 'dashboard' ? (
            <ScreenDashboard
              investigation={investigation}
              onNavigate={navigate}
              onSelectCase={selectCase}
            />
          ) : route.path === 'cases' ? (
            <ScreenCases
              investigation={investigation}
              initialFilter={route.filter || 'all'}
              initialQuery={route.q || ''}
              onNavigate={navigate}
              onSelectCase={selectCase}
            />
          ) : route.path === 'case-detail' ? (
            <ScreenCaseDetail
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
            />
          ) : route.path === 'evidence' ? (
            <ScreenEvidence
              investigation={investigation}
              onNavigate={navigate}
              onSelectCase={selectCase}
            />
          ) : route.path === 'intake' ? (
            <Screen1Intake
              investigation={investigation}
              onAnalyse={() => {
                runAnalysis()
                navigate('analysis', { caseId: caseRecord?.case_id })
              }}
            />
          ) : route.path === 'analysis' ? (
            <Screen2Analysis
              investigation={investigation}
              onPropagation={() => navigate('provenance', { caseId: caseRecord?.case_id })}
            />
          ) : route.path === 'provenance' ? (
            <ScreenProvenance
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
            />
          ) : route.path === 'reports' ? (
            <ScreenReports
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
            />
          ) : route.path === 'audit' ? (
            <ScreenAudit
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
            />
          ) : route.path === 'settings' ? (
            <ScreenSettings investigation={investigation} theme={theme} />
          ) : (
            <ScreenDashboard
              investigation={investigation}
              onNavigate={navigate}
              onSelectCase={selectCase}
            />
          )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
