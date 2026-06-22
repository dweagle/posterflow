import { useState, useMemo } from 'react'
import { Download, ListPlus, Loader2, Search, X } from 'lucide-react'
import { FallbackItem } from '../../api/posterManager'
import { type ListItemInput } from '../../api/client'
import { publishToCommunityLists } from './publishToCommunityLists'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { useCommunityClaimStatus } from '../../hooks/useCommunityClaimStatus'
import { useToast } from '../Toast'
import PosterStyleTmdbSearch from './PosterStyleTmdbSearch'
import SortControls from './SortControls'
import { sortItems, useSortPrefs } from './itemSort'

type PosterStyleModalProps = {
  preferredStyle: string
  fallbackStyle: string
  items: FallbackItem[]
  tmdbApiKeyConfigured: boolean
  onClose: () => void
  onDownload: () => void
}

// FallbackItem plus the fields the sort/group helpers need. Shows are collapsed
// to one row carrying their missing-season list.
type GroupedItem = FallbackItem & { seasons?: (number | null)[]; seasonCount: number }

export default function PosterStyleModal({
  preferredStyle,
  fallbackStyle,
  items,
  tmdbApiKeyConfigured,
  onClose,
  onDownload,
}: PosterStyleModalProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [prefs, setPrefs] = useSortPrefs('posterStyleSort')
  const { showToast } = useToast()
  const { isConnected, token, login } = useDiscordAuth()
  const { getStatus: getClaimStatus } = useCommunityClaimStatus()
  const [publishing, setPublishing] = useState(false)

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  // Publish the fallback list to the community Lists tab, tagged with the preferred style.
  const handleAddToLists = async () => {
    if (!isConnected || !token) { login(); return }
    const styleTag = preferredStyle.toUpperCase().includes('CL2K') ? 'CL2K' : preferredStyle.toUpperCase().includes('MM2K') ? 'MM2K' : preferredStyle
    const clean = (t: string, y: number | null) => (y ? t.replace(/\s*\(\d{4}\)\s*$/, '').trim() : t)
    const refs = (it: FallbackItem) => ({
      tmdb_id: it.tmdb_id ?? null,
      tvdb_id: it.tvdb_id ?? null,
      imdb_id: it.imdb_id ?? null,
      poster_path: it.poster_url ?? null,
    })

    // Movies/collections publish one card each. Shows are grouped by title+year:
    //  - if the show's main poster is missing, add a single show card (the seasons
    //    ride with it — no separate season cards);
    //  - if only individual seasons are missing, add ONE season card whose missing
    //    seasons render as individual badges (encoded in notes "Seasons: …").
    const inputs: ListItemInput[] = []
    const seriesOrder: string[] = []
    const seriesGroups = new Map<string, FallbackItem[]>()
    for (const item of items) {
      if (item.type === 'show') {
        const key = `${item.title}::${item.year ?? ''}`
        if (!seriesGroups.has(key)) { seriesGroups.set(key, []); seriesOrder.push(key) }
        seriesGroups.get(key)!.push(item)
      } else {
        inputs.push({
          media_type: item.type,
          title: clean(item.title, item.year),
          year: item.year,
          season_number: null,
          ...refs(item),
          style_tag: styleTag,
          source: 'style_fallback',
        })
      }
    }
    for (const key of seriesOrder) {
      const group = seriesGroups.get(key)!
      const main = group.find((g) => g.season == null)
      const ref = main ?? group[0]
      const base = {
        title: clean(ref.title, ref.year),
        year: ref.year,
        ...refs(ref),
        style_tag: styleTag,
        source: 'style_fallback' as const,
      }
      if (main) {
        inputs.push({ ...base, media_type: 'show', season_number: null })
      } else {
        const seasons = group
          .map((g) => g.season)
          .filter((s): s is number => s != null)
          .sort((a, b) => a - b)
        inputs.push({ ...base, media_type: 'season', season_number: null, notes: `Seasons: ${seasons.join(', ')}` })
      }
    }
    if (!inputs.length) return
    setPublishing(true)
    try {
      await publishToCommunityLists(inputs, token, showToast)
    } finally {
      setPublishing(false)
    }
  }

  const lowerQuery = searchQuery.trim().toLowerCase()
  const filteredItems = lowerQuery
    ? items.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : items

  // Group shows by (title, year) so each show appears once with season badges below
  const groupedItems = useMemo<GroupedItem[]>(() => {
    const result: GroupedItem[] = []
    const showMap = new Map<string, GroupedItem>()
    for (const item of filteredItems) {
      if (item.type === 'show') {
        const key = `${item.title}::${item.year}`
        if (showMap.has(key)) {
          if (item.season != null) showMap.get(key)!.seasons!.push(item.season)
        } else {
          const grouped: GroupedItem = { ...item, season: null, seasons: item.season != null ? [item.season] : [], seasonCount: 0 }
          showMap.set(key, grouped)
          result.push(grouped)
        }
      } else {
        result.push({ ...item, seasonCount: 0 })
      }
    }
    for (const item of result) {
      if (item.seasons) {
        item.seasons.sort((a, b) => (a ?? 0) - (b ?? 0))
        item.seasonCount = item.seasons.length
      }
    }
    return result
  }, [filteredItems])

  const typeFiltered = prefs.group === 'all' ? groupedItems : groupedItems.filter((i) => i.type === prefs.group)
  const sortedItems = useMemo(() => sortItems(typeFiltered, prefs), [typeFiltered, prefs])
  const hasShows = groupedItems.some((i) => i.type === 'show')

  const preferredKey = preferredStyle.toLowerCase().replace(/[^a-z0-9]/g, '')
  const fallbackKey = fallbackStyle.toLowerCase().replace(/[^a-z0-9]/g, '')

  const renderItem = (item: GroupedItem) => (
    <PosterStyleTmdbSearch
      key={`${item.type}::${item.title}::${item.year}`}
      item={item}
      tmdbApiKeyConfigured={tmdbApiKeyConfigured}
      seasons={item.seasons}
      claimStatus={getClaimStatus({ tmdb_id: item.tmdb_id, tvdb_id: item.tvdb_id, media_type: item.type, title: item.title, year: item.year })}
    />
  )

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

          <SortControls prefs={prefs} onChange={setPrefs} showSeasons={hasShows} />

          <div className="unmatched-list">
            <p className="list-count">
              {sortedItems.length !== groupedItems.length
                ? `${sortedItems.length} of ${groupedItems.length} items`
                : `${groupedItems.length} items`}
            </p>
            {sortedItems.map(renderItem)}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Close</button>
          <button
            className="btn-secondary"
            onClick={handleAddToLists}
            disabled={publishing || items.length === 0}
            title={isConnected ? 'Publish these items to the Community Lists tab for makers' : 'Connect Discord to publish to Community Lists'}
          >
            {publishing ? <Loader2 size={14} className="spin-icon" /> : <ListPlus size={14} />}
            Add to Lists
          </button>
          <button className="btn-primary" onClick={onDownload}>
            <Download size={14} />
            Download List
          </button>
        </div>
      </div>
    </div>
  )
}
