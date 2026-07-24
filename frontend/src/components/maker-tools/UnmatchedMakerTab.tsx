import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clapperboard as MovieIcon, FolderOpen, Loader2, RefreshCw, Search, Tv, X } from 'lucide-react'
import {
  checkTmdbPosterAvailability,
  PosterAvailability,
  searchUnmatchedTmdb,
  TmdbCandidate,
  TmdbSearchResult,
  UnmatchedStats,
} from '../../api/client'
import TmdbItemCard, { PsdConfig } from './TmdbItemCard'
import { useToast } from '../Toast'
import Toolbar from '../Toolbar'
import ArtworkUnmatchedMakerPanel from './ArtworkUnmatchedMakerPanel'

type Category = 'movie' | 'series' | 'season' | 'collection'
type FilterTab = 'all' | Category

interface FlatItem {
  key: string
  title: string                                  // cleaned title (no trailing "(year)")
  year: number | null
  category: Category
  tmdbType: 'movie' | 'show' | 'collection'      // for searchUnmatchedTmdb
  mediaType: 'movie' | 'tv' | 'collection'       // for TmdbItemCard / poster-check
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  poster_url: string | null
  missingSeasons: number[]
  instance: string
}

const CATEGORY_LABEL: Record<Category, string> = {
  movie: 'Movie',
  series: 'Series',
  season: 'Season',
  collection: 'Collection',
}

// Tab labels — pluralized where it reads naturally ("Series" is already plural).
const TAB_LABEL: Record<FilterTab, string> = {
  all: 'All',
  movie: 'Movies',
  series: 'Series',
  season: 'Seasons',
  collection: 'Collections',
}

const cleanTitle = (title: string, year: number | null): string =>
  year ? title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : title

const tmdbHomepage = (mediaType: FlatItem['mediaType'], id: number): string => {
  const seg = mediaType === 'movie' ? 'movie' : mediaType === 'collection' ? 'collection' : 'tv'
  return `https://www.themoviedb.org/${seg}/${id}`
}

// Build the TmdbSearchResult the card consumes from an unmatched row + a resolved tmdb id.
const buildResult = (item: FlatItem, tmdbId: number, posterUrl: string | null, imdbId: string | null, tvdbId: number | null): TmdbSearchResult => ({
  tmdb_id: tmdbId,
  media_type: item.mediaType,
  title: item.title,
  year: item.year ? String(item.year) : '',
  overview: '',
  poster_url: posterUrl || '',
  homepage: tmdbId ? tmdbHomepage(item.mediaType, tmdbId) : '',
  imdb_id: imdbId ?? null,
  tvdb_id: tvdbId ?? null,
})

// Flatten the unmatched detection result into one card per piece of missing work.
// A series can yield both a "series" row (missing main poster) and a "season" row
// (missing season posters), matching how the Asset Manager modal splits them.
function flatten(stats: UnmatchedStats): FlatItem[] {
  const out: FlatItem[] = []

  ;(stats.unmatched.movies ?? []).forEach((m, i) => {
    out.push({
      key: `movie-${i}`,
      title: cleanTitle(m.title, m.year),
      year: m.year ?? null,
      category: 'movie',
      tmdbType: 'movie',
      mediaType: 'movie',
      tmdb_id: m.tmdb_id ?? null,
      tvdb_id: m.tvdb_id ?? null,
      imdb_id: m.imdb_id ?? null,
      poster_url: m.poster_url ?? null,
      missingSeasons: [],
      instance: m.instance,
    })
  })

  ;(stats.unmatched.series ?? []).forEach((s, i) => {
    if (s.missing_main_poster) {
      out.push({
        key: `series-${i}`,
        title: cleanTitle(s.title, s.year),
        year: s.year ?? null,
        category: 'series',
        tmdbType: 'show',
        mediaType: 'tv',
        tmdb_id: s.tmdb_id ?? null,
        tvdb_id: s.tvdb_id ?? null,
        imdb_id: s.imdb_id ?? null,
        poster_url: s.poster_url ?? null,
        missingSeasons: [],
        instance: s.instance,
      })
    }
    if (s.missing_seasons && s.missing_seasons.length > 0) {
      out.push({
        key: `season-${i}`,
        title: cleanTitle(s.title, s.year),
        year: s.year ?? null,
        category: 'season',
        tmdbType: 'show',
        mediaType: 'tv',
        tmdb_id: s.tmdb_id ?? null,
        tvdb_id: s.tvdb_id ?? null,
        imdb_id: s.imdb_id ?? null,
        poster_url: s.poster_url ?? null,
        missingSeasons: [...s.missing_seasons].sort((a, b) => a - b),
        instance: s.instance,
      })
    }
  })

  ;(stats.unmatched.collections ?? []).forEach((c, i) => {
    out.push({
      key: `collection-${i}`,
      title: cleanTitle(c.title, c.year),
      year: c.year ?? null,
      category: 'collection',
      tmdbType: 'collection',
      mediaType: 'collection',
      tmdb_id: c.tmdb_id ?? null,
      tvdb_id: c.tvdb_id ?? null,
      imdb_id: c.imdb_id ?? null,
      poster_url: c.poster_url ?? null,
      missingSeasons: [],
      instance: c.instance,
    })
  })

  return out
}

function SeasonBadges({ seasons }: { seasons: number[] }) {
  if (seasons.length === 0) return null
  return (
    <div className="unmatched-maker-seasons">
      <span className="unmatched-maker-seasons-label">Missing seasons:</span>
      {seasons.map((s) => (
        <span key={s} className="badge badge-orange">{s === 0 ? 'Specials' : `S${s}`}</span>
      ))}
    </div>
  )
}

type CardProps = {
  item: FlatItem
  psdConfig: PsdConfig
  posterAvailability?: PosterAvailability
  posterAvailabilityChecked: boolean
}

// One card per unmatched item. When the row already carries a TMDB id (the common case,
// from Plex/*arr) the full poster-maker card renders immediately. Otherwise the maker
// resolves it against TMDB first, then the same card takes over.
function UnmatchedAssetCard({ item, psdConfig, posterAvailability, posterAvailabilityChecked }: CardProps) {
  const { showToast } = useToast()
  const [resolved, setResolved] = useState<TmdbSearchResult | null>(
    item.tmdb_id ? buildResult(item, item.tmdb_id, item.poster_url, item.imdb_id, item.tvdb_id) : null,
  )
  const [candidates, setCandidates] = useState<TmdbCandidate[] | null>(null)
  const [loading, setLoading] = useState(false)

  const handleResolve = async () => {
    setLoading(true)
    try {
      const { candidates: found } = await searchUnmatchedTmdb({ title: item.title, year: item.year, type: item.tmdbType })
      setCandidates(found)
      if (found.length === 0) showToast('No TMDB matches found for this title', 'info')
    } catch {
      setCandidates([])
      showToast('TMDB search failed', 'error')
    } finally {
      setLoading(false)
    }
  }

  const pickCandidate = (c: TmdbCandidate) => {
    setResolved(buildResult(item, c.tmdb_id, c.poster_url, c.imdb_id, c.tvdb_id))
  }

  if (resolved) {
    return (
      <div className="unmatched-maker-item">
        <SeasonBadges seasons={item.missingSeasons} />
        <TmdbItemCard
          item={resolved}
          psdConfig={psdConfig}
          posterAvailability={posterAvailability}
          posterAvailabilityChecked={posterAvailabilityChecked}
        />
      </div>
    )
  }

  return (
    <div className="unmatched-maker-item">
      <SeasonBadges seasons={item.missingSeasons} />
      <div className="unmatched-maker-resolve">
        <div className="unmatched-maker-resolve-poster">
          {item.poster_url
            ? <img src={item.poster_url} alt={item.title} loading="lazy" />
            : item.mediaType === 'movie' ? <MovieIcon size={28} /> : item.mediaType === 'tv' ? <Tv size={28} /> : <FolderOpen size={28} />}
        </div>
        <div className="unmatched-maker-resolve-info">
          <div className="unmatched-maker-resolve-title">
            <span className="tmdb-result-title">{item.title}</span>
            {item.year && <span className="tmdb-result-year">{item.year}</span>}
            <span className="badge badge-grey">{CATEGORY_LABEL[item.category]}</span>
          </div>
          <p className="muted unmatched-maker-resolve-note">No TMDB id on this item — find it to browse images, or export a blank PSD below.</p>
          {candidates === null ? (
            <button type="button" className="btn-toolbar" onClick={() => void handleResolve()} disabled={loading}>
              {loading ? <Loader2 size={14} className="spin-icon" /> : <Search size={14} />}
              {loading ? 'Searching…' : 'Find on TMDB'}
            </button>
          ) : candidates.length === 0 ? (
            <p className="muted">No TMDB matches found.</p>
          ) : (
            <div className="unmatched-maker-candidates">
              {candidates.map((c) => (
                <button key={`${c.media_type}-${c.tmdb_id}`} type="button" className="unmatched-maker-candidate" onClick={() => pickCandidate(c)}>
                  {c.poster_url ? <img src={c.poster_url} alt="" loading="lazy" /> : <span className="unmatched-maker-candidate-placeholder"><Search size={14} /></span>}
                  <span className="unmatched-maker-candidate-meta">
                    <span className="unmatched-maker-candidate-title">{c.title}</span>
                    {c.year && <span className="tmdb-result-year">{c.year}</span>}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {/* No TMDB match yet: still expose the export buttons (blank/existing PSD) without resolving. */}
      <TmdbItemCard
        item={buildResult(item, 0, item.poster_url, item.imdb_id, item.tvdb_id)}
        psdConfig={psdConfig}
        posterAvailability={posterAvailability}
        posterAvailabilityChecked={posterAvailabilityChecked}
        hidePoster
        hideTitle
      />
    </div>
  )
}

type UnmatchedMakerTabProps = {
  unmatchedStats: UnmatchedStats | null
  psdConfig: PsdConfig
}

export default function UnmatchedMakerTab({ unmatchedStats, psdConfig }: UnmatchedMakerTabProps) {
  const navigate = useNavigate()
  // Posters | Artwork sub-tabs
  const [scope, setScope] = useState<'posters' | 'artwork'>(
    () => (localStorage.getItem('posterflow.makerTools.unmatchedScope') === 'artwork' ? 'artwork' : 'posters'),
  )
  const selectScope = (next: 'posters' | 'artwork') => {
    setScope(next)
    localStorage.setItem('posterflow.makerTools.unmatchedScope', next)
  }
  const [filter, setFilter] = useState<FilterTab>('all')
  const [query, setQuery] = useState('')
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  const [posterAvailabilityChecked, setPosterAvailabilityChecked] = useState(false)

  const items = useMemo(() => (unmatchedStats ? flatten(unmatchedStats) : []), [unmatchedStats])

  const counts = useMemo(() => {
    const c: Record<FilterTab, number> = { all: items.length, movie: 0, series: 0, season: 0, collection: 0 }
    items.forEach((it) => { c[it.category] += 1 })
    return c
  }, [items])

  // Check synced-drive poster availability once for every item that has a TMDB id,
  // so each card shows the same "Search Drives" availability cap as the search tab.
  const checkItems = useMemo(() => {
    const seen = new Set<number>()
    const out: Array<{ tmdb_id: number; title: string; year: string; media_type: FlatItem['mediaType'] }> = []
    for (const it of items) {
      if (it.tmdb_id && !seen.has(it.tmdb_id)) {
        seen.add(it.tmdb_id)
        out.push({ tmdb_id: it.tmdb_id, title: it.title, year: it.year ? String(it.year) : '', media_type: it.mediaType })
      }
    }
    return out
  }, [items])
  const checkKey = useMemo(() => checkItems.map((i) => i.tmdb_id).join(','), [checkItems])

  useEffect(() => {
    if (checkItems.length === 0) {
      setPosterAvailability({})
      setPosterAvailabilityChecked(false)
      return
    }
    let cancelled = false
    setPosterAvailabilityChecked(false)
    checkTmdbPosterAvailability(checkItems)
      .then((data) => { if (!cancelled) { setPosterAvailability(data); setPosterAvailabilityChecked(true) } })
      .catch(() => { /* non-critical */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkKey])

  const visibleItems = useMemo(() => {
    const lower = query.trim().toLowerCase()
    return items.filter((it) => {
      if (filter !== 'all' && it.category !== filter) return false
      if (lower && !it.title.toLowerCase().includes(lower)) return false
      return true
    })
  }, [items, filter, query])

  const goToDetection = () => {
    localStorage.setItem('posterflow.posterManager.activeTab', 'unmatched')
    navigate('/poster-manager')
  }

  const filterTabs: FilterTab[] = ['all', 'movie', 'series', 'season', 'collection']

  return (
    <div className="maker-tools-panel">
      <Toolbar
        title="Unmatched Assets"
        description="Your library items missing posters, from the last Unmatched Detection run. Work each one as a poster-maker card — browse images and export a PSD without leaving this page."
      >
        <button className="btn-toolbar" type="button" onClick={goToDetection} title="Run Unmatched Detection in Asset Manager">
          <RefreshCw size={16} /> Detect Unmatched
        </button>
      </Toolbar>

      {/* Posters | Artwork sub-tabs */}
      <div className="maker-subtabs" role="tablist" aria-label="Unmatched scope">
        <button type="button" role="tab" aria-selected={scope === 'posters'} className={scope === 'posters' ? 'active' : ''} onClick={() => selectScope('posters')}>
          Posters
        </button>
        <button type="button" role="tab" aria-selected={scope === 'artwork'} className={scope === 'artwork' ? 'active' : ''} onClick={() => selectScope('artwork')}>
          Artwork
        </button>
      </div>

      {scope === 'artwork' ? (
        <ArtworkUnmatchedMakerPanel />
      ) : !unmatchedStats || !unmatchedStats.last_run ? (
        <div className="unmatched-maker-empty">
          <AlertCircle size={44} />
          <h3>No unmatched detection results</h3>
          <p>Run Unmatched Detection in Asset Manager to populate this list, then come back to make posters.</p>
          <button className="btn-toolbar btn-primary" type="button" onClick={goToDetection}>
            <RefreshCw size={16} /> Go to Detection
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="unmatched-maker-empty">
          <AlertCircle size={44} />
          <h3>All assets matched</h3>
          <p>Your last detection run found no missing posters. Nice work.</p>
        </div>
      ) : (
        <>
          <div className="maker-result-tabs" role="tablist" aria-label="Unmatched category tabs">
            {filterTabs.filter((t) => t === 'all' || counts[t] > 0).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={filter === t}
                className={filter === t ? 'active' : ''}
                onClick={() => setFilter(t)}
              >
                {TAB_LABEL[t]}
                <span className="unmatched-maker-tab-count">{counts[t]}</span>
              </button>
            ))}
          </div>

          <div className="tmdb-search-bar unmatched-maker-search">
            <Search size={18} className="tmdb-search-icon" />
            <input
              type="text"
              className="tmdb-search-input"
              placeholder="Filter unmatched items by title…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button type="button" className="unmatched-maker-search-clear" onClick={() => setQuery('')} title="Clear filter">
                <X size={15} />
              </button>
            )}
          </div>

          <p className="muted unmatched-maker-count">
            {visibleItems.length === items.length ? `${items.length} items` : `${visibleItems.length} of ${items.length} items`}
          </p>

          {visibleItems.length === 0 ? (
            <p className="tmdb-empty">No items match your filter.</p>
          ) : (
            <div className="tmdb-results-grid">
              {visibleItems.map((it) => (
                <UnmatchedAssetCard
                  key={it.key}
                  item={it}
                  psdConfig={psdConfig}
                  posterAvailability={it.tmdb_id ? posterAvailability[it.tmdb_id] : undefined}
                  posterAvailabilityChecked={posterAvailabilityChecked}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
