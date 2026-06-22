import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Globe, Search, Loader2, Check, Info, Trash2, ChevronDown } from 'lucide-react'
import { getCommunityListItems, getCommunityListOwners, type CommunityListItem, type CommunityListOwner } from '../../api/community'
import { getSettings } from '../../api/client'
import { checkTmdbPosterAvailability, type PosterAvailability } from '../../api/makerTools'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { useUnmatched } from '../../contexts/UnmatchedContext'
import { useToast } from '../Toast'
import { derivePsdConfig, type PsdConfig } from '../maker-tools/TmdbItemCard'
import RequestItemCard, { getStyleLabel, type CardMediaType } from './RequestItemCard'
import { useIdarrQuickAdd } from './useIdarrQuickAdd'

type MediaTypeFilter = 'all' | 'movie' | 'show' | 'season' | 'collection'
type SortOrder = 'newest' | 'oldest'

const PAGE_SIZE = 50

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

export default function ListsView() {
  const navigate = useNavigate()
  const { showToast } = useToast()
  const { isConnected, isMaker, isOwner, discordUserId, updateListItem } = useDiscordAuth()
  const { refreshCommunityRequestCount } = useUnmatched()
  const { enabled: idarrEnabled, setEnabled: setIdarrEnabled, doIdarrUpload, targetOptions: idarrTargets, selectedTargetValue: idarrTarget, setSelectedTarget: setIdarrTarget } = useIdarrQuickAdd()

  const [items, setItems] = useState<CommunityListItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)        // first page (filters changed)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaTypeFilter>('all')
  const [ownerFilter, setOwnerFilter] = useState<string>('all')   // 'all' or an added_by_discord_id
  const [statusFilter, setStatusFilter] = useState<'active' | 'fulfilled' | 'all'>('active')
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest')
  const [owners, setOwners] = useState<CommunityListOwner[]>([])

  // Per-card action state: itemId → 'loading' | error string
  const [actionStates, setActionStates] = useState<Map<string, 'loading' | string>>(new Map())
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [psdConfig, setPsdConfig] = useState<PsdConfig>({ exportFolder: '', templatePath: '', openPhotopea: false, imageExportFolder: '' })
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  const [posterAvailabilityChecked, setPosterAvailabilityChecked] = useState(false)
  const [claimConflict, setClaimConflict] = useState<string | null>(null)
  const [confirmClearMine, setConfirmClearMine] = useState(false)
  const [clearingMine, setClearingMine] = useState(false)
  const fetchRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    getSettings().then((s) => setPsdConfig(derivePsdConfig(s))).catch(() => {})
  }, [])

  // Fetch a page. offset 0 (append=false) replaces the list (filters changed);
  // offset = items.length (append=true) appends the next batch ("Load more").
  // All filtering/sorting is server-side so each batch is a correct slice.
  const fetchPage = useCallback(async (offset: number, append: boolean) => {
    if (append) setLoadingMore(true)
    else setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {
        status: statusFilter,
        sort: sortOrder,
        limit: String(PAGE_SIZE),
        offset: String(offset),
      }
      if (mediaType !== 'all') params.media_type = mediaType
      if (ownerFilter !== 'all') params.added_by_discord_id = ownerFilter
      const data = await getCommunityListItems(params)
      setTotal(data.total)
      setItems((prev) => {
        if (!append) return data.items
        const seen = new Set(prev.map((i) => i.id))
        return [...prev, ...data.items.filter((i) => !seen.has(i.id))]
      })
    } catch {
      setError('Failed to load community lists. Check your network connection.')
    } finally {
      if (append) setLoadingMore(false)
      else setLoading(false)
    }
  }, [statusFilter, sortOrder, mediaType, ownerFilter])

  // (Re)load the first page whenever a filter or sort changes.
  useEffect(() => {
    fetchRef.current = () => fetchPage(0, false)
    fetchPage(0, false)
  }, [fetchPage])

  const loadMore = useCallback(() => {
    fetchPage(items.length, true)
  }, [fetchPage, items.length])

  // Distinct publishers across the whole current view (not just the loaded
  // page), so the owner filter lists everyone even with pagination.
  const fetchOwners = useCallback(async () => {
    try {
      const params: Record<string, string> = { status: statusFilter }
      if (mediaType !== 'all') params.media_type = mediaType
      const data = await getCommunityListOwners(params)
      setOwners(data.owners)
    } catch { /* non-critical: leave the dropdown as-is */ }
  }, [statusFilter, mediaType])

  useEffect(() => { fetchOwners() }, [fetchOwners])

  // If the selected owner is no longer present (e.g. status filter changed),
  // fall back to "All owners".
  useEffect(() => {
    if (ownerFilter !== 'all' && owners.length > 0 && !owners.some((o) => o.id === ownerFilter)) {
      setOwnerFilter('all')
    }
  }, [owners, ownerFilter])

  // Poster availability for the loaded items
  useEffect(() => {
    const lookups = items
      .filter((i) => i.tmdb_id != null)
      .map((i) => ({
        tmdb_id: i.tmdb_id!,
        title: i.title,
        year: i.year ? String(i.year) : '',
        media_type: i.media_type === 'movie' ? 'movie' : i.media_type === 'collection' ? 'collection' : ('tv' as const),
      }))
    if (lookups.length === 0) return
    setPosterAvailabilityChecked(false)
    checkTmdbPosterAvailability(lookups)
      .then((availability) => {
        setPosterAvailability(availability)
        setPosterAvailabilityChecked(true)
      })
      .catch(() => {})
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
      } else {
        // complete / reject / remove all drop the item from the list.
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

  const handleDrop = useCallback((e: React.DragEvent, _itemId: string) => {
    e.preventDefault()
    setDragOverId(null)
    const files = Array.from(e.dataTransfer.files)
    if (!files.length) return
    if (idarrEnabled) void doIdarrUpload(files)
  }, [idarrEnabled, doIdarrUpload])

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
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as 'active' | 'fulfilled' | 'all')}>
              <option value="active">Active</option>
              <option value="fulfilled">Fulfilled</option>
              <option value="all">All</option>
            </select>
          </div>
          <div className="community-filter-group">
            <label>List owner</label>
            <select value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}>
              <option value="all">All owners</option>
              {owners.map((o) => (
                <option key={o.id} value={o.id}>{o.name} ({o.count})</option>
              ))}
            </select>
          </div>
          <div className="community-filter-group">
            <label>Sort</label>
            <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </div>
        </div>
        <div className="community-toolbar-actions">
          <button className="community-refresh-btn" onClick={() => { void fetchPage(0, false); void fetchOwners() }} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin-icon' : ''} />
            Refresh
          </button>
          {isConnected && discordUserId && owners.some((o) => o.id === discordUserId) && (
            <button
              className="community-refresh-btn community-clear-mine-btn"
              onClick={() => setConfirmClearMine(true)}
              disabled={clearingMine}
              title="Remove all items you published from the list"
            >
              {clearingMine ? <Loader2 size={14} className="spin-icon" /> : <Trash2 size={14} />}
              Remove My Items
            </button>
          )}
        </div>
      </div>

      {error && <div className="community-error">{error}</div>}

      {loading && items.length === 0 ? (
        <div className="community-loading">
          <RefreshCw size={20} className="spin-icon" />
          <span>Loading lists…</span>
        </div>
      ) : items.length === 0 ? (
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

          {items.map((item) => {
            const showMakerTools = isMaker && isConnected && item.tmdb_id != null
            const as_ = actionStates.get(item.id)
            const actionLoading = as_ === 'loading'
            const actionError = typeof as_ === 'string' && as_ !== 'loading' ? as_ : null
            const canComplete = item.claimed_by_discord_id === discordUserId || isOwner
            const isPublisher = isConnected && discordUserId != null && item.added_by_discord_id === discordUserId
            // The server (guild) owner can remove any item; publishers their own.
            const canRemove = isPublisher || (isConnected && isOwner)

            const infoLines = (
              <>
                {item.added_by && (
                  <div className="request-maker-info request-maker-requested">
                    📋 From <strong>{item.added_by}</strong>'s list
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
                  title="Search in Maker Tools"
                  onClick={() => navigate('/maker-tools', { state: { tmdbSearch: item.year ? `${item.title} ${item.year}` : item.title } })}
                >
                  <Search size={11} />
                  <span>Maker</span>
                </button>
                {isMaker && (item.status === 'open' || item.status === 'in_progress') && (
                  <div className="request-status-actions">
                    {item.status === 'open' && (
                      <button
                        type="button"
                        className="request-action-btn action-claim"
                        disabled={actionLoading}
                        title="Claim this item"
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
                          title="Mark as complete (removes it from the list)"
                          onClick={() => handleAction(item, 'complete')}
                        >
                          {actionLoading ? <Loader2 size={11} className="spin-icon" /> : <Check size={11} />}
                          <span>Complete</span>
                        </button>
                        <button
                          type="button"
                          className="request-action-btn"
                          disabled={actionLoading}
                          title="Release this item back to the list"
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
                      title="Reject this item"
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
                      title={isPublisher ? 'Remove this item from your list' : 'Remove this item (server owner)'}
                      onClick={() => handleAction(item, 'remove')}
                    >
                      <span>Remove</span>
                    </button>
                  </div>
                )}
                {actionError && (
                  <span className="request-action-error" title={actionError}>{actionError}</span>
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
    </>
  )
}
