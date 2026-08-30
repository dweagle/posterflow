import { useEffect, useState } from 'react'
import { getTmdbOverview } from '../api/client'
import type { TmdbCardMeta } from '../api/makerTools'

// Card metadata shared across cards; keyed "type:id", failed fetches evicted so they can retry
const metaCache = new Map<string, Promise<TmdbCardMeta>>()

const EMPTY_META: TmdbCardMeta = { overview: '', poster_url: null }

/**
 * A card's description — and TMDB poster URL — fetched only when the source data didn't carry one.
 *
 * TMDB search results and resolved unmatched candidates already include an overview. Cards built
 * from unmatched detection are assembled from the library sources instead — ids and maybe a poster,
 * no TMDB text — so those look it up here rather than making every scan fetch descriptions for
 * the whole library. The same details response carries poster_path, so media-server-sourced cards
 * (no public poster URL of their own) get a TMDB CDN poster from the same single request.
 */
export function useCardOverview(item: { tmdb_id: number; media_type: string; overview?: string }): { overview: string; posterUrl: string | null } {
  const own = item.overview ?? ''
  const [fetched, setFetched] = useState<TmdbCardMeta>(EMPTY_META)
  const needsFetch = !own && (item.tmdb_id ?? 0) > 0

  useEffect(() => {
    if (!needsFetch) {
      setFetched(EMPTY_META)
      return
    }
    const key = `${item.media_type}:${item.tmdb_id}`
    let pending = metaCache.get(key)
    if (!pending) {
      pending = getTmdbOverview(item.tmdb_id, item.media_type)
      metaCache.set(key, pending)
      pending.catch(() => metaCache.delete(key))
    }
    let alive = true
    pending
      .then((meta) => { if (alive) setFetched(meta) })
      .catch(() => { /* non-blocking: the card just shows no description */ })
    return () => { alive = false }
  }, [needsFetch, item.tmdb_id, item.media_type])

  return { overview: own || fetched.overview, posterUrl: fetched.poster_url }
}
