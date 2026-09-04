/**
 * Report artefact card.
 *
 * The backend renders and hashes the PDF. This file lists it, states its
 * properties and downloads it -- and deliberately draws a document *glyph*
 * rather than a page preview, because a mocked first page would be the frontend
 * inventing report content that the real PDF may not contain.
 *
 * `audit_chain_valid` is the backend's assertion about the chain at the moment
 * the report was rendered. It is reported as exactly that -- a property of the
 * artefact, timestamped -- and never promoted into a live claim that the chain is
 * intact now.
 */

import { Icon } from './Icon'
import { HashChip } from './Hash'
import { Button, Field, Fields, StatusPill } from './Primitives'
import { formatBytes, formatTimestamp, NOT_MEASURED } from '../lib/format'
import { humanise } from '../lib/tone'
import type { ReportResponse } from '../api/types'

export function ReportArtefact({
  report,
  onDownload,
  busy,
}: {
  report: ReportResponse
  onDownload?: (report: ReportResponse) => void
  busy?: boolean
}) {
  return (
    <div className="artefact">
      <div className="artefact__glyph" aria-hidden="true">
        <span className="artefact__glyph-line" />
        <span className="artefact__glyph-line" />
        <span className="artefact__glyph-line" />
        <span className="artefact__glyph-line" />
        <span className="artefact__glyph-line" />
        <span className="artefact__glyph-line" />
      </div>

      <div className="artefact__main">
        <div className="artefact__head">
          <div className="artefact__titles">
            <div className="artefact__name">{report.filename}</div>
            <div className="artefact__ids">
              <span className="mono">{report.report_id}</span>
              <span>{formatTimestamp(report.generated_at)}</span>
            </div>
          </div>
          <div className="artefact__actions">
            <StatusPill tone={report.document_status === 'final' ? 'ok' : 'warn'}>
              {humanise(report.document_status)}
            </StatusPill>
            {onDownload ? (
              <Button size="sm" icon="download" busy={busy} onClick={() => onDownload(report)}>
                Download PDF
              </Button>
            ) : null}
          </div>
        </div>

        <Fields variant="wide">
          <Field label="Size" value={formatBytes(report.size_bytes)} mono />
          <Field
            label="Pages"
            value={report.pages === null ? NOT_MEASURED : `${report.pages}`}
            mono
            unmeasured={report.pages === null}
            note={report.pages === null ? 'The renderer did not report a page count.' : undefined}
          />
          <Field label="Renderer" value={`${report.generator} · ${report.renderer}`} mono />
          <Field label="Report SHA-256" value={<HashChip value={report.sha256} algo={null} length={18} />} />
          <Field
            label="Audit head at render"
            value={<HashChip value={report.audit_head_hash} algo={null} length={18} />}
            note="The head of the audit chain at the moment this PDF was produced."
          />
          <Field
            label="Chain state at render"
            value={report.audit_chain_valid ? 'Verified when rendered' : 'Not verified when rendered'}
            unmeasured={!report.audit_chain_valid}
            note={
              report.audit_chain_valid
                ? 'The backend verified the chain while producing this document. It is not a statement about the chain now — re-verify on the Audit screen for that.'
                : 'The backend did not confirm chain integrity while producing this document.'
            }
          />
        </Fields>

        <p className="artefact__note">
          <Icon name="lock" size={12} /> This PDF is the forensic record. PRAMAAN's interface does not
          reproduce or summarise its contents — download it to read what was filed.
        </p>
      </div>
    </div>
  )
}
