/**
 * Screen: Forensic Reports.
 *
 * One rule governs this screen: the canonical forensic report is the PDF the
 * BACKEND renders at `POST /api/cases/{case_id}/report`. This screen lists the
 * reports the backend actually produced and downloads those exact bytes. It
 * never renders a report of its own.
 *
 * A previous build shipped a frontend print-to-PDF path (`ReportPrintView`) that
 * assembled a three-page "forensic report" in the browser, stamped it with
 * `new Date()`, and filled every field the store had not loaded from a hardcoded
 * literal: a case number, an examiner name, a pHash/dHash pair, a compression
 * score, image dimensions, an audit row count and the fusion arithmetic itself.
 * That file is deleted. A document assembled in a browser from placeholder
 * constants is not evidence of anything, and it is indistinguishable from the
 * real one once printed.
 *
 * Generation is gated on the case holding evidence, because that is what the
 * backend writes the report about (`report._collect` fuses every evidence row in
 * the case). Offering the action on an empty case would produce a document with
 * no findings in it.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { ReportResponse } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { NOT_MEASURED, formatBytes, formatTimestamp, orPlaceholder, shortHash } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { isReady, type Investigation } from '../state/useInvestigation'

export function ScreenReports({
  caseId,
  investigation,
  onNavigate,
}: {
  caseId: string | null
  investigation: Investigation
  onNavigate: (path: RoutePath, params?: { caseId?: string; filter?: string }) => void
}) {
  const { caseRecord, evidence, analysis, report, generateReport, auditVerification } = investigation
  const currentCaseId = caseId || caseRecord?.case_id || null

  const [reportsList, setReportsList] = useState<ReportResponse[]>([])
  const [loading, setLoading] = useState(Boolean(currentCaseId))
  const [error, setError] = useState<unknown>(null)
  const [searchQuery, setSearchQuery] = useState('')

  /**
   * The examiner name.
   *
   * Empty by default. The old build defaulted this to `'Analyst'` and the
   * backend defaulted its own copy to `'integration-check'` -- the name the
   * integration test harness uses. Attributing an examination to a name nobody
   * typed is a false attestation, so an unfilled box stays unfilled and the
   * backend prints "Not specified".
   */
  const [examinerName, setExaminerName] = useState(caseRecord?.examiner ?? '')
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<unknown>(null)

  useEffect(() => {
    setExaminerName(caseRecord?.examiner ?? '')
  }, [caseRecord?.case_id, caseRecord?.examiner])

  useEffect(() => {
    if (!currentCaseId) {
      setReportsList([])
      setLoading(false)
      return
    }
    let active = true
    setLoading(true)
    setError(null)
    api
      .listReports(currentCaseId)
      .then((data) => {
        if (!active) return
        setReportsList(data.reports)
        setLoading(false)
      })
      .catch((err) => {
        if (!active) return
        // The list failed. Show the failure -- an empty table would read as
        // "this case has no reports", which is a different claim.
        setError(err)
        setReportsList([])
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [currentCaseId, report.data])

  const analysisData = isReady(analysis) ? analysis.data : null
  const auditData = isReady(auditVerification) ? auditVerification.data : null

  /** Evidence count from the backend's own record, never a placeholder. */
  const evidenceCount = evidence.length > 0 ? evidence.length : (caseRecord?.evidence_count ?? 0)

  const gateReason = !currentCaseId
    ? 'No case is open. A report is written about one case record, so open or create a case first.'
    : evidenceCount === 0
      ? 'This case holds no evidence yet. The backend renders the report from the evidence in the case record, so there is nothing to report on.'
      : null
  const generating = report.phase === 'loading'
  const canGenerate = gateReason === null && !generating

  const filteredReports = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return reportsList
    return reportsList.filter(
      (r) =>
        r.report_id.toLowerCase().includes(q) ||
        r.filename.toLowerCase().includes(q) ||
        r.case_id.toLowerCase().includes(q),
    )
  }, [reportsList, searchQuery])

  const selected =
    reportsList.find((r) => r.report_id === selectedReportId) ?? reportsList[0] ?? null

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
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      <div className="screen__head">
        <div>
          <h1 className="screen__title">REPORTS</h1>
          <p className="screen__lead">
            The canonical report is rendered by the backend and hashed on the way out. This screen
            lists what it produced.
          </p>
        </div>

        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <input
            className="input input--sm"
            style={{ width: 190, fontSize: 'var(--text-xs)' }}
            placeholder="Examiner (optional)"
            value={examinerName}
            onChange={(e) => setExaminerName(e.target.value)}
            aria-label="Examiner name to record on the report"
          />
          <button
            type="button"
            className="btn-new-case"
            style={{ padding: '8px 18px', opacity: canGenerate ? 1 : 0.5 }}
            disabled={!canGenerate}
            title={gateReason ?? 'Ask the backend to render a report for this case'}
            onClick={() => generateReport(examinerName.trim() || undefined)}
          >
            {generating ? <Spinner /> : <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>}
            <span>{generating ? 'Generating…' : 'Generate Report'}</span>
          </button>
        </div>
      </div>

      {gateReason ? (
        <Banner tone="info" title="Report generation unavailable" detail={gateReason}>
          {currentCaseId ? (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onNavigate('intake')}
            >
              Add evidence
            </button>
          ) : null}
        </Banner>
      ) : null}

      {report.phase === 'error' ? (
        <ErrorBanner context="Report generation" error={report.error} />
      ) : null}

      {isReady(report) ? (
        <Banner
          tone="ok"
          title="Report rendered by the backend"
          detail={`${report.data.filename} - ${report.data.pages ?? NOT_MEASURED} pages, ${formatBytes(report.data.size_bytes)}, renderer "${report.data.renderer}". SHA-256 of the PDF bytes: ${report.data.sha256}`}
        />
      ) : null}

      <div className="card row row--wrap" style={{ padding: '10px 14px', gap: 10, alignItems: 'center' }}>
        <div className="search-box" style={{ minWidth: 220, maxWidth: 380, flex: 1 }}>
          <Icon name="search" size={13} style={{ color: 'var(--text-faint)' }} />
          <input
            className="search-box__input"
            type="search"
            placeholder="Search by report id, filename or case id…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
          {reportsList.length} report{reportsList.length === 1 ? '' : 's'} on record for this case
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 340px',
          gap: 'var(--space-4)',
          alignItems: 'start',
        }}
      >
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-3)' }}>
          {error ? <ErrorBanner context="Reports list" error={error} /> : null}

          {loading ? (
            <Spinner label="Loading reports for this case…" />
          ) : !currentCaseId ? (
            <Empty>No case is open, so there is no report list to show.</Empty>
          ) : reportsList.length === 0 ? (
            <Empty>
              No report has been generated for this case yet. Use “Generate Report” — the backend
              renders the PDF, hashes it and records it in the audit chain.
            </Empty>
          ) : filteredReports.length === 0 ? (
            <Empty>No report on record matches “{searchQuery.trim()}”.</Empty>
          ) : (
            <div className="table-wrapper">
              <table className="table">
                <thead>
                  <tr>
                    <th>REPORT ID</th>
                    <th>FILENAME</th>
                    <th>PAGES</th>
                    <th>SIZE</th>
                    <th>CHAIN</th>
                    <th>GENERATED</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredReports.map((r) => {
                    const isSelected = selected?.report_id === r.report_id
                    return (
                      <tr
                        key={r.report_id}
                        onClick={() => setSelectedReportId(r.report_id)}
                        style={{
                          cursor: 'pointer',
                          background: isSelected ? 'var(--surface-2)' : undefined,
                        }}
                      >
                        <td
                          className="mono"
                          style={{ fontSize: '11px', fontWeight: 700, color: 'var(--accent-bright)' }}
                        >
                          {r.report_id.slice(0, 8)}
                        </td>
                        <td className="mono" style={{ fontSize: '11px' }}>
                          {r.filename}
                        </td>
                        <td className="mono" style={{ fontSize: '11px' }}>
                          {r.pages ?? NOT_MEASURED}
                        </td>
                        <td className="mono" style={{ fontSize: '11px' }}>
                          {formatBytes(r.size_bytes)}
                        </td>
                        <td>
                          <Pill variant={r.audit_chain_valid ? 'ok' : 'warn'}>
                            {r.audit_chain_valid ? 'VALID' : 'UNVERIFIED'}
                          </Pill>
                        </td>
                        <td
                          style={{
                            fontSize: 'var(--text-xs)',
                            whiteSpace: 'nowrap',
                            fontFamily: 'var(--mono)',
                            color: 'var(--text-muted)',
                          }}
                        >
                          {formatTimestamp(r.generated_at)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div
          className="card stack"
          style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}
        >
          <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
            REPORT RECORD
          </span>

          {selected === null ? (
            <Empty>
              Select a generated report to see the record the backend stored for it: the PDF's own
              SHA-256, its page count, the renderer that produced it and the audit head hash it was
              sealed against.
            </Empty>
          ) : (
            <>
              <dl className="stack" style={{ gap: 6, fontSize: 'var(--text-xs)', margin: 0 }}>
                <Row label="Report ID" value={selected.report_id} mono />
                <Row label="Case ID" value={selected.case_id} mono />
                <Row label="Filename" value={selected.filename} mono />
                <Row label="Pages" value={selected.pages === null ? NOT_MEASURED : String(selected.pages)} />
                <Row label="Size" value={formatBytes(selected.size_bytes)} />
                <Row label="Generated" value={formatTimestamp(selected.generated_at)} />
                <Row label="Generator" value={orPlaceholder(selected.generator)} mono />
                <Row label="Renderer" value={orPlaceholder(selected.renderer)} mono />
                <Row
                  label="PDF SHA-256"
                  value={shortHash(selected.sha256, 16)}
                  mono
                  action={<CopyButton value={selected.sha256} label="Copy PDF SHA-256" />}
                />
                <Row
                  label="Audit head"
                  value={shortHash(selected.audit_head_hash, 16)}
                  mono
                  action={<CopyButton value={selected.audit_head_hash} label="Copy audit head hash" />}
                />
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ color: 'var(--text-muted)' }}>Chain at generation</span>
                  <Pill variant={selected.audit_chain_valid ? 'ok' : 'warn'}>
                    {selected.audit_chain_valid ? 'VALID' : 'UNVERIFIED'}
                  </Pill>
                </div>
              </dl>

              <p style={{ fontSize: '10.5px', color: 'var(--text-muted)', margin: 0, lineHeight: 1.5 }}>
                {selected.document_status}
              </p>

              <div className="stack" style={{ gap: 8, marginTop: 4 }}>
                <button
                  type="button"
                  className="btn btn--primary"
                  style={{ padding: '8px 12px', fontSize: 'var(--text-xs)', fontWeight: 700 }}
                  onClick={() => downloadSigned(selected)}
                  disabled={downloadingId === selected.report_id}
                >
                  {downloadingId === selected.report_id ? (
                    <Spinner />
                  ) : (
                    <Icon name="download" size={13} />
                  )}
                  Download PDF
                </button>

                <a
                  className="btn btn--ghost"
                  style={{ padding: '8px 12px', fontSize: 'var(--text-xs)', textAlign: 'center' }}
                  href={api.reportDownloadUrl(selected.download_url)}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Icon name="external" size={13} />
                  Open in new tab
                </a>

                {currentCaseId ? (
                  <button
                    type="button"
                    className="btn btn--ghost"
                    style={{
                      padding: '7px 12px',
                      fontSize: 'var(--text-xs)',
                      color: 'var(--accent-bright)',
                    }}
                    onClick={() => onNavigate('case-detail', { caseId: currentCaseId })}
                  >
                    ← View case record
                  </button>
                ) : null}
              </div>
            </>
          )}

          {downloadError ? <ErrorBanner context="Download" error={downloadError} /> : null}
        </div>
      </div>

      {analysisData?.caveat ? (
        <p style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)', margin: 0 }}>
          {analysisData.caveat}
        </p>
      ) : null}

      {auditData && !auditData.valid ? (
        <Banner
          tone="warn"
          title="Audit chain verification failed"
          detail={`The chain did not verify (first invalid sequence: ${auditData.first_invalid_seq ?? NOT_MEASURED}). A report generated now still records the head hash, but the chain behind it is not intact.`}
        />
      ) : null}
    </div>
  )
}

/** One label/value row. `value` is always a string the caller already formatted. */
function Row({
  label,
  value,
  mono = false,
  action,
}: {
  label: string
  value: string
  mono?: boolean
  action?: React.ReactNode
}) {
  return (
    <div className="row" style={{ justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
      <span style={{ color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>{label}</span>
      <span className="row" style={{ gap: 4, alignItems: 'center', minWidth: 0 }}>
        <span
          style={{
            fontFamily: mono ? 'var(--mono)' : undefined,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
          title={value}
        >
          {value}
        </span>
        {action}
      </span>
    </div>
  )
}
