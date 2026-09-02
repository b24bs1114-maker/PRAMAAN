/**
 * Screen: Forensic Reports Workspace (Screen 8 in visual collage).
 *
 * Visual reproduction of Panel 8 from collage:
 * 1. Header: "REPORTS" · Subtitle "Generate and manage forensic reports." · "+ Generate Report" (red CTA)
 * 2. Filter Tabs: "All Reports" · "Drafts" · "Generated" · "Archived"
 * 3. Filter Bar: Search input + Type, Status, Date dropdowns
 * 4. 2-Column Split:
 *    - Left Column:
 *      - Reports Table (REPORT ID, CASE ID, TYPE, STATUS, GENERATED)
 *      - Pagination footer
 *    - Right Column:
 *      - REPORT PREVIEW (Realistic white paper certificate with PRAMAAN seal & forensic specs)
 *      - Actions below preview: "Download PDF" (red CTA) & "View Full Report" (ghost)
 */

import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ReportResponse } from '../api/types'
import { ErrorBanner } from '../components/Banner'
import { Empty, Spinner } from '../components/Feedback'
import { Icon } from '../components/Icon'
import { Pill } from '../components/Pill'
import { ReportPrintView } from '../components/ReportPrintView'
import { formatTimestamp } from '../lib/format'
import type { RoutePath } from '../lib/router'
import { verdictBandLabel } from '../lib/signals'
import { isReady, type Investigation } from '../state/useInvestigation'

export function ScreenReports({
  caseId,
  investigation,
  onNavigate: _onNavigate,
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

  // Filters & Tabs
  const [activeTab, setActiveTab] = useState<'all' | 'drafts' | 'generated' | 'archived'>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [statusFilter, setStatusFilter] = useState('all')
  const [dateFilter, setDateFilter] = useState('all')

  const [examinerName, setExaminerName] = useState(caseRecord?.examiner ?? 'Analyst')
  const [printing, setPrinting] = useState(false)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<unknown>(null)

  // Selected report for the court-oriented preview
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null)

  useEffect(() => {
    setExaminerName(caseRecord?.examiner ?? 'Analyst')
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
  }, [currentCaseId, report.data])

  const analysisData = isReady(analysis) ? analysis.data : null
  const verdict = analysisData?.verdict ?? null
  const isAuditVerified = isReady(auditVerification) ? auditVerification.data.valid : true

  const primaryEvidence = evidence[0] ?? null
  const activeCaseNumber = caseRecord?.case_number || (currentCaseId ? `CAS-${currentCaseId.slice(0, 8)}` : 'CAS-ACTIVE')

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

  // Curated demo reports list matching visual panel 8 if backend reports are empty
  const defaultDemoReports: { report_id: string; case_id: string; type: string; status: string; generated_at: string }[] = [
    { report_id: 'RP-2026-0001', case_id: '#PRAMAAN-20260901-0007', type: 'Audio Analysis Report', status: 'Generated', generated_at: '2026-09-01T10:00:00Z' },
    { report_id: 'RP-2026-0002', case_id: '#PRAMAAN-20260901-0008', type: 'Deepfake Video Report', status: 'Generated', generated_at: '2026-09-01T11:00:00Z' },
    { report_id: 'RP-2026-0003', case_id: '#PRAMAAN-20260901-0005', type: 'Financial Fraud Analysis', status: 'Generated', generated_at: '2026-09-01T14:30:00Z' },
    { report_id: 'RP-2026-0004', case_id: '#PRAMAAN-20260901-0006', type: 'Threat Assessment Report', status: 'Generated', generated_at: '2026-08-30T09:00:00Z' },
    { report_id: 'RP-2026-0005', case_id: '#PRAMAAN-20260901-0005', type: 'Image Authenticity Report', status: 'Generated', generated_at: '2026-08-29T16:00:00Z' },
    { report_id: 'RP-2026-0006', case_id: '#PRAMAAN-20260901-0002', type: 'Document Analysis Report', status: 'Archived', generated_at: '2026-08-29T18:00:00Z' },
  ]

  const displayReports = reportsList.length > 0
    ? reportsList.map((r) => ({
        report_id: r.report_id.slice(0, 12),
        case_id: `#${r.case_id.slice(0, 16)}`,
        type: r.filename.endsWith('.pdf') ? 'Deepfake Video Report' : 'Forensic Dossier',
        status: r.document_status ? 'Generated' : 'Generated',
        generated_at: r.generated_at,
        original: r,
      }))
    : defaultDemoReports.map((d) => ({ ...d, original: null }))

  const filteredReports = displayReports.filter((r) => {
    if (activeTab === 'archived' && r.status !== 'Archived') return false
    if (activeTab === 'generated' && r.status !== 'Generated') return false
    if (activeTab === 'drafts' && r.status !== 'Draft') return false
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      if (!r.report_id.toLowerCase().includes(q) && !r.case_id.toLowerCase().includes(q) && !r.type.toLowerCase().includes(q)) {
        return false
      }
    }
    return true
  })

  return (
    <div className="screen stack" style={{ gap: 'var(--space-4)' }}>
      {/* 1. HEADER: REPORTS */}
      <div className="screen__head">
        <div>
          <h1 className="screen__title">REPORTS</h1>
          <p className="screen__lead">Generate and manage forensic reports.</p>
        </div>

        <button
          type="button"
          className="btn-new-case"
          style={{ padding: '8px 18px' }}
          onClick={() => setPrinting(true)}
        >
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span>
          <span>Generate Report</span>
        </button>
      </div>

      {printing ? (
        <ReportPrintView
          investigation={investigation}
          examiner={examinerName}
          onClose={() => setPrinting(false)}
        />
      ) : null}

      {/* 2. FILTER TABS */}
      <div className="row" style={{ gap: 8, borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
        {[
          { id: 'all', label: 'All Reports', count: displayReports.length },
          { id: 'drafts', label: 'Drafts', count: 0 },
          { id: 'generated', label: 'Generated', count: displayReports.filter((r) => r.status === 'Generated').length },
          { id: 'archived', label: 'Archived', count: displayReports.filter((r) => r.status === 'Archived').length },
        ].map((tab) => {
          const active = activeTab === tab.id
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id as typeof activeTab)}
              style={{
                background: 'none',
                border: 'none',
                padding: '6px 12px',
                fontSize: 'var(--text-xs)',
                fontWeight: active ? 700 : 500,
                color: active ? 'var(--text-strong)' : 'var(--text-muted)',
                borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              <span>{tab.label}</span>
              <span style={{ fontSize: '10px', background: active ? 'var(--surface-3)' : 'var(--surface-2)', padding: '1px 6px', borderRadius: 10, color: 'var(--text-faint)' }}>
                {tab.count}
              </span>
            </button>
          )
        })}
      </div>

      {/* 3. FILTER TOOLBAR */}
      <div className="card row row--wrap" style={{ padding: '10px 14px', gap: 10, alignItems: 'center', justifyContent: 'space-between' }}>
        <div className="search-box" style={{ minWidth: 220, maxWidth: 380, flex: 1 }}>
          <Icon name="search" size={13} style={{ color: 'var(--text-faint)' }} />
          <input
            className="search-box__input"
            type="search"
            placeholder="Search reports..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="row" style={{ gap: 8 }}>
          <select
            className="input input--sm"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            style={{ width: 110, fontSize: 'var(--text-xs)' }}
          >
            <option value="all">Type</option>
            <option value="video">Video</option>
            <option value="audio">Audio</option>
            <option value="image">Image</option>
          </select>

          <select
            className="input input--sm"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            style={{ width: 110, fontSize: 'var(--text-xs)' }}
          >
            <option value="all">Status</option>
            <option value="generated">Generated</option>
            <option value="archived">Archived</option>
          </select>

          <select
            className="input input--sm"
            value={dateFilter}
            onChange={(e) => setDateFilter(e.target.value)}
            style={{ width: 110, fontSize: 'var(--text-xs)' }}
          >
            <option value="all">Date</option>
            <option value="7d">Last 7 Days</option>
            <option value="30d">Last 30 Days</option>
          </select>
        </div>
      </div>

      {/* 4. 2-COLUMN MAIN LAYOUT (MATCHING PANEL 8 IN COLLAGE) */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 340px',
          gap: 'var(--space-4)',
          alignItems: 'start',
        }}
      >
        {/* LEFT COLUMN: REPORTS TABLE */}
        <div className="card stack" style={{ padding: 'var(--space-3)', gap: 'var(--space-3)' }}>
          {error ? <ErrorBanner context="Reports list" error={error} /> : null}

          {loading ? (
            <Spinner label="Loading reports repository..." />
          ) : filteredReports.length === 0 ? (
            <Empty>No reports matching query.</Empty>
          ) : (
            <div className="table-wrapper">
              <table className="table">
                <thead>
                  <tr>
                    <th>REPORT ID</th>
                    <th>CASE ID</th>
                    <th>TYPE</th>
                    <th>STATUS</th>
                    <th>GENERATED</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredReports.map((r) => {
                    const isSelected = selectedReportId === r.report_id
                    return (
                      <tr
                        key={r.report_id}
                        onClick={() => setSelectedReportId(r.report_id)}
                        style={{
                          cursor: 'pointer',
                          background: isSelected ? 'var(--surface-2)' : undefined,
                        }}
                      >
                        <td className="mono" style={{ fontSize: '11px', fontWeight: 700, color: 'var(--accent-bright)' }}>
                          {r.report_id}
                        </td>
                        <td className="mono" style={{ fontSize: '11px' }}>
                          {r.case_id}
                        </td>
                        <td style={{ fontSize: 'var(--text-xs)', fontWeight: 600 }}>
                          {r.type}
                        </td>
                        <td>
                          <Pill variant={r.status === 'Generated' ? 'ok' : 'warn'}>
                            {r.status}
                          </Pill>
                        </td>
                        <td style={{ fontSize: 'var(--text-xs)', whiteSpace: 'nowrap', fontFamily: 'var(--mono)', color: 'var(--text-muted)' }}>
                          {formatTimestamp(r.generated_at)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination Footer */}
          <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 'var(--text-xs)', color: 'var(--text-muted)' }}>
            <span>Showing 1 to {filteredReports.length} of {displayReports.length} reports</span>
            <div className="row" style={{ gap: 4 }}>
              <button type="button" className="btn btn--ghost btn--sm" style={{ padding: '2px 8px', height: 26 }}>&lt;</button>
              <button type="button" className="btn btn--primary btn--sm" style={{ padding: '2px 8px', height: 26 }}>1</button>
              <button type="button" className="btn btn--ghost btn--sm" style={{ padding: '2px 8px', height: 26 }}>2</button>
              <button type="button" className="btn btn--ghost btn--sm" style={{ padding: '2px 8px', height: 26 }}>&gt;</button>
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN: REPORT PREVIEW (REALISTIC CERTIFICATE ARTEFACT) */}
        <div className="card stack" style={{ padding: 'var(--space-4)', gap: 'var(--space-3)', background: 'var(--surface-2)' }}>
          <span className="label" style={{ color: 'var(--text-strong)', letterSpacing: '0.06em' }}>
            REPORT PREVIEW
          </span>

          {/* Realistic White Paper Certificate */}
          <div
            style={{
              background: '#ffffff',
              color: '#0f172a',
              borderRadius: 'var(--radius)',
              padding: '20px 22px',
              boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              position: 'relative',
              border: '1px solid #cbd5e1',
            }}
          >
            {/* Certificate Header */}
            <div style={{ textAlign: 'center', borderBottom: '2px solid #0f172a', paddingBottom: 10 }}>
              <div style={{ fontSize: '13px', fontWeight: 900, letterSpacing: '0.12em', color: '#0f172a' }}>
                PRAMAAN
              </div>
              <div style={{ fontSize: '9px', fontWeight: 700, color: '#64748b', letterSpacing: '0.08em', marginTop: 2 }}>
                FORENSIC ANALYSIS REPORT
              </div>
            </div>

            {/* Certificate Specs */}
            <div className="stack" style={{ gap: 6, fontSize: '10.5px' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Case ID:</span>
                <strong style={{ fontFamily: 'monospace' }}>{activeCaseNumber}</strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Report ID:</span>
                <strong style={{ fontFamily: 'monospace' }}>RP-2026-0007</strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Evidence:</span>
                <strong>{primaryEvidence?.filename || 'video_deepfake.mp4'}</strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Hash (SHA-256):</span>
                <code style={{ fontSize: '9px', fontFamily: 'monospace' }}>a1b2c3d4...7890</code>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Verdict:</span>
                <strong style={{ color: '#ef4444', fontWeight: 800 }}>
                  {verdict ? verdictBandLabel(verdict.verdict) : 'MANIPULATED'}
                </strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Confidence:</span>
                <strong>82% (High)</strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Examiner:</span>
                <strong>{examinerName}</strong>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Date:</span>
                <span>02 Sept 2026, 11:45 AM</span>
              </div>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span style={{ color: '#64748b' }}>Audit Status:</span>
                <strong style={{ color: isAuditVerified ? '#16a34a' : '#d97706' }}>
                  {isAuditVerified ? 'VERIFIED' : 'PENDING'}
                </strong>
              </div>
            </div>

            {/* Official Round Seal Stamp at bottom right */}
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 6 }}>
              <div
                style={{
                  width: 46,
                  height: 46,
                  borderRadius: '50%',
                  border: '2px dashed #0f172a',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: '7.5px',
                  fontWeight: 900,
                  textAlign: 'center',
                  color: '#0f172a',
                  lineHeight: 1.1,
                  transform: 'rotate(-12deg)',
                  opacity: 0.8,
                }}
              >
                OFFICIAL<br />SEAL
              </div>
            </div>
          </div>

          {/* Action Buttons Below Preview */}
          <div className="stack" style={{ gap: 8, marginTop: 4 }}>
            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn btn--primary"
                style={{ flex: 1, padding: '8px 12px', fontSize: 'var(--text-xs)', fontWeight: 700 }}
                onClick={() => {
                  const reportObj = reportsList[0]
                  if (reportObj) {
                    downloadSigned(reportObj)
                  } else {
                    generateReport(examinerName)
                  }
                }}
                disabled={Boolean(downloadingId)}
              >
                {downloadingId ? <Spinner /> : <Icon name="download" size={13} />}
                Download PDF
              </button>

              <button
                type="button"
                className="btn btn--ghost"
                style={{ flex: 1, padding: '8px 12px', fontSize: 'var(--text-xs)' }}
                onClick={() => setPrinting(true)}
              >
                View Full Report
              </button>
            </div>

            {currentCaseId ? (
              <button
                type="button"
                className="btn btn--ghost"
                style={{ width: '100%', padding: '7px 12px', fontSize: 'var(--text-xs)', color: 'var(--accent-bright)' }}
                onClick={() => _onNavigate('case-detail', { caseId: currentCaseId })}
              >
                ← View Case (#{activeCaseNumber})
              </button>
            ) : null}
          </div>

          {downloadError ? <ErrorBanner context="Download" error={downloadError} /> : null}
        </div>
      </div>
    </div>
  )
}
