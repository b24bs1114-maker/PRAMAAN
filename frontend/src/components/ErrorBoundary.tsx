/**
 * ErrorBoundary - the app's last line of defence against a blank screen.
 *
 * A render-time exception in any screen would otherwise unmount the whole React
 * tree and leave an empty page with no explanation. This boundary catches it,
 * keeps the surrounding shell (header + sidebar) intact, and shows the actual
 * error plus a way to recover - because an honest error state is always better
 * than a blank one.
 *
 * It is deliberately generic: it wraps the routed screen in App, so every page
 * inherits the guarantee without each one re-implementing try/catch rendering.
 * Give it a `key` that changes with the route so navigating away resets it.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react'
import { ErrorBanner } from './Banner'
import { Icon } from './Icon'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the details in the console for debugging; the UI shows the message.
    console.error('Screen render error:', error, info.componentStack)
  }

  private reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      return (
        <div className="screen stack" style={{ gap: 'var(--space-5)' }}>
          <div className="screen__head">
            <h1 className="screen__title">This screen hit an error</h1>
            <p className="screen__lead">
              The page failed to render. Your case data is intact - nothing was written or lost.
              Retry, or switch to another screen from the sidebar.
            </p>
          </div>
          <ErrorBanner context="Screen" error={this.state.error} />
          <div className="btn-row">
            <button type="button" className="btn btn--primary" onClick={this.reset}>
              <Icon name="refresh" size={15} />
              Reload screen
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
