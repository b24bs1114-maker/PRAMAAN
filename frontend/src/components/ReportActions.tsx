/**
 * The two things an operator does with a generated report, in one place.
 *
 * Both actions fetch the PDF through the authenticated transport: the download
 * route requires the operator's bearer token, so an `<a href>` pointed at it
 * cannot work -- a plain link is an unauthenticated browser navigation. The three
 * "Open PDF" links this replaces were exactly that, and the two "Download PDF"
 * buttons beside them were two copies of the same blob dance, one of which
 * swallowed its errors entirely.
 *
 * Opening in a new tab keeps the tab that the click opened: the window is opened
 * synchronously inside the gesture and pointed at the blob once it arrives, because
 * a `window.open` issued after an await is what popup blockers exist to stop.
 */

import { useState } from 'react'
import { api } from '../api'
import { Icon } from './Icon'
import { Spinner } from './Feedback'
import type { StoredReport } from '../api/types'
import { ErrorBanner } from './Banner'

type Busy = null | 'download' | 'open'

export function ReportFileActions({
  report,
  layout = 'row',
  size = 'sm',
  openLabel = 'Open PDF',
}: {
  report: StoredReport
  layout?: 'row' | 'stack'
  size?: 'sm' | 'md'
  /** The Reports screen says "Open in new tab"; the dossier says "Open PDF". */
  openLabel?: string
}) {
  const [busy, setBusy] = useState<Busy>(null)
  const [error, setError] = useState<unknown>(null)

  const btnSize = size === 'sm' ? ' btn--sm' : ''

  const download = async () => {
    setBusy('download')
    setError(null)
    try {
      const blob = await api.downloadReport(report.download_url)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = report.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (cause) {
      setError(cause)
    } finally {
      setBusy(null)
    }
  }

  const open = async () => {
    // Opened first, inside the click, or the browser treats it as a popup.
    const tab = window.open('', '_blank')
    setBusy('open')
    setError(null)
    try {
      const blob = await api.downloadReport(report.download_url)
      const url = URL.createObjectURL(blob)
      if (tab) {
        tab.location.href = url
      } else {
        // The tab was blocked anyway. Fall back to a download rather than
        // discarding the bytes the operator already waited for.
        const a = document.createElement('a')
        a.href = url
        a.download = report.filename
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
      }
    } catch (cause) {
      tab?.close()
      setError(cause)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="stack" style={{ gap: 8 }}>
      <div
        className={layout === 'row' ? 'row' : 'stack'}
        style={layout === 'row' ? { gap: 8, flexWrap: 'wrap' } : { gap: 8 }}
      >
        <button
          type="button"
          className={`btn btn--primary${btnSize}`}
          onClick={download}
          disabled={busy !== null}
        >
          {busy === 'download' ? <Spinner /> : <Icon name="download" size={13} />}
          {busy === 'download' ? 'Preparing PDF…' : 'Download PDF'}
        </button>
        <button
          type="button"
          className={`btn btn--ghost${btnSize}`}
          onClick={open}
          disabled={busy !== null}
        >
          {busy === 'open' ? <Spinner /> : <Icon name="external" size={13} />}
          {busy === 'open' ? 'Opening…' : openLabel}
        </button>
      </div>
      {/* The failure is stated where the click was, not swallowed: one of these
          call sites used to report nothing at all when the fetch failed. */}
      {error ? <ErrorBanner error={error} context="Report PDF retrieval" /> : null}
    </div>
  )
}
