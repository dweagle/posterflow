import { useState, useMemo } from 'react'
import { Download, Search, X } from 'lucide-react'
import { FallbackItem } from '../../api/posterManager'
import PosterStyleTmdbSearch from './PosterStyleTmdbSearch'

type PosterStyleModalProps = {
  preferredStyle: string
  fallbackStyle: string
  items: FallbackItem[]
  tmdbApiKeyConfigured: boolean
  onClose: () => void
  onDownload: () => void
}

export default function PosterStyleModal({
  preferredStyle,
  fallbackStyle,
  items,
  tmdbApiKeyConfigured,
  onClose,
  onDownload,
}: PosterStyleModalProps) {
  const [searchQuery, setSearchQuery] = useState('')

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  const lowerQuery = searchQuery.trim().toLowerCase()
  const filteredItems = lowerQuery
    ? items.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : items

  // Group shows by (title, year) so each show appears once with season badges below
  type GroupedItem = FallbackItem & { seasons?: (number | null)[] }
  const groupedItems = useMemo<GroupedItem[]>(() => {
    const result: GroupedItem[] = []
    const showMap = new Map<string, GroupedItem>()
    for (const item of filteredItems) {
      if (item.type === 'show') {
        const key = `${item.title}::${item.year}`
        if (showMap.has(key)) {
          if (item.season != null) showMap.get(key)!.seasons!.push(item.season)
        } else {
          const grouped: GroupedItem = { ...item, season: null, seasons: item.season != null ? [item.season] : [] }
          showMap.set(key, grouped)
          result.push(grouped)
        }
      } else {
        result.push(item)
      }
    }
    for (const item of result) {
      if (item.seasons) item.seasons.sort((a, b) => (a ?? 0) - (b ?? 0))
    }
    return result
  }, [filteredItems])

  const preferredKey = preferredStyle.toLowerCase().replace(/[^a-z0-9]/g, '')
  const fallbackKey = fallbackStyle.toLowerCase().replace(/[^a-z0-9]/g, '')

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>
            Missing <span className={`style-badge style-${preferredKey}`}>{preferredStyle}</span> Posters
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <p className="style-fallback-modal-subtitle">
            These {items.length.toLocaleString()} item{items.length !== 1 ? 's' : ''} used{' '}
            <span className={`style-badge style-${fallbackKey}`}>{fallbackStyle}</span> because no{' '}
            <span className={`style-badge style-${preferredKey}`}>{preferredStyle}</span> poster was available.
          </p>

          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${items.length} items…`}
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
                ? `${filteredItems.length} of ${items.length} items`
                : `${items.length} items`}
            </p>
            {groupedItems.map((item, i) => (
              <PosterStyleTmdbSearch key={i} item={item} tmdbApiKeyConfigured={tmdbApiKeyConfigured} seasons={item.seasons} />
            ))}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          <button className="btn-primary" onClick={onDownload}>
            <Download size={14} />
            Download List
          </button>
        </div>
      </div>
    </div>
  )
}
