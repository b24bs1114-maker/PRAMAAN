/**
 * Provenance presentation: origin, graph, timeline.
 *
 * This is the feature the product is named for, and it is also the easiest place
 * in the app to lie. Three rules are enforced structurally here:
 *
 *  1. **The corpus is not the world.** The earliest row PRAMAAN holds is the
 *     earliest known instance *in the indexed evidence corpus*. When
 *     `is_absolute_origin` is false the card says so in its own heading, not in
 *     small print, and the backend `caveat` is always printed.
 *  2. **Observed is not inferred.** An edge PRAMAAN measured by hash distance is
 *     solid and cyan; an edge asserted by stored lineage metadata is dashed and
 *     grey, and the legend states the difference in words.
 *  3. **A timestamp is only as good as its source.** Every time shown carries
 *     `timestamp_source`, because a platform's claimed upload time and a time
 *     PRAMAAN recorded are different evidence.
 *
 * The graph is laid out here rather than by a library: nodes are real `<button>`
 * elements absolutely positioned over an SVG edge layer, so every node is
 * focusable and selectable with a keyboard. That is the fix for the previous
 * build's non-interactive graph, where node detail was only reachable through
 * the timeline table.
 */

import { useMemo, useState, type ReactNode } from 'react'
import { Icon } from './Icon'
import { HashChip } from './Hash'
import { Button, Callout, Empty, Field, Fields, Panel, StatusPill } from './Primitives'
import { cx } from '../lib/cx'
import {
  formatDistance,
  formatSimilarity,
  formatTimestamp,
  formatTimestampShort,
  NOT_MEASURED,
  orPlaceholder,
} from '../lib/format'
import { humanise } from '../lib/tone'
import type {
  Origin,
  PropagationEdge,
  PropagationGraph,
  PropagationNode,
  TimelineEvent,
} from '../api/types'

/** How a timestamp came to be known, stated rather than assumed. */
function sourceNote(source: string | null | undefined): string {
  if (!source) return 'Timestamp source not recorded.'
  const token = source.toLowerCase()
  if (token.includes('platform')) {
    return 'Time as claimed by the platform record, not independently established by PRAMAAN.'
  }
  if (token.includes('ingest')) {
    return 'Time PRAMAAN recorded when the file entered the corpus.'
  }
  if (token.includes('metadata') || token.includes('exif')) {
    return 'Time read from file metadata, which the file itself asserts.'
  }
  return `Timestamp source: ${humanise(source)}.`
}

/**
 * The origin card.
 *
 * `is_absolute_origin` decides the heading, and it is almost always false: a
 * corpus search cannot rule out earlier copies it never indexed. So the default
 * heading is "earliest known instance in indexed evidence corpus" and the card
 * spends its most prominent line saying what that does *not* mean.
 */
export function OriginCard({ origin, actions }: { origin: Origin | null; actions?: ReactNode }) {
  if (!origin) {
    return (
      <div className="origin origin--none">
        <div className="origin__top">
          <div className="origin__mark">
            <Icon name="search" size={22} />
          </div>
          <div>
            <div className="origin__label">No earlier instance found in the indexed corpus</div>
            <div className="origin__filename">No corpus match</div>
          </div>
          {actions ? <div className="origin__actions">{actions}</div> : null}
        </div>
        <div className="origin__body">
          <p className="origin__caveat">
            Nothing in PRAMAAN's indexed evidence corpus matched this file closely enough to be treated as
            an earlier instance. That is not evidence of originality: the corpus is the set of files
            PRAMAAN has indexed, not the internet, and an earlier copy may exist outside it.
          </p>
        </div>
      </div>
    )
  }

  const absolute = origin.is_absolute_origin

  return (
    <div className={cx('origin', 'origin--found')}>
      <div className="origin__top">
        <div className="origin__mark">
          <Icon name="target" size={22} />
        </div>
        <div className="origin__titles">
          <div className="origin__label">
            {absolute ? origin.label : 'Earliest known instance in indexed evidence corpus'}
          </div>
          <div className="origin__filename">{origin.filename}</div>
          <div className="origin__ids">
            <span className="mono">{origin.evidence_id}</span>
            {origin.is_synthetic ? (
              <StatusPill tone="rare" icon="flag" title="This corpus row is demonstration data.">
                Synthetic corpus row
              </StatusPill>
            ) : null}
          </div>
        </div>
        {actions ? <div className="origin__actions">{actions}</div> : null}
      </div>

      <div className="origin__body">
        <p className="origin__caveat">{origin.caveat}</p>

        <Fields variant="wide">
          <Field
            label="Observed at"
            value={orPlaceholder(origin.timestamp ? formatTimestamp(origin.timestamp) : null)}
            mono
            unmeasured={!origin.timestamp}
            note={sourceNote(origin.timestamp_source)}
          />
          <Field
            label="Platform"
            value={orPlaceholder(origin.platform ? humanise(origin.platform) : null)}
            unmeasured={!origin.platform}
            note={origin.platform ? 'Platform as recorded on the corpus row.' : undefined}
          />
          <Field
            label="Generation"
            value={origin.generation === null ? NOT_MEASURED : `${origin.generation}`}
            mono
            unmeasured={origin.generation === null}
            note="Recorded re-encode depth on this corpus row, where the corpus records it."
          />
          <Field
            label="Distance to case evidence"
            value={formatDistance(origin.distance_to_case_evidence)}
            mono
            unmeasured={origin.distance_to_case_evidence === null}
            note="Perceptual hash distance measured by PRAMAAN between this instance and the evidence under examination."
          />
          <Field
            label="Role"
            value={humanise(origin.role)}
            note={origin.discovered_by ? `Discovered by ${humanise(origin.discovered_by)}.` : undefined}
          />
          <Field
            label="Source id"
            value={orPlaceholder(origin.source_id)}
            mono
            unmeasured={!origin.source_id}
          />
        </Fields>

        {!absolute ? (
          <Callout label="What this does not establish">
            This is the earliest instance PRAMAAN holds, not a proven first publication. The system
            searched only its indexed evidence corpus; it did not search the web, and it cannot exclude
            earlier copies it has never seen.
          </Callout>
        ) : null}
      </div>
    </div>
  )
}

/* --- Graph layout ---------------------------------------------------------
   Laid out by generation: one lane per recorded generation, nodes stacked
   within their lane. A node whose generation the corpus never recorded goes in
   an explicit "Generation not recorded" lane rather than being guessed into a
   numbered one. */

const NODE_W = 188
const LANE_W = 236
const ROW_H = 132
const PAD_X = 20
const PAD_Y = 34

interface Placed {
  node: PropagationNode
  x: number
  y: number
  height: number
}

interface Lane {
  key: string
  label: string
  x: number
}

interface Layout {
  placed: Placed[]
  lanes: Lane[]
  width: number
  height: number
  byId: Map<string, Placed>
}

function layoutGraph(nodes: PropagationNode[]): Layout {
  // Group by generation, keeping "not recorded" as its own bucket at the end.
  const buckets = new Map<string, PropagationNode[]>()
  for (const node of nodes) {
    const key = node.generation === null ? 'unknown' : `g${node.generation}`
    const list = buckets.get(key)
    if (list) list.push(node)
    else buckets.set(key, [node])
  }

  const keys = Array.from(buckets.keys()).sort((a, b) => {
    if (a === 'unknown') return 1
    if (b === 'unknown') return -1
    return Number(a.slice(1)) - Number(b.slice(1))
  })

  const lanes: Lane[] = []
  const placed: Placed[] = []
  let tallest = 0

  keys.forEach((key, laneIndex) => {
    const list = buckets.get(key) ?? []
    const x = PAD_X + laneIndex * LANE_W
    lanes.push({
      key,
      label: key === 'unknown' ? 'Generation not recorded' : `Generation ${key.slice(1)}`,
      x,
    })

    // Case evidence first within a lane, then earliest, then by filename, so
    // the reading order inside a column is stable across renders.
    const ordered = [...list].sort((a, b) => {
      if (a.is_case_evidence !== b.is_case_evidence) return a.is_case_evidence ? -1 : 1
      return a.filename.localeCompare(b.filename)
    })

    ordered.forEach((node, rowIndex) => {
      const height = 104
      placed.push({ node, x, y: PAD_Y + rowIndex * ROW_H, height })
    })
    tallest = Math.max(tallest, ordered.length)
  })

  const byId = new Map(placed.map((p) => [p.node.evidence_id, p]))

  return {
    placed,
    lanes,
    width: PAD_X * 2 + Math.max(keys.length, 1) * LANE_W - (LANE_W - NODE_W),
    height: PAD_Y * 2 + Math.max(tallest, 1) * ROW_H,
    byId,
  }
}

/** An orthogonal connector, so crossing edges stay readable at density. */
function edgePath(from: Placed, to: Placed): string {
  const x1 = from.x + NODE_W
  const y1 = from.y + from.height / 2
  const x2 = to.x
  const y2 = to.y + to.height / 2
  if (x2 <= x1) {
    // Same lane or backwards: route below both nodes rather than through them.
    const drop = Math.max(from.y + from.height, to.y + to.height) + 16
    return `M${x1} ${y1} L${x1 + 12} ${y1} L${x1 + 12} ${drop} L${x2 - 12} ${drop} L${x2 - 12} ${y2} L${x2} ${y2}`
  }
  const mid = x1 + (x2 - x1) / 2
  return `M${x1} ${y1} L${mid} ${y1} L${mid} ${y2} L${x2} ${y2}`
}

/** The role label shown on a node, using the backend's own token. */
function nodeRole(node: PropagationNode, originId: string | null): string {
  if (node.is_case_evidence) return 'Case evidence'
  if (originId && node.evidence_id === originId) return 'Earliest known'
  return humanise(node.role)
}

/**
 * The propagation graph.
 *
 * Every node is a button; selecting one highlights the edges that touch it and
 * raises the detail panel beside the canvas. Nothing here computes a
 * relationship: `verified_by_pramaan` on each edge decides observed vs inferred,
 * and the legend says what each means.
 */
export function ProvenanceGraph({
  graph,
  originId,
  relations,
  onOpenEvidence,
}: {
  graph: PropagationGraph
  originId?: string | null
  /** `PropagationResponse.relations`: the backend's own glossary for edge tokens. */
  relations?: Record<string, string>
  onOpenEvidence?: (evidenceId: string) => void
}) {
  const [selected, setSelected] = useState<string | null>(null)
  const layout = useMemo(() => layoutGraph(graph.nodes), [graph.nodes])

  if (graph.nodes.length === 0) {
    return (
      <Empty
        icon="sitemap"
        title="No propagation graph"
        detail="The backend returned no instances for this evidence, so there is no graph to draw. This is an absence of corpus matches, not a finding about the file."
      />
    )
  }

  const active = selected ? (layout.byId.get(selected) ?? null) : null
  const touching = (edge: PropagationEdge) =>
    selected !== null && (edge.source === selected || edge.target === selected)

  return (
    <div className="pgraph-wrap">
      <div className="pgraph">
        <div
          className="pgraph__canvas"
          style={{ width: layout.width, height: layout.height }}
          role="group"
          aria-label={`Propagation graph, ${graph.node_count} instances and ${graph.edge_count} relationships`}
        >
          {layout.lanes.map((lane) => (
            <div key={lane.key} className="pgraph__lane" style={{ left: lane.x - 12 }}>
              <span className="pgraph__lane-label">{lane.label}</span>
            </div>
          ))}

          <svg className="pgraph__edges" width={layout.width} height={layout.height} aria-hidden="true">
            {graph.edges.map((edge, i) => {
              const from = layout.byId.get(edge.source)
              const to = layout.byId.get(edge.target)
              if (!from || !to) return null
              return (
                <path
                  key={`${edge.source}-${edge.target}-${i}`}
                  className={cx(
                    'pgraph__edge',
                    edge.verified_by_pramaan ? 'pgraph__edge--observed' : 'pgraph__edge--inferred',
                    touching(edge) && 'pgraph__edge--active',
                  )}
                  d={edgePath(from, to)}
                />
              )
            })}
          </svg>

          {layout.placed.map(({ node, x, y }) => {
            const isOrigin = originId != null && node.evidence_id === originId
            return (
              <button
                key={node.evidence_id}
                type="button"
                className={cx(
                  'pnode',
                  node.is_case_evidence && 'pnode--subject',
                  isOrigin && 'pnode--earliest',
                  selected === node.evidence_id && 'pnode--active',
                )}
                style={{ left: x, top: y }}
                aria-pressed={selected === node.evidence_id}
                onClick={() =>
                  setSelected((current) => (current === node.evidence_id ? null : node.evidence_id))
                }
              >
                <span className="pnode__top">
                  <Icon
                    name={node.is_case_evidence ? 'evidence' : isOrigin ? 'target' : 'layers'}
                    size={13}
                  />
                  <span className="pnode__role">{nodeRole(node, originId ?? null)}</span>
                </span>
                <span className="pnode__name" title={node.filename}>
                  {node.filename}
                </span>
                <span className="pnode__rows">
                  <span className="pnode__row">
                    <span className="pnode__key">Platform</span>
                    <span className="pnode__val">
                      {node.platform ? humanise(node.platform) : NOT_MEASURED}
                    </span>
                  </span>
                  <span className="pnode__row">
                    <span className="pnode__key">Distance</span>
                    <span className="pnode__val">{formatDistance(node.distance_to_case_evidence)}</span>
                  </span>
                  <span className="pnode__row">
                    <span className="pnode__key">Seen</span>
                    <span className="pnode__val">
                      {node.timestamp ? formatTimestampShort(node.timestamp) : NOT_MEASURED}
                    </span>
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      </div>

      <div className="pgraph__legend">
        <span className="pgraph__legend-item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="pgraph__edge pgraph__edge--observed" />
          </svg>
          Observed by PRAMAAN — established by perceptual hash match
        </span>
        <span className="pgraph__legend-item">
          <svg width="26" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="26" y2="4" className="pgraph__edge pgraph__edge--inferred" />
          </svg>
          Inferred from recorded lineage metadata — asserted, not measured
        </span>
        <span className="pgraph__legend-item">
          <span className="pgraph__legend-swatch pgraph__legend-swatch--subject" />
          Evidence under examination
        </span>
        <span className="pgraph__legend-item">
          <span className="pgraph__legend-swatch pgraph__legend-swatch--earliest" />
          Earliest known instance in corpus
        </span>
      </div>

      {active ? (
        <NodeDetail
          node={active.node}
          edges={graph.edges.filter((e) => e.source === active.node.evidence_id || e.target === active.node.evidence_id)}
          relations={relations}
          isOrigin={originId != null && active.node.evidence_id === originId}
          onOpenEvidence={onOpenEvidence}
          onClose={() => setSelected(null)}
        />
      ) : (
        <p className="pgraph__hint">
          Select any instance to see its recorded metadata and the relationships that touch it.{' '}
          {graph.node_count} {graph.node_count === 1 ? 'instance' : 'instances'}, {graph.edge_count}{' '}
          {graph.edge_count === 1 ? 'relationship' : 'relationships'}.
        </p>
      )}
    </div>
  )
}

/**
 * The selected node, spelled out.
 *
 * Each relationship line states its basis, because "the same image appeared on
 * another platform" and "a stored record claims this came from that" are
 * different strengths of evidence and the graph's line style alone should not
 * have to carry that.
 */
function NodeDetail({
  node,
  edges,
  relations,
  isOrigin,
  onOpenEvidence,
  onClose,
}: {
  node: PropagationNode
  edges: PropagationEdge[]
  relations?: Record<string, string>
  isOrigin: boolean
  onOpenEvidence?: (evidenceId: string) => void
  onClose: () => void
}) {
  return (
    <Panel
      title={node.filename}
      subtitle={
        <>
          {nodeRole(node, isOrigin ? node.evidence_id : null)} · <span className="mono">{node.evidence_id}</span>
        </>
      }
      actions={
        <>
          {onOpenEvidence && node.is_case_evidence ? (
            <Button size="sm" variant="ghost" icon="evidence" onClick={() => onOpenEvidence(node.evidence_id)}>
              Open evidence
            </Button>
          ) : null}
          <Button size="sm" variant="bare" icon="close" iconOnly onClick={onClose}>
            Clear selection
          </Button>
        </>
      }
    >
      <Fields variant="wide">
        <Field
          label="Observed at"
          value={orPlaceholder(node.timestamp ? formatTimestamp(node.timestamp) : null)}
          mono
          unmeasured={!node.timestamp}
          note={sourceNote(node.timestamp_source)}
        />
        <Field
          label="Platform"
          value={orPlaceholder(node.platform ? humanise(node.platform) : null)}
          unmeasured={!node.platform}
        />
        <Field
          label="Generation"
          value={node.generation === null ? NOT_MEASURED : `${node.generation}`}
          mono
          unmeasured={node.generation === null}
        />
        <Field
          label="Transformation"
          value={orPlaceholder(node.transformation ? humanise(node.transformation) : null)}
          unmeasured={!node.transformation}
          note={node.transformation ? 'Transformation recorded on this corpus row.' : undefined}
        />
        <Field
          label="Distance to case evidence"
          value={formatDistance(node.distance_to_case_evidence)}
          mono
          unmeasured={node.distance_to_case_evidence === null}
          note="Perceptual hash distance measured by PRAMAAN."
        />
        <Field
          label="Similarity to case evidence"
          value={
            node.similarity_to_case_evidence === null
              ? NOT_MEASURED
              : formatSimilarity(node.similarity_to_case_evidence)
          }
          mono
          unmeasured={node.similarity_to_case_evidence === null}
        />
        <Field label="SHA-256" value={<HashChip value={node.sha256} algo={null} length={20} />} />
        <Field
          label="Discovered by"
          value={orPlaceholder(node.discovered_by ? humanise(node.discovered_by) : null)}
          unmeasured={!node.discovered_by}
        />
      </Fields>

      {node.is_synthetic ? (
        <Callout label="Synthetic corpus row">
          This instance is demonstration data held in the corpus. It is not seized evidence and must not be
          cited as such.
        </Callout>
      ) : null}

      <div className="pgraph__rels">
        <div className="eyebrow">
          Relationships touching this instance · {edges.length}
        </div>
        {edges.length === 0 ? (
          <p className="pgraph__hint">
            No recorded relationship connects this instance to another in the returned graph.
          </p>
        ) : (
          <ul className="pgraph__rel-list">
            {edges.map((edge, i) => (
              <li key={`${edge.source}-${edge.target}-${i}`} className="pgraph__rel">
                <StatusPill tone={edge.verified_by_pramaan ? 'accent' : 'neutral'}>
                  {edge.verified_by_pramaan ? 'Observed' : 'Inferred'}
                </StatusPill>
                <span className="pgraph__rel-text">
                  <span className="mono">{edge.source === node.evidence_id ? 'this' : edge.source}</span>{' '}
                  <span className="pgraph__rel-arrow" aria-hidden="true">
                    →
                  </span>{' '}
                  <span className="mono">{edge.target === node.evidence_id ? 'this' : edge.target}</span>
                  {' · '}
                  {relations?.[edge.relation] ?? humanise(edge.relation)}
                </span>
                <span className="pgraph__rel-basis">
                  {edge.basis}
                  {edge.transformation ? ` · ${humanise(edge.transformation)}` : ''}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  )
}

/**
 * The propagation timeline.
 *
 * Rows with no recorded time are not silently dropped and not silently sorted to
 * the end of the dated ones as though they were latest -- they are collected
 * into an explicitly labelled undated group, because "we do not know when" is a
 * finding.
 */
export function ProvenanceTimeline({
  events,
  originId,
  caseEvidenceIds,
}: {
  events: TimelineEvent[]
  originId?: string | null
  /** Ids belonging to the case, so the subject can be marked in the gutter. */
  caseEvidenceIds?: string[]
}) {
  const dated = events.filter((e) => !!e.occurred_at)
  const undated = events.filter((e) => !e.occurred_at)

  if (events.length === 0) {
    return (
      <Empty
        icon="clock"
        title="No timeline events"
        detail="The backend returned no dated instances for this evidence. No chronology can be drawn from an empty set."
      />
    )
  }

  const row = (event: TimelineEvent, index: number) => {
    const isOrigin = originId != null && event.evidence_id === originId
    const isSubject = caseEvidenceIds?.includes(event.evidence_id) ?? false
    return (
      <div className="timeline__row" key={`${event.evidence_id}-${event.event_type}-${index}`}>
        <div className="timeline__when">
          {event.occurred_at ? formatTimestamp(event.occurred_at) : 'Time not recorded'}
        </div>
        <div className="timeline__gutter">
          <span
            className={cx(
              'timeline__node',
              isOrigin && 'timeline__node--earliest',
              isSubject && 'timeline__node--subject',
            )}
          />
        </div>
        <div className="timeline__content">
          <div className="timeline__title">{event.description}</div>
          <div className="timeline__detail">
            <span>{humanise(event.event_type)}</span>
            <span className="mono">{event.evidence_id}</span>
            {event.platform ? <span>{humanise(event.platform)}</span> : null}
            {event.generation !== null ? <span>generation {event.generation}</span> : null}
            {event.transformation ? <span>{humanise(event.transformation)}</span> : null}
            {event.distance_to_case_evidence !== null ? (
              <span>distance {formatDistance(event.distance_to_case_evidence)}</span>
            ) : null}
            {event.is_synthetic ? <span>synthetic corpus row</span> : null}
          </div>
          <div className="timeline__source">{sourceNote(event.timestamp_source)}</div>
        </div>
      </div>
    )
  }

  return (
    <div className="timeline">
      {dated.map(row)}
      {undated.length > 0 ? (
        <>
          <div className="timeline__break">
            {undated.length} {undated.length === 1 ? 'instance has' : 'instances have'} no recorded time and
            cannot be placed in this chronology
          </div>
          {undated.map((event, i) => row(event, dated.length + i))}
        </>
      ) : null}
    </div>
  )
}

