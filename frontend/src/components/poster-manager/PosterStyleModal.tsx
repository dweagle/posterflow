import { useState, useMemo, useCallback } from 'react'
import { Download, ListPlus, ListChecks, Loader2, Search, X } from 'lucide-react'
import { FallbackItem } from '../../api/posterManager'
import { type ListItemInput } from '../../api/client'
import { publishToCommunityLists } from './publishToCommunityLists'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { useCommunityClaimStatus } from '../../hooks/useCommunityClaimStatus'
import { useToast } from '../Toast'
import CreateListModal, { type SelectableListItem } from './CreateListModal'
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
  const [createListOpen, setCreateListOpen] = useState(false)

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  // Build community-list inputs from a set of fallback items, tagged with the
  // preferred style. Movies/collections publish one card each. Shows group by
  // title+year: a missing main poster → one show card (seasons ride with it); only
  // missing seasons → one season card encoding "Seasons: …". Shared by "Add to
  // Lists" (all) and "Create List" (a chosen subset).
  const buildInputs = useCallback((source: FallbackItem[]): ListItemInput[] => {
    const styleTag = preferredStyle.toUpperCase().includes('CL2K') ? 'CL2K' : preferredStyle.toUpperCase().includes('MM2K') ? 'MM2K' : preferredStyle
    const clean = (t: string, y: number | null) => (y ? t.replace(/\s*\(\d{4}\)\s*$/, '').trim() : t)
    const refs = (it: FallbackItem) => ({
      tmdb_id: it.tmdb_id ?? null,
      tvdb_id: it.tvdb_id ?? null,
      imdb_id: it.imdb_id ?? null,
      poster_path: it.poster_url ?? null,
    })
    const inputs: ListItemInput[] = []
    const seriesOrder: string[] = []
    const seriesGroups = new Map<string, FallbackItem[]>()
    for (const item of source) {
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
    return inputs
  }, [preferredStyle])

  const publishInputs = useCallback(async (inputs: ListItemInput[], after?: () => void) => {
    if (!token) { login(); return }
    if (!inputs.length) return
    setPublishing(true)
    try {
      await publishToCommunityLists(inputs, token, showToast)
      after?.()
    } finally {
      setPublishing(false)
    }
  }, [token, login, showToast])

  // Publish the whole fallback list to the community Lists tab.
  const handleAddToLists = () => {
    if (!isConnected || !token) { login(); return }
    void publishInputs(buildInputs(items))
  }

  // Picker rows: one per group (shows collapse to a single row by title+year).
  const selectableItems: SelectableListItem[] = useMemo(() => {
    const rows: SelectableListItem[] = []
    const seen = new Set<string>()
    for (const it of items) {
      const key = `${it.type}::${it.title}::${it.year}`
      if (seen.has(key)) continue
      seen.add(key)
      rows.push({
        key,
        title: it.year ? it.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : it.title,
        year: it.year,
        badgeType: it.type === 'show' ? 'series' : it.type === 'collection' ? 'collection' : 'movie',
        posterUrl: it.poster_url,
        claimStatus: getClaimStatus({ tmdb_id: it.tmdb_id, tvdb_id: it.tvdb_id, media_type: it.type, title: it.title, year: it.year }),
        available: it.available,
      })
    }
    return rows
  }, [items, getClaimStatus])

  const handleCreateListAdd = (keys: string[]) => {
    if (!isConnected || !token) { login(); return }
    const set = new Set(keys)
    const inputs = buildInputs(items.filter((it) => set.has(`${it.type}::${it.title}::${it.year}`)))
    void publishInputs(inputs, () => setCreateListOpen(false))
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
    <>
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal list-items-modal">
        <div className="modal-header">
          <h2>
            Missing <span className={`style-badge style-${preferredKey}`}>{preferredStyle}</span> Posters
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <p className="style-fallback-modal-subtitle">
            These {items.length.toLocaleString()} poster{items.length !== 1 ? 's' : ''} across{' '}
            {selectableItems.length.toLocaleString()} title{selectableItems.length !== 1 ? 's' : ''} used{' '}
            <span className={`style-badge style-${fallbackKey}`}>{fallbackStyle}</span> because no{' '}
            <span className={`style-badge style-${preferredKey}`}>{preferredStyle}</span> poster was available.
          </p>

          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${items.length} posters…`}
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
                ? `${sortedItems.length} of ${groupedItems.length} titles`
                : `${groupedItems.length} titles`}
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
            Add All to List
          </button>
          <button
            className="btn-secondary"
            onClick={() => { if (!isConnected || !token) { login(); return } setCreateListOpen(true) }}
            disabled={publishing || items.length === 0}
            title={isConnected ? 'Choose specific items to publish to Community Lists' : 'Connect Discord to publish to Community Lists'}
          >
            <ListChecks size={14} />
            Create List
          </button>
          <button className="btn-primary" onClick={onDownload}>
            <Download size={14} />
            Download List
          </button>
        </div>
      </div>
    </div>

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
