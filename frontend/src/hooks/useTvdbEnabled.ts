import { useEffect, useState } from 'react'
import { getSettings } from '../api/client'

// Whether a TheTVDB API key is configured, which is what gates the image browser's TVDB tab.
// Artwork cards render one per search result, so the lookup is shared across every card that
// mounts together. The short TTL (rather than a permanent cache) means adding the key in
// Settings takes effect on the next visit instead of needing a reload.
const TTL_MS = 30_000
let pending: Promise<boolean> | null = null
let fetchedAt = 0

export function useTvdbEnabled(): boolean {
  const [enabled, setEnabled] = useState(false)

  useEffect(() => {
    const now = Date.now()
    if (!pending || now - fetchedAt > TTL_MS) {
      fetchedAt = now
      // tvdb_api_key is sensitive, so it arrives masked when set — presence is all we need.
      pending = getSettings().then((s) => !!(s.tvdb_api_key || '').trim()).catch(() => false)
    }
    let alive = true
    void pending.then((value) => { if (alive) setEnabled(value) })
    return () => { alive = false }
  }, [])

  return enabled
}
