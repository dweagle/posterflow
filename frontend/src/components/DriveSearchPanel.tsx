import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { API_URL } from '../api/http'
import { PosterSearchItem, searchPosters } from '../api/client'
import { useToast } from './Toast'
import '../pages/PosterSearch.css'

type DriveFilter = 'cl2k' | 'mm2k' | 'custom'

type DriveGroupItem = {
  poster_name: string
  image_url: string
}

type DriveGroup = {
  drive_id: string
  drive_name: string
  drive_type: DriveFilter
  items: DriveGroupItem[]
}

type HoverPreviewState = {
  imageUrl: string
  posterName: string
  x: number
  y: number
}

type DriveSearchPanelProps = {
  /** Pre-fill the search box and run a search on mount. */
  initialQuery?: string
  /** Focus the search input on mount. */
  autoFocus?: boolean
  /** Enable the page-level "/" shortcut to focus the search box (page use only). */
  enableSlashFocus?: boolean
}

const HOVER_PREVIEW_DELAY_MS = 220
const SEARCH_DEBOUNCE_MS = 250

/**
 * Reusable drive poster search: input + drive filters + results grouped by
 * drive, with hover previews and copy. Used by the Poster Search page and the
 * in-place drive search modal so neither has to re-implement the lookup.
 */
export default function DriveSearchPanel({ initialQuery = '', autoFocus = false, enableSlashFocus = false }: DriveSearchPanelProps) {
  const [query, setQuery] = useState(initialQuery)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<PosterSearchItem[]>([])
  const [hasSearched, setHasSearched] = useState(false)
  const [hoverPreview, setHoverPreview] = useState<HoverPreviewState | null>(null)
  const searchInputRef = useRef<HTMLInputElement | null>(null)
  const hoverDelayTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [driveFilters, setDriveFilters] = useState<Record<DriveFilter, boolean>>({
    cl2k: true,
    mm2k: true,
    custom: true,
  })
  const { showToast } = useToast()

  const driveGroups = useMemo<DriveGroup[]>(() => {
    const groupMap = new Map<string, DriveGroup>()

    results.forEach((posterResult) => {
      posterResult.drives.forEach((drive) => {
        if (!driveFilters[drive.drive_type]) {
          return
        }

        const existingGroup = groupMap.get(drive.drive_id)
        if (!existingGroup) {
          groupMap.set(drive.drive_id, {
            drive_id: drive.drive_id,
            drive_name: drive.drive_name,
            drive_type: drive.drive_type,
            items: [{
              poster_name: posterResult.poster_name,
              image_url: drive.image_url,
            }],
          })
          return
        }

        const alreadyAdded = existingGroup.items.some((item) => item.poster_name === posterResult.poster_name)
        if (!alreadyAdded) {
          existingGroup.items.push({
            poster_name: posterResult.poster_name,
            image_url: drive.image_url,
          })
        }
      })
    })

    const sortedGroups = Array.from(groupMap.values())
      .map((group) => ({
        ...group,
        items: [...group.items].sort((a, b) => a.poster_name.localeCompare(b.poster_name)),
      }))
      .sort((a, b) => a.drive_name.localeCompare(b.drive_name))

    return sortedGroups
  }, [results, driveFilters])

  const totalPosterMatches = useMemo(() => {
    return driveGroups.reduce((accumulator, group) => accumulator + group.items.length, 0)
  }, [driveGroups])

  const totalDriveGroups = driveGroups.length

  const resultCountLabel = useMemo(() => {
    if (!hasSearched) {
      return 'Search for a poster name to see matching drive folders.'
    }

    if (driveGroups.length === 0) {
      return 'No matching posters found in drive folders.'
    }

    return `${totalPosterMatches} poster match${totalPosterMatches === 1 ? '' : 'es'} across ${totalDriveGroups} drive${totalDriveGroups === 1 ? '' : 's'}`
  }, [hasSearched, driveGroups.length, totalDriveGroups, totalPosterMatches])

  useEffect(() => {
    const clearPreview = () => setHoverPreview(null)
    window.addEventListener('scroll', clearPreview, true)
    return () => {
      if (hoverDelayTimeoutRef.current) {
        clearTimeout(hoverDelayTimeoutRef.current)
        hoverDelayTimeoutRef.current = null
      }
      window.removeEventListener('scroll', clearPreview, true)
    }
  }, [])

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

  const runSearch = async (queryOverride?: string) => {
    const trimmed = (queryOverride ?? query).trim()
    if (!trimmed) {
      setHasSearched(true)
      setResults([])
      setHoverPreview(null)
      return
    }

    setLoading(true)
    setHasSearched(true)

    try {
      const data = await searchPosters(trimmed)
      setResults(data.items)
      setHoverPreview(null)
    } catch (error) {
      console.error('Failed to search posters:', error)
      showToast('Failed to search posters', 'error')
    } finally {
      setLoading(false)
    }
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

  const getPopupPosition = (clientX: number, clientY: number) => {
    const popupWidth = 240
    const popupHeight = 430
    const offset = 18

    let x = clientX + offset
    let y = clientY + offset

    if (x + popupWidth > window.innerWidth - 12) {
      x = clientX - popupWidth - offset
    }

    if (y + popupHeight > window.innerHeight - 12) {
      y = window.innerHeight - popupHeight - 12
    }

    if (x < 12) {
      x = 12
    }

    if (y < 12) {
      y = 12
    }

    return { x, y }
  }

  const showHoverPreview = (imageUrl: string, posterName: string, clientX: number, clientY: number) => {
    const position = getPopupPosition(clientX, clientY)
    setHoverPreview({
      imageUrl,
      posterName,
      x: position.x,
      y: position.y,
    })
  }

  const hideHoverPreview = () => {
    clearHoverDelayTimeout()
    setHoverPreview(null)
  }

  const handleCopyPosterTitle = async (posterName: string) => {
    try {
      await navigator.clipboard.writeText(posterName)
      showToast('Poster title copied')
    } catch {
      // Fallback for non-secure (HTTP) contexts where Clipboard API is unavailable
      try {
        const textArea = document.createElement('textarea')
        textArea.value = posterName
        textArea.style.position = 'fixed'
        textArea.style.left = '-9999px'
        textArea.style.top = '-9999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        // Cast through unknown to avoid deprecated type annotation on execCommand
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(textArea)
        showToast('Poster title copied')
      } catch (fallbackError) {
        console.error('Failed to copy poster title:', fallbackError)
        showToast('Failed to copy poster title', 'error')
      }
    }
  }

  const handlePosterMouseEnter = (imageUrl: string, posterName: string, clientX: number, clientY: number) => {
    clearHoverDelayTimeout()
    hoverDelayTimeoutRef.current = setTimeout(() => {
      showHoverPreview(imageUrl, posterName, clientX, clientY)
    }, HOVER_PREVIEW_DELAY_MS)
  }

  const handlePosterMouseMove = (imageUrl: string, posterName: string, clientX: number, clientY: number) => {
    if (hoverPreview && hoverPreview.imageUrl === imageUrl && hoverPreview.posterName === posterName) {
      showHoverPreview(imageUrl, posterName, clientX, clientY)
    }
  }

  return (
    <div className="poster-search">
      <form className="poster-search-toolbar" onSubmit={handleSubmit}>
        <div className="poster-search-input-wrap">
          <Search size={18} className="poster-search-input-icon" />
          <input
            ref={searchInputRef}
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search poster name..."
            aria-label="Search poster name"
          />
        </div>
        <button type="submit" className="poster-search-button" disabled={loading}>
          {loading ? 'Searching...' : 'Search'}
        </button>
      </form>

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
                          key={`${group.drive_id}-${item.poster_name}`}
                          className="poster-search-result-item"
                          onMouseLeave={hideHoverPreview}
                        >
                          <span
                            className="result-title result-hover-target"
                            onMouseEnter={(event) => handlePosterMouseEnter(item.image_url, item.poster_name, event.clientX, event.clientY)}
                            onMouseMove={(event) => handlePosterMouseMove(item.image_url, item.poster_name, event.clientX, event.clientY)}
                            onMouseLeave={hideHoverPreview}
                          >
                            {item.poster_name}
                          </span>
                          <button
                            type="button"
                            className="result-copy-btn"
                            onClick={() => handleCopyPosterTitle(item.poster_name)}
                            aria-label={`Copy title ${item.poster_name}`}
                            title="Copy title"
                          >
                            Copy
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )
              })
            ) : (
              <div className="poster-search-empty">
                {hasSearched ? 'No matches found.' : 'Run a search to see posters and drives.'}
              </div>
            )}
          </div>
        </section>
      </div>

      {hoverPreview && (
        <div className="hover-preview-popup" style={{ left: `${hoverPreview.x}px`, top: `${hoverPreview.y}px` }}>
          <img src={`${API_URL}${hoverPreview.imageUrl}`} alt={hoverPreview.posterName} className="hover-preview-image" />
          <div className="hover-preview-title">{hoverPreview.posterName}</div>
        </div>
      )}
    </div>
  )
}
