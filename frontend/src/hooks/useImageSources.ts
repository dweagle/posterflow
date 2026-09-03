import { useEffect, useState } from 'react'
import { getSettings } from '../api/client'

/** Which optional image sources have a key configured — what gates the browser's TVDB and fanart.tv tabs. */
export type EnabledImageSources = { tvdb: boolean; fanart: boolean }

const NONE: EnabledImageSources = { tvdb: false, fanart: false }

// Artwork cards render one per search result, so the lookup is shared across every card that
// mounts together. The short TTL (rather than a permanent cache) means adding a key in
// Settings takes effect on the next visit instead of needing a reload.
const TTL_MS = 30_000
let pending: Promise<EnabledImageSources> | null = null
let fetchedAt = 0

export function useEnabledImageSources(): EnabledImageSources {
  const [enabled, setEnabled] = useState<EnabledImageSources>(NONE)

  useEffect(() => {
    const now = Date.now()
    if (!pending || now - fetchedAt > TTL_MS) {
      fetchedAt = now
      // The keys are sensitive, so they arrive masked when set — presence is all we need.
      pending = getSettings()
        .then((s) => ({ tvdb: !!(s.tvdb_api_key || '').trim(), fanart: !!(s.fanart_api_key || '').trim() }))
        .catch(() => NONE)
    }
    let alive = true
    void pending.then((value) => { if (alive) setEnabled(value) })
    return () => { alive = false }
  }, [])

  return enabled
}
