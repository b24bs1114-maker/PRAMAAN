/**
 * Button primitive, bound one-to-one to the `.btn` classes in global.css.
 *
 * Everything else that used to live here (Panel, Metric, Pager, Tabs, …) had no
 * live render site after the dead component files were removed; this file now
 * holds only what the live tree imports. A shared base is not worth
 * re-abstracting until a second consumer exists.
 */

import type { ButtonHTMLAttributes } from 'react'
import { Icon, type IconName } from './Icon'
import { cx } from '../lib/cx'

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
