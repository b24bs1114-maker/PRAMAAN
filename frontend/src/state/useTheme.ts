import { useCallback, useEffect, useState } from 'react'

export type ThemeMode = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'pramaan_theme'

function readStoredMode(): ThemeMode {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'light' || saved === 'dark' || saved === 'system') return saved
  return 'dark'
}

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function effectiveTheme(mode: ThemeMode): 'light' | 'dark' {
  return mode === 'system' ? (systemPrefersDark() ? 'dark' : 'light') : mode
}

/**
 * Theme controller.
 *
 * Persists the user's chosen MODE (Day / Night / System) and applies the
 * resolved theme to <html data-theme>. In System mode it tracks the OS
 * preference live, so the workstation follows the operator's environment
 * without a manual toggle.
 */
export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readStoredMode)
  const [resolved, setResolved] = useState<'light' | 'dark'>(() => effectiveTheme(readStoredMode()))

  // Apply the resolved theme and persist the chosen mode.
  useEffect(() => {
    const applied = effectiveTheme(mode)
    setResolved(applied)
    document.documentElement.setAttribute('data-theme', applied)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [mode])

  // While in System mode, react to OS light/dark changes.
  useEffect(() => {
    if (mode !== 'system') return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => {
      const applied = mq.matches ? 'dark' : 'light'
      setResolved(applied)
      document.documentElement.setAttribute('data-theme', applied)
    }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [mode])

  const setThemeMode = useCallback((next: ThemeMode) => setMode(next), [])

  return { mode, resolved, setMode: setThemeMode }
}

/** The controller returned by {@link useTheme}, for passing down to the Settings screen. */
export type ThemeController = ReturnType<typeof useTheme>
