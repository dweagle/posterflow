import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Globe, Search, Loader2, Check, Info, Trash2, ChevronDown, Send, Upload, CalendarArrowDown, CalendarArrowUp } from 'lucide-react'
import { getCommunityListItems, getCommunityListOwners, submitCommunityRequest, type CommunityListItem, type CommunityListOwner, type SubmitRequestPayload } from '../../api/community'
import { getSettings } from '../../api/client'
import { checkTmdbPosterAvailability, type PosterAvailability } from '../../api/makerTools'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { useUnmatched } from '../../contexts/UnmatchedContext'
import { useToast } from '../Toast'
import { derivePsdConfig, EMPTY_PSD_CONFIG, type PsdConfig } from '../maker-tools/TmdbItemCard'
import RequestItemCard, { getStyleLabel, type CardMediaType } from './RequestItemCard'
import MoveToRequestModal, { type MoveToRequestValues } from './MoveToRequestModal'
import { useIdarrQuickAdd } from './useIdarrQuickAdd'
import { useCommunityClaimStatus } from '../../hooks/useCommunityClaimStatus'

type MediaTypeFilter = 'all' | 'movie' | 'show' | 'season' | 'collection'
type SortOrder = 'newest' | 'oldest'

const PAGE_SIZE = 50

// "No MM2K" hides MM2K-style items. Persisted so the filter sticks across
// navigation and reloads (mirrors the Requests tab's sticky Claimed filter).
const NO_MM2K_STORAGE_KEY = 'posterflow.communityLists.noMm2k'
function loadNoMm2kFilter(): boolean {
  try {
    return localStorage.getItem(NO_MM2K_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

const MEDIA_TYPE_TABS: { key: MediaTypeFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'movie', label: 'Movies' },
  { key: 'show', label: 'Shows' },
  { key: 'season', label: 'Seasons' },
  { key: 'collection', label: 'Collections' },
]

// Season badge labels for a card: a single season, or a multi-season set encoded
// in notes ("Seasons: 1, 2, 3") gets one badge per season. 0 → "Specials".
function seasonLabels(item: CommunityListItem): string[] {
  const fmt = (s: string | number) => (String(s) === '0' ? 'Specials' : `S${s}`)
  if (item.season_number != null) return [fmt(item.season_number)]
  if (item.media_type === 'season' && item.notes?.startsWith('Seasons: ')) {
    return item.notes.split('\n')[0].slice('Seasons: '.length)
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
      .map(fmt)
  }
  return []
}

// Map a list item onto a full poster-request payload. The item's identity fields
// carry over unchanged; style, notes and ping come from the Move-to-Request modal
// (season encoding "Seasons: 1,2,3" is preserved inside the modal's notes value).
function toRequestPayload(item: CommunityListItem, token: string, values: MoveToRequestValues): SubmitRequestPayload {
  return {
    tmdb_id: item.tmdb_id,
    media_type: item.media_type,
    title: item.title,
    year: item.year,
    season_number: item.season_number,
    poster_path: item.poster_path,
    imdb_id: item.imdb_id,
    tvdb_id: item.tvdb_id,
    notes: values.notes,
    style_tags: values.styleTags.length ? values.styleTags : undefined,
    ping_discord_id: values.pingDiscordId,
    // requested_by is left to the edge function (the connected user's Discord name).
    discord_token: token,
  }
}

export default function ListsView() {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const { isConnected, isMaker, isOwner, discordUserId, token, login, updateListItem } = useDiscordAuth()
  const { getOverlap, refresh: refreshClaimStatus } = useCommunityClaimStatus()
  const { refreshCommunityRequestCount } = useUnmatched()
  const { enabled: idarrEnabled, setEnabled: setIdarrEnabled, doIdarrUpload, targetOptions: idarrTargets, selectedTargetValue: idarrTarget, setSelectedTarget: setIdarrTarget } = useIdarrQuickAdd()

  const [items, setItems] = useState<CommunityListItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)        // first page (filters changed)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaTypeFilter>('all')
  const [ownerFilter, setOwnerFilter] = useState<string>('all')   // 'all' | a wanter's discord_id
  const [ownerLabel, setOwnerLabel] = useState('')                // remembered name of the selected wanter
  const [statusFilter, setStatusFilter] = useState<'active' | 'in_progress' | 'fulfilled' | 'all'>('active')
  const [claimedByMe, setClaimedByMe] = useState(false)  // maker-only: only my claimed items
  const [noMm2k, setNoMm2k] = useState<boolean>(() => loadNoMm2kFilter())  // hide MM2K-style items; sticks across navigation
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest')
  const [searchInput, setSearchInput] = useState('')   // raw input box value
  const [search, setSearch] = useState('')             // debounced value sent to the API
  const [owners, setOwners] = useState<CommunityListOwner[]>([])

  // Per-card action state: itemId → 'loading' | error string
  const [actionStates, setActionStates] = useState<Map<string, 'loading' | string>>(new Map())
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [psdConfig, setPsdConfig] = useState<PsdConfig>(EMPTY_PSD_CONFIG)
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  const [posterAvailabilityChecked, setPosterAvailabilityChecked] = useState(false)
  const [claimConflict, setClaimConflict] = useState<string | null>(null)
  const [confirmClearMine, setConfirmClearMine] = useState(false)
  const [clearingMine, setClearingMine] = useState(false)
  // Item pending "Move to Request" confirmation, and the in-flight flag.
  const [moveTarget, setMoveTarget] = useState<CommunityListItem | null>(null)
  const [moving, setMoving] = useState(false)
  // Per-card IDarr upload-button state: itemId → 'uploading' | 'done'.
  const [uploadStates, setUploadStates] = useState<Map<string, 'uploading' | 'done'>>(new Map())
  const fileInputRef = useRef<HTMLInputElement>(null)
  const uploadTargetRef = useRef<string | null>(null)
  const fetchRef = useRef<(() => void) | null>(null)
  const checkedTmdbRef = useRef<Set<number>>(new Set())
  const requestSeqRef = useRef(0)

  useEffect(() => {
    getSettings().then((s) => setPsdConfig(derivePsdConfig(s))).catch(() => {})
  }, [])

  // Persist the No MM2K filter so it stays set across navigation and reloads.
  useEffect(() => {
    try {
      if (noMm2k) localStorage.setItem(NO_MM2K_STORAGE_KEY, '1')
      else localStorage.removeItem(NO_MM2K_STORAGE_KEY)
    } catch { /* storage unavailable; skip persistence */ }
  }, [noMm2k])

  // Build the query for one page at `offset` from the current filters.
  const buildParams = useCallback((offset: number): Record<string, string> => {
    const params: Record<string, string> = {
      // "Claimed" overrides the status dropdown — it's my in-progress claims.
      status: claimedByMe ? 'in_progress' : statusFilter,
      sort: sortOrder,
      limit: String(PAGE_SIZE),
      offset: String(offset),
    }
    if (mediaType !== 'all') params.media_type = mediaType
    // Wanter filter → items the selected person wants (backend resolves
    // added_by_discord_id via the wanters table).
    if (ownerFilter !== 'all') params.added_by_discord_id = ownerFilter
    if (claimedByMe && discordUserId) params.claimed_by_discord_id = discordUserId
    if (search) params.search = search
    return params
  }, [statusFilter, claimedByMe, discordUserId, sortOrder, mediaType, ownerFilter, search])

  // Fetch a page. offset 0 (append=false) replaces the list (filters changed);
  // offset = items.length (append=true) appends the next batch ("Load more").
  // All filtering/sorting is server-side so each batch is a correct slice.
  const fetchPage = useCallback(async (offset: number, append: boolean) => {
    // Sequence every fetch. Filter/sort/search changes (and the owner-reset
    // cascade) fire overlapping requests whose responses can resolve out of
    // order; "Load more" runs concurrently with all of them. Only the latest
    // request is allowed to touch state, so a stale response can't blank the
    // view or append items from a since-changed filter.
    const seq = ++requestSeqRef.current
    if (append) {
      setLoadingMore(true)
    } else {
      setLoading(true)
      setLoadingMore(false)  // a fresh fetch supersedes any in-flight "Load more"
    }
    setError(null)
    try {
      const data = await getCommunityListItems(buildParams(offset))
      if (seq !== requestSeqRef.current) return  // superseded — drop this response
      setTotal(data.total)
      setItems((prev) => {
        if (!append) return data.items
        const seen = new Set(prev.map((i) => i.id))
        return [...prev, ...data.items.filter((i) => !seen.has(i.id))]
      })
    } catch {
      if (seq !== requestSeqRef.current) return
      setError('Failed to load community lists. Check your network connection.')
    } finally {
      // Only the latest request owns the spinners — a superseded one must not
      // clear a flag the current request set.
      if (seq === requestSeqRef.current) {
        if (append) setLoadingMore(false)
        else setLoading(false)
      }
    }
  }, [buildParams])

  // Debounce the search box so we fetch on a pause, not every keystroke.
  useEffect(() => {
    const t = setTimeout(() => setSearch(searchInput.trim()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  // (Re)load the first page whenever a filter, sort, or search changes.
  useEffect(() => {
    fetchRef.current = () => fetchPage(0, false)
    fetchPage(0, false)
  }, [fetchPage])

  const loadMore = useCallback(() => {
    fetchPage(items.length, true)
  }, [fetchPage, items.length])

  // People who want items in the current view — populates the wanter filter.
  const fetchOwners = useCallback(async () => {
    try {
      const params: Record<string, string> = { status: statusFilter }
      if (mediaType !== 'all') params.media_type = mediaType
      const data = await getCommunityListOwners(params)
      setOwners(data.owners)
    } catch { /* non-critical: leave the dropdown as-is */ }
  }, [statusFilter, mediaType])

  useEffect(() => { fetchOwners() }, [fetchOwners])

  // Silently re-pull the pages already loaded and swap them in atomically, so a
  // background refresh reflects others' claims/completions/adds without losing
  // scroll depth or flashing a spinner. Stable id-ordered pagination keeps the
  // pages disjoint; dedupe guards the boundaries anyway.
  const reloadInPlace = useCallback(async () => {
    const depth = Math.max(PAGE_SIZE, items.length)
    const seq = ++requestSeqRef.current
    try {
      const fresh: CommunityListItem[] = []
      let freshTotal = 0
      for (let offset = 0; offset < depth; offset += PAGE_SIZE) {
        const data = await getCommunityListItems(buildParams(offset))
        if (seq !== requestSeqRef.current) return  // superseded by a newer fetch
        freshTotal = data.total
        fresh.push(...data.items)
        if (data.items.length < PAGE_SIZE) break  // reached the end
      }
      const seen = new Set<string>()
      const merged: CommunityListItem[] = []
      for (const it of fresh) if (!seen.has(it.id)) { seen.add(it.id); merged.push(it) }
      setItems(merged)
      setTotal(freshTotal)
      setError(null)
    } catch { /* keep the current view; the manual Refresh button remains */ }
    finally {
      // If this reload superseded an in-flight page fetch, clear the spinner it set.
      if (seq === requestSeqRef.current) {
        setLoading(false)
        setLoadingMore(false)
      }
    }
  }, [items.length, buildParams])

  // Re-sync when the user returns to the tab/window. focus and visibilitychange
  // both fire on a tab return, so throttle to collapse the double-fire.
  const lastFocusSyncRef = useRef(0)
  useEffect(() => {
    const onReturn = () => {
      if (document.hidden) return
      const now = Date.now()
      if (now - lastFocusSyncRef.current < 3000) return
      lastFocusSyncRef.current = now
      void reloadInPlace()
      void fetchOwners()
      refreshClaimStatus()  // throttled internally (heavy community-wide scan)
    }
    window.addEventListener('focus', onReturn)
    document.addEventListener('visibilitychange', onReturn)
    return () => {
      window.removeEventListener('focus', onReturn)
      document.removeEventListener('visibilitychange', onReturn)
    }
  }, [reloadInPlace, fetchOwners, refreshClaimStatus])

  // Poster availability for the loaded items. Check only tmdb_ids we haven't
  // checked yet and merge the result — a "Load more" appends items, so re-checking
  // the whole (growing) set each time made every Load more slower than the last and
  // could stall the backend (one scan per item). In-place card updates
  // (claim/release/upload) add no new ids, so they never trigger a re-check or
  // flash the ✓/✗ indicators.
  useEffect(() => {
    const lookups: { tmdb_id: number; title: string; year: string; media_type: 'movie' | 'collection' | 'tv' }[] = []
    const batch = new Set<number>()
    for (const i of items) {
      if (i.tmdb_id == null || checkedTmdbRef.current.has(i.tmdb_id) || batch.has(i.tmdb_id)) continue
      batch.add(i.tmdb_id)
      lookups.push({
        tmdb_id: i.tmdb_id,
        title: i.title,
        year: i.year ? String(i.year) : '',
        media_type: i.media_type === 'movie' ? 'movie' : i.media_type === 'collection' ? 'collection' : 'tv',
      })
    }
    if (lookups.length === 0) return
    lookups.forEach((l) => checkedTmdbRef.current.add(l.tmdb_id))
    checkTmdbPosterAvailability(lookups)
      .then((availability) => {
        setPosterAvailability((prev) => ({ ...prev, ...availability }))
        setPosterAvailabilityChecked(true)
      })
      .catch(() => {
        // let these ids be retried on a later change
        lookups.forEach((l) => checkedTmdbRef.current.delete(l.tmdb_id))
      })
  }, [items])

  const handleAction = useCallback(async (item: CommunityListItem, action: 'claim' | 'complete' | 'release' | 'reject' | 'remove') => {
    setActionStates((prev) => new Map(prev).set(item.id, 'loading'))
    try {
      const result = await updateListItem(item.id, action)
      setActionStates((prev) => { const next = new Map(prev); next.delete(item.id); return next })
      if (action === 'claim' || action === 'release') {
        // Keep the card, update its claim state in place.
        setItems((prev) => prev.map((i) => i.id === item.id
          ? { ...i, status: (result.status as CommunityListItem['status']) ?? i.status, claimed_by: result.claimed_by ?? null, claimed_by_discord_id: result.claimed_by_discord_id ?? null }
          : i))
      } else if (action === 'remove') {
        // Detach this user — the poster stays if others still want it, so re-read.
        fetchRef.current?.()
      } else {
        // complete / reject — terminal; drop the card.
        setItems((prev) => prev.filter((i) => i.id !== item.id))
        setTotal((t) => Math.max(0, t - 1))
      }
      void refreshCommunityRequestCount()
    } catch (err) {
      const msg = err instanceof Error ? err.message : `${action} failed`
      if (action === 'claim') {
        setActionStates((prev) => { const next = new Map(prev); next.delete(item.id); return next })
        showToast(msg, 'error')
        setClaimConflict(msg)
        fetchRef.current?.()
      } else {
        setActionStates((prev) => new Map(prev).set(item.id, msg))
      }
    }
  }, [updateListItem, showToast, refreshCommunityRequestCount])

  // Move a list item to a full community request: post it via the real request
  // route, then remove it from the list. The cross-sync (direction A) may have
  // already removed the publisher's copy server-side, so tolerate "already gone".
  const handleMoveToRequest = useCallback(async (values: MoveToRequestValues) => {
    if (!moveTarget) return
    if (!token) { login(); return }
    const item = moveTarget
    setMoving(true)
    const drop = () => {
      setItems((prev) => prev.filter((i) => i.id !== item.id))
      setTotal((t) => Math.max(0, t - 1))
      setMoveTarget(null)
      void refreshCommunityRequestCount()
    }
    try {
      const result = await submitCommunityRequest(toRequestPayload(item, token, values))
      try { await updateListItem(item.id, 'remove') } catch { /* may already be removed by cross-sync */ }
      drop()
      showToast(
        result.status === 'already_requested'
          ? 'Already requested — removed from your list'
          : 'Moved to Community Requests',
        result.status === 'already_requested' ? 'info' : 'success',
      )
    } catch (err) {
      const status = (err as { response?: { status?: number } })?.response?.status
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      // A 409 means a request for this item already exists — the goal is already
      // met, so still drop the now-redundant list item to complete the move.
      if (status === 409) {
        try { await updateListItem(item.id, 'remove') } catch { /* */ }
        drop()
        showToast(detail ?? 'Already requested — removed from your list', 'info')
      } else {
        showToast(detail ?? 'Failed to move to a request', 'error')
      }
    } finally {
      setMoving(false)
    }
  }, [moveTarget, token, login, updateListItem, showToast, refreshCommunityRequestCount])

  const handleDrop = useCallback((e: React.DragEvent, _itemId: string) => {
    e.preventDefault()
    setDragOverId(null)
    const files = Array.from(e.dataTransfer.files)
    if (!files.length) return
    if (idarrEnabled) void doIdarrUpload(files)
  }, [idarrEnabled, doIdarrUpload])

  // Click-to-upload equivalent of the IDarr drop: open a file picker and push the
  // chosen poster(s) to the maker's IDarr pipeline (same path as a drag-drop).
  const doIdarrUploadForItem = useCallback(async (itemId: string, files: File[]) => {
    setUploadStates((prev) => new Map(prev).set(itemId, 'uploading'))
    await doIdarrUpload(files)
    setUploadStates((prev) => new Map(prev).set(itemId, 'done'))
    setTimeout(() => setUploadStates((prev) => {
      const next = new Map(prev)
      next.delete(itemId)
      return next
    }), 3000)
  }, [doIdarrUpload])

  const handleUploadClick = useCallback((itemId: string) => {
    uploadTargetRef.current = itemId
    fileInputRef.current?.click()
  }, [])

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    const itemId = uploadTargetRef.current
    e.target.value = ''
    if (files.length && itemId) void doIdarrUploadForItem(itemId, files)
  }, [doIdarrUploadForItem])

  // Bulk: remove every item the connected user published.
  const handleClearMine = useCallback(async () => {
    setClearingMine(true)
    try {
      const result = await updateListItem(null, 'remove_mine')
      const n = typeof result.removed === 'number' ? result.removed : 0
      setConfirmClearMine(false)
      showToast(`Removed ${n} item${n !== 1 ? 's' : ''} from your list`, 'success')
      // Reload from the first page (the user's items may have spanned pages).
      void fetchPage(0, false)
      void fetchOwners()
      void refreshCommunityRequestCount()
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to clear your items', 'error')
    } finally {
      setClearingMine(false)
    }
  }, [updateListItem, showToast, fetchPage, fetchOwners, refreshCommunityRequestCount])

  // Clear the search box and reset every filter to its default, so a user can
  // recover from a zero-result view without leaving and re-entering the page.
  const resetFilters = useCallback(() => {
    setSearchInput('')
    setSearch('')
    setMediaType('all')
    setOwnerFilter('all')
    setOwnerLabel('')
    setStatusFilter('active')
    setClaimedByMe(false)
    setNoMm2k(false)
  }, [])

  // A search or any non-default filter "narrows" the view. While narrowed we keep
  // the search/header mounted even at zero results, so the search box can't vanish
  // mid-search and the input keeps focus (no flashing on result changes).
  const isFiltered = search !== '' || mediaType !== 'all' || ownerFilter !== 'all' || statusFilter !== 'active' || claimedByMe || noMm2k

  // "No MM2K" hides MM2K-style items from the loaded page (client-side view filter).
  const visibleItems = noMm2k
    ? items.filter((i) => getStyleLabel(i.style_tag ? [i.style_tag] : null) !== 'MM2K')
    : items

  return (
    <>
      <div className="community-toolbar">
        <div className="community-type-tabs">
          {MEDIA_TYPE_TABS.map((tab) => (
            <button
              key={tab.key}
              className={`community-tab-btn${mediaType === tab.key ? ' active' : ''}`}
              onClick={() => setMediaType(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="community-filters">
          <div className="community-filter-group">
            <label>Status</label>
            <select className="community-status-select" value={statusFilter} disabled={claimedByMe} onChange={(e) => setStatusFilter(e.target.value as 'active' | 'in_progress' | 'fulfilled' | 'all')}>
              <option value="active">Active</option>
              <option value="in_progress">In Progress</option>
              <option value="fulfilled">Fulfilled</option>
              <option value="all">All</option>
            </select>
          </div>
          <div className="community-filter-group">
            <label>Wanted by</label>
            <select
              value={ownerFilter}
              onChange={(e) => {
                const id = e.target.value
                setOwnerFilter(id)
                setOwnerLabel(id === 'all' ? '' : (owners.find((o) => o.id === id)?.name ?? ownerLabel))
              }}
            >
              <option value="all">Everyone</option>
              {/* Keep the selected wanter listed even when the current media-type/
                  status view has none of their items, so switching tabs never
                  silently drops the filter back to Everyone. */}
              {ownerFilter !== 'all' && !owners.some((o) => o.id === ownerFilter) && (
                <option value={ownerFilter}>{ownerLabel || ownerFilter}</option>
              )}
              {owners.map((o) => (
                <option key={o.id} value={o.id}>{o.name} ({o.count})</option>
              ))}
            </select>
          </div>
          <button
            className="community-tab-btn community-sort-btn"
            onClick={() => setSortOrder((o) => (o === 'newest' ? 'oldest' : 'newest'))}
            title={sortOrder === 'newest' ? 'Newest first — click to show oldest first' : 'Oldest first — click to show newest first'}
            aria-label={sortOrder === 'newest' ? 'Sorted newest first' : 'Sorted oldest first'}
          >
            {sortOrder === 'newest' ? <CalendarArrowDown size={16} /> : <CalendarArrowUp size={16} />}
          </button>
          {isMaker && (
            <button
              className={`community-tab-btn${claimedByMe ? ' active' : ''}`}
              onClick={() => setClaimedByMe((v) => !v)}
              title="Show only the items you've claimed"
            >
              Claimed
            </button>
          )}
          {isMaker && (
            <button
              className={`community-tab-btn community-nomm2k-btn${noMm2k ? ' active' : ''}`}
              onClick={() => setNoMm2k((v) => !v)}
              title="Hide MM2K-style items — stays set when you navigate away and back"
            >
              No MM2K
            </button>
          )}
        </div>
        <div className="community-toolbar-actions">
          <button className="community-refresh-btn" onClick={() => { void fetchPage(0, false); void fetchOwners() }} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin-icon' : ''} />
            Refresh
          </button>
          {isConnected && discordUserId && (
            <button
              className="community-refresh-btn community-clear-mine-btn"
              onClick={() => setConfirmClearMine(true)}
              disabled={clearingMine}
              title="Stop wanting every item you've added"
            >
              {clearingMine ? <Loader2 size={14} className="spin-icon" /> : <Trash2 size={14} />}
              Remove My Items
            </button>
          )}
        </div>
      </div>

      {error && <div className="community-error">{error}</div>}

      {loading && items.length === 0 && !isFiltered ? (
        <div className="community-loading">
          <RefreshCw size={20} className="spin-icon" />
          <span>Loading lists…</span>
        </div>
      ) : items.length === 0 && !isFiltered ? (
        <div className="community-empty">
          <Globe size={48} />
          <p>No list items found</p>
          <p className="community-empty-sub">
            Publish items from the <strong>Unmatched Assets</strong> tab or the <strong>Drive Priority</strong> style list in Poster Manager.
          </p>
        </div>
      ) : (
        <div className="community-list">
          <div className="community-list-header">
            <div className="community-list-search">
              <input
                type="text"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                placeholder="Search title or user…"
                aria-label="Search community list by item title or user"
              />
              {searchInput && (
                <button type="button" className="community-list-search-clear" onClick={() => setSearchInput('')} aria-label="Clear search">×</button>
              )}
            </div>
            <p className="community-count">
              {items.length < total
                ? `Showing ${items.length} of ${total} items`
                : `${total} item${total !== 1 ? 's' : ''}`}
            </p>
            <div className="community-list-header-controls">
            {isMaker && isConnected && (
              <label className="maker-idarr-toggle-label">
                <span>Image Drop also adds to IDarr</span>
                <span className="maker-idarr-toggle-info-wrap">
                  <span className="maker-idarr-info-icon"><Info size={12} /></span>
                  <span className="maker-idarr-tooltip">
                    When enabled, any image you drop on a list card is sent to your IDarr quick add folder and processed — identical to dragging files onto the IDarr sidebar icon.
                  </span>
                </span>
                <span className="idarr-toggle-control">
                  <input type="checkbox" checked={idarrEnabled} onChange={(e) => setIdarrEnabled(e.target.checked)} />
                  <span className="idarr-toggle-slider" />
                </span>
              </label>
            )}
            {isMaker && isConnected && idarrEnabled && idarrTargets.length > 1 && (
              <label className="maker-idarr-target-picker">
                <span>IDarr drive</span>
                <select value={idarrTarget} onChange={(e) => setIdarrTarget(e.target.value)}>
                  {idarrTargets.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </label>
            )}
            </div>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            accept="image/*"
            multiple
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />

          {loading && items.length === 0 && (
            <div className="community-loading">
              <RefreshCw size={20} className="spin-icon" />
              <span>Loading lists…</span>
            </div>
          )}
          {!loading && visibleItems.length === 0 && (
            <div className="community-empty">
              <Globe size={48} />
              <p>No items match your search or filters</p>
              <button type="button" className="community-refresh-btn" onClick={resetFilters}>
                <RefreshCw size={14} />
                Reset filters
              </button>
            </div>
          )}

          {visibleItems.map((item) => {
            const showMakerTools = isMaker && isConnected && item.tmdb_id != null
            const as_ = actionStates.get(item.id)
            const actionLoading = as_ === 'loading'
            const uploadState = uploadStates.get(item.id)
            const actionError = typeof as_ === 'string' && as_ !== 'loading' ? as_ : null
            const canComplete = item.claimed_by_discord_id === discordUserId || isOwner
            const wanters = item.poster_list_wanters ?? []
            const iWant = isConnected && discordUserId != null && wanters.some((w) => w.discord_id === discordUserId)
            // I can drop my own want; the server (guild) owner can force-remove the poster.
            const canRemove = iWant || (isConnected && isOwner)
            // Passive overlap: this item also has an open formal request. Reminds
            // makers that completing it won't auto-close the request (they should).
            const alsoRequested = getOverlap(item).requested
            // A wanter (or owner) can escalate an unclaimed poster to a formal request.
            const canMove = canRemove && item.status === 'open'

            const infoLines = (
              <>
                {wanters.length > 0 && (
                  <div className="request-maker-info request-maker-requested">
                    📋 Wanted by <strong>{wanters.map((w) => w.name || w.discord_id).join(', ')}</strong>
                  </div>
                )}
                {item.available_in_drive && (
                  <div className="request-maker-info list-available-info">
                    ✅ A copy is in a drive — run your workflow to pull it
                  </div>
                )}
                {item.status === 'in_progress' && item.claimed_by && (
                  <div className="request-maker-info request-maker-claimed">
                    🎨 Claimed by <strong>{item.claimed_by}</strong>
                  </div>
                )}
                {item.status === 'fulfilled' && item.fulfilled_by && (
                  <div className="request-maker-info request-maker-fulfilled">
                    ✅ Completed by <strong>{item.fulfilled_by}</strong>
                  </div>
                )}
              </>
            )

            const actions = (
              <>
                <button
                  type="button"
                  className="request-maker-btn"
                  data-tooltip="Search in Maker Tools"
                  onClick={() => navigate('/maker-tools', { state: { tmdbSearch: item.year ? `${item.title} ${item.year}` : item.title } })}
                >
                  <Search size={11} />
                  <span>Maker</span>
                </button>
                {isMaker && idarrEnabled && (
                  <button
                    type="button"
                    className={`request-upload-btn${uploadState === 'done' ? ' upload-done' : ''}`}
                    data-tooltip="Upload poster(s) to IDarr"
                    disabled={uploadState === 'uploading'}
                    onClick={() => handleUploadClick(item.id)}
                  >
                    {uploadState === 'uploading' ? <Loader2 size={11} className="spin-icon" /> :
                     uploadState === 'done' ? <Check size={11} /> :
                     <Upload size={11} />}
                    <span>{uploadState === 'uploading' ? 'Uploading…' : uploadState === 'done' ? 'Added!' : 'Upload'}</span>
                  </button>
                )}
                {canMove && (
                  <button
                    type="button"
                    className="request-action-btn action-request"
                    data-tooltip="Move this item to a Community Request (removes it from the list)"
                    onClick={() => setMoveTarget(item)}
                  >
                    <Send size={11} />
                    <span>Request</span>
                  </button>
                )}
                {isMaker && (item.status === 'open' || item.status === 'in_progress') && (
                  <div className="request-status-actions">
                    {item.status === 'open' && (
                      <button
                        type="button"
                        className="request-action-btn action-claim"
                        disabled={actionLoading}
                        data-tooltip="Claim this item"
                        onClick={() => handleAction(item, 'claim')}
                      >
                        {actionLoading ? <Loader2 size={11} className="spin-icon" /> : null}
                        <span>Claim</span>
                      </button>
                    )}
                    {item.status === 'in_progress' && canComplete && (
                      <>
                        <button
                          type="button"
                          className="request-action-btn action-complete"
                          disabled={actionLoading}
                          data-tooltip="Mark as complete (removes it from the list)"
                          onClick={() => handleAction(item, 'complete')}
                        >
                          {actionLoading ? <Loader2 size={11} className="spin-icon" /> : <Check size={11} />}
                          <span>Complete</span>
                        </button>
                        <button
                          type="button"
                          className="request-action-btn"
                          disabled={actionLoading}
                          data-tooltip="Release this item back to the list"
                          onClick={() => handleAction(item, 'release')}
                        >
                          <span>Release</span>
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      className="request-action-btn action-reject"
                      disabled={actionLoading}
                      data-tooltip="Reject this item"
                      onClick={() => handleAction(item, 'reject')}
                    >
                      <span>Reject</span>
                    </button>
                  </div>
                )}
                {canRemove && (
                  <div className="request-status-actions">
                    <button
                      type="button"
                      className="request-action-btn action-reject"
                      disabled={actionLoading}
                      data-tooltip={iWant ? 'Stop wanting this (removes it for you)' : 'Remove this poster (server owner)'}
                      onClick={() => handleAction(item, 'remove')}
                    >
                      <span>Remove</span>
                    </button>
                  </div>
                )}
                {actionError && (
                  <span className="request-action-error" data-tooltip={actionError}>{actionError}</span>
                )}
              </>
            )

            return (
              <RequestItemCard
                key={item.id}
                id={item.id}
                posterPath={item.poster_path}
                title={item.title}
                year={item.year}
                seasonLabels={seasonLabels(item)}
                mediaType={item.media_type as CardMediaType}
                styleLabel={getStyleLabel(item.style_tag ? [item.style_tag] : null)}
                status={item.status}
                accent={item.available_in_drive ? 'available' : null}
                titleExtras={<>
                  {item.available_in_drive && <span className="list-available-badge" title="A copy is already in a drive — run your workflow to pull it">✓ In drives</span>}
                  {alsoRequested && <span className="request-overlap-chip" title="This item also has an open poster request">📨 also requested</span>}
                </>}
                notes={item.notes}
                createdAt={item.created_at}
                imdbId={item.imdb_id}
                tvdbId={item.tvdb_id}
                tmdbId={item.tmdb_id}
                infoLines={infoLines}
                actions={actions}
                isMaker={isMaker}
                showMakerTools={showMakerTools}
                psdConfig={psdConfig}
                posterAvailability={item.tmdb_id != null ? posterAvailability[item.tmdb_id] : undefined}
                posterAvailabilityChecked={posterAvailabilityChecked}
                dragOver={dragOverId === item.id}
                onDragEnter={() => setDragOverId(item.id)}
                onDragLeave={() => setDragOverId(null)}
                onDrop={(e) => handleDrop(e, item.id)}
              />
            )
          })}

          {items.length < total && (
            <div className="community-load-more">
              <button className="community-refresh-btn" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? <Loader2 size={14} className="spin-icon" /> : <ChevronDown size={14} />}
                Load more ({items.length} of {total})
              </button>
            </div>
          )}
        </div>
      )}

      {claimConflict && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setClaimConflict(null) }}>
          <div className="modal-content schedule-modal" style={{ maxWidth: '380px' }}>
            <div className="modal-header">
              <h2>Already claimed</h2>
              <button className="modal-close" onClick={() => setClaimConflict(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ margin: 0 }}>{claimConflict}</p>
              <p className="muted" style={{ marginTop: '0.75rem' }}>The list has been refreshed to show its current status.</p>
            </div>
            <div className="modal-footer">
              <button className="btn-primary" onClick={() => setClaimConflict(null)}>Got it</button>
            </div>
          </div>
        </div>
      )}

      {confirmClearMine && (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !clearingMine) setConfirmClearMine(false) }}>
          <div className="modal-content schedule-modal" style={{ maxWidth: '420px' }}>
            <div className="modal-header">
              <h2>Remove all your items?</h2>
              <button className="modal-close" onClick={() => setConfirmClearMine(false)} disabled={clearingMine}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ margin: 0 }}>
                This permanently removes every item you published to the community list. Claimed items are removed too. This can't be undone.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setConfirmClearMine(false)} disabled={clearingMine}>Cancel</button>
              <button className="btn-primary" onClick={handleClearMine} disabled={clearingMine}>
                {clearingMine ? <Loader2 size={13} className="spin-icon" /> : <Trash2 size={13} />}
                {' '}Remove My Items
              </button>
            </div>
          </div>
        </div>
      )}

      {moveTarget && (
        <MoveToRequestModal
          item={moveTarget}
          submitting={moving}
          onCancel={() => setMoveTarget(null)}
          onConfirm={handleMoveToRequest}
        />
      )}
    </>
  )
}
