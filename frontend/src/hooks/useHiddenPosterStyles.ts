import { useEffect, useState } from 'react'
import { getSettings } from '../api/settings'

// General settings → "Hide MM2K". A JSON list of community styles hidden across the UI; hiding is
// presentation only (drives keep syncing, priority and renamer are untouched).
export const HIDDEN_POSTER_STYLES_KEY = 'hidden_poster_styles'
// Styles the Settings page offers to hide; the save handler keeps at least one visible.
export const HIDEABLE_POSTER_STYLES = ['CL2K', 'MM2K'] as const

type Listener = (hidden: Set<string>) => void

let cached: Set<string> | null = null
let inflight: Promise<Set<string>> | null = null
const listeners = new Set<Listener>()

export function parseHiddenPosterStyles(raw: string | null | undefined): Set<string> {
  if (!raw) return new Set()
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((s): s is string => typeof s === 'string').map((s) => s.toUpperCase()))
  } catch {
    return new Set()
  }
}

function loadHiddenPosterStyles(): Promise<Set<string>> {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = getSettings()
      .then((settings) => parseHiddenPosterStyles(settings[HIDDEN_POSTER_STYLES_KEY]))
      .catch(() => new Set<string>())
      .then((set) => {
        cached = set
        inflight = null
        return set
      })
  }
  return inflight
}

// Sync read for non-hook code (the remembered poster-style choice); empty until the first load.
export function isPosterStyleHidden(style: string): boolean {
  return cached?.has(style.toUpperCase()) ?? false
}

// The Settings page calls this after a save so mounted pages update without a reload.
export function setHiddenPosterStylesCache(styles: Iterable<string>): void {
  const next = new Set([...styles].map((s) => s.toUpperCase()))
  cached = next
  listeners.forEach((fn) => fn(next))
}

export function resetHiddenPosterStylesCache(): void {
  cached = null
  inflight = null
}

export function useHiddenPosterStyles(): Set<string> {
  const [hidden, setHidden] = useState<Set<string>>(() => cached ?? new Set())
  useEffect(() => {
    let active = true
    void loadHiddenPosterStyles().then((set) => { if (active) setHidden(set) })
    const listener: Listener = (set) => setHidden(set)
    listeners.add(listener)
    return () => {
      active = false
      listeners.delete(listener)
    }
  }, [])
  return hidden
}
