import { useMemo, useState } from 'react'
import { Download, Search, X } from 'lucide-react'
import { DriveUsage, FallbackItem } from '../../api/posterManager'
import SortControls from './SortControls'
import { SLOT_LABELS, SLOT_ORDER, sortItems, useSortPrefs } from './itemSort'

type UsageView = 'used' | 'outranked'

type DriveUsageModalProps = {
  drive: DriveUsage
  items: FallbackItem[]
  outrankedItems: FallbackItem[]
  noun?: string
  onClose: () => void
  onDownload: (mode: UsageView) => void
}

// FallbackItem plus grouping fields: an item's files collapse to one row (a show's
// seasons, an item's artwork slots); hasMain marks whether a show's main poster
// (not just seasons) came from this drive.
type GroupedItem = FallbackItem & { seasons: number[]; slots: string[]; hasMain: boolean; seasonCount: number }

export default function DriveUsageModal({ drive, items, outrankedItems, noun = 'poster', onClose, onDownload }: DriveUsageModalProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [prefs, setPrefs] = useSortPrefs('driveUsageSort')
  const [view, setView] = useState<UsageView>(items.length > 0 ? 'used' : 'outranked')

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  const activeItems = view === 'used' ? items : outrankedItems

  const lowerQuery = searchQuery.trim().toLowerCase()
  const filteredItems = lowerQuery
    ? activeItems.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : activeItems

  const groupedItems = useMemo<GroupedItem[]>(() => {
    const result: GroupedItem[] = []
    const map = new Map<string, GroupedItem>()
    for (const item of filteredItems) {
      const key = `${item.type}::${item.title}::${item.year}`
      const existing = map.get(key)
      if (existing) {
        if (item.type === 'show' && item.season != null) existing.seasons.push(item.season)
        else if (item.type === 'show') existing.hasMain = true
        if (item.slot && !existing.slots.includes(item.slot)) existing.slots.push(item.slot)
      } else {
        const grouped: GroupedItem = {
          ...item,
          season: null,
          seasons: item.type === 'show' && item.season != null ? [item.season] : [],
          slots: item.slot ? [item.slot] : [],
          hasMain: item.type !== 'show' || item.season == null,
          seasonCount: 0,
        }
        map.set(key, grouped)
        result.push(grouped)
      }
    }
    for (const item of result) {
      item.seasons.sort((a, b) => a - b)
      item.seasonCount = item.seasons.length
      item.slots.sort((a, b) => SLOT_ORDER.indexOf(a) - SLOT_ORDER.indexOf(b))
    }
    return result
  }, [filteredItems])

  const typeFiltered = prefs.group === 'all' ? groupedItems : groupedItems.filter((i) => i.type === prefs.group)
  const sortedItems = useMemo(() => sortItems(typeFiltered, prefs), [typeFiltered, prefs])

  const titleCount = useMemo(() => {
    return new Set(activeItems.map((it) => `${it.type}::${it.title}::${it.year}`)).size
  }, [activeItems])

  const styleKey = (drive.style ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal list-items-modal">
        <div className="modal-header">
          <h2>
            {drive.name}{drive.style && <> <span className={`style-badge style-${styleKey}`}>{drive.style}</span></>}
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="drive-usage-view-tabs" role="group" aria-label="Used or not used">
            <button
              type="button"
              className={`drive-usage-view-tab${view === 'used' ? ' active' : ''}`}
              onClick={() => setView('used')}
            >
              Used ({items.length.toLocaleString()})
            </button>
            <button
              type="button"
              className={`drive-usage-view-tab${view === 'outranked' ? ' active' : ''}`}
              onClick={() => setView('outranked')}
            >
              Not used ({outrankedItems.length.toLocaleString()})
            </button>
          </div>

          <p className="style-fallback-modal-subtitle">
            {view === 'used' ? (
              <>
                {activeItems.length.toLocaleString()} {noun}{activeItems.length !== 1 ? 's' : ''} across{' '}
                {titleCount.toLocaleString()} title{titleCount !== 1 ? 's' : ''} came from this drive during the last rename.
              </>
            ) : (
              <>
                {activeItems.length.toLocaleString()} {noun}{activeItems.length !== 1 ? 's' : ''} across{' '}
                {titleCount.toLocaleString()} title{titleCount !== 1 ? 's' : ''} matched from this drive, but a
                higher-priority drive was used. Moving this drive up the priority list would use these.
              </>
            )}
          </p>

          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${activeItems.length} ${noun}s…`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button className="search-clear-btn" onClick={() => setSearchQuery('')} title="Clear search">
                <X size={14} />
              </button>
            )}
          </div>

          <SortControls prefs={prefs} onChange={setPrefs} showSeasons={false} />

          <div className="unmatched-list">
            <p className="list-count">
              {sortedItems.length !== groupedItems.length
                ? `${sortedItems.length} of ${groupedItems.length} titles`
                : `${groupedItems.length} titles`}
            </p>
            {sortedItems.map((item) => {
              const cleanTitle = item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title
              const badgeClass = item.type === 'movie' ? 'movie' : item.type === 'collection' ? 'collection' : 'series'
              const badgeLabel = item.type === 'movie' ? 'Movie' : item.type === 'collection' ? 'Collection' : 'Show'
              return (
                <div key={`${item.type}::${item.title}::${item.year}`} className="unmatched-item">
                  <div className="unmatched-item-top">
                    <div className="unmatched-item-meta">
                      <span className="item-title">{cleanTitle}</span>
                      {item.year && <span className="item-year">({item.year})</span>}
                      {(item.type !== 'show' || item.hasMain) && (
                        <span className={`unmatched-cat-badge unmatched-cat-badge--${badgeClass}`}>{badgeLabel}</span>
                      )}
                      {item.slots.map((s) => (
                        <span key={s} className="unmatched-cat-badge unmatched-cat-badge--season">
                          {SLOT_LABELS[s] ?? s}
                        </span>
                      ))}
                    </div>
                  </div>
                  {item.seasons.length > 0 && (
                    <div className="unmatched-seasons-row">
                      {item.seasons.map((s) => (
                        <span key={s} className="unmatched-cat-badge unmatched-cat-badge--season">
                          {s === 0 ? 'Specials' : `Season ${s}`}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>

        <div className="modal-footer">
          <div className="modal-footer-actions">
            <button className="btn-secondary" onClick={onClose}>Close</button>
            <button className="btn-primary" onClick={() => onDownload(view)} disabled={activeItems.length === 0}>
              <Download size={14} />
              Download List
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
