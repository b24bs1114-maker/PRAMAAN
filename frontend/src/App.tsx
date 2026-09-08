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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

  // The header user pill is a menu: click it to reveal Log Out. This works in
  // every auth mode, including the development bypass -- which used to show only
  // a "DEV" badge and left no way to end the session from the header.
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const userMenuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!userMenuOpen) return
    const onPointerDown = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setUserMenuOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setUserMenuOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [userMenuOpen])

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
      <ScreenLogin auth={auth} health={health} onRetryHealth={recheckHealth} />
    )
  }

  return (
    <div className="app">
      {/* Top Header Bar */}
      <header className="workstation-bar">
        {/* LEFT: Ashoka emblem + brand block */}
        <div
          className="workstation-bar__brand"
          onClick={() => navigate('dashboard')}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') navigate('dashboard')
          }}
          title="PRAMAAN | प्रमाण - Return to Dashboard"
          style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', flexShrink: 0, minWidth: 'auto' }}
        >
          <img
            src="/assets/ashoka-emblem-gold.png"
            alt="Ashoka Emblem"
            style={{ height: 40, width: 'auto', objectFit: 'contain', flexShrink: 0 }}
          />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            <div style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '0.14em', color: 'rgba(201,162,39,0.7)', textTransform: 'uppercase', fontFamily: 'var(--mono)', lineHeight: 1.2 }}>
              CHANDIGARH POLICE
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
              <span style={{ fontSize: 17, fontWeight: 800, color: 'var(--text-primary)', letterSpacing: '-0.01em', lineHeight: 1.1 }}>PRAMAAN</span>
              <span style={{ fontSize: 12, fontWeight: 400, color: 'rgba(201,162,39,0.55)', letterSpacing: '0.02em' }}>| प्रमाण</span>
            </div>
            <div style={{ fontSize: 9, color: 'var(--text-muted)', fontFamily: 'var(--mono)', letterSpacing: '0.06em', lineHeight: 1 }}>
              Digital Forensics &amp; Evidence Investigation
            </div>
          </div>
        </div>

        {/* CENTER: Search box */}
        <div className="search-box" style={{ flex: 1, maxWidth: 480 }}>
          <Icon name="search" size={14} style={{ color: 'var(--text-faint)', flexShrink: 0 }} />
          <input
            className="search-box__input"
            type="search"
            aria-label="Search cases, evidence, hashes and platforms. Press Enter to search the case queue."
            placeholder="Search cases, evidence, hashes, platforms..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={handleGlobalSearchKeyDown}
          />
          <span className="search-box__shortcut" aria-hidden="true">⌘ K</span>
        </div>

        {/* RIGHT: Datetime + User pill */}
        <div className="workstation-bar__right">
          <div className="workstation-bar__datetime">
            <div className="workstation-bar__date">{dateStr}</div>
            <div className="workstation-bar__time">{timeStr}</div>
          </div>
          {auth.user && (
            <div ref={userMenuRef} style={{ position: 'relative', flexShrink: 0 }}>
              <button
                type="button"
                className="header-user-pill"
                aria-haspopup="menu"
                aria-expanded={userMenuOpen}
                title={`Signed in as ${auth.user.username} · ${auth.user.role}`}
                onClick={() => setUserMenuOpen((open) => !open)}
                style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: 32, padding: '5px 10px 5px 5px', cursor: 'pointer' }}
              >
                <div style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--gold)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: '1.5px solid rgba(201,162,39,0.5)' }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: '#040608', letterSpacing: '-0.01em' }}>
                    {auth.user.display_name.slice(0, 2).toUpperCase()}
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 1, lineHeight: 1.2, textAlign: 'left' }}>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>{auth.user.display_name}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--text-muted)', fontFamily: 'var(--mono)', letterSpacing: '0.04em' }}>{auth.user.role}</span>
                </div>
                {auth.user.dev_bypass && (
                  <span
                    style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.08em', color: 'var(--warning)', background: 'var(--warning-wash)', border: '1px solid var(--warning-line)', borderRadius: 4, padding: '2px 6px', marginLeft: 2 }}
                    title="Local development auth bypass is active. Not a real authenticated operator."
                  >
                    DEV
                  </span>
                )}
                <span aria-hidden="true" style={{ marginLeft: 2, fontSize: 9, color: 'var(--text-faint)', transform: userMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 150ms ease' }}>▾</span>
              </button>

              {userMenuOpen && (
                <div
                  role="menu"
                  style={{ position: 'absolute', top: 'calc(100% + 8px)', right: 0, minWidth: 200, background: 'var(--surface-1, #10141a)', border: '1px solid rgba(255,255,255,0.10)', borderRadius: 10, boxShadow: '0 12px 32px rgba(0,0,0,0.5)', padding: 6, zIndex: 100 }}
                >
                  <div style={{ padding: '6px 10px 8px', borderBottom: '1px solid rgba(255,255,255,0.06)', marginBottom: 4 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)' }}>{auth.user.display_name}</div>
                    <div style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: 'var(--mono)' }}>{auth.user.username}</div>
                  </div>
                  {auth.user.dev_bypass && (
                    <div style={{ padding: '4px 10px 8px', fontSize: 10, color: 'var(--text-muted)', lineHeight: 1.5 }}>
                      Development auth bypass is active. Logging out returns to the bypass identity, not a login prompt.
                    </div>
                  )}
                  <button
                    type="button"
                    role="menuitem"
                    onClick={() => {
                      setUserMenuOpen(false)
                      handleSignOut()
                    }}
                    disabled={auth.signingOut}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '8px 10px', background: 'transparent', border: 'none', borderRadius: 6, color: 'rgba(239,68,68,0.9)', fontSize: 12, fontWeight: 600, cursor: 'pointer', textAlign: 'left', transition: 'background 150ms ease' }}
                    onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.background = 'rgba(239,68,68,0.12)' }}
                    onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
                  >
                    <Icon name="lock" size={13} />
                    <span>{auth.signingOut ? 'Logging out…' : 'Log Out'}</span>
                  </button>
                </div>
              )}
            </div>
          )}
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
              operator={auth.user}
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
              operator={auth.user}
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
