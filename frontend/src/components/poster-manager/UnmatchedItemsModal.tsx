import { useState, useCallback } from 'react'
import { AlertCircle, CheckCircle, Copy, Check, Download, ExternalLink, Loader2, Search, X } from 'lucide-react'
import type { MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { type UnmatchedStats, type TmdbCandidate, searchUnmatchedTmdb } from '../../api/client'
import { useToast } from '../Toast'

export type UnmatchedModalType = 'movies' | 'series' | 'collections' | 'seasons' | 'all' | null

type TmdbSearchType = 'movie' | 'show' | 'collection'

interface NormalizedItem {
  title: string
  year: number | null
  origIdx: number
  missingSeasonsText?: string
  tmdbType?: TmdbSearchType
  category?: string
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

function buildAllItems(modalType: UnmatchedModalType, unmatchedStats: UnmatchedStats): NormalizedItem[] {
  if (modalType === 'movies') {
    return (unmatchedStats.unmatched.movies ?? []).map((item, i) => ({
      title: item.title,
      year: item.year ?? null,
      origIdx: i,
    }))
  }
  if (modalType === 'series') {
    return (unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_main_poster)
      .map((item, i) => ({ title: item.title, year: item.year ?? null, origIdx: i }))
  }
  if (modalType === 'seasons') {
    return (unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_seasons.length > 0)
      .map((item, i) => ({
        title: item.title,
        year: item.year ?? null,
        origIdx: i,
        missingSeasonsText: item.missing_seasons.map((s) => `S${s}`).join(', '),
      }))
  }
  if (modalType === 'collections') {
    return (unmatchedStats.unmatched.collections ?? []).map((item, i) => ({
      title: item.title,
      year: item.year ?? null,
      origIdx: i,
    }))
  }
  if (modalType === 'all') {
    const result: NormalizedItem[] = []
    ;(unmatchedStats.unmatched.movies ?? []).forEach((item, i) => {
      result.push({ title: item.title, year: item.year ?? null, origIdx: i, tmdbType: 'movie', category: 'Movie' })
    })
    ;(unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_main_poster)
      .forEach((item, i) => {
        result.push({ title: item.title, year: item.year ?? null, origIdx: i, tmdbType: 'show', category: 'Series' })
      })
    ;(unmatchedStats.unmatched.series ?? [])
      .filter((s) => s.missing_seasons.length > 0)
      .forEach((item, i) => {
        result.push({
          title: item.title,
          year: item.year ?? null,
          origIdx: i,
          missingSeasonsText: item.missing_seasons.map((s) => `S${s}`).join(', '),
          tmdbType: 'show',
          category: 'Season',
        })
      })
    ;(unmatchedStats.unmatched.collections ?? []).forEach((item, i) => {
      result.push({ title: item.title, year: item.year ?? null, origIdx: i, tmdbType: 'collection', category: 'Collection' })
    })
    return result
  }
  return []
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
  const navigate = useNavigate()
  const [searchQuery, setSearchQuery] = useState('')
  const [candidatesMap, setCandidatesMap] = useState<Record<string, TmdbCandidate[]>>({})
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set())
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const [copiedLink, setCopiedLink] = useState<string | null>(null)
  const [copiedTitle, setCopiedTitle] = useState<string | null>(null)
  const [noKeyItems, setNoKeyItems] = useState<Set<string>>(new Set())
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

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

  const lowerQuery = searchQuery.trim().toLowerCase()
  const filteredItems = lowerQuery
    ? allItems.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : allItems.slice(0, modalDisplayLimit)

  const hasMore = !lowerQuery && allItems.length > modalDisplayLimit

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

          <div className="unmatched-list">
            <p className="list-count">
              {lowerQuery
                ? `${filteredItems.length} of ${allItems.length} items`
                : `${allItems.length} items`}
            </p>

            {hasMore && (
              <p className="performance-warning">
                ⚠️ Showing first {modalDisplayLimit} of {allItems.length} items. Use search to find specific items.
              </p>
            )}

            {filteredItems.map((item) => {
              const key = itemKey(item)
              const isExpanded = expandedKey === key
              const isLoading = loadingKeys.has(key)
              const candidates = candidatesMap[key]
              const isNoKey = noKeyItems.has(key)

              return (
                <div key={key} className={`unmatched-item-with-tmdb${isExpanded ? ' expanded' : ''}`}>
                  <div className="unmatched-item">
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
                    </div>
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
                      title="Search in Maker Tools"
                      onClick={() => {
                        const cleanedTitle = item.year
                          ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
                          : item.title
                        navigate('/maker-tools', { state: { tmdbSearch: item.year ? `${cleanedTitle} ${item.year}` : cleanedTitle } })
                      }}
                    >
                      <Search size={13} />
                      <span>Maker</span>
                    </button>
                  </div>

                  {item.missingSeasonsText && (
                    <div className="missing-seasons">Missing: {item.missingSeasonsText}</div>
                  )}

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
            })}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Close</button>
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
    </>
  )
}

export default UnmatchedItemsModal

