import { useEffect, useState } from 'react'
import { getTmdbOverview } from '../api/client'

/**
 * A card's description, fetched only when its source data didn't carry one.
 *
 * TMDB search results and resolved unmatched candidates already include an overview. Cards built
 * from unmatched detection are assembled from the *arr library instead — ids and an *arr poster,
 * no TMDB text — so those look it up here rather than making every scan fetch descriptions for
 * the whole library. Returns '' while loading or when there's nothing to look up.
 */
export function useCardOverview(item: { tmdb_id: number; media_type: string; overview?: string }): string {
  const own = item.overview ?? ''
  const [fetched, setFetched] = useState('')
  const needsFetch = !own && (item.tmdb_id ?? 0) > 0

  useEffect(() => {
    if (!needsFetch) {
      setFetched('')
      return
    }
    let alive = true
    getTmdbOverview(item.tmdb_id, item.media_type)
      .then((text) => { if (alive) setFetched(text) })
      .catch(() => { /* non-blocking: the card just shows no description */ })
    return () => { alive = false }
  }, [needsFetch, item.tmdb_id, item.media_type])

  return own || fetched
}
