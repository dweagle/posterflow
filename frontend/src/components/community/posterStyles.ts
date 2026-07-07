// Shared community poster-style vocabulary, used by every request-creation
// surface (the New Community Request modal and the Lists "Move to Request" flow)
// so the two never drift.
import { useCallback, useState } from 'react'

// Required, single-select style. The stored tag values keep the "… Style" suffix
// for consistency with existing requests; cards derive the badge from them.
export const POSTER_STYLES: { value: string; label: string }[] = [
  { value: 'CL2K Style', label: 'CL2K' },
  { value: 'MM2K Style', label: 'MM2K' },
]

// Optional extra style preferences (multi-select).
export const EXTRA_TAGS = ['Anime Movie', 'Anime TV']

export function isValidDiscordUsername(value: string): boolean {
  const v = value.trim()
  return v.length >= 2 && v.length <= 32 && !/^@?(everyone|here)$/i.test(v)
}

// ── Remembered poster-style choice ───────────────────────────────────────────
// The last CL2K/MM2K the user picked, kept in localStorage so it sticks across
// modal opens and page reloads instead of resetting each time.
const POSTER_STYLE_STORAGE_KEY = 'posterflow.posterStyle'

function isKnownPosterStyle(value: unknown): value is string {
  return typeof value === 'string' && POSTER_STYLES.some((s) => s.value === value)
}

export function getStoredPosterStyle(): string | null {
  try {
    const raw = localStorage.getItem(POSTER_STYLE_STORAGE_KEY)
    return isKnownPosterStyle(raw) ? raw : null
  } catch {
    return null
  }
}

export function setStoredPosterStyle(value: string): void {
  try {
    localStorage.setItem(POSTER_STYLE_STORAGE_KEY, value)
  } catch {
    /* ignore blocked/unavailable storage */
  }
}

// State seeded from — and written back to — the persisted choice, so selecting a
// style remembers it for next time. For toggles that only ever set a value.
export function usePersistedPosterStyle(): [string | null, (value: string) => void] {
  const [style, setStyle] = useState<string | null>(getStoredPosterStyle)
  const update = useCallback((value: string) => {
    setStyle(value)
    setStoredPosterStyle(value)
  }, [])
  return [style, update]
}
