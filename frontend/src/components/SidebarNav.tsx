import { Icon, type IconName } from './Icon'
import type { ThemeController } from '../state/useTheme'

/**
 * Workstation destinations grouped into 4 sections:
 * INVESTIGATE, FORENSICS, OUTPUT, SYSTEM.
 */
export type NavSection =
  | 'dashboard'
  | 'cases'
  | 'evidence'
  | 'analysis'
  | 'provenance'
  | 'reports'
  | 'audit'
  | 'settings'

const SECTIONS: { title: string; items: { id: NavSection; label: string; icon: IconName }[] }[] = [
  {
    title: 'INVESTIGATE',
    items: [
      { id: 'dashboard', label: 'Dashboard', icon: 'shield' },
      { id: 'cases', label: 'Cases', icon: 'document' },
      { id: 'evidence', label: 'Evidence', icon: 'upload' },
    ],
  },
  {
    title: 'FORENSICS',
    items: [
      { id: 'analysis', label: 'Analysis', icon: 'search' },
      { id: 'provenance', label: 'Provenance', icon: 'external' },
    ],
  },
  {
    title: 'OUTPUT',
    items: [
      { id: 'reports', label: 'Reports', icon: 'download' },
      { id: 'audit', label: 'Audit', icon: 'lock' },
    ],
  },
  {
    title: 'SYSTEM',
    items: [
      { id: 'settings', label: 'Settings', icon: 'settings' },
    ],
  },
]

function ThemeSegmentedControl({ theme }: { theme: ThemeController }) {
  const current = theme.resolved // 'light' | 'dark'

  return (
    <div className="sidebar-theme-toggle" role="radiogroup" aria-label="Theme selection">
      <button
        type="button"
        className={`sidebar-theme-toggle__btn${current === 'light' ? ' sidebar-theme-toggle__btn--active' : ''}`}
        onClick={() => theme.setMode('light')}
        role="radio"
        aria-checked={current === 'light'}
        title="Switch to Light mode"
      >
        <span className="sidebar-theme-toggle__icon" aria-hidden="true">☀</span>
        <span>LIGHT</span>
      </button>
      <button
        type="button"
        className={`sidebar-theme-toggle__btn${current === 'dark' ? ' sidebar-theme-toggle__btn--active' : ''}`}
        onClick={() => theme.setMode('dark')}
        role="radio"
        aria-checked={current === 'dark'}
        title="Switch to Dark mode"
      >
        <span className="sidebar-theme-toggle__icon" aria-hidden="true">☾</span>
        <span>DARK</span>
      </button>
    </div>
  )
}

function SidebarHealthBadge() {
  return (
    <div className="sidebar-health">
      <div className="sidebar-health__head">
        <span className="sidebar-health__dot" />
        <span className="sidebar-health__title">SYSTEM ONLINE</span>
      </div>
      <span className="sidebar-health__sub">AI 3/3 · INDEX READY · AUDIT VALID</span>
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
                  <Icon name={item.icon} size={14} />
                  <span>{item.label}</span>
                </button>
              )
            })}
          </nav>
        ))}
      </div>

      <div className="sidebar-nav__footer">
        <SidebarHealthBadge />
        <ThemeSegmentedControl theme={theme} />
        <button
          type="button"
          className={`sidebar-nav__item${activeSection === 'settings' ? ' sidebar-nav__item--active' : ''}`}
          onClick={() => onSelectSection('settings')}
          aria-current={activeSection === 'settings' ? 'page' : undefined}
        >
          <Icon name="settings" size={14} />
          <span>Settings</span>
        </button>
      </div>
    </aside>
  )
}
