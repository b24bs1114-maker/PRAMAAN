/**
 * Application Shell - PRAMAAN Digital Forensics Platform.
 *
 * Four primary global destinations (Dashboard, Cases, Reports, Settings)
 * reached from the left sidebar. Case-specific workflow navigation (Case, Evidence,
 * Analysis, Provenance, Audit, Report) is anchored directly in the persistent case header.
 * Direct URL-hash navigation and browser back/forward are supported.
 *
 * Nothing below is reachable without a signed-in operator. That is not decoration:
 * the backend records the authenticated account as the examiner on every piece of
 * evidence, so there is no coherent way to run intake anonymously, and no screen
 * here invents an identity to stand in for one.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT } from './api'
import { Banner } from './components/Banner'
import { CaseContextBar } from './components/CaseWorkflowStepper'
import { ErrorBoundary } from './components/ErrorBoundary'
import { Icon } from './components/Icon'
import { Spinner } from './components/Feedback'
import { SidebarNav, type NavSection } from './components/SidebarNav'
import { beginNewCase } from './lib/newcase'
import { useRouter } from './lib/router'
import { Screen1Intake } from './screens/Screen1Intake'
import { Screen2Analysis } from './screens/Screen2Analysis'
import { ScreenAudit } from './screens/ScreenAudit'
import { ScreenCaseDetail } from './screens/ScreenCaseDetail'
import { ScreenCases } from './screens/ScreenCases'
import { ScreenDashboard } from './screens/ScreenDashboard'
import { ScreenEvidence } from './screens/ScreenEvidence'
import { ScreenLogin } from './screens/ScreenLogin'
import { ScreenProvenance } from './screens/ScreenProvenance'
import { ScreenReports } from './screens/ScreenReports'
import { ScreenSettings } from './screens/ScreenSettings'
import { useAuth } from './state/useAuth'
import { useInvestigation } from './state/useInvestigation'
import { useTheme } from './state/useTheme'

export function App() {
  const investigation = useInvestigation()
  const { route, navigate } = useRouter()
  const [searchQuery, setSearchQuery] = useState('')

  const theme = useTheme()
  const auth = useAuth()
  const { caseRecord, health, healthError, recheckHealth, selectCase, runAnalysis, reset } = investigation

  // "New Case" is a state reset, not merely a route change: the investigation
  // store is shared across screens, so navigating to intake without clearing it
  // would show the previous case's sealed evidence under a fresh case. See
  // lib/newcase.
  const handleNewCase = useCallback(() => beginNewCase({ reset, navigate }), [reset, navigate])

  /**
   * Sign out, and leave nothing of this operator's work behind.
   *
   * The investigation store is cleared first. It holds the open case, its sealed
   * evidence and its analysis results; leaving that in memory across a sign-out
   * would show one examiner's case to whoever signs in next on the same machine.
   */
  const handleSignOut = useCallback(() => {
    reset()
    navigate('dashboard')
    void auth.signOut()
  }, [reset, navigate, auth])

  // Live clock
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000)
    return () => clearInterval(id)
  }, [])

  const dateStr = useMemo(() => now.toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' }), [now])
  const timeStr = useMemo(() => now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true, timeZoneName: 'short' }).replace(':00 ', ' ').toUpperCase(), [now])

  const signedIn = auth.user !== null

  useEffect(() => {
    // Gated on a signed-in operator: no case is fetched while the login screen is
    // up, so a deep link to a case cannot pull records before identity is known.
    if (!signedIn) return
    if (route.caseId && (!caseRecord || caseRecord.case_id !== route.caseId)) {
      selectCase(route.caseId)
    }
  }, [signedIn, route.caseId, caseRecord, selectCase])

  const getActiveNavSection = (): NavSection => {
    switch (route.path) {
      case 'dashboard':
        return 'dashboard'
      case 'cases':
      case 'case-detail':
      case 'evidence':
        return 'cases'
      case 'intake':
        return 'intake'
      case 'analysis':
        return 'analysis'
      case 'provenance':
        return 'provenance'
      case 'audit':
        return 'audit'
      case 'reports':
        return 'reports'
      case 'settings':
        return 'settings'
      default:
        return 'dashboard'
    }
  }

  const handleNavSelect = (section: NavSection) => {
    const activeCaseId = caseRecord?.case_id || route.caseId || null
    switch (section) {
      case 'dashboard':
        navigate('dashboard')
        break
      case 'cases':
        navigate('cases')
        break
      case 'intake':
        if (activeCaseId) {
          navigate('intake', { caseId: activeCaseId })
        } else {
          handleNewCase()
        }
        break
      case 'analysis':
        navigate('analysis', { caseId: activeCaseId })
        break
      case 'provenance':
        navigate('provenance', { caseId: activeCaseId })
        break
      case 'audit':
        navigate('audit', { caseId: activeCaseId })
        break
      case 'reports':
        navigate('reports', { caseId: activeCaseId })
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

  // A stored token is being spent on /api/auth/me. Neither the console nor the
  // login form is true yet, so show neither: rendering the console here would
  // flash case chrome at someone who may turn out to be signed out, and rendering
  // the login form would ask a signed-in examiner to sign in again on every reload.
  if (auth.restoring) {
    return (
      <div className="login-shell">
        <div className="login-restore">
          <Spinner />
          <span>Restoring session…</span>
        </div>
      </div>
    )
  }

  // No authenticated operator: no console. Every case this tool opens is attributed
  // to the account that opened it, so the gate is the first thing, not a setting.
  if (!auth.user) {
    return (
      <ScreenLogin auth={auth} health={health} theme={theme} onRetryHealth={recheckHealth} />
    )
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
          {/* A placeholder is not a label: it is announced inconsistently and
              disappears the moment anyone types. This field has no visible
              caption by design -- the magnifier and its position in the header
              carry the meaning for a sighted reader -- so the name it is
              missing is supplied here, and it says what pressing Enter does,
              because that is the only way to run this search. */}
          <input
            className="search-box__input"
            type="search"
            aria-label="Search cases, evidence, hashes and platforms. Press Enter to search the case queue."
            placeholder="Search cases, evidence, hashes, platforms..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleGlobalSearchKeyDown}
          />
        </div>

        <div className="workstation-bar__right">
          {/* Live Datetime Stamp */}
          <div className="workstation-bar__datetime">
            <div className="workstation-bar__date">{dateStr}</div>
            <div className="workstation-bar__time">{timeStr}</div>
          </div>
        </div>
      </header>

      {/* Main Body Layout */}
      <div className="app__body">
        <SidebarNav
          activeSection={getActiveNavSection()}
          onSelectSection={handleNavSelect}
          theme={theme}
          health={health}
          user={auth.user}
          onSignOut={handleSignOut}
          signingOut={auth.signingOut}
          openCaseNumber={caseRecord?.case_number ?? null}
        />

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

          {/* The one canonical case-context row: which case is open, and where
              in its workflow the operator is.

              Rendered here by the shell for every case screen, and by nothing
              else. The screens render page content only -- a screen-local second
              copy of this row (there used to be four) is the bug this structure
              exists to prevent, and so is a screen reprinting the case number
              from its own copy of the case row.

              It decides for itself that it belongs only on the case-scoped
              routes, so it cannot end up claiming a workflow position above the
              Dashboard, the Cases queue or Settings. See
              components/CaseWorkflowStepper. */}
          <CaseContextBar
            investigation={investigation}
            routePath={route.path}
            routeCaseId={route.caseId ?? null}
            onNavigate={navigate}
          />

          {/* Render Active Route Screen */}
          <ErrorBoundary key={`${route.path}:${route.caseId ?? ''}`}>

            {route.path === 'dashboard' ? (
            <ScreenDashboard
              investigation={investigation}
              onNavigate={navigate}
              onSelectCase={selectCase}
              onNewCase={handleNewCase}
            />
          ) : route.path === 'cases' ? (
            <ScreenCases
              investigation={investigation}
              initialQuery={route.q || ''}
              onNavigate={navigate}
              onSelectCase={selectCase}
              onNewCase={handleNewCase}
            />
          ) : route.path === 'case-detail' ? (
            <ScreenCaseDetail
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
            />
          ) : route.path === 'evidence' ? (
            <ScreenEvidence
              /* Scope from the URL only. The store's open case must not silently
                 filter the global catalogue: `#evidence` means the catalogue, and
                 `#evidence?caseId=…` -- where workflow step 2 lands -- means one
                 case's exhibits. */
              caseId={route.caseId}
              investigation={investigation}
              onNavigate={navigate}
              onSelectCase={selectCase}
            />
          ) : route.path === 'intake' ? (
            <Screen1Intake
              investigation={investigation}
              operator={auth.user}
              onAnalyse={() => {
                runAnalysis()
                navigate('analysis', { caseId: caseRecord?.case_id })
              }}
            />
          ) : route.path === 'analysis' ? (
            <Screen2Analysis
              caseId={route.caseId || caseRecord?.case_id || null}
              investigation={investigation}
              onNavigate={navigate}
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
              onNewCase={handleNewCase}
            />
          )}
          </ErrorBoundary>
        </main>
      </div>
    </div>
  )
}
