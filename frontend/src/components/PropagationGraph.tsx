import { useMemo } from 'react'
import type { PropagationGraph as Graph, PropagationNode } from '../api/types'
import { orPlaceholder } from '../lib/format'
import { Icon } from './Icon'

/**
 * Propagation graph.
 *
 * Nodes are shape-coded, not colour-coded: a diamond is the earliest known
 * instance, a square is the case evidence, a circle is any other indexed
 * instance. Colour is a secondary cue only, so the graph survives being read by
 * someone with a colour vision deficiency or printed in greyscale.
 *
 * The instance table beside this graph is its accessible equivalent, so the SVG
 * is marked up as a single labelled image rather than an interactive tree that a
 * screen reader would have to walk.
 *
 * Layout is deterministic: generation drives the column, so a copy that is three
 * re-encodings removed from the earliest instance always sits three columns to
 * the right. Nothing here is force-directed or randomised.
 */

const WIDTH = 640
const NODE_R = 9
const COL_GAP = 118
const ROW_GAP = 52
const PAD_X = 48
const PAD_Y = 34

interface Placed {
  node: PropagationNode
  x: number
  y: number
}

export function PropagationGraph({
  graph,
  earliestEvidenceId,
}: {
  graph: Graph
  earliestEvidenceId: string | null
}) {
  const { placed, height, edges } = useMemo(() => {
    // Column per generation; instances with no recorded generation go last, in
    // their own column, rather than being guessed into the sequence.
    const generations = Array.from(
      new Set(graph.nodes.map((n) => n.generation).filter((g): g is number => g !== null)),
    ).sort((a, b) => a - b)

    const columnOf = (node: PropagationNode): number =>
      node.generation === null ? generations.length : generations.indexOf(node.generation)

    const byColumn = new Map<number, PropagationNode[]>()
    for (const node of graph.nodes) {
      const col = columnOf(node)
      const bucket = byColumn.get(col)
      if (bucket) bucket.push(node)
      else byColumn.set(col, [node])
    }

    const tallest = Math.max(1, ...Array.from(byColumn.values(), (b) => b.length))
    const svgHeight = PAD_Y * 2 + (tallest - 1) * ROW_GAP

    const positions = new Map<string, Placed>()
    for (const [col, bucket] of byColumn) {
      // Centre each column vertically so the graph reads as a spine.
      const offset = (svgHeight - (bucket.length - 1) * ROW_GAP) / 2
      bucket.forEach((node, row) => {
        positions.set(node.evidence_id, {
          node,
          x: PAD_X + col * COL_GAP,
          y: offset + row * ROW_GAP,
        })
      })
    }

    const drawnEdges = graph.edges
      .map((edge) => {
        const from = positions.get(edge.source)
        const to = positions.get(edge.target)
        return from && to ? { edge, from, to } : null
      })
      .filter((e): e is NonNullable<typeof e> => e !== null)

    return { placed: Array.from(positions.values()), height: svgHeight, edges: drawnEdges }
  }, [graph])

  const columns = Math.max(1, ...placed.map((p) => (p.x - PAD_X) / COL_GAP + 1))
  const width = Math.max(WIDTH, PAD_X * 2 + (columns - 1) * COL_GAP)

  return (
    <>
      <svg
        className="graph"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Propagation graph: ${graph.node_count} instances connected by ${graph.edge_count} relationships. The table beside this graph lists the same instances.`}
      >
        {edges.map(({ edge, from, to }, i) => (
          <line
            key={`${edge.source}-${edge.target}-${edge.relation}-${i}`}
            className="graph__edge"
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            // A recorded-metadata link is dashed; a hash-verified link is solid.
            // The distinction matters: one is a claim, the other is a measurement.
            strokeDasharray={edge.verified_by_pramaan ? undefined : '3 3'}
            style={{ animation: 'edgeDraw 300ms cubic-bezier(0.16, 1, 0.3, 1) backwards', animationDelay: `${i * 25}ms` }}
          />
        ))}

        {placed.map(({ node, x, y }, idx) => {
          const isEarliest = node.evidence_id === earliestEvidenceId
          const isSubject = node.is_case_evidence
          const cls = `graph__node${
            isEarliest ? ' graph__node--earliest' : isSubject ? ' graph__node--subject' : ''
          }`
          const role = isEarliest
            ? 'earliest known instance'
            : isSubject
              ? 'evidence under examination'
              : 'indexed instance'
          return (
            <g
              key={node.evidence_id}
              className="graph__group"
              style={{ animation: 'nodePop 240ms cubic-bezier(0.16, 1, 0.3, 1) backwards', animationDelay: `${idx * 35}ms` }}
            >
              {/* Hover/focus tooltip; the table remains the accessible equivalent. */}
              <title>{`${node.filename} - ${role} · ${orPlaceholder(node.platform)} · gen ${orPlaceholder(
                node.generation,
              )}`}</title>
              {isEarliest ? (
                // Diamond: earliest known instance in the indexed evidence corpus.
                <rect
                  className={cls}
                  x={x - NODE_R}
                  y={y - NODE_R}
                  width={NODE_R * 2}
                  height={NODE_R * 2}
                  transform={`rotate(45 ${x} ${y})`}
                />
              ) : isSubject ? (
                // Square: the evidence under examination.
                <rect
                  className={cls}
                  x={x - NODE_R}
                  y={y - NODE_R}
                  width={NODE_R * 2}
                  height={NODE_R * 2}
                />
              ) : (
                // Circle: any other indexed instance.
                <circle className={cls} cx={x} cy={y} r={NODE_R} />
              )}
              <text className="graph__label" x={x} y={y + NODE_R + 12} textAnchor="middle">
                {orPlaceholder(node.platform)}
              </text>
              <text className="graph__label" x={x} y={y + NODE_R + 22} textAnchor="middle">
                gen {orPlaceholder(node.generation)}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="graph-legend">
        <span className="graph-legend__item">
          <Icon name="diamond" size={12} className="graph-legend__swatch graph-legend__swatch--earliest" />
          earliest known instance in the indexed evidence corpus
        </span>
        <span className="graph-legend__item">
          <Icon name="square" size={12} className="graph-legend__swatch graph-legend__swatch--subject" />
          the evidence under examination
        </span>
        <span className="graph-legend__item">
          <Icon name="dot" size={12} className="graph-legend__swatch" />
          another instance in the indexed corpus
        </span>
        <span className="graph-legend__item">
          Solid line: link verified by hash comparison. Dashed: recorded metadata claim.
        </span>
      </div>
    </>
  )
}
