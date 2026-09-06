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
import type { StoredReport } from '../api/types'
import { Banner, ErrorBanner } from '../components/Banner'
import { CopyButton } from '../components/CopyButton'
import { Empty, NoCaseSelected, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { ReportFileActions } from '../components/ReportActions'
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

  const [reportsList, setReportsList] = useState<StoredReport[]>([])
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

  // When a fresh report is rendered for THIS case, select it so the record
  // panel and its download action point straight at the document just produced.
  // The generate -> download path should not make the examiner hunt for the new
  // row in the list; the report_id is the backend's own, so it resolves to the
  // real row once the list refetch below lands.
  useEffect(() => {
    if (isReady(report) && report.data.case_id === currentCaseId) {
      setSelectedReportId(report.data.report_id)
    }
  }, [report, currentCaseId])

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

  // Case-id guards: these slices are shared across screens and survive a case
  // switch, so a report or audit verification produced for Case A must not
  // render as a banner on Case B's reports screen.
  const analysisData =
    isReady(analysis) && (!currentCaseId || analysis.data.case.case_id === currentCaseId)
      ? analysis.data
      : null
  const auditData =
    isReady(auditVerification) && (!currentCaseId || auditVerification.data.case_id === currentCaseId)
      ? auditVerification.data
      : null

  /** Evidence count from the backend's own record, never a placeholder. */
  const evidenceCount = evidence.length > 0 ? evidence.length : (caseRecord?.evidence_count ?? 0)

  /*
   * Why generation is unavailable, or null when it is available.
   *
   * The no-case branch is gone: that case is handled by an early return below, so
   * reaching here means a case is open and the only remaining obstacle is an
   * empty one.
   */
  const gateReason =
    evidenceCount === 0
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

  const downloadSigned = async (r: StoredReport) => {
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

  /*
   * No case, no report screen.
   *
   * A report is written about one case, so with none open this screen had nothing
   * to render and said so three times over, in three different voices: a banner
   * ("No case is open. A report is written about one case record…"), a counter
   * that read "0 reports on record for **this case**" when there was no such
   * case, and an empty list ("No case is open, so there is no report list to
   * show"). The counter was the false one -- it asserted a fact about a case that
   * did not exist -- and the other two were the same sentence twice.
   *
   * The same guard the other three case-scoped screens use, so all four now read
   * alike.
   */
  if (!currentCaseId) {
    return (
      <NoCaseSelected
        purpose="generate or review its forensic report"
        onViewCases={() => onNavigate('cases')}
      />
    )
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
            className="btn btn--primary"
            style={{ padding: '8px 18px', fontWeight: 700, fontSize: 'var(--text-xs)', opacity: canGenerate ? 1 : 0.5 }}
            disabled={!canGenerate || downloadingId !== null}
            title={gateReason ?? 'Generate canonical forensic examination report and download PDF'}
            onClick={async () => {
              if (!canGenerate || !currentCaseId) return
              const exam = examinerName.trim() || undefined
              const res = await generateReport(exam)
              if (res) {
                setSelectedReportId(res.report_id)
                await downloadSigned(res)
              }
            }}
          >
            {generating || downloadingId ? <Spinner /> : <Icon name="download" size={14} />}
            {/* Two phases, named separately: the PDF is rendered by the backend
                first, then fetched. Reporting the fetch as "Compiling" would
                claim work that has already finished. */}
            <span>
              {generating ? 'Compiling Report…' : downloadingId ? 'Retrieving PDF…' : 'Generate Forensic Report'}
            </span>
          </button>
        </div>
      </div>

      {gateReason ? (
        <Banner tone="info" title="Report generation unavailable" detail={gateReason}>
          <button
            type="button"
            className="btn btn--ghost btn--sm"
            onClick={() => onNavigate('intake', { caseId: currentCaseId })}
          >
            <Icon name="upload" size={13} />
            Ingest Evidence
          </button>
        </Banner>
      ) : null}

      {report.phase === 'error' ? (
        <ErrorBanner context="Report generation" error={report.error} />
      ) : null}

      {isReady(report) && (!currentCaseId || report.data.case_id === currentCaseId) ? (
        <Banner
          tone="ok"
          title="Report rendered by the backend"
          detail={`${report.data.filename} - ${report.data.pages ?? NOT_MEASURED} pages, ${formatBytes(report.data.size_bytes)}, renderer "${report.data.renderer}". SHA-256 of the PDF bytes: ${report.data.sha256}`}
        >
          {/*
            One-click open/download for the document just produced. These act on
            the generate response itself -- the same bytes the backend hashed --
            using its friendly filename, so the examiner does not have to locate
            the new row before saving it.
          */}
          <ReportFileActions report={report.data} openLabel="Open in new tab" />
        </Banner>
      ) : null}

      <div className="card row row--wrap" style={{ padding: '10px 14px', gap: 10, alignItems: 'center' }}>
        <div className="search-box" style={{ minWidth: 220, maxWidth: 380, flex: 1 }}>
          <Icon name="search" size={13} style={{ color: 'var(--text-faint)' }} />
          <input
            className="search-box__input"
            type="search"
            aria-label="Filter the reports on this page by report ID, filename or case ID"
            placeholder="Search by report id, filename or case id…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
        {/* Suppressed while the list is in flight: "0 reports on record" during
            the fetch states a fact about the case file that has not been read
            yet, and it is the first thing the eye lands on. */}
        {loading ? null : (
          <span style={{ fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            {searchQuery.trim() && filteredReports.length !== reportsList.length
              ? `${filteredReports.length} of ${reportsList.length} report${reportsList.length === 1 ? '' : 's'} match`
              : `${reportsList.length} report${reportsList.length === 1 ? '' : 's'} on record for this case`}
          </span>
        )}
      </div>

      {/* Reports list + the examiner/gate rail. `workspace-2col` collapses
          the pair to one column under 960px. */}
      <div
        className="workspace-2col"
        style={{
          gridTemplateColumns: 'minmax(0, 1fr) 340px',
        }}
      >
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-3)' }}>
          {error ? <ErrorBanner context="Reports list" error={error} /> : null}

          {loading ? (
            <Spinner label="Loading reports for this case…" />
          ) : reportsList.length === 0 ? (
            <Empty>
              {/* Names the control exactly as it is labelled above. This said
                  “Generate Report”, which is not a button that exists on this
                  screen -- an empty state that tells the operator to press
                  something they cannot find. */}
              No report has been generated for this case yet. Use “Generate Forensic Report” — the
              backend renders the PDF, hashes it and records it in the audit chain.
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
                <ReportFileActions report={selected} layout="stack" size="md" openLabel="Open in new tab" />

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
