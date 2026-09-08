/**
 * Screen: Sign in.
 *
 * The gate in front of the whole console, and the reason the intake form no
 * longer has an examiner box. Whoever signs in here is the name the backend
 * stamps on every piece of evidence they ingest, resolved server-side from the
 * bearer token this screen obtains -- so identity is authenticated, not typed.
 *
 * The command-centre scene behind the card -- the Chandigarh Police masthead,
 * the PRAMAAN wordmark and gold motto, the examination workstation and the
 * physical evidence dossier -- is one baked cinematic still
 * (public/assets/signin-cinematic-bg.jpg). Only the card on the right is live.
 * The page is deliberately fixed gold-on-navy, the livery of the Chandigarh
 * Police, and does not follow the console's light/dark theme: this is the
 * institutional front door, not a workspace surface. Its palette is self-
 * contained in `.signin-*` styles so it cannot drift when the app theme changes.
 *
 * What this screen deliberately does not do:
 *
 *   - guess or pre-fill an operator id; there is no "default operator"
 *   - offer sign-in methods the backend does not have. Single sign-on and self-
 *     service password reset are shown only as plainly disabled, captioned facts,
 *     because inventing a working-looking control the server cannot honour would
 *     be a lie told to an investigating officer
 *   - tell the operator which half of a wrong credential pair was wrong, because
 *     the backend answers both identically and this shows its message verbatim
 *   - report success before the token has been exchanged and accepted
 *
 * The backend seeds operator accounts on first boot from its own settings, so a
 * fresh deployment has real credentials without anyone being invented here. When
 * the local development auth bypass is active the backend answers `/api/auth/me`
 * with the development operator and this screen is never reached -- so nothing
 * here needs to know or care about the bypass.
 */

import { useEffect, useRef, useState } from 'react'
import { Icon } from '../components/Icon'
import type { AuthController } from '../state/useAuth'
import type { BackendHealth } from '../state/useInvestigation'

export function ScreenLogin({
  auth,
}: {
  auth: AuthController
  // Retained on the contract so the shell can pass live backend health, but the
  // scene no longer prints a reachability banner: a failed sign-in surfaces the
  // backend's own words below, and success is still gated on a real token.
  health: BackendHealth
  onRetryHealth: () => void
}) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  // Default on, matching the historical behaviour (token in localStorage). An
  // operator on a shared workstation turns it off to keep the session in this
  // tab only. See useAuth's writeStoredToken.
  const [remember, setRemember] = useState(true)
  const [showPassword, setShowPassword] = useState(false)
  const usernameRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  const { signingIn, error, signIn } = auth
  // Both fields have content. The backend enforces this too (a blank password is
  // a 422), so this only spares the operator a round trip.
  const complete = username.trim().length > 0 && password.length > 0

  const submit = (event: React.FormEvent) => {
    event.preventDefault()
    if (!complete || signingIn) return
    void signIn(username.trim(), password, remember)
  }

  return (
    <div className="signin-scene">
      {/* The wordmark, department and motto are baked into the scene image for
          sighted operators; name them here so the page is not silent to a
          screen reader landing on the sign-in gate. */}
      <h1 className="sr-only">
        PRAMAAN — प्रमाण — Digital Forensics &amp; Evidence Investigation, Chandigarh Police
      </h1>
      <p className="sr-only">Evidence builds truth. We help you find it.</p>

      {/* Baked command-centre still + a right-edge scrim for card legibility.
          Both are decorative; the meaningful text lives in the card and the
          screen-reader-only header above. */}
      <div className="signin-scene__bg" aria-hidden="true" />
      <div className="signin-scene__scrim" aria-hidden="true" />

      {/* ---- Sign-in Card (the only live element) ------------------------- */}
      <section className="signin-card" aria-labelledby="signin-heading">
        <header className="signin-card__head">
          <p className="signin-card__welcome">Welcome to</p>
          <div className="signin-card__brand-title" id="signin-heading">
            <span className="signin-card__pramaan">PRAMAAN</span>
            <span className="signin-card__hindi" lang="hi">प्रमाण</span>
          </div>
          <p className="signin-card__dept">Chandigarh Police</p>
          <p className="signin-card__subdept">Digital Forensics &amp; Evidence Investigation</p>
          <div className="signin-card__access-banner">
            Secure Access for Authorized Personnel
          </div>
        </header>

        <form className="signin-form" onSubmit={submit} noValidate>
          <div className="signin-field">
            <label className="signin-label" htmlFor="signin-operator">
              Official email
            </label>
            <div className="signin-input-wrap">
              <span className="signin-input-icon" aria-hidden="true">
                <Icon name="mail" size={16} />
              </span>
              <input
                ref={usernameRef}
                id="signin-operator"
                className="signin-input signin-input--with-icon"
                type="text"
                autoComplete="username"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
                placeholder="name@agency.gov.in"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={signingIn}
                required
              />
            </div>
          </div>

          <div className="signin-field">
            <label className="signin-label" htmlFor="signin-password">
              Password
            </label>
            <div className="signin-input-wrap">
              <span className="signin-input-icon" aria-hidden="true">
                <Icon name="lock" size={16} />
              </span>
              <input
                id="signin-password"
                className="signin-input signin-input--with-icon signin-input--password"
                type={showPassword ? 'text' : 'password'}
                autoComplete="current-password"
                placeholder="Enter your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={signingIn}
                required
              />
              <button
                type="button"
                className="signin-reveal"
                onClick={() => setShowPassword((v) => !v)}
                aria-pressed={showPassword}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
                disabled={signingIn}
                tabIndex={0}
              >
                <Icon name={showPassword ? 'eye-off' : 'eye'} size={17} />
              </button>
            </div>
          </div>

          {/* Backend's exact words for failed login */}
          {error ? (
            <div className="signin-alert" role="alert">
              <Icon name="error" size={16} className="signin-alert__icon" />
              <div className="signin-alert__body">
                <p className="signin-alert__title">Sign-in failed</p>
                <p className="signin-alert__detail">{error}</p>
              </div>
            </div>
          ) : null}

          <div className="signin-options-row">
            <label className="signin-remember">
              <input
                type="checkbox"
                className="signin-remember__box"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                disabled={signingIn}
              />
              <span className="signin-remember__text">Keep me signed in</span>
            </label>
            <span
              className="signin-forgot-link"
              title="Password reset requires departmental administrator authorization."
            >
              Forgot password?
            </span>
          </div>

          <button
            type="submit"
            className="signin-submit"
            disabled={!complete || signingIn}
          >
            {signingIn ? (
              <>
                <span className="signin-submit__spin" aria-hidden="true" />
                Signing in…
              </>
            ) : (
              <>
                Sign in
                <Icon name="arrow-right" size={16} />
              </>
            )}
          </button>
        </form>

        {/* Institutional / SSO Alternative Access */}
        <div className="signin-alt">
          <div className="signin-divider">
            <span>OR</span>
          </div>
          <button
            type="button"
            className="signin-sso"
            disabled
            aria-disabled="true"
            title="Single sign-on requires departmental institutional credentials."
          >
            <Icon name="user" size={16} />
            Sign in with organization account
          </button>
        </div>

        <div className="signin-security-badge">
          <Icon name="shield" size={18} className="signin-security-badge__icon" />
          <div className="signin-security-badge__text">
            <strong>Secure examination environment</strong>
            <span>All access is logged and monitored</span>
          </div>
        </div>
      </section>
    </div>
  )
}
