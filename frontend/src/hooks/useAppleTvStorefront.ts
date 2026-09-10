import { useCallback, useRef, useState } from 'react'
import { getTmdbAppleStorefront } from '../api/client'

export type AppleTvStorefront = {
  storefront: string        // Apple storefront id for the artwork-finder link
  iso: string               // its country
  soldIn: string[] | null   // countries whose Apple TV Store lists the title; null until resolved
  ensure: () => void        // resolve once, on first hover/focus of the link
}

const US = { storefront: '143441', iso: 'US', soldIn: null }

/** Resolves the Apple TV artwork storefront lazily, so the link opens the right region by click time. */
export function useAppleTvStorefront(item: { tmdb_id: number; media_type: string }): AppleTvStorefront {
  const [pick, setPick] = useState<Omit<AppleTvStorefront, 'ensure'>>(US)
  const fetchedRef = useRef(false)

  const ensure = useCallback(() => {
    if (fetchedRef.current) return
    if ((item.tmdb_id ?? 0) <= 0) return
    if (item.media_type !== 'movie' && item.media_type !== 'tv') return
    fetchedRef.current = true
    getTmdbAppleStorefront(item.tmdb_id, item.media_type)
      .then((r) => setPick({ storefront: r.storefront, iso: r.iso, soldIn: r.sold_in }))
      .catch(() => { /* keep the US default */ })
  }, [item.tmdb_id, item.media_type])

  return { ...pick, ensure }
}
