import { Icon, type IconName } from './Icon'

export interface TabItem {
  id: string
  label: string
  icon?: IconName
}

/**
 * Horizontal tab strip for switching views within a screen (e.g. the
 * IMAGE / VIDEO / AUDIO modalities of the standalone detector). Controlled:
 * the parent owns the active id.
 */
export function Tabs({
  tabs,
  active,
  onChange,
  ariaLabel,
}: {
  tabs: TabItem[]
  active: string
  onChange: (id: string) => void
  ariaLabel?: string
}) {
  return (
    <div className="tabs" role="tablist" aria-label={ariaLabel}>
      {tabs.map((tab) => {
        const on = tab.id === active
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={on}
            className={`tab${on ? ' tab--active' : ''}`}
            onClick={() => onChange(tab.id)}
          >
            {tab.icon ? <Icon name={tab.icon} size={14} /> : null}
            <span>{tab.label}</span>
          </button>
        )
      })}
    </div>
  )
}
