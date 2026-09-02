import { Icon, type IconName } from './Icon'
import type { ThemeController } from '../state/useTheme'

/**
 * Workstation destinations grouped into global sections:
 * INVESTIGATE, OUTPUT, and footer-level SETTINGS.
 */
export type NavSection =
  | 'dashboard'
  | 'cases'
  | 'reports'
  | 'settings'

const SECTIONS: { title: string; items: { id: NavSection; label: string; icon: IconName }[] }[] = [
  {
    title: 'INVESTIGATE',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: 'shield' },
      { id: 'cases', label: 'Cases', icon: 'document' },
    ],
  },
  {
    title: 'OUTPUT',
    items: [
      { id: 'reports', label: 'Reports', icon: 'download' },
    ],
  },
]

function SidebarSystemStatus() {
  return (
    <div className="sidebar-status-card">
      <div className="sidebar-status-card__header">
        <span className="sidebar-status-card__dot" />
        <span className="sidebar-status-card__title">SYSTEM STATUS</span>
      </div>
      <div className="sidebar-status-card__main">
        <span>○ All Systems Operational</span>
      </div>
      <div className="sidebar-status-card__sub">
        AI Models • 11/11 Loaded
      </div>
      <div className="sidebar-status-card__sub">
        Storage • 245 GB Free
      </div>
    </div>
  )
}

function SidebarUserProfile() {
  return (
    <div className="sidebar-user-pill">
      <div className="sidebar-user-pill__avatar">
        <img
          src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Crect width='40' height='40' fill='%231e293b'/%3E%3Ccircle cx='20' cy='15' r='7' fill='%23f87171'/%3E%3Cpath d='M8 35 c0-7 6-11 12-11 s12 4 12 11' fill='%23f87171'/%3E%3C/svg%3E"
          alt="Analyst"
          className="sidebar-user-pill__img"
        />
      </div>
      <div className="sidebar-user-pill__info">
        <span className="sidebar-user-pill__name">Analyst</span>
        <span className="sidebar-user-pill__role">Forensic Team</span>
      </div>
      <span className="sidebar-user-pill__chevron">⌵</span>
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
}: {
  activeSection: NavSection
  onSelectSection: (section: NavSection) => void
  theme: ThemeController
}) {
  return (
    <aside className="sidebar-nav" aria-label="Workstation navigation">
      <div className="sidebar-nav__scroll">
        {SECTIONS.map((sec) => (
          <nav key={sec.title} className="sidebar-nav__group">
            <span className="sidebar-nav__label">{sec.title}</span>
            {sec.items.map((item) => {
              const active = activeSection === item.id
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`sidebar-nav__item${active ? ' sidebar-nav__item--active' : ''}`}
                  onClick={() => onSelectSection(item.id)}
                  aria-current={active ? 'page' : undefined}
                >
                  <Icon name={item.icon} size={15} />
                  <span>{item.label}</span>
                </button>
              )
            })}
          </nav>
        ))}
      </div>

      <div className="sidebar-nav__footer">
        <SidebarSystemStatus />
        <SidebarUserProfile />
        <SidebarThemeSegmented theme={theme} />
        <button
          type="button"
          className={`sidebar-nav__item${activeSection === 'settings' ? ' sidebar-nav__item--active' : ''}`}
          onClick={() => onSelectSection('settings')}
          aria-current={activeSection === 'settings' ? 'page' : undefined}
          style={{ width: '100%', marginTop: 'var(--space-1)' }}
        >
          <Icon name="settings" size={15} />
          <span>Settings</span>
        </button>
      </div>
    </aside>
  )
}
