/**
 * Screen: Sign in.
 *
 * The gate in front of the whole console, and the reason the intake form no
 * longer has an examiner box. Whoever signs in here is the name the backend
 * stamps on every piece of evidence they ingest, resolved server-side from the
 * bearer token this screen obtains -- so identity is authenticated, not typed.
 *
 * What this screen deliberately does not do:
 *
 *   - guess or pre-fill a username; there is no "default operator"
 *   - tell the operator which half of a wrong credential pair was wrong, because
 *     the backend answers both identically and this shows its message verbatim
 *   - report success before the token has been exchanged and accepted
 *
 * The backend seeds operator accounts on first boot from its own settings, so a
 * fresh deployment has real credentials without anyone being invented here.
 */

import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL, API_BASE_URL_IS_EXPLICIT } from '../api'
import { Banner } from '../components/Banner'
import { Icon } from '../components/Icon'
import { Spinner } from '../components/Feedback'
import type { AuthController } from '../state/useAuth'
import type { BackendHealth } from '../state/useInvestigation'
import type { ThemeController } from '../state/useTheme'

export function ScreenLogin({
  auth,
  health,
  theme,
  onRetryHealth,
}: {
  auth: AuthController
  health: BackendHealth
  theme: ThemeController
  onRetryHealth: () => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const usernameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  const { signingIn, error, signIn } = auth
  // Both fields have content. The backend enforces this too (a blank password is
  // a 422), so this only spares the operator a round trip.
  const complete = username.trim().length > 0 && password.length > 0
  const unreachable = health === 'down'

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!complete || signingIn) return
    void signIn(username.trim(), password)
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={submit}>
        <div className="login-card__brand">
          <img
            src={
              theme.resolved === 'light'
                ? '/assets/pramaan-logo-light.png'
                : '/assets/pramaan-logo-dark.png'
            }
            alt="PRAMAAN | प्रमाण"
            className="login-card__logo"
          />
          <p className="login-card__tagline">
            Digital Evidence Examination &amp; Provenance
          </p>
        </div>

        <div className="login-card__notice">
          <Icon name="lock" size={13} />
          <span>
            Evidence is recorded against the operator who signs in. Sign in with your
            own credentials.
          </span>
        </div>

        {unreachable ? (
          <Banner
            tone="error"
            title="Backend not reachable"
            detail="Sign-in requires the backend. Credentials are not checked in the browser."
            meta={`Configured base URL: ${API_BASE_URL}${
              API_BASE_URL_IS_EXPLICIT ? ' (from VITE_API_URL)' : ' (default - VITE_API_URL not set)'
            }`}
          >
            <div className="btn-row" style={{ marginTop: 10 }}>
              <button type="button" className="btn btn--ghost" onClick={onRetryHealth}>
                <Icon name="refresh" size={14} />
                Retry connection
              </button>
            </div>
          </Banner>
        ) : null}

        <div className="field">
          <label className="field__label" htmlFor="login-username">
            Operator ID
          </label>
          <input
            ref={usernameRef}
            id="login-username"
            className="input"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={signingIn}
            required
          />
        </div>

        <div className="field">
          <label className="field__label" htmlFor="login-password">
            Password
          </label>
          <input
            id="login-password"
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={signingIn}
            required
          />
        </div>

        {/* The backend's own words. A wrong operator ID and a wrong password
            produce the same sentence, on purpose -- nothing here embellishes it
            into a hint about which one was wrong. */}
        {error ? <Banner tone="error" title="Sign-in failed" detail={error} /> : null}

        <button
          type="submit"
          className="btn btn--primary"
          disabled={!complete || signingIn}
          style={{ width: '100%', justifyContent: 'center', padding: '10px 20px', fontWeight: 700 }}
        >
          {signingIn ? <Spinner /> : <Icon name="shield" size={15} />}
          {signingIn ? 'Verifying credentials…' : 'Sign In'}
        </button>

        <p className="login-card__foot">
          Sessions expire, and every sign-in is written to the audit trail.
        </p>
      </form>
    </div>
  )
}
