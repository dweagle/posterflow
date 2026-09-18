import { FormEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Check, Search } from 'lucide-react'
import { API_URL } from '../api/http'
import { ArtworkSearchType, searchArtwork, searchPosters } from '../api/client'
import type { DrivePosterPick, FileBadge } from '../utils/posterOverrideTarget'
import { useFloatTip } from './FloatTip'
import { useToast } from './Toast'
import '../pages/AssetSearch.css'

export type AssetKind = 'posters' | 'artwork'

type DriveFilter = 'cl2k' | 'mm2k' | 'custom'
type DriveType = DriveFilter | 'artwork'

// One search hit, whichever index it came from: a poster name, or an artwork name + type.
type SearchHit = {
  name: string
  type: ArtworkSearchType | null
  drives: { drive_id: string; drive_name: string; drive_type: DriveType; image_url: string; file_path: string }[]
}

type DriveGroupItem = {
  name: string
  type: ArtworkSearchType | null
  image_url: string
  file_path: string
}

type DriveGroup = {
  drive_id: string
  drive_name: string
  drive_type: DriveType
  items: DriveGroupItem[]
}

type HoverPreviewState = {
  imageUrl: string
  name: string
  type: ArtworkSearchType | null
}

// [row badge, filter chip] per artwork type, in display order.
const ARTWORK_TYPE_LABELS: Record<ArtworkSearchType, [string, string]> = {
  logo: ['Logo', 'Logos'],
  background: ['Background', 'Backgrounds'],
  squareart: ['Square', 'Square art'],
}
const ARTWORK_TYPES = Object.keys(ARTWORK_TYPE_LABELS) as ArtworkSearchType[]
const typeOrder = (type: ArtworkSearchType | null) => (type ? ARTWORK_TYPES.indexOf(type) : -1)

type DriveSearchPanelProps = {
  /** Pre-fill the search box and run a search on mount. */
  initialQuery?: string
  /** Focus the search input on mount. */
  autoFocus?: boolean
  /** Enable the page-level "/" shortcut to focus the search box (page use only). */
  enableSlashFocus?: boolean
  /** Lock the panel to one index (the override picker is posters only) and hide the toggle. */
  fixedKind?: AssetKind
  /** Adds a per-result action (pin the file as an override); artwork hits carry their type. */
  onUse?: (pick: DrivePosterPick) => void
  useLabel?: string
  useTitle?: string
  /** Files currently pinned for the caller's target; their rows read "Using". */
  pickedFiles?: string[]
  /** The item's current file(s) for the slot being picked; those rows read "In use" instead of offering Use. */
  inUseFiles?: string[]
  /** Extra notes per file: files other overrides already pin. */
  fileBadges?: Record<string, FileBadge[]>
  useBusy?: boolean
}

const HOVER_PREVIEW_DELAY_MS = 220
const SEARCH_DEBOUNCE_MS = 250

/**
 * Reusable drive asset search: posters or artwork (logos / backgrounds / square art), input +
 * filters + results grouped by drive, with hover previews and copy. Used by the Asset Search
 * page and the in-place drive search modal so neither has to re-implement the lookup.
 */
export default function DriveSearchPanel({
  initialQuery = '',
  autoFocus = false,
  enableSlashFocus = false,
  fixedKind,
  onUse,
  useLabel = 'Use',
  useTitle = 'Use this file',
  pickedFiles = [],
  inUseFiles = [],
  fileBadges = {},
  useBusy = false,
}: DriveSearchPanelProps) {
  const [query, setQuery] = useState(initialQuery)
  const [kind, setKind] = useState<AssetKind>(fixedKind ?? 'posters')
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<SearchHit[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const hoverDelayTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [driveFilters, setDriveFilters] = useState<Record<DriveFilter, boolean>>({
    cl2k: true,
    mm2k: true,
    custom: true,
  })
  const [typeFilters, setTypeFilters] = useState<Record<ArtworkSearchType, boolean>>({
    logo: true,
    background: true,
    squareart: true,
  })
  const { showToast } = useToast()
  const { show: showTip, hide: hideTip, tip } = useFloatTip()
  const noun = kind === 'posters' ? 'poster' : 'artwork'

  const driveGroups = useMemo<DriveGroup[]>(() => {
    const groupMap = new Map<string, DriveGroup>()

    results.forEach((hit) => {
      if (hit.type && !typeFilters[hit.type]) {
        return
      }
      hit.drives.forEach((drive) => {
        // Poster drives filter by style; artwork drives have no style scheme.
        if (drive.drive_type !== 'artwork' && !driveFilters[drive.drive_type]) {
          return
        }

        const item: DriveGroupItem = { name: hit.name, type: hit.type, image_url: drive.image_url, file_path: drive.file_path }
        const existingGroup = groupMap.get(drive.drive_id)
        if (!existingGroup) {
          groupMap.set(drive.drive_id, {
            drive_id: drive.drive_id,
            drive_name: drive.drive_name,
            drive_type: drive.drive_type,
            items: [item],
          })
          return
        }

        const alreadyAdded = existingGroup.items.some((it) => it.name === hit.name && it.type === hit.type)
        if (!alreadyAdded) {
          existingGroup.items.push(item)
        }
      })
    })

    const sortedGroups = Array.from(groupMap.values())
      .map((group) => ({
        ...group,
        items: [...group.items].sort((a, b) => a.name.localeCompare(b.name) || typeOrder(a.type) - typeOrder(b.type)),
      }))
      .sort((a, b) => a.drive_name.localeCompare(b.drive_name))

    return sortedGroups
  }, [results, driveFilters, typeFilters])

  const totalMatches = useMemo(() => {
    return driveGroups.reduce((accumulator, group) => accumulator + group.items.length, 0)
  }, [driveGroups])

  const totalDriveGroups = driveGroups.length

  const resultCountLabel = useMemo(() => {
    if (!hasSearched) {
      return `Search for ${kind === 'posters' ? 'a poster' : 'an artwork'} name to see matching drive folders.`
    }

    if (driveGroups.length === 0) {
      return `No matching ${kind === 'posters' ? 'posters' : 'artwork'} found in drive folders.`
    }

    return `${totalMatches} ${noun} match${totalMatches === 1 ? '' : 'es'} across ${totalDriveGroups} drive${totalDriveGroups === 1 ? '' : 's'}`
  }, [hasSearched, driveGroups.length, totalDriveGroups, totalMatches, kind, noun])

  useEffect(() => {
    const clearPreview = () => {
      setHoverPreview(null)
      hideTip()
    }
    window.addEventListener('scroll', clearPreview, true)
    return () => {
      if (hoverDelayTimeoutRef.current) {
        clearTimeout(hoverDelayTimeoutRef.current)
        hoverDelayTimeoutRef.current = null
      }
      window.removeEventListener('scroll', clearPreview, true)
    }
  }, [hideTip])

  useEffect(() => {
    if (autoFocus) {
      searchInputRef.current?.focus()
      searchInputRef.current?.select()
    }
  }, [autoFocus])

  useEffect(() => {
    if (!enableSlashFocus) {
      return
    }

    const handleSlashFocus = (event: KeyboardEvent) => {
      if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) {
        return
      }

      const target = event.target as HTMLElement | null
      if (!target) {
        return
      }

      const tagName = target.tagName
      const isEditable =
        target.isContentEditable ||
        tagName === 'INPUT' ||
        tagName === 'TEXTAREA' ||
        tagName === 'SELECT'

      if (isEditable) {
        return
      }

      event.preventDefault()
      searchInputRef.current?.focus()
      searchInputRef.current?.select()
    }

    window.addEventListener('keydown', handleSlashFocus)
    return () => {
      window.removeEventListener('keydown', handleSlashFocus)
    }
  }, [enableSlashFocus])

  const clearHoverDelayTimeout = () => {
    if (hoverDelayTimeoutRef.current) {
      clearTimeout(hoverDelayTimeoutRef.current)
      hoverDelayTimeoutRef.current = null
    }
  }

  const toggleDriveFilter = (filter: DriveFilter) => {
    setDriveFilters((current) => ({
      ...current,
      [filter]: !current[filter],
    }))
  }

  const toggleTypeFilter = (type: ArtworkSearchType) => {
    setTypeFilters((current) => ({
      ...current,
      [type]: !current[type],
    }))
  }

  const runSearch = async (queryOverride?: string, kindOverride?: AssetKind) => {
    const trimmed = (queryOverride ?? query).trim()
    const searchKind = kindOverride ?? kind
    if (!trimmed) {
      setHasSearched(true)
      setResults([])
      setHoverPreview(null)
      return
    }

    setLoading(true)
    setHasSearched(true)

    try {
      if (searchKind === 'posters') {
        const data = await searchPosters(trimmed)
        setResults(data.items.map((item) => ({ name: item.poster_name, type: null, drives: item.drives })))
      } else {
        const data = await searchArtwork(trimmed)
        setResults(data.items.map((item) => ({ name: item.artwork_name, type: item.artwork_type, drives: item.drives })))
      }
      setHoverPreview(null)
    } catch (error) {
      console.error(`Failed to search ${searchKind}:`, error)
      showToast(`Failed to search ${searchKind}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  const switchKind = (next: AssetKind) => {
    if (next === kind) return
    setKind(next)
    setResults([])
    setHasSearched(false)
    setHoverPreview(null)
    if (query.trim()) runSearch(query, next)
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    await runSearch()
  }

  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed) {
      setHasSearched(false)
      setResults([])
      setHoverPreview(null)
      return
    }

    const timeoutId = setTimeout(() => {
      runSearch(trimmed)
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timeoutId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  // Placement is imperative: mouse moves reposition the popup element directly instead of
  // re-rendering the whole result list on every pointer event, and the real popup size is
  // measured so a short title doesn't leave it parked too high.
  const popupRef = useRef<HTMLDivElement | null>(null)
  const pointerRef = useRef({ x: 0, y: 0 })

  const placePopup = useCallback(() => {
    const el = popupRef.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    const { x: clientX, y: clientY } = pointerRef.current
    const offset = 18
    const margin = 12
    let x = clientX + offset
    let y = clientY + offset
    if (x + width > window.innerWidth - margin) x = clientX - width - offset
    // Below the cursor when it fits, else above it; never off-screen.
    if (y + height > window.innerHeight - margin) y = clientY - height - offset
    x = Math.max(margin, x)
    y = Math.max(margin, Math.min(y, window.innerHeight - height - margin))
    el.style.left = `${x}px`
    el.style.top = `${y}px`
  }, [])

  useLayoutEffect(() => {
    placePopup()
  }, [hoverPreview, placePopup])

  const showHoverPreview = (imageUrl: string, name: string, type: ArtworkSearchType | null) => {
    setHoverPreview({ imageUrl, name, type })
  }

  const hideHoverPreview = () => {
    clearHoverDelayTimeout()
    setHoverPreview(null)
  }

  const handleCopyName = async (name: string) => {
    const copied = kind === 'posters' ? 'Poster title copied' : 'Artwork name copied'
    try {
      await navigator.clipboard.writeText(name)
      showToast(copied)
    } catch {
      // Fallback for non-secure (HTTP) contexts where Clipboard API is unavailable
      try {
        const textArea = document.createElement('textarea')
        textArea.value = name
        textArea.style.position = 'fixed'
        textArea.style.left = '-9999px'
        textArea.style.top = '-9999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        // Cast through unknown to avoid deprecated type annotation on execCommand
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(textArea)
        showToast(copied)
      } catch (fallbackError) {
        console.error('Failed to copy name:', fallbackError)
        showToast('Failed to copy name', 'error')
      }
    }
  }

  const handlePosterMouseEnter = (item: DriveGroupItem, clientX: number, clientY: number) => {
    clearHoverDelayTimeout()
    pointerRef.current = { x: clientX, y: clientY }
    hoverDelayTimeoutRef.current = setTimeout(() => {
      showHoverPreview(item.image_url, item.name, item.type)
    }, HOVER_PREVIEW_DELAY_MS)
  }

  const handlePosterMouseMove = (item: DriveGroupItem, clientX: number, clientY: number) => {
    pointerRef.current = { x: clientX, y: clientY }
    if (hoverPreview && hoverPreview.imageUrl === item.image_url) {
      placePopup()
    }
  }

  return (
    <div className="poster-search">
      {!fixedKind && (
        <div className="asset-kind-toggle" role="tablist" aria-label="Asset kind">
          <button
            type="button"
            role="tab"
            aria-selected={kind === 'posters'}
            className={`asset-kind-tab${kind === 'posters' ? ' active' : ''}`}
            onClick={() => switchKind('posters')}
          >
            Posters
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={kind === 'artwork'}
            className={`asset-kind-tab${kind === 'artwork' ? ' active' : ''}`}
            onClick={() => switchKind('artwork')}
          >
            Artwork
          </button>
        </div>
      )}

      <form className="poster-search-toolbar" onSubmit={handleSubmit}>
        <div className="poster-search-input-wrap">
          <Search size={18} className="poster-search-input-icon" />
          <input
            ref={searchInputRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={kind === 'posters' ? 'Search poster name...' : 'Search artwork name...'}
            aria-label={kind === 'posters' ? 'Search poster name' : 'Search artwork name'}
          />
        </div>
        <button type="submit" className="poster-search-button" disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </button>
      </form>

      {kind === 'posters' ? (
        <div className="poster-search-filter-row">
          <span className="poster-search-filter-label">Filter drives:</span>
          <button
            type="button"
            className={`poster-filter-btn ${driveFilters.cl2k ? 'active' : ''}`}
            onClick={() => toggleDriveFilter('cl2k')}
          >
            CL2K
          </button>
          <button
            type="button"
            className={`poster-filter-btn ${driveFilters.mm2k ? 'active' : ''}`}
            onClick={() => toggleDriveFilter('mm2k')}
          >
            MM2K
          </button>
          <button
            type="button"
            className={`poster-filter-btn ${driveFilters.custom ? 'active' : ''}`}
            onClick={() => toggleDriveFilter('custom')}
          >
            Custom
          </button>
        </div>
      ) : (
        <div className="poster-search-filter-row">
          <span className="poster-search-filter-label">Filter types:</span>
          {ARTWORK_TYPES.map((type) => (
            <button
              key={type}
              type="button"
              className={`poster-filter-btn ${typeFilters[type] ? 'active' : ''}`}
              onClick={() => toggleTypeFilter(type)}
            >
              {ARTWORK_TYPE_LABELS[type][1]}
            </button>
          ))}
        </div>
      )}

      <div className="poster-search-count">{resultCountLabel}</div>

      <div className="poster-search-grid">
        <section className="poster-search-card poster-search-results-card">
          <h2>Matches by Drive</h2>
          <div className="poster-search-results-list">
            {driveGroups.length > 0 ? (
              driveGroups.map((group) => {
                return (
                  <div key={group.drive_id} className="drive-group">
                    <div className="drive-group-header">
                      <span className="drive-group-name">{group.drive_name}</span>
                      <span className={`drive-group-type ${group.drive_type}`}>{group.drive_type.toUpperCase()}</span>
                      <span className="drive-group-count">{group.items.length}</span>
                    </div>
                    <ul className="drive-group-list">
                      {group.items.map((item) => (
                        <li
                          key={`${group.drive_id}-${item.name}-${item.type ?? 'poster'}`}
                          className="poster-search-result-item"
                          onMouseLeave={hideHoverPreview}
                        >
                          <span
                            className="result-title result-hover-target"
                            onMouseEnter={(event) => handlePosterMouseEnter(item, event.clientX, event.clientY)}
                            onMouseMove={(event) => handlePosterMouseMove(item, event.clientX, event.clientY)}
                            onMouseLeave={hideHoverPreview}
                          >
                            {item.name}
                          </span>
                          {item.type && (
                            <span className={`asset-type-badge asset-type-badge--${item.type}`}>{ARTWORK_TYPE_LABELS[item.type][0]}</span>
                          )}
                          {(fileBadges[item.file_path] ?? []).map((badge, index) => (
                            <span
                              key={`${badge.kind}-${index}`}
                              className={`asset-file-badge asset-file-badge--${badge.kind}`}
                              onPointerEnter={badge.tip ? (e) => showTip(e, badge.tip as string) : undefined}
                              onPointerLeave={badge.tip ? hideTip : undefined}
                            >
                              {badge.text}
                            </span>
                          ))}
                          <button
                            type="button"
                            className="result-copy-btn"
                            onClick={() => { hideTip(); handleCopyName(item.name) }}
                            aria-label={`Copy title ${item.name}`}
                            onPointerEnter={(e) => showTip(e, kind === 'posters' ? 'Copy the poster title' : 'Copy the artwork name')}
                            onPointerLeave={hideTip}
                          >
                            Copy
                          </button>
                          {onUse && (() => {
                            const picked = pickedFiles.includes(item.file_path)
                            if (!picked && inUseFiles.includes(item.file_path)) {
                              return (
                                <span
                                  className="asset-file-badge asset-file-badge--inuse asset-inuse-pill"
                                  onPointerEnter={(e) => showTip(e, "The item's current file for this slot as of the last rename")}
                                  onPointerLeave={hideTip}
                                >
                                  In use
                                </span>
                              )
                            }
                            return (
                              <button
                                type="button"
                                className={`result-copy-btn result-use-btn${picked ? ' active' : ''}`}
                                onClick={() => {
                                  hideTip()
                                  onUse({
                                    poster_name: item.name,
                                    drive_id: group.drive_id,
                                    drive_name: group.drive_name,
                                    file_path: item.file_path,
                                    artwork_type: item.type,
                                  })
                                }}
                                disabled={useBusy}
                                aria-label={`${picked ? 'Stop using' : useLabel} ${item.name}`}
                                onPointerEnter={(e) => showTip(e, picked ? 'Pinned - click to remove the override' : useTitle)}
                                onPointerLeave={hideTip}
                              >
                                {picked ? <><Check size={12} /> Using</> : useLabel}
                              </button>
                            )
                          })()}
                        </li>
                      ))}
                    </ul>
                  </div>
                )
              })
            ) : (
              <div className="poster-search-empty">
                {hasSearched ? 'No matches found.' : `Run a search to see ${kind} and drives.`}
              </div>
            )}
          </div>
        </section>
      </div>

      {hoverPreview && (
        <div
          ref={popupRef}
          className={`hover-preview-popup${hoverPreview.type ? ` preview-${hoverPreview.type}` : ''}`}
          style={{ left: '-9999px', top: '-9999px' }}
        >
          <img src={`${API_URL}${hoverPreview.imageUrl}`} alt={hoverPreview.name} className="hover-preview-image" />
          <div className="hover-preview-title">
            {hoverPreview.name}
            {hoverPreview.type ? ` · ${ARTWORK_TYPE_LABELS[hoverPreview.type][0]}` : ''}
          </div>
        </div>
      )}
      {tip}
    </div>
  )
}
