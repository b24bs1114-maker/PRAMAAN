/**
 * The signed-in operator: the single source of identity in the frontend.
 *
 * Nothing in the UI types, guesses or defaults an examiner's name. The name that
 * ends up on evidence is `user.display_name` here, which came from
 * `GET /api/auth/me` -- and the backend stamps that same identity server-side from
 * the bearer token, so the field the intake screen shows is a mirror of what will
 * be recorded, not an input to it.
 *
 * Three states, and they are deliberately distinguishable:
 *
 *   restoring = true             a stored token is being checked; render nothing
 *   user === null                nobody is signed in; render the login screen
 *   user !== null                signed in, and the server confirmed it just now
 *
 * A stored token is never trusted on its face. On load it is installed into the
 * transport and immediately spent on `/api/auth/me`; only a 200 promotes it to a
 * session. That is the difference between "we have a token" and "we are signed
 * in", and it is why a revoked or expired token drops straight to the login
 * screen instead of failing on the operator's first real action.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  api,
  setAuthToken,
  setUnauthorizedHandler,
  type AuthUser,
} from '../api'

/**
 * Where the token is kept between page loads.
 *
 * Two stores, chosen by the sign-in screen's "Keep me signed in" box:
 *
 *   remember = true   localStorage: survives closing the tab and reopening the
 *                     console, so an examiner mid-case is not signed out by a
 *                     reload. This is the default and the historical behaviour.
 *   remember = false  sessionStorage: cleared when the tab closes, for a shared
 *                     or public machine. A reload still keeps the session --
 *                     sessionStorage outlives a refresh, only not the tab.
 *
 * Either way the stored value is the token only -- never the password, and never
 * the user record, which is re-fetched from the server on every load so a
 * renamed or revoked account cannot linger in a cache.
 */
const TOKEN_KEY = 'pramaan_auth_token'

function readStoredToken(): string | null {
  try {
    // localStorage first (a "remembered" session), then sessionStorage. A token
    // only ever lives in one at a time; writeStoredToken clears the other.
    const saved =
      localStorage.getItem(TOKEN_KEY) ?? sessionStorage.getItem(TOKEN_KEY)
    return saved && saved.trim() ? saved : null
  } catch {
    // Private-browsing modes can throw on access. No stored session, then.
    return null
  }
}

function writeStoredToken(token: string | null, remember = true): void {
  try {
    // Always clear both stores first, so switching "remember" off cannot leave a
    // stale copy behind in localStorage, and signing out clears either home.
    localStorage.removeItem(TOKEN_KEY)
    sessionStorage.removeItem(TOKEN_KEY)
    if (token) {
      ;(remember ? localStorage : sessionStorage).setItem(TOKEN_KEY, token)
    }
  } catch {
    // Storage unavailable: the session still works, it just will not survive a
    // reload. Failing the sign-in over this would be worse.
  }
}

export interface AuthState {
  /** The signed-in operator, or null when nobody is. */
  user: AuthUser | null
  /** True while a stored token is being verified against the backend. */
  restoring: boolean
  /** True while a sign-in request is in flight. */
  signingIn: boolean
  /** True while the token is being revoked server-side. */
  signingOut: boolean
  /** Why the last sign-in attempt failed, in the backend's words. */
  error: string | null
  /**
   * Sign in with real credentials. `remember` chooses where the token is kept:
   * true (default) persists it in localStorage across tab close; false keeps it
   * in sessionStorage, cleared when the tab closes. It never changes what the
   * backend accepts -- only how long this browser holds the resulting session.
   */
  signIn: (username: string, password: string, remember?: boolean) => Promise<boolean>
  signOut: () => Promise<void>
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<AuthUser | null>(null)
  // Always true to start: identity is unknown until `/api/auth/me` answers, and
  // that holds whether or not a token was stored, because the server may also
  // supply a development-bypass identity for a request that carries no token.
  // Starting false would flash the login screen at someone who is about to be
  // told they are already signed in.
  const [restoring, setRestoring] = useState(true)
  const [signingIn, setSigningIn] = useState(false)
  const [signingOut, setSigningOut] = useState(false)
  const [error, setError] = useState<string | null>(null)

  /**
   * Forget the session locally.
   *
   * Used both by an explicit sign-out and by the transport's 401 handler. Clears
   * the token from storage and from the transport before clearing React state, so
   * no request already being assembled can still carry the dead token.
   */
  const forget = useCallback(() => {
    writeStoredToken(null)
    setAuthToken(null)
    setUser(null)
  }, [])

  // A ref so the effect below can install the handler once, without re-running
  // every time `forget` is re-created.
  const forgetRef = useRef(forget)
  forgetRef.current = forget

  /**
   * Sign out when the server rejects the token we are holding.
   *
   * A session can expire or be revoked while a tab sits open. Without this the UI
   * would keep a dead token and show an error on every action; with it, the app
   * falls back to the login screen, which is the truthful state.
   */
  useEffect(() => {
    setUnauthorizedHandler(() => forgetRef.current())
    return () => setUnauthorizedHandler(null)
  }, [])

  /**
   * Establish identity once, on mount.
   *
   * `/api/auth/me` is asked in both cases, with a stored token if there is one
   * and without if there is not. That single request is also how the local
   * development auth bypass is discovered: when it is active the backend answers
   * an unauthenticated `me` with the development operator and `dev_bypass: true`,
   * and when it is not it answers 401 and the login screen goes up.
   *
   * Asking the server is the whole design. The browser is never allowed to decide
   * that authentication is off -- there is no local flag to set, and a tampered
   * localStorage cannot produce a session, because the only thing that can is a
   * 200 from this endpoint.
   */
  useEffect(() => {
    const token = readStoredToken()
    let cancelled = false
    if (token) setAuthToken(token)
    api
      .currentUser()
      .then((confirmed) => {
        if (cancelled) return
        setUser(confirmed)
      })
      .catch(() => {
        // No identity: the token is expired or revoked, nobody is signed in, or
        // the backend is unreachable. There is no authenticated operator to name
        // on evidence, so the session is dropped rather than assumed. The 401
        // handler above may already have done this.
        if (cancelled) return
        forgetRef.current()
      })
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (
    username: string,
    password: string,
    remember = true,
  ): Promise<boolean> => {
    setSigningIn(true)
    setError(null)
    try {
      const session = await api.login({ username, password })
      // Install before setting state: the token has to be attachable by the time
      // anything reacts to a signed-in operator and starts fetching.
      setAuthToken(session.token)
      writeStoredToken(session.token, remember)
      setUser(session.user)
      return true
    } catch (cause) {
      // The backend's own message, verbatim. It reports a wrong username and a
      // wrong password identically on purpose, and this does not embellish it.
      setError(
        cause instanceof ApiError
          ? cause.message
          : 'Sign-in failed. The backend could not be reached.',
      )
      forget()
      return false
    } finally {
      setSigningIn(false)
    }
  }, [forget])

  const signOut = useCallback(async () => {
    setSigningOut(true)
    try {
      // Revoke server-side first, while the token is still attached, so the row is
      // actually invalidated and the sign-out reaches the audit trail.
      await api.logout()
    } catch {
      // The token may already be gone, or the backend unreachable. The local
      // session is dropped regardless: refusing to sign out because the server
      // did not answer would leave the operator stuck signed in.
    }
    forget()
    setError(null)
    setSigningOut(false)
    // Signing out ends at the sign-in screen, full stop. We deliberately do NOT
    // re-ask `/api/auth/me` here: under the development bypass the server would
    // answer with the development operator again and silently sign the console
    // back in, so the Log Out button would appear to do nothing. `forget` has
    // already cleared the user, which drops the app to ScreenLogin. A page reload
    // still restores the bypass identity via the mount effect -- the bypass is
    // not disabled, it is simply not honoured as an *implicit* re-login on an
    // explicit sign-out.
  }, [forget])

  return { user, restoring, signingIn, signingOut, error, signIn, signOut }
}

/** The controller returned by {@link useAuth}, for passing down to screens. */
export type AuthController = ReturnType<typeof useAuth>
