// Shared community poster-style vocabulary, used by every request-creation
// surface (the New Community Request modal and the Lists "Move to Request" flow)
// so the two never drift.
import { useCallback, useEffect, useState } from 'react'
import { getFulfilledRequestStyles } from '../../api/community'

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

// ── "Already made" warning ───────────────────────────────────────────────────
// Re-requesting a fulfilled item is allowed, but the user should know it was
// already made and explain in the notes what they want different. Returns the
// warning text for the current item + chosen style, or null.
export function useAlreadyMadeWarning(
  item: {
    tmdb_id?: number | null
    media_type?: string | null
    season_number?: number | null
    title?: string | null
  } | null,
  styleValue: string | null,
): string | null {
  const [fulfilled, setFulfilled] = useState<string[] | null>(null)
  const tmdbId = item?.tmdb_id ?? null
  const mediaType = item?.media_type ?? null
  const seasonNumber = item?.season_number ?? null
  const title = (item?.title ?? '').trim()

  useEffect(() => {
    setFulfilled(null)
    if (!mediaType || (tmdbId == null && !title)) return
    // Multi-season list items carry no season number; a show-wide lookup would
    // false-warn off any one fulfilled season, so skip the check for those.
    if (mediaType === 'season' && seasonNumber == null) return
    const params: Record<string, string | number> = { media_type: mediaType }
    if (tmdbId != null) {
      params.tmdb_id = tmdbId
      if (seasonNumber != null) params.season_number = seasonNumber
    } else {
      params.title = title
    }
    let cancelled = false
    // Small debounce so typing a custom title doesn't fire a request per key.
    const id = setTimeout(() => {
      getFulfilledRequestStyles(params)
        .then((r) => { if (!cancelled) setFulfilled(r.styles) })
        .catch(() => { /* best-effort — no warning on lookup failure */ })
    }, 350)
    return () => { cancelled = true; clearTimeout(id) }
  }, [tmdbId, mediaType, seasonNumber, title])

  if (!fulfilled || !styleValue) return null
  const label = POSTER_STYLES.find((s) => s.value === styleValue)?.label ?? styleValue
  if (fulfilled.includes(label)) {
    return `This poster was already made in ${label} style. Only request it again if you need something different — and say what in the notes.`
  }
  if (fulfilled.includes('')) {
    return 'This poster may have already been made (an earlier fulfilled request doesn’t say which style). Only request it if you need something different — and say what in the notes.'
  }
  return null
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
