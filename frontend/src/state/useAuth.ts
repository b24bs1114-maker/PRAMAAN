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
 * localStorage, not sessionStorage: an examiner who reloads mid-case, or reopens
 * the console after closing the tab, should not be signed out and lose the case
 * they were working on. The stored value is the token only -- never the password,
 * and never the user record, which is re-fetched from the server on every load so
 * a renamed or revoked account cannot linger in a cache.
 */
const TOKEN_KEY = 'pramaan_auth_token'

function readStoredToken(): string | null {
  try {
    const saved = localStorage.getItem(TOKEN_KEY)
    return saved && saved.trim() ? saved : null
  } catch {
    // Private-browsing modes can throw on access. No stored session, then.
    return null
  }
}

function writeStoredToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
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
  signIn: (username: string, password: string) => Promise<boolean>
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

    async function resolveIdentity() {
      try {
        const confirmed = await api.currentUser()
        if (!cancelled) setUser(confirmed)
      } catch {
        if (cancelled) return
        forgetRef.current()
        // If an invalid or revoked token was stored, clear it and query what identity remains.
        // In normal mode (Mode A), an unauthenticated request returns 401 and drops to ScreenLogin.
        // In dev bypass mode (Mode B), it returns 200 with the development operator,
        // opening the console immediately without forcing a second reload.
        try {
          const remaining = await api.currentUser()
          if (!cancelled) setUser(remaining)
        } catch {
          // Genuinely unauthenticated or backend unreachable.
        }
      } finally {
        if (!cancelled) setRestoring(false)
      }
    }

    void resolveIdentity()

    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (username: string, password: string): Promise<boolean> => {
    setSigningIn(true)
    setError(null)
    try {
      const session = await api.login({ username, password })
      // Install before setting state: the token has to be attachable by the time
      // anything reacts to a signed-in operator and starts fetching.
      setAuthToken(session.token)
      writeStoredToken(session.token)
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
    try {
      // Ask the server what identity remains. Normally none, and this 401s
      // straight to the login screen. Under the development bypass the answer is
      // the development operator, and honouring it keeps the console open --
      // pinning the UI to the login screen would demand a password that the
      // server is currently configured not to require.
      const remaining = await api.currentUser()
      setUser(remaining)
    } catch {
      // No identity left. `forget` already cleared it; nothing more to do.
    } finally {
      setSigningOut(false)
    }
  }, [forget])

  return { user, restoring, signingIn, signingOut, error, signIn, signOut }
}

/** The controller returned by {@link useAuth}, for passing down to screens. */
export type AuthController = ReturnType<typeof useAuth>
