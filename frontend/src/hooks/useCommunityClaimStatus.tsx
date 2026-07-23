import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { getCommunityRequests, getCommunityListItems, type CommunityRequest, type CommunityListItem } from '../api/community'

// Page through everything (the API caps a page at 200) up to a sane ceiling, so
// the claim/overlap index stays correct as the community grows.
const PAGE = 200
const MAX_PAGES = 25
async function fetchAllRequests(): Promise<CommunityRequest[]> {
  const out: CommunityRequest[] = []
  for (let i = 0; i < MAX_PAGES; i++) {
    const d = await getCommunityRequests({ status: 'all', limit: PAGE, offset: i * PAGE }).catch(() => ({ requests: [] }))
    out.push(...d.requests)
    if (d.requests.length < PAGE) break
  }
  return out
}
async function fetchAllListItems(): Promise<CommunityListItem[]> {
  const out: CommunityListItem[] = []
  for (let i = 0; i < MAX_PAGES; i++) {
    const d = await getCommunityListItems({ status: 'all', limit: PAGE, offset: i * PAGE }).catch(() => ({ items: [], total: 0 }))
    out.push(...d.items)
    if (d.items.length < PAGE) break
  }
  return out
}

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

// Whether an item also has an active counterpart elsewhere in the community:
// onList = a matching open/in_progress list item; requested = a matching
// pending/in_progress request. Powers the "also on a list" / "also requested" chips.
export type CommunityOverlap = { onList: boolean; requested: boolean }

interface CommunityClaimStatusValue {
  getStatus: (item: LookupItem) => ClaimStatus | null
  getOverlap: (item: LookupItem) => CommunityOverlap
  loaded: boolean
  refresh: () => void
}

// No provider → no flags (and no fetch). Keeps consumers safe in isolation.
const defaultValue: CommunityClaimStatusValue = {
  getStatus: () => null,
  getOverlap: () => ({ onList: false, requested: false }),
  loaded: false,
  refresh: () => {},
}

const CommunityClaimStatusContext = createContext<CommunityClaimStatusValue>(defaultValue)

/**
 * Fetches community requests + list items ONCE and shares the claimed
 * (in_progress) / completed (fulfilled) index with every consumer below it (the
 * Asset Manager Unmatched tab and its modals), instead of each component
 * fetching its own copy. fulfilled wins over in_progress. Best-effort: network
 * errors just yield no flags.
 */
export function CommunityClaimStatusProvider({ children }: { children: ReactNode }) {
  const [index, setIndex] = useState<Map<string, ClaimStatus>>(new Map())
  // Keys with an active (pending/open + in_progress) counterpart, split by source.
  const [activeRequests, setActiveRequests] = useState<Set<string>>(new Set())
  const [activeLists, setActiveLists] = useState<Set<string>>(new Set())
  const [loaded, setLoaded] = useState(false)
  const lastLoadRef = useRef(0)

  const load = useCallback(async () => {
    lastLoadRef.current = Date.now()
    const [requests, items] = await Promise.all([fetchAllRequests(), fetchAllListItems()])
    const map = new Map<string, ClaimStatus>()
    const reqKeys = new Set<string>()
    const listKeys = new Set<string>()
    const add = (item: LookupItem, status: string) => {
      const s: ClaimStatus | null = status === 'fulfilled' ? 'fulfilled' : status === 'in_progress' ? 'in_progress' : null
      if (!s) return
      for (const k of keysFor(item)) {
        // fulfilled beats in_progress
        if (s === 'fulfilled' || map.get(k) !== 'fulfilled') map.set(k, s)
      }
    }
    for (const r of requests) {
      add(r, r.status)
      if (r.status === 'pending' || r.status === 'in_progress') for (const k of keysFor(r)) reqKeys.add(k)
    }
    for (const it of items) {
      add(it, it.status)
      if (it.status === 'open' || it.status === 'in_progress') for (const k of keysFor(it)) listKeys.add(k)
    }
    setIndex(map)
    setActiveRequests(reqKeys)
    setActiveLists(listKeys)
    setLoaded(true)
  }, [])

  useEffect(() => { void load() }, [load])

  // On-demand re-pull, throttled — callers fire this on tab focus, but the scan is
  // community-wide and heavy, so actually refetch at most once every 5 minutes.
  const refresh = useCallback(() => {
    if (Date.now() - lastLoadRef.current < 300_000) return
    void load()
  }, [load])

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
    getOverlap: (item: LookupItem): CommunityOverlap => {
      let onList = false
      let requested = false
      for (const k of keysFor(item)) {
        if (activeLists.has(k)) onList = true
        if (activeRequests.has(k)) requested = true
      }
      return { onList, requested }
    },
    loaded,
    refresh,
  }), [index, activeRequests, activeLists, loaded, refresh])

  return <CommunityClaimStatusContext.Provider value={value}>{children}</CommunityClaimStatusContext.Provider>
}

export function useCommunityClaimStatus(): CommunityClaimStatusValue {
  return useContext(CommunityClaimStatusContext)
}
