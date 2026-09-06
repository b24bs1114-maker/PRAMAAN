import { Icon, type IconName } from './Icon'
import type { AuthUser } from '../api'
import type { ThemeController } from '../state/useTheme'
import type { BackendHealth } from '../state/useInvestigation'

/**
 * Workstation destinations grouped into global sections:
 * INVESTIGATE, OUTPUT, and footer-level SETTINGS.
 */
export type NavSection =
  | 'dashboard'
  | 'cases'
  | 'intake'
  | 'analysis'
  | 'provenance'
  | 'audit'
  | 'reports'
  | 'settings'

/**
 * Destinations that stand on their own, with or without an open case.
 *
 * `intake` belongs here deliberately: selecting it with no case open opens the
 * new-case flow (see `handleNavSelect` in App.tsx), so it is a way *into* a case
 * rather than a view of one.
 */
const GLOBAL_ITEMS: { id: NavSection; label: string; icon: IconName }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: 'shield' },
  { id: 'cases', label: 'Cases', icon: 'document' },
  { id: 'intake', label: 'Evidence Intake', icon: 'upload' },
]

/**
 * Destinations that can only describe one case.
 *
 * All four read from a case: Analysis fuses that case's exhibits, Provenance
 * traces them, Audit replays that case's chain, Reports renders that case's PDF.
 * With nothing open they had no answer to give and simply said so after the
 * click -- four entries in a seven-item list that were live-looking dead ends.
 *
 * They are still listed, because hiding them would hide the workflow, but they
 * are disabled and grouped under the case they apply to, so the sidebar states
 * the dependency instead of demonstrating it.
 */
const CASE_ITEMS: { id: NavSection; label: string; icon: IconName; needs: string }[] = [
  { id: 'analysis', label: 'Analysis', icon: 'layers', needs: 'examines one case’s exhibits' },
  { id: 'provenance', label: 'Provenance', icon: 'sitemap', needs: 'traces one case’s exhibits' },
  { id: 'audit', label: 'Audit', icon: 'lock', needs: 'replays one case’s custody chain' },
  { id: 'reports', label: 'Reports', icon: 'download', needs: 'renders one case’s report' },
]

/**
 * Backend connectivity line. The previous card hardcoded
 * "All Systems Operational", "11/11 Loaded" and "245 GB Free" -- three claims
 * with no data behind them on a forensic tool. What the app actually measures
 * is the health probe, so that is all this shows.
 */
function SidebarSystemStatus({ health }: { health: BackendHealth }) {
  const up = health === 'up'
  return (
    <div className="sidebar-status-card">
      <div className="sidebar-status-card__header">
        <span className={`sidebar-status-card__dot${up ? '' : ' sidebar-status-card__dot--down'}`} />
        <span className="sidebar-status-card__title">BACKEND STATUS</span>
      </div>
      <div className="sidebar-status-card__main">
        {up ? 'OPERATIONAL' : health === 'down' ? 'UNREACHABLE' : 'CHECKING…'}
      </div>
      <div className="sidebar-status-card__sub">
        Health probe · /health
      </div>
    </div>
  )
}

/**
 * The signed-in operator, and the way out.
 *
 * This used to read "Analyst / Forensic Team" for everyone -- two labels with
 * nobody behind them, on the one surface where a reader looks to find out whose
 * name is going on the evidence. It now shows the account the backend
 * authenticated, and nothing when there is none.
 */
function SidebarUserProfile({
  user,
  onSignOut,
  signingOut,
}: {
  user: AuthUser | null
  onSignOut: () => void
  signingOut: boolean
}) {
  if (!user) return null
  const bypass = user.dev_bypass === true
  return (
    <div className="sidebar-user-pill">
      <div className="sidebar-user-pill__avatar" aria-hidden="true">
        <img
          src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Crect width='40' height='40' fill='%231e293b'/%3E%3Ccircle cx='20' cy='15' r='7' fill='%2394a3b8'/%3E%3Cpath d='M8 35 c0-7 6-11 12-11 s12 4 12 11' fill='%2394a3b8'/%3E%3C/svg%3E"
          alt=""
          className="sidebar-user-pill__img"
        />
      </div>
      <div className="sidebar-user-pill__info">
        <span
          className="sidebar-user-pill__name"
          title={bypass ? `Development auth bypass: ${user.username}` : `Signed in as ${user.username}`}
        >
          {user.display_name}
        </span>
        <span className="sidebar-user-pill__role">{user.role}</span>
      </div>
      {/* No sign-out under the bypass: there is no session to revoke, and a
          button that cannot log anyone out would be a lie about the state of the
          server. The badge says what this identity actually is instead. */}
      {bypass ? (
        <span className="sidebar-user-pill__badge" title="Local development auth bypass is active on the backend. Not an authenticated operator.">
          DEV
        </span>
      ) : (
        <button
          type="button"
          className="sidebar-user-pill__signout"
          onClick={onSignOut}
          disabled={signingOut}
          title={`Sign out ${user.display_name}`}
        >
          <Icon name="lock" size={13} />
          <span className="visually-hidden">Sign out</span>
        </button>
      )}
    </div>
  )
}

function SidebarThemeSegmented({ theme }: { theme: ThemeController }) {
  const isDark = theme.resolved === 'dark'

  return (
    <div className="sidebar-theme-segmented">
      <button
        type="button"
        className={`sidebar-theme-tab${!isDark ? ' sidebar-theme-tab--active' : ''}`}
        onClick={() => theme.setMode('light')}
      >
        <span style={{ fontSize: 13, color: !isDark ? '#2563eb' : 'var(--text-muted)' }}>☀</span>
        <span>Light</span>
      </button>

      <button
        type="button"
        className={`sidebar-theme-tab${isDark ? ' sidebar-theme-tab--active' : ''}`}
        onClick={() => theme.setMode('dark')}
      >
        <span style={{ fontSize: 13, color: isDark ? '#93c5fd' : 'var(--text-muted)' }}>☾</span>
        <span>Dark</span>
      </button>
    </div>
  )
}

export function SidebarNav({
  activeSection,
  onSelectSection,
  theme,
  health,
  user,
  onSignOut,
  signingOut = false,
  openCaseNumber = null,
}: {
  activeSection: NavSection
  onSelectSection: (section: NavSection) => void
  theme: ThemeController
  health: BackendHealth
  /** The authenticated operator, or null when nobody is signed in. */
  user: AuthUser | null
  onSignOut: () => void
  signingOut?: boolean
  /**
   * The case number of the open case, or null when none is open.
   *
   * The number rather than the id: the sidebar names the case to the reader, and
   * `PRAMAAN-20260905-0009` is the name an examiner and a court both use. A null
   * disables the case-scoped group.
   */
  openCaseNumber?: string | null
}) {
  const caseOpen = Boolean(openCaseNumber)

  const renderItem = (
    item: { id: NavSection; label: string; icon: IconName },
    disabled = false,
    title?: string,
  ) => {
    const active = activeSection === item.id
    return (
      <button
        key={item.id}
        type="button"
        className={`sidebar-nav__item${active ? ' sidebar-nav__item--active' : ''}`}
        onClick={() => onSelectSection(item.id)}
        aria-current={active ? 'page' : undefined}
        disabled={disabled}
        title={title}
      >
        <Icon name={item.icon} size={15} />
        <span>{item.label}</span>
      </button>
    )
  }

  return (
    <aside className="sidebar-nav" aria-label="Workstation navigation">
      <div className="sidebar-nav__brand-header" style={{ padding: '16px 16px 12px 16px', borderBottom: '1px solid var(--border-subtle)' }}>
        <div style={{ fontSize: '13px', fontWeight: 800, letterSpacing: '0.08em', color: 'var(--text-primary)' }}>PRAMAAN</div>
        <div style={{ fontSize: '11px', color: 'var(--text-muted)', fontWeight: 500 }}>Investigation Console</div>
      </div>
      <div className="sidebar-nav__scroll">
        <nav className="sidebar-nav__group" aria-label="Workspace navigation">
          <span className="sidebar-nav__label">WORKSPACE</span>
          {GLOBAL_ITEMS.map((item) => renderItem(item))}
        </nav>

        {/*
          The case-scoped half of the workflow, labelled with the case it applies
          to. Kept a separate <nav> with its own accessible name so a screen
          reader reaches "Case navigation" and the case number before the four
          entries, rather than finding four disabled buttons with no explanation.
        */}
        <nav
          className="sidebar-nav__group"
          aria-label={openCaseNumber ? `Navigation for case ${openCaseNumber}` : 'Case navigation'}
        >
          <span className="sidebar-nav__label">CASE</span>
          {openCaseNumber ? (
            <span className="sidebar-nav__scope" title={`Open case ${openCaseNumber}`}>
              #{openCaseNumber}
            </span>
          ) : (
            <span className="sidebar-nav__scope sidebar-nav__scope--empty">
              No case open. Open one from Cases to examine, trace, verify or report on
              its evidence.
            </span>
          )}
          {CASE_ITEMS.map((item) =>
            renderItem(
              item,
              !caseOpen,
              caseOpen
                ? `${item.label} for case ${openCaseNumber}`
                : `${item.label} ${item.needs} — open a case first`,
            ),
          )}
        </nav>
      </div>

      <div className="sidebar-nav__footer">
        <div style={{ marginBottom: 8 }}>
          <span className="sidebar-nav__label" style={{ padding: '0 8px 4px 8px', display: 'block' }}>SYSTEM</span>
          <button
            type="button"
            className={`sidebar-nav__item${activeSection === 'settings' ? ' sidebar-nav__item--active' : ''}`}
            onClick={() => onSelectSection('settings')}
            aria-current={activeSection === 'settings' ? 'page' : undefined}
            style={{ width: '100%' }}
          >
            <Icon name="settings" size={15} />
            <span>Settings</span>
          </button>
        </div>
        <SidebarSystemStatus health={health} />
        <SidebarUserProfile user={user} onSignOut={onSignOut} signingOut={signingOut} />
        <SidebarThemeSegmented theme={theme} />
      </div>
    </aside>

  )
}
