/**
 * Screen: Reports.
 *
 * The last step of the workflow: turn the analysis into a document. There are
 * two honest outputs, and the screen keeps them distinct:
 *
 *   1. PRINT VIEW (primary) — a clean, human-readable forensic report rendered
 *      in the browser from the analysis the backend already returned, saved to
 *      PDF via the browser's own Print → Save as PDF. This is what an examiner
 *      reads and hands over.
 *
 *   2. SIGNED PDF (archival) — the backend-generated PDF, anchored to the audit
 *      head hash. Secondary, for the case file. Unchanged backend artefact.
 *
 * No fabricated examiner. The name defaults to the case's recorded examiner if
 * there is one, otherwise it is left blank — an unsigned report is honest; a
 * report signed "Officer Singh" by default is not.
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ReportResponse } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill, type PillTone } from '../components/Pill'
import { ReportPrintView } from '../components/ReportPrintView'
import { Section } from '../components/Section'
import { formatBytes, formatTimestamp, orPlaceholder } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel, verdictTone } from '../lib/signals'
import { isReady, type Investigation } from '../state/useInvestigation'

/** Map the verdict tone onto the pill palette (verdictTone has its own enum). */
function verdictPillTone(band: string | null | undefined): PillTone {
  switch (verdictTone(band)) {
    case 'authentic':
      return 'weak-authentic'
    case 'manipulated':
      return 'strong-manipulated'
    default:
      return 'neutral'
  }
}

export function ScreenReports({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, analysis, report, generateReport } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  const [reportsList, setReportsList] = useState<ReportResponse[]>([])
  const [loading, setLoading] = useState(Boolean(currentCaseId))
  const [error, setError] = useState<unknown>(null)
  // Bumped by the Retry action to re-run the load effect below.
  const [reloadNonce, setReloadNonce] = useState(0)

  // Examiner: the case's own recorded examiner, or blank. Never a stand-in name.
  const [examinerName, setExaminerName] = useState(caseRecord?.examiner ?? '')
  const [printing, setPrinting] = useState(false)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<unknown>(null)

  // Keep the examiner field in step with whichever case is loaded.
  useEffect(() => {
    setExaminerName(caseRecord?.examiner ?? '')
  }, [caseRecord?.case_id, caseRecord?.examiner])

  useEffect(() => {
    if (!currentCaseId) return
    let active = true
    setLoading(true)
    setError(null)
    api
      .listReports(currentCaseId)
      .then((data) => {
        if (active) {
          setReportsList(data.reports)
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
  }, [currentCaseId, report.data, reloadNonce])

  if (!currentCaseId) {
    return (
      <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
        <div className="screen__head">
          <h1 className="screen__title">Reports</h1>
          <p className="screen__lead">
            Produce the forensic opinion for a case — a clean printable report, or the signed
            archival PDF.
          </p>
        </div>
        <Empty>No case is selected. Open a case to generate its report.</Empty>
        <div className="btn-row">
          <button type="button" className="btn btn--primary" onClick={() => onNavigate('cases')}>
            View cases
          </button>
        </div>
      </div>
    )
  }

  const analysisData = isReady(analysis) ? analysis.data : null
  const verdict = analysisData?.verdict ?? null
  const generating = report.phase === 'loading'

  const downloadSigned = async (r: ReportResponse) => {
    setDownloadingId(r.report_id)
    setDownloadError(null)
    try {
      const blob = await api.downloadReport(r.download_url)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = r.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      setDownloadError(err)
    } finally {
      setDownloadingId(null)
    }
  }

  return (
    <div className="screen stack" style={{ gap: 'var(--space-6)' }}>
      <div className="screen__head">
        <h1 className="screen__title">Reports</h1>
        <p className="screen__lead">
          The report states where the evidence leans and on how much of the evidence base — it is
          not a legal determination. Open the print view for a clean PDF, or keep the signed copy
          for the archive.
        </p>
      </div>

      {printing ? (
        <ReportPrintView
          investigation={investigation}
          examiner={examinerName}
          onClose={() => setPrinting(false)}
        />
      ) : null}

      {/* PREPARE — what will be certified, who signs it, how to produce it. */}
      <Section
        title="Prepare report"
        aside={
          <span className="mono" style={{ fontSize: 'var(--text-xs)' }}>
            {caseRecord?.case_number ?? '—'}
          </span>
        }
      >
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-4)' }}>
          {/* What the report will say */}
          <div className="row row--wrap" style={{ gap: 10, alignItems: 'center' }}>
            {verdict ? (
              <>
                <Pill variant={verdictPillTone(verdict.verdict)}>
                  {verdictBandLabel(verdict.verdict)}
                </Pill>
                <span style={{ fontSize: 'var(--text-sm)' }}>on {verdict.filename}</span>
              </>
            ) : (
              <span className="muted" style={{ fontSize: 'var(--text-sm)' }}>
                No analysis is loaded for this case. Run analysis first so the report can include the
                verdict, signals and provenance.
              </span>
            )}
          </div>

          {/* Who signs */}
          <div className="field" style={{ maxWidth: 360 }}>
            <label className="field__label" htmlFor="report-examiner">
              Examiner <span style={{ color: 'var(--text-faint)' }}>(optional)</span>
            </label>
            <input
              id="report-examiner"
              className="input"
              type="text"
              placeholder="Name or ID of the signing examiner"
              value={examinerName}
              onChange={(e) => setExaminerName(e.target.value)}
            />
          </div>

          {report.phase === 'error' ? (
            <ErrorBanner context="Signed PDF" error={report.error} />
          ) : null}

          {/* How to produce it — print view primary, signed PDF secondary. */}
          <div className="btn-row">
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => setPrinting(true)}
            >
              <Icon name="document" size={14} />
              Open print view
            </button>
            <button
              type="button"
              className="btn"
              disabled={generating}
              onClick={() => generateReport(examinerName)}
            >
              {generating ? <Spinner label="Generating signed PDF…" /> : <Icon name="lock" size={14} />}
              Generate signed PDF (archival)
            </button>
            {!analysisData ? (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => onNavigate('analysis', { caseId: currentCaseId })}
              >
                Run analysis
                <Icon name="arrow-right" size={14} />
              </button>
            ) : null}
          </div>
        </div>
      </Section>

      {/* ARCHIVE — the signed PDFs already generated for this case. */}
      <Section
        title="Signed PDFs"
        aside={reportsList.length ? `${reportsList.length} on file` : null}
      >
        {downloadError ? <ErrorBanner context="Download" error={downloadError} /> : null}

        {loading ? (
          <Spinner label="Loading report history…" />
        ) : error ? (
          <div className="stack" style={{ gap: 'var(--space-3)' }}>
            <ErrorBanner context="Reports" error={error} />
            <div className="btn-row">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => setReloadNonce((n) => n + 1)}
              >
                <Icon name="refresh" size={15} />
                Retry
              </button>
            </div>
          </div>
        ) : reportsList.length === 0 ? (
          <Empty>
            No signed PDF has been generated for this case yet. The print view above needs none;
            generate a signed PDF only if you want the archival copy anchored to the audit head.
          </Empty>
        ) : (
          <>
            <div className="table-wrapper card">
              <table className="table">
                <thead>
                  <tr>
                    <th>Report</th>
                    <th>Generated</th>
                    <th className="table__num">Pages</th>
                    <th>Integrity</th>
                    <th>Status</th>
                    <th aria-label="Actions" />
                  </tr>
                </thead>
                <tbody>
                  {reportsList.map((r) => (
                    <tr key={r.report_id}>
                      <td>
                        <div className="row" style={{ gap: 10, minWidth: 0 }}>
                          <Icon name="document" size={18} style={{ color: 'var(--accent)' }} />
                          <div className="stack" style={{ gap: 1, minWidth: 0 }}>
                            <span style={{ fontWeight: 600, fontSize: 'var(--text-xs)' }}>
                              {r.filename}
                            </span>
                            <span className="faint" style={{ fontSize: 'var(--text-2xs)' }}>
                              {r.generator} · {formatBytes(r.size_bytes)}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap' }}>
                        {formatTimestamp(r.generated_at)}
                      </td>
                      <td className="table__num">{orPlaceholder(r.pages)}</td>
                      <td>
                        <Pill variant={r.audit_chain_valid ? 'ok' : 'error'}>
                          {r.audit_chain_valid ? 'CHAIN VALID' : 'CHAIN BROKEN'}
                        </Pill>
                      </td>
                      <td>
                        {r.document_status ? (
                          <Pill variant="neutral">{r.document_status.toUpperCase()}</Pill>
                        ) : (
                          <span className="faint">—</span>
                        )}
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className="btn btn--sm"
                          disabled={downloadingId === r.report_id}
                          onClick={() => downloadSigned(r)}
                        >
                          {downloadingId === r.report_id ? (
                            <Spinner />
                          ) : (
                            <Icon name="download" size={13} />
                          )}
                          Signed PDF
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Raw report anchors — hidden by default, never discarded. */}
            <details className="disclosure">
              <summary>
                <Icon name="arrow-right" size={14} className="disclosure__chevron" />
                View cryptographic details
              </summary>
              <div className="disclosure__panel stack" style={{ gap: 'var(--space-4)' }}>
                {reportsList.map((r) => (
                  <dl className="dl" key={r.report_id}>
                    <dt>Report</dt>
                    <dd>{r.filename}</dd>
                    <dt>PDF SHA-256</dt>
                    <dd>
                      <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                        <code className="mono break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                          {r.sha256}
                        </code>
                        <CopyButton value={r.sha256} label="" title="Copy PDF SHA-256" />
                      </span>
                    </dd>
                    <dt>Audit head hash</dt>
                    <dd>
                      <span className="row" style={{ gap: 6, alignItems: 'center' }}>
                        <code className="mono break-all" style={{ fontSize: 'var(--text-2xs)' }}>
                          {r.audit_head_hash}
                        </code>
                        <CopyButton value={r.audit_head_hash} label="" title="Copy audit head hash" />
                      </span>
                    </dd>
                    <dt>Renderer</dt>
                    <dd className="mono">{r.renderer}</dd>
                  </dl>
                ))}
              </div>
            </details>
          </>
        )}
      </Section>
    </div>
  )
}
