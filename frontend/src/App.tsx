/**
 * Application Shell — PRAMAAN Digital Forensics Platform.
 *
 * Eight primary destinations (Dashboard, Cases, Evidence, Analysis, Provenance,
 * Reports, Audit, Settings) reached from the left sidebar. The router also
 * serves two flows opened from within Cases — case-detail and intake — plus a
 * global search in the header. Direct URL-hash navigation and browser
 * back/forward are supported.
 */

import { useEffect, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT } from './api'
import { Banner } from './components/Banner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Icon } from './components/Icon'
import { Pill } from './components/Pill'
import { SidebarNav, type NavSection } from './components/SidebarNav'
import { useRouter, type RoutePath } from './lib/router'
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

function PersistentCaseHeader({
  caseRecord,
  routePath,
  onNavigate,
}: {
  caseRecord: { case_id: string; case_number: string; title?: string | null; latest_verdict?: string | null; priority?: string }
  routePath: RoutePath
  onNavigate: (path: RoutePath, params?: { caseId?: string }) => void
}) {
  const steps: { path: RoutePath; label: string; number: number }[] = [
    { path: 'case-detail', label: 'Case', number: 1 },
    { path: 'evidence', label: 'Evidence', number: 2 },
    { path: 'analysis', label: 'Analysis', number: 3 },
    { path: 'provenance', label: 'Provenance', number: 4 },
    { path: 'audit', label: 'Audit', number: 5 },
    { path: 'reports', label: 'Report', number: 6 },
  ]

  const isManipulated = caseRecord.latest_verdict?.includes('MANIPULATED')
  const isAuthentic = caseRecord.latest_verdict?.includes('AUTHENTIC')
  const verdictTone = isManipulated ? 'error' : isAuthentic ? 'ok' : 'warn'

  return (
    <div className="case-persistent-bar">
      <div className="case-persistent-bar__info">
        <span className="case-persistent-bar__id">{caseRecord.case_number}</span>
        <span className="case-persistent-bar__title">{caseRecord.title || 'Circulating Media Evidence Investigation'}</span>
        {caseRecord.latest_verdict ? (
          <Pill variant={verdictTone}>{caseRecord.latest_verdict.replace(/_/g, ' ')}</Pill>
        ) : null}
        {caseRecord.priority ? (
          <Pill variant={caseRecord.priority === 'high' ? 'error' : 'warn'}>{caseRecord.priority.toUpperCase()}</Pill>
        ) : null}
      </div>

      <div className="case-persistent-bar__steps">
        {steps.map((step) => {
          const active = routePath === step.path
          return (
            <button
              key={step.path}
              type="button"
              className={`case-persistent-bar__step${active ? ' case-persistent-bar__step--active' : ''}`}
              onClick={() => onNavigate(step.path, { caseId: caseRecord.case_id })}
            >
              <span className="case-persistent-bar__step-num">{step.number}</span>
              <span>{step.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

export function App() {
  const investigation = useInvestigation()
  const { route, navigate } = useRouter()
  const [searchQuery, setSearchQuery] = useState('')

  // Theme controller (Day / Night / System). Instantiated once here so the
  // resolved theme is applied to <html> for the whole app; the control itself
  // lives in Settings.
  const theme = useTheme()

  const { caseRecord, health, healthError, recheckHealth, selectCase, runAnalysis } = investigation

  // Sync route caseId to investigation state when present in URL
  useEffect(() => {
    if (route.caseId && (!caseRecord || caseRecord.case_id !== route.caseId)) {
      selectCase(route.caseId)
    }
  }, [route.caseId, caseRecord, selectCase])

  // Map the active route to its sidebar section. case-detail and intake are
  // flows opened from within Cases, so they keep Cases highlighted.
  const getActiveNavSection = (): NavSection => {
    switch (route.path) {
      case 'dashboard':
        return 'dashboard'
      case 'cases':
      case 'case-detail':
      case 'intake':
        return 'cases'
      case 'evidence':
        return 'evidence'
      case 'analysis':
        return 'analysis'
      case 'provenance':
        return 'provenance'
      case 'reports':
        return 'reports'
      case 'audit':
        return 'audit'
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
      case 'evidence':
        navigate('evidence')
        break
      case 'analysis':
        navigate('analysis', { caseId: caseRecord?.case_id })
        break
      case 'provenance':
        navigate('provenance', { caseId: caseRecord?.case_id })
        break
      case 'reports':
        navigate('reports', { caseId: caseRecord?.case_id })
        break
      case 'audit':
        navigate('audit', { caseId: caseRecord?.case_id })
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

  const showPersistentCaseHeader =
    Boolean(caseRecord) &&
    ['case-detail', 'evidence', 'analysis', 'provenance', 'reports', 'audit'].includes(route.path)

  return (
    <div className="app">
      {/* Top Header Bar — brand + product descriptor + global search. */}
      <header className="workstation-bar">
        <div
          className="workstation-bar__brand"
          style={{ cursor: 'pointer' }}
          onClick={() => navigate('dashboard')}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') navigate('dashboard')
          }}
        >
          <span>PRAMAAN</span>
          <span className="workstation-bar__dev">| प्रमाण</span>
          <span className="workstation-bar__descriptor">Digital evidence examination & provenance</span>
        </div>

        <div className="search-box">
          <Icon name="search" size={14} style={{ color: 'var(--text-faint)' }} />
          <input
            className="search-box__input"
            type="search"
            placeholder="Search cases, evidence, IDs… (press Enter)"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleGlobalSearchKeyDown}
          />
        </div>
      </header>

      {/* Main Body Layout */}
      <div className="app__body">
        <SidebarNav activeSection={getActiveNavSection()} onSelectSection={handleNavSelect} theme={theme} />

        <main className="app__main">
          {showPersistentCaseHeader && caseRecord ? (
            <PersistentCaseHeader caseRecord={caseRecord} routePath={route.path} onNavigate={navigate} />
          ) : null}
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
                  : ' (default — VITE_API_URL not set)'
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

          {/* Render Active Route Screen. Wrapped in an ErrorBoundary so a
              render-time exception in any screen surfaces as a visible error
              state here — never a blank page. Keyed by route so navigating
              away resets it. */}
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
            <ScreenEvidence onNavigate={navigate} onSelectCase={selectCase} />
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
