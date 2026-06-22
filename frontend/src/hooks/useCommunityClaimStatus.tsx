import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { getCommunityRequests, getCommunityListItems } from '../api/community'

// Whether an item is currently being worked (claimed) or already done somewhere
// in the community (a request or a published list item).
export type ClaimStatus = 'in_progress' | 'fulfilled'

type LookupItem = {
  tmdb_id?: number | null
  tvdb_id?: number | null
  media_type?: string | null
  title?: string | null
  year?: number | null
}

const groupOf = (mt?: string | null): string =>
  mt === 'movie' ? 'movie' : mt === 'collection' ? 'collection' : 'series' // show/season → series

const normTitle = (t?: string | null): string =>
  (t || '').toLowerCase().replace(/\s*\(\d{4}\)\s*$/, '').trim()

// All the keys an item can be matched on — tmdb/tvdb id first, then title+year.
function keysFor(item: LookupItem): string[] {
  const g = groupOf(item.media_type)
  const keys: string[] = []
  if (item.tmdb_id) keys.push(`t:${g}:${item.tmdb_id}`)
  if (item.tvdb_id) keys.push(`v:${g}:${item.tvdb_id}`)
  const nt = normTitle(item.title)
  if (nt) keys.push(`n:${g}:${nt}:${item.year ?? ''}`)
  return keys
}

interface CommunityClaimStatusValue {
  getStatus: (item: LookupItem) => ClaimStatus | null
  loaded: boolean
}

// No provider → no flags (and no fetch). Keeps consumers safe in isolation.
const defaultValue: CommunityClaimStatusValue = { getStatus: () => null, loaded: false }

const CommunityClaimStatusContext = createContext<CommunityClaimStatusValue>(defaultValue)

/**
 * Fetches community requests + list items ONCE and shares the claimed
 * (in_progress) / completed (fulfilled) index with every consumer below it (the
 * Poster Manager Unmatched tab and its modals), instead of each component
 * fetching its own copy. fulfilled wins over in_progress. Best-effort: network
 * errors just yield no flags.
 */
export function CommunityClaimStatusProvider({ children }: { children: ReactNode }) {
  const [index, setIndex] = useState<Map<string, ClaimStatus>>(new Map())
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      getCommunityRequests({ status: 'all', limit: 200 }).then((d) => d.requests).catch(() => []),
      getCommunityListItems({ status: 'all', limit: 200 }).then((d) => d.items).catch(() => []),
    ]).then(([requests, items]) => {
      if (cancelled) return
      const map = new Map<string, ClaimStatus>()
      const add = (item: LookupItem, status: string) => {
        const s: ClaimStatus | null = status === 'fulfilled' ? 'fulfilled' : status === 'in_progress' ? 'in_progress' : null
        if (!s) return
        for (const k of keysFor(item)) {
          // fulfilled beats in_progress
          if (s === 'fulfilled' || map.get(k) !== 'fulfilled') map.set(k, s)
        }
      }
      for (const r of requests) add(r, r.status)
      for (const it of items) add(it, it.status)
      setIndex(map)
      setLoaded(true)
    })
    return () => { cancelled = true }
  }, [])

  const value = useMemo<CommunityClaimStatusValue>(() => ({
    getStatus: (item: LookupItem): ClaimStatus | null => {
      let best: ClaimStatus | null = null
      for (const k of keysFor(item)) {
        const s = index.get(k)
        if (s === 'fulfilled') return 'fulfilled'
        if (s === 'in_progress') best = 'in_progress'
      }
      return best
    },
    loaded,
  }), [index, loaded])

  return <CommunityClaimStatusContext.Provider value={value}>{children}</CommunityClaimStatusContext.Provider>
}

export function useCommunityClaimStatus(): CommunityClaimStatusValue {
  return useContext(CommunityClaimStatusContext)
}
