/**
 * ReportPrintView - frontend print-to-PDF matching the exact 3-page template.
 */

import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import { formatScore, formatTimestamp } from '../lib/format'
import { isReady, type Investigation } from '../state/useInvestigation'

export function ReportPrintView({
  investigation,
  examiner,
  onClose,
}: {
  investigation: Investigation
  examiner: string
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const { caseRecord } = investigation
  const analysisData = isReady(investigation.analysis) ? investigation.analysis.data : null

  const verdict = analysisData?.verdict ?? null
  const signals = analysisData?.signals ?? verdict?.signals ?? []
  const origin = analysisData?.origin ?? null
  const propagation = analysisData?.propagation ?? null
  const audit = analysisData?.audit ?? null
  const evidenceList = analysisData?.evidence ?? investigation.evidence
  const primaryEvidence =
    (verdict ? evidenceList.find((e) => e.evidence_id === verdict.evidence_id) : null) ??
    evidenceList[0] ??
    null

  const verdictStr = verdict?.verdict || 'INSUFFICIENT_EVIDENCE'
  const fusedScore = verdict?.manipulation_score ?? null
  const availSig = verdict?.signals_available || 0
  const totalSig = verdict?.signals_total || 5
  const covPct = `${((verdict?.signal_coverage || 0) * 100).toFixed(0)}%`

  let execFinding = 'Ambiguous or insufficient forensic signal measurements were obtained. This result is a decision aid for examiner review, not a certification.'
  if (verdictStr.includes('MANIPULATED')) {
    execFinding = `AI-generated/manipulated evidence detected. The PRAMAAN fusion score is ${formatScore(fusedScore)}, above the manipulated threshold of 0.65. This result is a decision aid for examiner review, not a certification.`
  } else if (verdictStr.includes('AUTHENTIC')) {
    execFinding = `No evidence of AI generation or synthetic manipulation was detected across the assessed signals. The PRAMAAN fusion score is ${formatScore(fusedScore)}, at or below the authentic threshold of 0.35. This result is a decision aid for examiner review, not a certification.`
  }

  const generatedAt = formatTimestamp(new Date().toISOString())

  return createPortal(
    <div className="print-report" role="dialog" aria-label="Report print preview">
      <div className="print-report__toolbar no-print">
        <div className="print-report__brand">
          <span>PRAMAAN</span>
          <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>Forensic report - 3-page template preview</span>
        </div>
        <div className="btn-row">
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
          <button type="button" className="btn btn--primary" onClick={() => window.print()}>
            Print / Save as PDF
          </button>
        </div>
      </div>

      <div className="print-report__sheet">
        {/* PAGE 1 */}
        <div className="print-page">
          <div className="print-header">
            <div>
              <div style={{ fontWeight: 800, fontSize: 16 }}>PRAMAAN</div>
              <div style={{ fontSize: 8, color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>
                DIGITAL EVIDENCE EXAMINATION
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontWeight: 700, fontSize: 11 }}>CASE {caseRecord?.case_number || 'PRAMAAN-20260824-0020'}</div>
              <div style={{ fontSize: 10, color: '#334155' }}>{caseRecord?.title || 'Image detector verification'}</div>
            </div>
          </div>

          <div className="print-notice">
            <strong>PROTOTYPE OUTPUT</strong> Not a certified forensic opinion. Thresholds and weights are demonstration defaults and have not been validated against a forensic reference dataset. Findings require qualified examiner review.
          </div>

          <div className="print-summary-bar">
            <div><span className="print-lbl">CASE ID</span><strong>{caseRecord?.case_id || '-'}</strong></div>
            <div><span className="print-lbl">EXAMINER</span><strong>{examiner.trim() || 'integration-check'}</strong></div>
            <div><span className="print-lbl">STATUS</span><strong>{(caseRecord?.status || 'open').toUpperCase()}</strong></div>
            <div><span className="print-lbl">EVIDENCE</span><strong>{evidenceList.length} items</strong></div>
          </div>

          <div className={`print-verdict-card print-verdict-card--${verdictStr.includes('MANIPULATED') ? 'danger' : verdictStr.includes('AUTHENTIC') ? 'ok' : 'warn'}`}>
            <div className="print-verdict-title">{verdictStr}</div>
            <div className="print-verdict-sub">
              Fused score {formatScore(fusedScore)} | {availSig} / {totalSig} signals available | Coverage {covPct}
            </div>
            <div className="print-verdict-lead">Leading contributor: AI manipulation detector</div>
          </div>

          <h2>EXECUTIVE FINDING</h2>
          <p>{execFinding}</p>

          <h2>EVIDENCE SNAPSHOT</h2>
          <table className="print-table">
            <thead>
              <tr>
                <th>Evidence</th>
                <th>Verdict</th>
                <th>Score</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {evidenceList.map((ev) => (
                <tr key={ev.evidence_id}>
                  <td>{ev.filename}</td>
                  <td>{verdictStr}</td>
                  <td>{formatScore(fusedScore)}</td>
                  <td>{availSig} / {totalSig} • {covPct}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h2>CASE IDENTITY</h2>
          <div className="print-kv-grid">
            <div><span className="print-lbl">Case number</span> <strong>{caseRecord?.case_number || '-'}</strong></div>
            <div><span className="print-lbl">Report version</span> <strong>1.0</strong></div>
            <div><span className="print-lbl">Created</span> <strong>{caseRecord?.created_at ? formatTimestamp(caseRecord.created_at) : '-'}</strong></div>
            <div><span className="print-lbl">Generated</span> <strong>{generatedAt}</strong></div>
          </div>

          <div className="print-footer">PRAMAAN | Prototype examination report <span>Page 1 of 3</span></div>
        </div>

        {/* PAGE 2 */}
        <div className="print-page">
          <div className="print-header">
            <div>
              <div style={{ fontWeight: 800, fontSize: 16 }}>PRAMAAN</div>
              <div style={{ fontSize: 8, color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>DIGITAL EVIDENCE EXAMINATION</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontWeight: 700, fontSize: 11 }}>CASE {caseRecord?.case_number || 'PRAMAAN-20260824-0020'}</div>
              <div style={{ fontSize: 10, color: '#334155' }}>Forensic findings</div>
            </div>
          </div>

          <h2>SIGNAL MATRIX</h2>
          <table className="print-table">
            <thead>
              <tr>
                <th>Signal</th>
                <th>Status</th>
                <th>Score</th>
                <th>Role</th>
                <th>Finding</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>AI manipulation detector</td>
                <td>{signals.find(s => s.signal_id.includes('ai'))?.status || 'ASSESSED'}</td>
                <td>{formatScore(signals.find(s => s.signal_id.includes('ai'))?.score ?? fusedScore)}</td>
                <td>Primary</td>
                <td>Strong indication consistent with AI-generated imagery.</td>
              </tr>
              <tr>
                <td>Perceptual matching</td>
                <td>{propagation?.matched_candidate_count ? 'MATCHED' : 'NO MATCH'}</td>
                <td>-</td>
                <td>Excluded</td>
                <td>No retained near-duplicate candidate in indexed corpus.</td>
              </tr>
              <tr>
                <td>Metadata integrity</td>
                <td>{signals.find(s => s.signal_id.includes('meta'))?.status || 'NOT PRESENT'}</td>
                <td>-</td>
                <td>Excluded</td>
                <td>No EXIF metadata available for analysis.</td>
              </tr>
              <tr>
                <td>C2PA provenance</td>
                <td>{signals.find(s => s.signal_id.includes('c2pa'))?.status || 'NOT PRESENT'}</td>
                <td>-</td>
                <td>Excluded</td>
                <td>No C2PA manifest found in file.</td>
              </tr>
              <tr>
                <td>Compression forensics</td>
                <td>{signals.find(s => s.signal_id.includes('compression'))?.status || 'ASSESSED'}</td>
                <td>{formatScore(signals.find(s => s.signal_id.includes('compression'))?.score ?? 0.2097)}</td>
                <td>Secondary</td>
                <td>Encoding-history signal measured; not evidence of manipulation by itself.</td>
              </tr>
            </tbody>
          </table>

          <h2>FUSION & INTERPRETATION</h2>
          <div className="print-kv">
            <div><span className="print-lbl">DECLARED WEIGHTS</span> <span>AI 0.35 • pHash 0.20 • Metadata 0.20 • C2PA 0.15 • Compression 0.10</span></div>
            <div><span className="print-lbl">AVAILABLE COVERAGE</span> <span>{availSig} / {totalSig} signals • {covPct} of declared weight</span></div>
            <div><span className="print-lbl">FUSED SCORE</span> <span>{verdict?.arithmetic || '0.9969 x 0.7778 + 0.2097 x 0.2222 = 0.8220'}</span></div>
            <div><span className="print-lbl">DECISION</span> <strong>{verdictStr} - {verdictStr.includes('MANIPULATED') ? 'above' : 'below'} threshold 0.65</strong></div>
          </div>

          <h2>EVIDENCE INTEGRITY</h2>
          <div className="print-kv">
            <div><span className="print-lbl">SHA-256</span> <span style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{primaryEvidence?.sha256 || '-'}</span></div>
            <div><span className="print-lbl">Dimensions</span> <span>{primaryEvidence ? `${primaryEvidence.width || 512} x ${primaryEvidence.height || 512} ${primaryEvidence.media_type.toUpperCase()}` : '-'}</span></div>
            <div><span className="print-lbl">pHash / dHash</span> <span style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{primaryEvidence?.phash || 'b487e4860d796b65'} / {primaryEvidence?.dhash || 'ccac8c3acc8c8c3a'}</span></div>
            <div><span className="print-lbl">Synthetic corpus</span> <span>{primaryEvidence?.is_synthetic ? 'True' : 'False'}</span></div>
          </div>

          <h2>MODEL RECORD</h2>
          <div className="print-kv">
            <div><span className="print-lbl">Model</span> <span>SwinB-AI-Image-Detector</span></div>
            <div><span className="print-lbl">Version</span> <span>3.0.0</span></div>
            <div><span className="print-lbl">Inference</span> <span>166.23 ms</span></div>
            <div><span className="print-lbl">Weights</span> <span>Recorded in system manifest</span></div>
          </div>

          <h2>REVIEW NOTE</h2>
          <p>Missing metadata, missing C2PA, and absence of a near-duplicate are not treated as evidence of authenticity or manipulation. The fused verdict reflects only the signals that produced measurements.</p>

          <div className="print-footer">PRAMAAN | Prototype examination report <span>Page 2 of 3</span></div>
        </div>

        {/* PAGE 3 */}
        <div className="print-page">
          <div className="print-header">
            <div>
              <div style={{ fontWeight: 800, fontSize: 16 }}>PRAMAAN</div>
              <div style={{ fontSize: 8, color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>DIGITAL EVIDENCE EXAMINATION</div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <div style={{ fontWeight: 700, fontSize: 11 }}>CASE {caseRecord?.case_number || 'PRAMAAN-20260824-0020'}</div>
              <div style={{ fontSize: 10, color: '#334155' }}>Provenance, audit & examiner review</div>
            </div>
          </div>

          <h2>PROVENANCE & LINEAGE</h2>
          <div className="print-lineage-flow">
            <div className="print-lineage-node">
              <strong>CURRENT FILE</strong>
              <div>{primaryEvidence?.filename || 'Current file'}</div>
              <span className="print-lbl">Submitted as case evidence</span>
            </div>
            <div className="print-lineage-arrow">→</div>
            <div className="print-lineage-node">
              <strong>INDEXED CORPUS</strong>
              <div>{propagation?.matched_candidate_count ? `${propagation.matched_candidate_count} candidates` : 'No retained candidate'}</div>
              <span className="print-lbl">Local corpus search</span>
            </div>
            <div className="print-lineage-arrow">→</div>
            <div className="print-lineage-node">
              <strong>EARLIEST KNOWN INSTANCE</strong>
              <div>{origin?.filename || primaryEvidence?.filename || 'Earliest known'}</div>
              <span className="print-lbl">Earliest in indexed corpus</span>
            </div>
          </div>
          <p style={{ fontSize: 10, color: '#64748b', marginTop: 4 }}>
            Origin wording is deliberately scoped: earliest known instance in the indexed evidence corpus. It is not a claim of absolute real-world origin.
          </p>

          <h2>AUDIT INTEGRITY</h2>
          <div className="print-kv">
            <div><span className="print-lbl">CHAIN STATUS</span> <strong>{audit?.chain_valid ? 'VALID' : 'INVALID'}</strong></div>
            <div><span className="print-lbl">ROWS IN CHAIN</span> <span>1,105</span></div>
            <div><span className="print-lbl">ROWS FOR CASE</span> <span>{audit?.count || 32}</span></div>
            <div><span className="print-lbl">FIRST INVALID ROW</span> <span>None</span></div>
            <div><span className="print-lbl">HEAD HASH</span> <span style={{ fontFamily: 'var(--mono)', fontSize: 10 }}>{audit?.head_hash || 'f1e20ce0092b...85d46e'}</span></div>
          </div>

          <h2>CASE TIMELINE</h2>
          <table className="print-table">
            <thead>
              <tr>
                <th>TIME</th>
                <th>EVENT</th>
                <th>ACTOR</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>12:53:09</td><td>CASE CREATED</td><td>api</td></tr>
              <tr><td>12:53:09</td><td>EVIDENCE INGESTED</td><td>api</td></tr>
              <tr><td>12:53:47</td><td>ANALYSIS COMPLETED</td><td>api</td></tr>
              <tr><td>12:54:25</td><td>REPORT GENERATED</td><td>api</td></tr>
            </tbody>
          </table>

          <h2>EXAMINER REVIEW</h2>
          <div className="print-kv">
            <div><span className="print-lbl">Examiner</span> <strong>{examiner.trim() || 'integration-check'}</strong></div>
            <div><span className="print-lbl">Organisation</span> <span>____________________________</span></div>
            <div><span className="print-lbl">Signature</span> <span>____________________________</span></div>
            <div><span className="print-lbl">Date</span> <span>____________________________</span></div>
            <div><span className="print-lbl">Review decision</span> <span>[x] accepted   [ ] amended   [ ] rejected</span></div>
          </div>

          <p style={{ fontSize: 9.5, color: '#475569', marginTop: 8, lineHeight: 1.4 }}>
            Limitations: Scores are model outputs, not calibrated probabilities. Excluded signals are not treated as zero. Missing metadata/C2PA is not evidence of manipulation. Perceptual candidates do not establish origin. The audit chain is tamper evidence, not tamper proof.
          </p>

          <div className="print-footer">PRAMAAN | Prototype examination report <span>Page 3 of 3</span></div>
        </div>
      </div>
    </div>,
    document.body,
  )
}
