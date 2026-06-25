import { useState, useCallback } from 'react'
import { AlertCircle, CheckCircle, Copy, Check, Download, ExternalLink, Loader2, ListPlus, ListChecks, Search, Star, X } from 'lucide-react'
import type { MouseEvent } from 'react'
import { type UnmatchedStats, type TmdbCandidate, searchUnmatchedTmdb, type ListItemInput } from '../../api/client'
import { mediaTypeToTmdbFilter } from '../../api/makerTools'
import { useToast } from '../Toast'
import { publishToCommunityLists } from './publishToCommunityLists'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { useCommunityClaimStatus } from '../../hooks/useCommunityClaimStatus'
import CommunityStatusBadge from './CommunityStatusBadge'
import ArrMissingBadge from './ArrMissingBadge'
import CommunityRequestModal from './CommunityRequestModal'
import CreateListModal, { type SelectableListItem } from './CreateListModal'
import SortControls from './SortControls'
import { type ItemType, sortItems, useSortPrefs } from './itemSort'

export type UnmatchedModalType = 'movies' | 'series' | 'collections' | 'seasons' | 'all' | null

type TmdbSearchType = 'movie' | 'show' | 'collection'

interface NormalizedItem {
  title: string
  year: number | null
  origIdx: number
  type: ItemType
  seasonCount: number
  missingSeasonsNumbers?: number[]
  tmdbType?: TmdbSearchType
  category?: string
  // Authoritative refs carried from Plex/*arr (when available)
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  poster_url?: string | null
  available?: boolean | null
}

type UnmatchedItemsModalProps = {
  modalType: UnmatchedModalType
  unmatchedStats: UnmatchedStats | null
  modalDisplayLimit: number
  tmdbApiKeyConfigured: boolean
  onClose: () => void
  onDownloadList: (type: Exclude<UnmatchedModalType, null | 'all'>) => void
}

function getTmdbSearchType(modalType: UnmatchedModalType): TmdbSearchType {
  if (modalType === 'movies') return 'movie'
  if (modalType === 'collections') return 'collection'
  return 'show'
}

function getTmdbLink(candidate: TmdbCandidate): string {
  if (candidate.media_type === 'movie') return `https://www.themoviedb.org/movie/${candidate.tmdb_id}`
  if (candidate.media_type === 'collection') return `https://www.themoviedb.org/collection/${candidate.tmdb_id}`
  return `https://www.themoviedb.org/tv/${candidate.tmdb_id}`
}

function getModalTitle(modalType: UnmatchedModalType): string {
  if (modalType === 'movies') return 'Movies Missing Posters'
  if (modalType === 'series') return 'Series Missing Main Posters'
  if (modalType === 'seasons') return 'Series Missing Season Posters'
  if (modalType === 'collections') return 'Collections Missing Posters'
  if (modalType === 'all') return 'All Missing Posters'
  return ''
}

// Authoritative refs (IDs + poster) carried from Plex/*arr, normalized to null.
function srcRefs(item: { tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null; poster_url?: string | null; available?: boolean | null }) {
  return {
    tmdb_id: item.tmdb_id ?? null,
    tvdb_id: item.tvdb_id ?? null,
    imdb_id: item.imdb_id ?? null,
    poster_url: item.poster_url ?? null,
    available: item.available ?? null,
  }
}

function buildAllItems(modalType: UnmatchedModalType, unmatchedStats: UnmatchedStats): NormalizedItem[] {
  if (modalType === 'movies') {
    return (unmatchedStats.unmatched.movies ?? []).map((item, i) => ({
      title: item.title,
      year: item.year ?? null,
      origIdx: i,
      type: 'movie',
      seasonCount: 0,
      ...srcRefs(item),
    }))
  }
  if (modalType === 'series') {
    return (unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_main_poster)
      .map((item, i) => ({ title: item.title, year: item.year ?? null, origIdx: i, type: 'show', seasonCount: 0, ...srcRefs(item) }))
  }
  if (modalType === 'seasons') {
    return (unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_seasons.length > 0)
      .map((item, i) => ({
        title: item.title,
        year: item.year ?? null,
        origIdx: i,
        type: 'show',
        seasonCount: item.missing_seasons.length,
        missingSeasonsNumbers: item.missing_seasons,
        ...srcRefs(item),
      }))
  }
  if (modalType === 'collections') {
    return (unmatchedStats.unmatched.collections ?? []).map((item, i) => ({
      title: item.title,
      year: item.year ?? null,
      origIdx: i,
      type: 'collection',
      seasonCount: 0,
      ...srcRefs(item),
    }))
  }
  if (modalType === 'all') {
    const result: NormalizedItem[] = []
    ;(unmatchedStats.unmatched.movies ?? []).forEach((item, i) => {
      result.push({ title: item.title, year: item.year ?? null, origIdx: i, type: 'movie', seasonCount: 0, tmdbType: 'movie', category: 'Movie', ...srcRefs(item) })
    })
    ;(unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_main_poster)
      .forEach((item, i) => {
        result.push({ title: item.title, year: item.year ?? null, origIdx: i, type: 'show', seasonCount: 0, tmdbType: 'show', category: 'Series', ...srcRefs(item) })
      })
    ;(unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_seasons.length > 0)
      .forEach((item, i) => {
        result.push({
          title: item.title,
          year: item.year ?? null,
          origIdx: i,
          type: 'show',
          seasonCount: item.missing_seasons.length,
          missingSeasonsNumbers: item.missing_seasons,
          tmdbType: 'show',
          category: 'Season',
          ...srcRefs(item),
        })
      })
    ;(unmatchedStats.unmatched.collections ?? []).forEach((item, i) => {
      result.push({ title: item.title, year: item.year ?? null, origIdx: i, type: 'collection', seasonCount: 0, tmdbType: 'collection', category: 'Collection', ...srcRefs(item) })
    })
    return result
  }
  return []
}

// Map an unmatched row to a community list item, carrying the authoritative
// Plex/*arr refs (IDs + poster) when present so the card matches exactly.
// Season rows use the same encoding as the Poster Style modal — media_type
// 'season' + raw "Seasons: 1, 2, 3" notes — so ListsView renders per-season
// badges identically regardless of where the item was published.
function toListInput(item: NormalizedItem): ListItemInput {
  const cleanTitle = item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title
  const seasons = [...(item.missingSeasonsNumbers ?? [])].sort((a, b) => a - b)
  const isSeasonItem = seasons.length > 0
  return {
    media_type: isSeasonItem ? 'season' : item.type,
    title: cleanTitle,
    year: item.year,
    season_number: null,
    tmdb_id: item.tmdb_id ?? null,
    tvdb_id: item.tvdb_id ?? null,
    imdb_id: item.imdb_id ?? null,
    poster_path: item.poster_url ?? null,
    notes: isSeasonItem ? `Seasons: ${seasons.join(', ')}` : null,
    source: 'unmatched',
  }
}

function UnmatchedItemsModal({
  modalType,
  unmatchedStats,
  modalDisplayLimit,
  tmdbApiKeyConfigured,
  onClose,
  onDownloadList,
}: UnmatchedItemsModalProps) {
  const { showToast } = useToast()
  const { isConnected, token, login } = useDiscordAuth()
  const { getStatus: getClaimStatus } = useCommunityClaimStatus()
  const [publishing, setPublishing] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [prefs, setPrefs] = useSortPrefs('unmatchedSort')
  const [candidatesMap, setCandidatesMap] = useState<Record<string, TmdbCandidate[]>>({})
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set())
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [copiedLink, setCopiedLink] = useState<string | null>(null)
  const [copiedTitle, setCopiedTitle] = useState<string | null>(null)
  const [noKeyItems, setNoKeyItems] = useState<Set<string>>(new Set())
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [requestItem, setRequestItem] = useState<NormalizedItem | null>(null)
  const [createListOpen, setCreateListOpen] = useState(false)

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  const itemKey = useCallback((item: NormalizedItem) => `${item.category ?? ''}::${item.origIdx}::${item.title}`, [])

  const handleTmdbSearch = useCallback(
    async (item: NormalizedItem) => {
      const key = itemKey(item)
      if (loadingKeys.has(key)) return

      // Toggle off if already expanded with results
      if (expandedKey === key) {
        setExpandedKey(null)
        return
      }

      setExpandedKey(key)

      // Warn if no API key configured
      if (!tmdbApiKeyConfigured) {
        setNoKeyItems((prev) => new Set(prev).add(key))
        return
      }

      // Clear any prior no-key warning for this item
      setNoKeyItems((prev) => { const next = new Set(prev); next.delete(key); return next })

      // Use cache if available
      if (candidatesMap[key] !== undefined) return

      setLoadingKeys((prev) => new Set(prev).add(key))
      try {
        const result = await searchUnmatchedTmdb({
          title: item.year
            ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
            : item.title,
          year: item.year,
          type: item.tmdbType ?? getTmdbSearchType(modalType),
        })
        setCandidatesMap((prev) => ({ ...prev, [key]: result.candidates }))
      } catch {
        setCandidatesMap((prev) => ({ ...prev, [key]: [] }))
      } finally {
        setLoadingKeys((prev) => {
          const next = new Set(prev)
          next.delete(key)
          return next
        })
      }
    },
    [modalType, candidatesMap, expandedKey, loadingKeys, itemKey, tmdbApiKeyConfigured],
  )

  const handleCopyLink = useCallback(async (link: string) => {
    try {
      await navigator.clipboard.writeText(link)
      setCopiedLink(link)
      setTimeout(() => setCopiedLink(null), 2000)
      showToast('Link copied')
    } catch {
      try {
        const el = document.createElement('textarea')
        el.value = link
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        setCopiedLink(link)
        setTimeout(() => setCopiedLink(null), 2000)
        showToast('Link copied')
      } catch (fallbackError) {
        console.error('Failed to copy link:', fallbackError)
        showToast('Failed to copy link', 'error')
      }
    }
  }, [showToast])

  const handleCopyTitle = useCallback(async (candidate: TmdbCandidate, key: string) => {
    const text = candidate.year ? `${candidate.title} (${candidate.year})` : candidate.title
    try {
      await navigator.clipboard.writeText(text)
      setCopiedTitle(key)
      setTimeout(() => setCopiedTitle(null), 2000)
      showToast('Title copied')
    } catch {
      try {
        const el = document.createElement('textarea')
        el.value = text
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        setCopiedTitle(key)
        setTimeout(() => setCopiedTitle(null), 2000)
        showToast('Title copied')
      } catch (fallbackError) {
        console.error('Failed to copy title:', fallbackError)
        showToast('Failed to copy title', 'error')
      }
    }
  }, [showToast])

  if (!modalType || !unmatchedStats?.unmatched) return null

  const allItems = buildAllItems(modalType, unmatchedStats)

  if (allItems.length === 0) {
    return (
      <div className="modal-overlay" onClick={handleOverlayClick}>
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>{getModalTitle(modalType)}</h2>
            <button className="modal-close" onClick={onClose}>×</button>
          </div>
          <div className="modal-body">
            <div className="success-modal-body">
              <CheckCircle className="success-icon" size={64} />
              <p className="success-message">All assets matched!</p>
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    )
  }

  // The All view mixes types, so it gets the group pills; single-type views just sort.
  const showGroup = modalType === 'all'
  const hasSeasons = allItems.some((item) => item.seasonCount > 0)

  const lowerQuery = searchQuery.trim().toLowerCase()
  const searchedItems = lowerQuery
    ? allItems.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : allItems
  const groupFilteredItems = showGroup && prefs.group !== 'all'
    ? searchedItems.filter((item) => item.type === prefs.group)
    : searchedItems
  const sortedItems = sortItems(groupFilteredItems, prefs)

  // Publish the current (filtered) list to the community Lists tab for makers to work from.
  const handleAddToLists = async () => {
    if (!isConnected || !token) { login(); return }
    const inputs = sortedItems.map(toListInput)
    if (!inputs.length) return
    setPublishing(true)
    try {
      await publishToCommunityLists(inputs, token, showToast)
    } finally {
      setPublishing(false)
    }
  }

  // Create List: rows for the picker (all items of this view), and a publish
  // handler that turns the chosen keys back into list inputs.
  const selectableItems: SelectableListItem[] = allItems.map((item) => ({
    key: itemKey(item),
    title: item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title,
    year: item.year,
    badgeType: item.category
      ? (item.category.toLowerCase() as 'movie' | 'series' | 'season' | 'collection')
      : item.seasonCount > 0 ? 'season'
      : item.type === 'show' ? 'series'
      : item.type === 'collection' ? 'collection'
      : 'movie',
    posterUrl: item.poster_url,
    claimStatus: getClaimStatus({ tmdb_id: item.tmdb_id, tvdb_id: item.tvdb_id, media_type: item.type, title: item.title, year: item.year }),
    available: item.available,
  }))

  const handleCreateListAdd = async (keys: string[]) => {
    if (!isConnected || !token) { login(); return }
    const set = new Set(keys)
    const inputs = allItems.filter((i) => set.has(itemKey(i))).map(toListInput)
    if (!inputs.length) return
    setPublishing(true)
    try {
      await publishToCommunityLists(inputs, token, showToast)
      setCreateListOpen(false)
    } finally {
      setPublishing(false)
    }
  }

  // Only cap the rendered count when not searching (search results show in full).
  const displayItems = lowerQuery ? sortedItems : sortedItems.slice(0, modalDisplayLimit)
  const hasMore = !lowerQuery && sortedItems.length > modalDisplayLimit

  const renderRow = (item: NormalizedItem) => {
    const key = itemKey(item)
    const isExpanded = expandedKey === key
    const isLoading = loadingKeys.has(key)
    const candidates = candidatesMap[key]
    const isNoKey = noKeyItems.has(key)

    return (
      <div key={key} className={`unmatched-item-with-tmdb${isExpanded ? ' expanded' : ''}`}>
        <div className="unmatched-item">
          <div className="unmatched-item-top">
          <div className="unmatched-item-meta">
            <span className="item-title">
              {item.year
                ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
                : item.title}
            </span>
            {item.year && <span className="item-year">({item.year})</span>}
            {item.category && (
              <span className={`unmatched-cat-badge unmatched-cat-badge--${item.category.toLowerCase()}`}>
                {item.category}
              </span>
            )}
            <CommunityStatusBadge status={getClaimStatus({ tmdb_id: item.tmdb_id, tvdb_id: item.tvdb_id, media_type: item.type, title: item.title, year: item.year })} />
            <ArrMissingBadge available={item.available} />
          </div>
          <div className="unmatched-item-actions">
          <button
            className={`tmdb-search-btn${isExpanded ? ' active' : ''}`}
            onClick={() => handleTmdbSearch(item)}
            title="Search TMDB for this item"
          >
            {isLoading ? <Loader2 size={13} className="spin-icon" /> : <Search size={13} />}
            <span>TMDB</span>
          </button>
          <button
            type="button"
            className="maker-nav-btn"
            title="Search in Maker Tools (opens in a new tab)"
            onClick={() => {
              const cleanedTitle = item.year
                ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
                : item.title
              const query = item.year ? `${cleanedTitle} ${item.year}` : cleanedTitle
              const filter = mediaTypeToTmdbFilter(item.type)
              window.open(`/maker-tools?tmdbSearch=${encodeURIComponent(query)}${filter ? `&type=${filter}` : ''}`, '_blank', 'noopener,noreferrer')
            }}
          >
            <Search size={13} />
            <span>Maker</span>
          </button>
          <button
            type="button"
            className="community-request-btn"
            title="Request this poster from the community"
            onClick={() => setRequestItem(item)}
          >
            <Star size={13} />
            <span>Request</span>
          </button>
          </div>
          </div>
          {item.missingSeasonsNumbers && item.missingSeasonsNumbers.length > 0 && (
            <div className="unmatched-seasons-row">
              {item.missingSeasonsNumbers.map((s) => (
                <span key={s} className="unmatched-cat-badge unmatched-cat-badge--season">
                  {s === 0 ? 'Specials' : `S${s}`}
                </span>
              ))}
            </div>
          )}
        </div>

        {isExpanded && (
          <div className="tmdb-candidates-panel">
            {isNoKey ? (
              <div className="tmdb-candidates-warning">
                <AlertCircle size={14} />
                <span>No TMDB API key configured. Add it in <strong>Settings → General → API Keys</strong>.</span>
              </div>
            ) : isLoading ? (
              <div className="tmdb-candidates-loading">
                <Loader2 size={16} className="spin-icon" />
                <span>Searching TMDB…</span>
              </div>
            ) : !candidates || candidates.length === 0 ? (
              <div className="tmdb-candidates-empty">No TMDB results found</div>

            ) : (
              candidates.map((candidate, cidx) => {
                const link = getTmdbLink(candidate)
                const isCopied = copiedLink === link
                const isTitleCopied = copiedTitle === link
                const previewSrc = candidate.poster_url
                  ? candidate.poster_url.replace('/w185/', '/w342/')
                  : null
                return (
                  <div key={cidx} className="tmdb-candidate-item">
                    {previewSrc ? (
                      <button
                        className="tmdb-candidate-poster-btn"
                        onClick={() => setPreviewUrl(previewSrc)}
                        title="Click to preview poster"
                      >
                        <img
                          src={candidate.poster_url!}
                          alt=""
                          className="tmdb-candidate-poster"
                          loading="lazy"
                        />
                      </button>
                    ) : (
                      <div className="tmdb-candidate-poster tmdb-candidate-poster--empty" />
                    )}
                    <div className="tmdb-candidate-info">
                      <div className="tmdb-candidate-title-row">
                        <span className="candidate-title">{candidate.title}</span>
                        {candidate.year && <span className="candidate-year">({candidate.year})</span>}
                        <span className={`tmdb-type-badge tmdb-type-badge--${candidate.media_type}`}>
                          {candidate.media_type}
                        </span>
                      </div>
                      <div className="tmdb-candidate-link-row">
                        <span className="tmdb-link-text">{link}</span>
                        <a
                          href={link}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="tmdb-icon-btn"
                          title="Open in TMDB"
                        >
                          <ExternalLink size={13} />
                          <span>Open</span>
                        </a>
                        <button
                          type="button"
                          className={`tmdb-copy-btn${isCopied ? ' copied' : ''}`}
                          onClick={() => handleCopyLink(link)}
                          title={isCopied ? 'Copied!' : 'Copy link'}
                        >
                          {isCopied ? <Check size={13} /> : <Copy size={13} />}
                          <span>{isCopied ? 'Copied' : 'Copy'}</span>
                        </button>
                        <button
                          type="button"
                          className={`tmdb-copy-btn${isTitleCopied ? ' copied' : ''}`}
                          onClick={() => handleCopyTitle(candidate, link)}
                          title={isTitleCopied ? 'Copied!' : 'Copy title & year'}
                        >
                          {isTitleCopied ? <Check size={13} /> : <Copy size={13} />}
                          <span>{isTitleCopied ? 'Copied' : 'Title'}</span>
                        </button>

                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <>
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>{getModalTitle(modalType)}</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {/* Search bar */}
          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${allItems.length} items…`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button className="search-clear-btn" onClick={() => setSearchQuery('')} title="Clear search">
                <X size={14} />
              </button>
            )}
          </div>

          <SortControls prefs={prefs} onChange={setPrefs} showGroup={showGroup} showSeasons={hasSeasons} />

          <div className="unmatched-list">
            <p className="list-count">
              {sortedItems.length !== allItems.length
                ? `${sortedItems.length} of ${allItems.length} items`
                : `${allItems.length} items`}
            </p>

            {hasMore && (
              <p className="performance-warning">
                ⚠️ Showing first {modalDisplayLimit} of {sortedItems.length} items. Use search to find specific items.
              </p>
            )}

            {displayItems.map(renderRow)}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          <button
            className="btn-secondary"
            onClick={handleAddToLists}
            disabled={publishing || sortedItems.length === 0}
            title={isConnected ? 'Publish these items to the Community Lists tab for makers' : 'Connect Discord to publish to Community Lists'}
          >
            {publishing ? <Loader2 size={16} className="spin-icon" /> : <ListPlus size={16} />}
            Add All to List
          </button>
          <button
            className="btn-secondary"
            onClick={() => { if (!isConnected || !token) { login(); return } setCreateListOpen(true) }}
            disabled={publishing || allItems.length === 0}
            title={isConnected ? 'Choose specific items to publish to Community Lists' : 'Connect Discord to publish to Community Lists'}
          >
            <ListChecks size={16} />
            Create List
          </button>
          {modalType !== 'all' && (
            <button className="btn-primary" onClick={() => onDownloadList(modalType!)} title="Download full list as text file">
              <Download size={18} />
              Download List
            </button>
          )}
        </div>
      </div>
    </div>

    {previewUrl && (
      <div className="modal-overlay tmdb-poster-preview-overlay" onClick={() => setPreviewUrl(null)}>
        <div className="tmdb-poster-preview-modal">
          <img
            src={previewUrl ?? undefined}
            alt="Poster preview"
            className="tmdb-poster-preview-image"
          />
        </div>
      </div>
    )}

    {requestItem && (
      <CommunityRequestModal
        title={requestItem.title}
        year={requestItem.year}
        tmdbType={requestItem.tmdbType ?? getTmdbSearchType(modalType)}
        seasonNumbers={requestItem.missingSeasonsNumbers}
        tmdbApiKeyConfigured={tmdbApiKeyConfigured}
        onClose={() => setRequestItem(null)}
      />
    )}

    {createListOpen && (
      <CreateListModal
        items={selectableItems}
        submitting={publishing}
        onAdd={handleCreateListAdd}
        onClose={() => setCreateListOpen(false)}
      />
    )}
    </>
  )
}

export default UnmatchedItemsModal
