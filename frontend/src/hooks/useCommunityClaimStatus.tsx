import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { getClaimIndex, type ClaimIndexRow } from '../api/community'
import { getStyleLabel } from '../components/community/posterStyles'

// Whether an item is currently being worked (claimed) or already done somewhere
// in the community (a request or a published list item).
export type ClaimStatus = 'in_progress' | 'fulfilled'

// The poster styles a claim/fulfillment can be tagged with; '' = a legacy row
// without style tags.
export type ClaimStyle = 'CL2K' | 'MM2K' | ''

// Community status per poster style, e.g. { CL2K: 'fulfilled', MM2K: 'in_progress' }.
// The badge shows every style so the user sees exactly what exists — no
// guessing which styles they collect.
export type StyleClaimStatus = Partial<Record<ClaimStyle, ClaimStatus>>

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

// The poster style a claim/fulfillment row was made in. Requests carry
// style_tags[], list items a single style_tag.
const styleOf = (row: ClaimIndexRow): ClaimStyle =>
  getStyleLabel(row.style_tags ?? (row.style_tag ? [row.style_tag] : null)) ?? ''

const ALL_STYLES: ClaimStyle[] = ['CL2K', 'MM2K', '']

// Whether an item also has an active counterpart elsewhere in the community:
// onList = a matching open/in_progress list item; requested = a matching
// pending/in_progress request. Powers the "also on a list" / "also requested" chips.
export type CommunityOverlap = { onList: boolean; requested: boolean }

interface CommunityClaimStatusValue {
  getStatus: (item: LookupItem) => StyleClaimStatus | null
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
 *
 * getStatus reports per poster style: which styles (CL2K/MM2K) the item is
 * made or claimed in, so the badge can spell them out and the user decides
 * what's relevant — deliberately NOT filtered by the user's own drives.
 * getOverlap stays style-blind — it powers "also requested / also on a list"
 * dedupe chips on the community boards, where any active counterpart matters.
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
    // One slim scan (match keys + status only) instead of paging both tables
    // with full rows — the backend caps and caches it.
    const { requests, list_items: items } = await getClaimIndex()
      .catch(() => ({ requests: [] as ClaimIndexRow[], list_items: [] as ClaimIndexRow[] }))
    const map = new Map<string, ClaimStatus>()
    const reqKeys = new Set<string>()
    const listKeys = new Set<string>()
    // Status keys carry the row's style ("CL2K|t:movie:123") so getStatus can
    // skip styles the user doesn't collect; overlap keys stay style-less.
    const add = (row: ClaimIndexRow) => {
      const s: ClaimStatus | null = row.status === 'fulfilled' ? 'fulfilled' : row.status === 'in_progress' ? 'in_progress' : null
      if (!s) return
      const style = styleOf(row)
      for (const k of keysFor(row)) {
        const sk = `${style}|${k}`
        // fulfilled beats in_progress
        if (s === 'fulfilled' || map.get(sk) !== 'fulfilled') map.set(sk, s)
      }
    }
    for (const r of requests) {
      add(r)
      if (r.status === 'pending' || r.status === 'in_progress') for (const k of keysFor(r)) reqKeys.add(k)
    }
    for (const it of items) {
      add(it)
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
    getStatus: (item: LookupItem): StyleClaimStatus | null => {
      const out: StyleClaimStatus = {}
      for (const k of keysFor(item)) {
        for (const style of ALL_STYLES) {
          const s = index.get(`${style}|${k}`)
          if (!s) continue
          // fulfilled beats in_progress, per style
          if (s === 'fulfilled' || !out[style]) out[style] = s
        }
      }
      return Object.keys(out).length ? out : null
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
