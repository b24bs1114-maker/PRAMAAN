import type { ReactNode } from 'react'

/**
 * A flat titled region - the default way content is grouped on every screen.
 *
 * This deliberately replaces the old bordered "panel". The console stacked
 * same-weight boxes, which flattened the hierarchy: an operator could not tell
 * the verdict from the detector build string at a glance. A Section is just a
 * heading with a hairline under it and a body beneath - proximity and the type
 * scale carry the grouping, not a container. Elevation (`.card`, the verdict
 * hero) is reserved for the few surfaces that actually carry a decision.
 */
export function Section({
  title,
  aside,
  children,
  headingLevel = 2,
  className,
}: {
  title?: ReactNode
  aside?: ReactNode
  children: ReactNode
  /** Kept sequential per screen so the heading outline stays navigable. */
  headingLevel?: 2 | 3
  className?: string
}) {
  const Heading = headingLevel === 2 ? 'h2' : 'h3'
  return (
    <section className={`section${className ? ` ${className}` : ''}`}>
      {title ? (
        <div className="section__head">
          <Heading className="section__title">{title}</Heading>
          {aside ? <div className="section__aside">{aside}</div> : null}
        </div>
      ) : null}
      <div className="section__body">{children}</div>
    </section>
  )
}
