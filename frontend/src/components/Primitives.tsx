/**
 * Product-agnostic primitives, bound one-to-one to the classes in ui.css.
 *
 * Everything a screen needs to lay out a page lives here, so no screen carries
 * inline `style` for layout or typography. The previous build spread hundreds of
 * inline style objects across ten screens, which is why the same panel had four
 * different paddings depending on which file you opened.
 *
 * Two conventions worth stating:
 *  - `Metric` takes `value: string | null`, not `number`. A null renders the
 *    "not measured" treatment rather than a zero; callers cannot accidentally
 *    coalesce.
 *  - Anything that navigates renders an `<a href>`, not a button, so
 *    cmd-click and "copy link" work and screen readers announce a link.
 */

import {
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
  type Ref,
} from 'react'
import { Icon, type IconName } from './Icon'
import { cx } from '../lib/cx'
import { pillClass, type Tone } from '../lib/tone'
import { ENTER, type EnterVariant } from '../lib/motion'

// --- Panel -------------------------------------------------------------------

export interface PanelProps {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  footer?: ReactNode
  children?: ReactNode
  /** No padding on the body: for tables and rows that own their own insets. */
  flushBody?: boolean
  tightBody?: boolean
  inset?: boolean
  quiet?: boolean
  tallHead?: boolean
  /** Left rail colour, e.g. `var(--danger)`. Marks a panel as carrying a finding. */
  edge?: string
  enter?: EnterVariant
  className?: string
  bodyClassName?: string
  style?: CSSProperties
  id?: string
}

export function Panel({
  title,
  subtitle,
  actions,
  footer,
  children,
  flushBody,
  tightBody,
  inset,
  quiet,
  tallHead,
  edge,
  enter,
  className,
  bodyClassName,
  style,
  id,
}: PanelProps) {
  const headingId = useId()
  const hasHead = Boolean(title || actions)
  return (
    <section
      id={id}
      className={cx(
        'panel',
        inset && 'panel--inset',
        quiet && 'panel--quiet',
        edge && 'panel--edge',
        enter && ENTER[enter],
        className,
      )}
      style={edge ? ({ ...style, '--edge': edge } as CSSProperties) : style}
      aria-labelledby={title ? headingId : undefined}
    >
      {hasHead ? (
        <header className={cx('panel__head', tallHead && 'panel__head--tall')}>
          <div className="panel__titles">
            {title ? (
              <h2 className="panel__title" id={headingId}>
                {title}
              </h2>
            ) : null}
            {subtitle ? <p className="panel__subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="panel__actions">{actions}</div> : null}
        </header>
      ) : null}
      {children !== undefined && children !== null ? (
        <div
          className={cx(
            'panel__body',
            flushBody && 'panel__body--flush',
            tightBody && 'panel__body--tight',
            bodyClassName,
          )}
        >
          {children}
        </div>
      ) : null}
      {footer ? <footer className="panel__foot">{footer}</footer> : null}
    </section>
  )
}

// --- Screen scaffold ---------------------------------------------------------

export function Screen({
  eyebrow,
  eyebrowIcon,
  title,
  lead,
  actions,
  children,
  className,
}: {
  eyebrow?: ReactNode
  eyebrowIcon?: IconName
  title: ReactNode
  lead?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cx('screen', className)}>
      <header className="screen__head">
        <div className="screen__titles">
          {eyebrow ? (
            <p className="screen__eyebrow">
              {eyebrowIcon ? <Icon name={eyebrowIcon} size={13} /> : null}
              {eyebrow}
            </p>
          ) : null}
          <h1 className="screen__title">{title}</h1>
          {lead ? <p className="screen__lead">{lead}</p> : null}
        </div>
        {actions ? <div className="screen__actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  )
}

export function Section({
  title,
  hint,
  link,
  children,
  className,
}: {
  title?: ReactNode
  hint?: ReactNode
  link?: { label: string; href: string }
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cx('section', className)}>
      {title || hint || link ? (
        <div className="section__head">
          {title ? <h2 className="section__title">{title}</h2> : null}
          {hint ? <span className="section__hint">{hint}</span> : null}
          {link ? (
            <a className="section__link" href={link.href}>
              {link.label}
              <Icon name="arrow-right" size={12} />
            </a>
          ) : null}
        </div>
      ) : null}
      {children}
    </section>
  )
}

// --- Fields ------------------------------------------------------------------

export function Fields({
  children,
  variant,
  className,
}: {
  children: ReactNode
  variant?: 'two' | 'wide' | 'ruled'
  className?: string
}) {
  return (
    <dl
      className={cx(
        'fields',
        variant === 'two' && 'fields--2',
        variant === 'wide' && 'fields--wide',
        variant === 'ruled' && 'fields--ruled',
        className,
      )}
    >
      {children}
    </dl>
  )
}

/**
 * One named value. `value` is a ReactNode so callers can pass a pill or a hash
 * chip, but the "not measured" look is reached by passing `unmeasured`, which
 * keeps the decision in one place instead of at every call site.
 */
export function Field({
  label,
  labelIcon,
  value,
  note,
  mono,
  strong,
  unmeasured,
}: {
  label: ReactNode
  labelIcon?: IconName
  value: ReactNode
  note?: ReactNode
  mono?: boolean
  strong?: boolean
  unmeasured?: boolean
}) {
  return (
    <div className="field">
      <dt className="field__label">
        {labelIcon ? <Icon name={labelIcon} size={12} /> : null}
        {label}
      </dt>
      <dd
        className={cx(
          'field__value',
          mono && 'field__value--mono',
          strong && 'field__value--strong',
          unmeasured && 'unmeasured',
        )}
      >
        {value}
        {note ? <div className="field__note">{note}</div> : null}
      </dd>
    </div>
  )
}

// --- Metric ------------------------------------------------------------------

/**
 * A single figure. `value` of null means the backend did not measure it, and is
 * rendered as the placeholder in the faint "unmeasured" treatment -- never 0.
 */
export function Metric({
  label,
  value,
  note,
  noneLabel = 'Not measured',
}: {
  label: ReactNode
  value: string | null
  note?: ReactNode
  noneLabel?: string
}) {
  return (
    <div className="metric">
      <span className={cx('metric__value', value === null && 'metric__value--none')}>
        {value === null ? noneLabel : value}
      </span>
      <span className="metric__label">{label}</span>
      {note ? <span className="metric__note">{note}</span> : null}
    </div>
  )
}

// --- Status pill -------------------------------------------------------------

export function StatusPill({
  tone = 'neutral',
  children,
  icon,
  dot,
  live,
  large,
  className,
  title,
}: {
  tone?: Tone
  children: ReactNode
  icon?: IconName
  dot?: boolean
  live?: boolean
  large?: boolean
  className?: string
  title?: string
}) {
  return (
    <span className={pillClass(tone, cx(large && 'pill--lg', className))} title={title}>
      {dot ? <span className={cx('pill__dot', live && 'pill__dot--live')} /> : null}
      {icon ? <Icon name={icon} size={large ? 13 : 11} strokeWidth={2.1} /> : null}
      {children}
    </span>
  )
}

// --- Buttons -----------------------------------------------------------------

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'default' | 'primary' | 'ghost' | 'bare' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  icon?: IconName
  iconRight?: IconName
  block?: boolean
  /** Icon-only: `children` becomes the accessible label instead of visible text. */
  iconOnly?: boolean
  busy?: boolean
}

export function Button({
  variant = 'default',
  size = 'md',
  icon,
  iconRight,
  block,
  iconOnly,
  busy,
  children,
  className,
  type = 'button',
  disabled,
  ...rest
}: ButtonProps) {
  const glyphSize = size === 'sm' ? 13 : 15
  return (
    <button
      {...rest}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      aria-label={iconOnly && typeof children === 'string' ? children : rest['aria-label']}
      className={cx(
        'btn',
        variant !== 'default' && `btn--${variant}`,
        size === 'sm' && 'btn--sm',
        size === 'lg' && 'btn--lg',
        block && 'btn--block',
        iconOnly && 'btn--icon',
        className,
      )}
    >
      {busy ? <span className="spinner" /> : icon ? <Icon name={icon} size={glyphSize} /> : null}
      {iconOnly ? null : children}
      {iconRight && !busy ? <Icon name={iconRight} size={glyphSize} /> : null}
    </button>
  )
}

export function ButtonRow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('btn-row', className)}>{children}</div>
}

// --- Feedback ----------------------------------------------------------------

export type BannerKind = 'info' | 'ok' | 'warn' | 'error'

const BANNER_ICON: Record<BannerKind, IconName> = {
  info: 'info',
  ok: 'check',
  warn: 'alert',
  error: 'error',
}

export function Banner({
  kind = 'info',
  title,
  children,
  meta,
  actions,
  className,
}: {
  kind?: BannerKind
  title?: ReactNode
  children?: ReactNode
  /** Machine detail: request id, endpoint, hash. Rendered monospace. */
  meta?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cx('banner', `banner--${kind}`, className)}
      role={kind === 'error' ? 'alert' : 'status'}
    >
      <span className="banner__icon">
        <Icon name={BANNER_ICON[kind]} size={17} />
      </span>
      <div className="banner__content">
        {title ? <p className="banner__title">{title}</p> : null}
        {children ? <div className="banner__detail">{children}</div> : null}
        {meta ? <p className="banner__meta">{meta}</p> : null}
        {actions ? <ButtonRow>{actions}</ButtonRow> : null}
      </div>
    </div>
  )
}

/** The dashed box that carries a forensic caveat next to a finding. */
export function Callout({
  label,
  children,
  className,
}: {
  label?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cx('callout', className)}>
      {label ? <span className="callout__label">{label}</span> : null}
      {children}
    </div>
  )
}

/**
 * Empty state. `detail` should say what would make the panel fill, and where a
 * filter is active it should name the filter -- "no cases match CRITICAL" is
 * actionable, "no data" is not.
 */
export function Empty({
  icon = 'search',
  title,
  detail,
  action,
  tight,
}: {
  icon?: IconName
  title: ReactNode
  detail?: ReactNode
  action?: ReactNode
  tight?: boolean
}) {
  return (
    <div className={cx('empty', tight && 'empty--tight')}>
      <span className="empty__mark">
        <Icon name={icon} size={20} />
      </span>
      <p className="empty__title">{title}</p>
      {detail ? <p className="empty__detail">{detail}</p> : null}
      {action}
    </div>
  )
}

export function LoadingRow({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="loading-row" role="status">
      <span className="spinner" />
      {label}
    </div>
  )
}

export function Skeleton({
  variant = 'text',
  count = 1,
  style,
}: {
  variant?: 'text' | 'line' | 'block'
  count?: number
  style?: CSSProperties
}) {
  return (
    <div className="stack stack--tight" aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className={cx('skeleton', `skeleton--${variant}`)} style={style} />
      ))}
    </div>
  )
}

/**
 * Progress bar. `fraction` null means indeterminate -- used for analysis, whose
 * duration the client genuinely cannot know. A determinate bar is only ever
 * bound to bytes the browser actually sent.
 */
export function Progress({
  fraction,
  label,
}: {
  fraction: number | null
  label?: string
}) {
  const pct = fraction === null ? null : Math.max(0, Math.min(1, fraction)) * 100
  return (
    <div
      className={cx('progress', pct === null && 'progress--indeterminate')}
      role="progressbar"
      aria-label={label}
      aria-valuenow={pct === null ? undefined : Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="progress__fill" style={pct === null ? undefined : { width: `${pct}%` }} />
    </div>
  )
}

// --- Tabs --------------------------------------------------------------------

export interface TabDef<T extends string> {
  id: T
  label: string
  count?: number | null
  icon?: IconName
}

/**
 * Tabs with real roving-focus keyboard support: arrows move, Home/End jump.
 * The previous build rendered tabs as plain buttons, so a keyboard user had to
 * tab through every one to reach the last panel.
 */
export function Tabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: ReadonlyArray<TabDef<T>>
  active: T
  onChange: (id: T) => void
  label: string
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  const move = (from: number, delta: number) => {
    const next = (from + delta + tabs.length) % tabs.length
    onChange(tabs[next].id)
    refs.current[next]?.focus()
  }

  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, i) => (
        <button
          key={tab.id}
          ref={(el) => {
            refs.current[i] = el
          }}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={tab.id === active}
          aria-controls={`panel-${tab.id}`}
          tabIndex={tab.id === active ? 0 : -1}
          className={cx('tab', tab.id === active && 'tab--active')}
          onClick={() => onChange(tab.id)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowRight') move(i, 1)
            else if (e.key === 'ArrowLeft') move(i, -1)
            else if (e.key === 'Home') move(i, -i)
            else if (e.key === 'End') move(i, tabs.length - 1 - i)
            else return
            e.preventDefault()
          }}
        >
          {tab.icon ? <Icon name={tab.icon} size={14} /> : null}
          {tab.label}
          {typeof tab.count === 'number' ? <span className="tab__count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  )
}

export function TabPanel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} className="m-fade">
      {children}
    </div>
  )
}

// --- Segmented control -------------------------------------------------------

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  block,
  size,
}: {
  options: ReadonlyArray<{ id: T; label: string; icon?: IconName; title?: string }>
  value: T
  onChange: (id: T) => void
  label: string
  block?: boolean
  size?: 'sm'
}) {
  return (
    <div
      className={cx('segmented', block && 'segmented--block')}
      role="radiogroup"
      aria-label={label}
    >
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          role="radio"
          aria-checked={opt.id === value}
          title={opt.title}
          className={cx('segmented__btn', opt.id === value && 'segmented__btn--active')}
          onClick={() => onChange(opt.id)}
        >
          {opt.icon ? <Icon name={opt.icon} size={size === 'sm' ? 12 : 13} /> : null}
          {opt.label}
        </button>
      ))}
    </div>
  )
}

// --- Switch ------------------------------------------------------------------

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean
  onChange: (next: boolean) => void
  label: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      className="switch"
      onClick={() => onChange(!checked)}
    >
      <span className="switch__thumb" />
    </button>
  )
}

// --- Disclosure --------------------------------------------------------------

export function Disclosure({
  title,
  count,
  children,
  defaultOpen = false,
}: {
  title: ReactNode
  count?: number
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const panelId = useId()
  return (
    <div className="disclosure">
      <button
        type="button"
        className="disclosure__btn"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((o) => !o)}
      >
        {title}
        {typeof count === 'number' ? <span className="tab__count">{count}</span> : null}
        <Icon name="chevron-right" size={14} className="disclosure__chevron" />
      </button>
      {open ? (
        <div className="disclosure__panel" id={panelId}>
          {children}
        </div>
      ) : null}
    </div>
  )
}

// --- Toolbar -----------------------------------------------------------------

export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('toolbar', className)}>{children}</div>
}

export function ToolbarSearch({
  value,
  onChange,
  placeholder,
  label,
  inputRef,
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  label: string
  inputRef?: Ref<HTMLInputElement>
}) {
  return (
    <div className="toolbar__search">
      <Icon name="search" size={14} className="toolbar__search-icon" />
      <input
        ref={inputRef}
        className="input"
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  )
}

// --- Pager -------------------------------------------------------------------

/**
 * Pagination over a client-side list. Present because the case list is expected
 * to reach the hundreds and the previous build rendered every row at once.
 */
export function Pager({
  page,
  pageCount,
  total,
  from,
  to,
  noun,
  onPage,
}: {
  page: number
  pageCount: number
  total: number
  from: number
  to: number
  noun: string
  onPage: (next: number) => void
}) {
  if (pageCount <= 1) return null
  return (
    <div className="pager">
      <span className="pager__status">
        {from}–{to} of {total} {noun}
      </span>
      <div className="pager__controls">
        <Button
          size="sm"
          variant="ghost"
          icon="chevron-left"
          iconOnly
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        >
          Previous page
        </Button>
        <span className="pager__status">
          {page + 1} / {pageCount}
        </span>
        <Button
          size="sm"
          variant="ghost"
          icon="chevron-right"
          iconOnly
          disabled={page >= pageCount - 1}
          onClick={() => onPage(page + 1)}
        >
          Next page
        </Button>
      </div>
    </div>
  )
}

// --- Small bits --------------------------------------------------------------

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

/** CSS-only tooltip. Focusable so the hint is reachable without a pointer. */
export function Tip({ text, children }: { text: string; children: ReactNode }) {
  return (
    <span className="tip" tabIndex={0}>
      {children}
      <span className="tip__bubble" role="tooltip">
        {text}
      </span>
    </span>
  )
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="table-wrap">{children}</div>
}

/** A horizontal set of derived counts, used under a dossier header. */
export function StatRail({
  items,
}: {
  items: ReadonlyArray<{ key: string; value: string | null; title?: string }>
}) {
  return (
    <dl className="statrail">
      {items.map((item) => (
        <div className="statrail__item" key={item.key} title={item.title}>
          <dd className={cx('statrail__val', item.value === null && 'statrail__val--none')}>
            {item.value ?? '—'}
          </dd>
          <dt className="statrail__key">{item.key}</dt>
        </div>
      ))}
    </dl>
  )
}

/** Vertical stack with the token gap scale, so screens never inline a gap. */
export function Stack({
  children,
  gap,
  className,
}: {
  children: ReactNode
  gap?: 'tight' | 'loose'
  className?: string
}) {
  return (
    <div className={cx('stack', gap === 'tight' && 'stack--tight', gap === 'loose' && 'stack--loose', className)}>
      {children}
    </div>
  )
}

export function Row({
  children,
  wrap,
  between,
  top,
  className,
}: {
  children: ReactNode
  wrap?: boolean
  between?: boolean
  top?: boolean
  className?: string
}) {
  return (
    <div
      className={cx(
        'row',
        wrap && 'row--wrap',
        between && 'row--between',
        top && 'row--top',
        className,
      )}
    >
      {children}
    </div>
  )
}

