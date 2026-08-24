/**
 * Screen: Case Detail — the investigation workspace.
 *
 * The primary answers sit at the top: what this case is, the evidence, and the
 * verdict with its reasoning. Analysis is never re-run implicitly — a POST would
 * write fresh audit rows for what should be a read. So the verdict, findings,
 * provenance summary and timeline are shown from the cached analysis when one has
 * been run this session; otherwise the case shows its last recorded verdict band
 * (if any) and a Run analysis call to action.
 *
 * Raw per-evidence metadata and full SHA-256 digests live behind "View technical
 * evidence" — present for the examiner who needs them, out of the way for the
 * glance that just needs the verdict.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { CaseRecord, Evidence, Signal, SignalStatus } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { Section } from '../components/Section'
import { VerdictCard } from '../components/VerdictCard'
import { formatBytes, formatTimestamp, formatTimestampShort, orPlaceholder } from '../lib/format'
import { evidenceFileUrl, isImageMedia } from '../lib/media'
import { statusLabel } from '../lib/signals'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

function priorityTone(priority: string | undefined): PillTone {
  if (priority === 'high') return 'error'
  if (priority === 'low') return 'accent'
  return 'warn'
}

function verdictTone(verdict: string): PillTone {
  if (verdict.includes('MANIPULATED')) return 'error'
  if (verdict.includes('AUTHENTIC')) return 'ok'
  return 'warn'
}

function statusTone(status: SignalStatus | string): PillTone {
  switch (status) {
    case 'OK':
      return 'ok'
    case 'INCONCLUSIVE':
      return 'warn'
    case 'ERROR':
      return 'error'
    case 'UNAVAILABLE':
      return 'unavailable'
    default:
      return 'neutral'
  }
}

/** First sentence of a longer explanation, for a one-line finding row. */
function firstSentence(text: string): string {
  const t = (text || '').trim()
  const m = t.match(/^(.*?[.!?])(\s|$)/)
  return m ? m[1] : t
}

/** Large media preview that degrades to a labelled placeholder if the file can't load. */
function EvidencePreview({ evidence }: { evidence: Evidence }) {
  const [failed, setFailed] = useState(false)
  const canShowImage = isImageMedia(evidence.media_type) && !failed

  return (
    <div className="media-frame">
      {canShowImage ? (
        <img
          src={evidenceFileUrl(evidence.evidence_id)}
          alt={evidence.filename}
          className="media-frame__img"
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="stack" style={{ alignItems: 'center', gap: 8, padding: 'var(--space-5)', color: 'var(--text-muted)' }}>
          <Icon name="document" size={32} />
          <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{evidence.filename}</span>
          <span style={{ fontSize: 'var(--text-xs)' }}>
            {evidence.media_type.toUpperCase()} · {formatBytes(evidence.size_bytes)}
          </span>
          {failed ? (
            <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-faint)' }}>Preview unavailable</span>
          ) : null}
        </div>
      )}
    </div>
  )
}

export function ScreenCaseDetail({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, runAnalysis, analysis } = investigation
  const [activeCase, setActiveCase] = useState<CaseRecord | null>(caseRecord)
  const [caseEvidence, setCaseEvidence] = useState<Evidence[]>(evidence)
  const [loading, setLoading] = useState(!caseRecord && Boolean(caseId))
  const [error, setError] = useState<unknown>(null)

  const currentCaseId = caseId || caseRecord?.case_id || null

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    setLoading(true)
    Promise.all([api.getCase(currentCaseId), api.listEvidence(currentCaseId)])
      .then(([c, ev]) => {
        if (active) {
          setActiveCase(c)
          setCaseEvidence(ev.evidence)
          setLoading(false)
        }
      })
      .catch((err) => {
        if (active) {
          setError(err)
          setLoading(false)
        }
      })
    return () => {
      active = false
    }
  }, [currentCaseId])

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
        <div className="screen__head">
          <h1 className="screen__title">Case detail</h1>
        </div>
        <Empty>No case selected. Choose a case from the list, or create a new one.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View cases
          </button>
          <button type="button" className="btn btn--ghost" onClick={() => onNavigate('intake')}>
            New case
          </button>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="screen" style={{ padding: 'var(--space-6)' }}>
        <Spinner label="Loading case…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="screen">
        <ErrorBanner context="Case detail" error={error} />
      </div>
    )
  }

  const c = activeCase
  const primaryEvidence = caseEvidence[0] || null

  // Only trust the cached analysis if it belongs to the case on screen.
  const a =
    isReady(analysis) && analysis.data.case.case_id === currentCaseId ? analysis.data : null

  // Order findings so assessed signals lead; cap at five for the primary view.
  const keyFindings: Signal[] = a
    ? [...a.signals].sort((x, y) => Number(y.included) - Number(x.included)).slice(0, 5)
    : []

  return (
    <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
      {/* Identity */}
      <div className="screen__head">
        <div className="row" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            {c?.case_number}
          </span>
          <Pill variant="accent">{(c?.status || 'open').toUpperCase()}</Pill>
          {c?.priority ? (
            <Pill variant={priorityTone(c.priority)}>{c.priority.toUpperCase()} PRIORITY</Pill>
          ) : null}
          {c?.latest_verdict ? (
            <Pill variant={verdictTone(c.latest_verdict)}>{c.latest_verdict.replace(/_/g, ' ')}</Pill>
          ) : null}
        </div>
        <h1 className="screen__title" style={{ marginTop: 4 }}>
          {c?.title || 'Untitled case'}
        </h1>
        <p className="screen__lead">
          Examiner: {c?.examiner || 'Unassigned'} · Opened {formatTimestamp(c?.created_at ?? null)} ·{' '}
          {caseEvidence.length} evidence
        </p>
      </div>

      {/* What do I do next */}
      <div className="btn-row">
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            runAnalysis()
            onNavigate('analysis', { caseId: currentCaseId })
          }}
        >
          <Icon name="refresh" size={14} />
          Run analysis
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => onNavigate('provenance', { caseId: currentCaseId })}
        >
          <Icon name="external" size={14} />
          Trace provenance
        </button>
        <button
          type="button"
          className="btn btn--ghost"
          onClick={() => onNavigate('reports', { caseId: currentCaseId })}
        >
          <Icon name="download" size={14} />
          Generate report
        </button>
      </div>

      {/* Evidence beside the verdict */}
      <div className="grid-asymmetric" style={{ gap: 'var(--space-5)' }}>
        <Section title="Evidence">
          {primaryEvidence ? (
            <div className="stack" style={{ gap: 'var(--space-2)' }}>
              <EvidencePreview key={primaryEvidence.evidence_id} evidence={primaryEvidence} />
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 'var(--text-sm)' }}>{primaryEvidence.filename}</span>
                <Pill variant="accent">{primaryEvidence.media_type.toUpperCase()}</Pill>
              </div>
              {caseEvidence.length > 1 ? (
                <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                  +{caseEvidence.length - 1} more evidence item{caseEvidence.length - 1 === 1 ? '' : 's'} in this case
                </span>
              ) : null}
            </div>
          ) : (
            <Empty>No evidence attached to this case.</Empty>
          )}
        </Section>

        <Section title="Verdict">
          {a ? (
            <VerdictCard verdict={a.verdict} />
          ) : c?.latest_verdict ? (
            <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-2)' }}>
              <span className="label">Last recorded verdict</span>
              <Pill variant={verdictTone(c.latest_verdict)}>{c.latest_verdict.replace(/_/g, ' ')}</Pill>
              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0 }}>
                Run analysis to see the reasoning, signal coverage and fusion arithmetic behind this verdict.
              </p>
            </div>
          ) : (
            <Empty>Not yet analysed. Run analysis to assess this evidence — this is not a finding either way.</Empty>
          )}
        </Section>
      </div>

      {/* Findings / provenance / timeline appear only once there is a real analysis to show. */}
      {a ? (
        <>
          <Section title="Key findings">
            {keyFindings.length === 0 ? (
              <Empty>No signals were produced for this case.</Empty>
            ) : (
              <div className="stack" style={{ gap: 'var(--space-2)' }}>
                {keyFindings.map((s) => (
                  <div
                    key={s.signal_id}
                    className="card row"
                    style={{ justifyContent: 'space-between', alignItems: 'center', gap: 'var(--space-3)', padding: 'var(--space-3)' }}
                  >
                    <div className="stack" style={{ gap: 2, minWidth: 0 }}>
                      <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{s.name}</span>
                      <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                        {firstSentence(s.explanation)}
                      </span>
                    </div>
                    <Pill variant={statusTone(s.status)} title={s.status}>
                      {statusLabel(s.status)}
                    </Pill>
                  </div>
                ))}
              </div>
            )}
          </Section>

          <div className="grid-asymmetric" style={{ gap: 'var(--space-5)' }}>
            <Section
              title="Provenance summary"
              aside={
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => onNavigate('provenance', { caseId: currentCaseId })}
                >
                  Trace provenance
                  <Icon name="arrow-right" size={13} />
                </button>
              }
            >
              <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-2)' }}>
                <div className="row" style={{ gap: 'var(--space-4)', flexWrap: 'wrap' }}>
                  <div className="stack" style={{ gap: 2 }}>
                    <span className="label">Instances</span>
                    <span style={{ fontWeight: 700, fontFamily: 'var(--mono)' }}>{a.propagation.instance_count}</span>
                  </div>
                  <div className="stack" style={{ gap: 2 }}>
                    <span className="label">Matched candidates</span>
                    <span style={{ fontWeight: 700, fontFamily: 'var(--mono)' }}>
                      {a.propagation.matched_candidate_count}
                    </span>
                  </div>
                </div>
                {a.origin ? (
                  <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                    {a.origin.label}
                    {a.origin.platform ? ` · ${a.origin.platform}` : ''}
                    {a.origin.timestamp ? ` · ${formatTimestampShort(a.origin.timestamp)}` : ''}
                    {a.origin.is_absolute_origin ? '' : ' — earlier copies may exist outside the corpus.'}
                  </p>
                ) : (
                  <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: '4px 0 0' }}>
                    No prior instance found in the indexed evidence corpus. This is not proof the file is original.
                  </p>
                )}
              </div>
            </Section>

            <Section title="Timeline">
              {a.timeline && a.timeline.length > 0 ? (
                <ol className="timeline__list">
                  {a.timeline.slice(0, 6).map((t, i) => (
                    <li key={`${t.evidence_id}-${i}`} className="timeline__item">
                      <span className="timeline__when">{formatTimestampShort(t.occurred_at)}</span>
                      <span style={{ fontSize: 'var(--text-xs)' }}>{t.description}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <Empty>No dated instances to place on a timeline.</Empty>
              )}
            </Section>
          </div>
        </>
      ) : null}

      {/* Raw detail for the examiner who needs it — never in the primary view. */}
      {primaryEvidence ? (
        <details className="disclosure">
          <summary>
            <Icon name="arrow-right" className="disclosure__chevron" size={13} />
            View technical evidence
          </summary>
          <div className="disclosure__panel stack" style={{ gap: 'var(--space-3)' }}>
            {caseEvidence.map((ev) => (
              <div key={ev.evidence_id} className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-2)' }}>
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                  <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>{ev.filename}</span>
                  <span style={{ fontSize: 'var(--text-2xs)', color: 'var(--text-muted)' }}>
                    {ev.media_type.toUpperCase()} · {ev.mime_type} · {formatBytes(ev.size_bytes)}
                  </span>
                </div>
                <dl className="dl">
                  <dt>Evidence ID</dt>
                  <dd className="mono">{ev.evidence_id}</dd>
                  <dt>Ingested</dt>
                  <dd>{formatTimestamp(ev.ingested_at)}</dd>
                  <dt>Dimensions</dt>
                  <dd>{ev.width && ev.height ? `${ev.width} × ${ev.height}` : orPlaceholder(ev.format)}</dd>
                  <dt>Indexed</dt>
                  <dd>{ev.indexed ? 'Yes' : 'No'}</dd>
                  {ev.phash ? (
                    <>
                      <dt>pHash</dt>
                      <dd className="mono break-all">{ev.phash}</dd>
                    </>
                  ) : null}
                  {ev.dhash ? (
                    <>
                      <dt>dHash</dt>
                      <dd className="mono break-all">{ev.dhash}</dd>
                    </>
                  ) : null}
                </dl>
                <div className="hash">
                  <span className="hash__label">SHA-256</span>
                  <code className="hash__value break-all">{ev.sha256}</code>
                  <CopyButton value={ev.sha256} title="Copy SHA-256 digest" />
                </div>
              </div>
            ))}
            {c?.complaint_reference ? (
              <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0 }}>
                Complaint reference: <span style={{ fontFamily: 'var(--mono)' }}>{c.complaint_reference}</span>
              </p>
            ) : null}
          </div>
        </details>
      ) : null}
    </div>
  )
}
