import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, Clapperboard as MovieIcon, FolderOpen, Loader2, RefreshCw, Search, Tv, X } from 'lucide-react'
import {
  getApiErrorMessage,
  getArtworkUnmatchedStats,
  searchUnmatchedTmdb,
  type ArtworkType,
  type ArtworkUnmatchedStats,
  type TmdbCandidate,
  type TmdbSearchResult,
  type UnmatchedStats,
} from '../../api/client'
import { useToast } from '../Toast'
import ArtworkFinderCard from './ArtworkFinderCard'
import { type AssetScope } from './useArtworkScopes'

type Category = 'movie' | 'series' | 'collection'

interface FlatArtworkItem {
  key: string
  title: string
  year: number | null
  category: Category
  tmdbType: 'movie' | 'show' | 'collection'
  mediaType: 'movie' | 'tv' | 'collection'
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  poster_url: string | null
}

const TYPE_LABEL: Record<ArtworkType, string> = {
  logo: 'Logo',
  background: 'Backdrop',
  squareart: 'Square Art',
}
const TYPE_ORDER: ArtworkType[] = ['logo', 'background', 'squareart']

export interface MergedArtworkItem extends FlatArtworkItem {
  missing: ArtworkType[]
}

// One row per ITEM (not per type): merge the three per-type unmatched lists, collecting which
// artwork types each item is missing. Keyed by namespaced ids so a movie/show sharing a tmdb
// number never fuse; title+year as the id-less fallback.
function mergeByItem(byType: Record<ArtworkType, FlatArtworkItem[]>): MergedArtworkItem[] {
  const map = new Map<string, MergedArtworkItem>()
  for (const t of TYPE_ORDER) {
    for (const it of byType[t]) {
      const key = it.tmdb_id ? `${it.mediaType}|tmdb-${it.tmdb_id}`
        : it.tvdb_id ? `tvdb-${it.tvdb_id}`
          : it.imdb_id ? `imdb-${it.imdb_id}`
            : `t|${it.title.toLowerCase()}|${it.year ?? ''}`
      const cur = map.get(key)
      if (cur) {
        if (!cur.missing.includes(t)) cur.missing.push(t)
      } else {
        map.set(key, { ...it, missing: [t] })
      }
    }
  }
  return [...map.values()].sort((a, b) => a.title.localeCompare(b.title))
}

const cleanTitle = (title: string, year: number | null): string =>
  year ? title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : title

const tmdbHomepage = (mediaType: FlatArtworkItem['mediaType'], id: number): string => {
  const seg = mediaType === 'movie' ? 'movie' : mediaType === 'collection' ? 'collection' : 'tv'
  return `https://www.themoviedb.org/${seg}/${id}`
}

const buildResult = (item: FlatArtworkItem, tmdbId: number, posterUrl: string | null, imdbId: string | null, tvdbId: number | null): TmdbSearchResult => ({
  tmdb_id: tmdbId,
  media_type: item.mediaType,
  title: item.title,
  year: item.year ? String(item.year) : '',
  overview: '',
  poster_url: posterUrl || '',
  homepage: tmdbHomepage(item.mediaType, tmdbId),
  imdb_id: imdbId ?? null,
  tvdb_id: tvdbId ?? null,
})

// Artwork is per-title, so series carry no season rows (the backend zeroes those stats).
function flattenArtwork(stats: UnmatchedStats): FlatArtworkItem[] {
  const out: FlatArtworkItem[] = []
  ;(stats.unmatched.movies ?? []).forEach((m, i) => {
    out.push({
      key: `movie-${i}`, title: cleanTitle(m.title, m.year), year: m.year ?? null,
      category: 'movie', tmdbType: 'movie', mediaType: 'movie',
      tmdb_id: m.tmdb_id ?? null, tvdb_id: m.tvdb_id ?? null, imdb_id: m.imdb_id ?? null,
      poster_url: m.poster_url ?? null,
    })
  })
  ;(stats.unmatched.series ?? []).forEach((s, i) => {
    out.push({
      key: `series-${i}`, title: cleanTitle(s.title, s.year), year: s.year ?? null,
      category: 'series', tmdbType: 'show', mediaType: 'tv',
      tmdb_id: s.tmdb_id ?? null, tvdb_id: s.tvdb_id ?? null, imdb_id: s.imdb_id ?? null,
      poster_url: s.poster_url ?? null,
    })
  })
  ;(stats.unmatched.collections ?? []).forEach((c, i) => {
    out.push({
      key: `collection-${i}`, title: cleanTitle(c.title, c.year), year: c.year ?? null,
      category: 'collection', tmdbType: 'collection', mediaType: 'collection',
      tmdb_id: c.tmdb_id ?? null, tvdb_id: c.tvdb_id ?? null, imdb_id: c.imdb_id ?? null,
      poster_url: c.poster_url ?? null,
    })
  })
  return out
}

// One unmatched artwork item: the artwork-finder card when a TMDB id is known, else the same
// resolve-first flow the poster unmatched cards use. Also reused by the My Drive browser.
export function ArtworkUnmatchedCard({ item, syncTargetIndex, scopeLabel }: {
  item: MergedArtworkItem
  syncTargetIndex: number | null
  scopeLabel: string | null
}) {
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

  if (resolved) {
    return (
      <ArtworkFinderCard
        item={resolved}
        syncTargetIndex={syncTargetIndex}
        scopeLabel={scopeLabel}
        missing={TYPE_ORDER.filter((t) => item.missing.includes(t))}
      />
    )
  }

  return (
    // marginBottom matches ArtworkFinderCard's spacing — these lists have no grid gap, so
    // without it a resolve card butts against the card below.
    <div className="unmatched-maker-item" style={{ marginBottom: 16 }}>
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
            <span className="badge badge-grey">{item.category === 'movie' ? 'Movie' : item.category === 'series' ? 'Series' : 'Collection'}</span>
            {item.missing.length > 0 && (
              <>
                <span className="artwork-missing-label">Missing:</span>
                {TYPE_ORDER.filter((t) => item.missing.includes(t)).map((t) => (
                  <span key={t} className="badge artwork-missing-badge">{TYPE_LABEL[t]}</span>
                ))}
              </>
            )}
          </div>
          <p className="muted unmatched-maker-resolve-note">No TMDB id on this item — find it to browse artwork and add it to your scope.</p>
          {candidates === null ? (
            // alignSelf keeps it content-sized — the flex column would otherwise stretch it
            // across the whole card (and .btn-toolbar's 185px min-width needs zeroing).
            <button type="button" className="btn-toolbar" style={{ alignSelf: 'flex-start', minWidth: 0, fontSize: '0.8rem', padding: '0.35rem 0.9rem' }} onClick={() => void handleResolve()} disabled={loading}>
              {loading ? <Loader2 size={14} className="spin-icon" /> : <Search size={14} />}
              {loading ? 'Searching…' : 'Find on TMDB'}
            </button>
          ) : candidates.length === 0 ? (
            <p className="muted">No TMDB matches found.</p>
          ) : (
            <div className="unmatched-maker-candidates">
              {candidates.map((c) => (
                <button key={`${c.media_type}-${c.tmdb_id}`} type="button" className="unmatched-maker-candidate" onClick={() => setResolved(buildResult(item, c.tmdb_id, c.poster_url, c.imdb_id, c.tvdb_id))}>
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
    </div>
  )
}

export default function ArtworkUnmatchedMakerPanel({ selected }: { selected: AssetScope | null }) {
  const navigate = useNavigate()
  const { showToast } = useToast()

  const PAGE_SIZE = 50   // cards rendered at a time; more stream in as the list scrolls
  const [stats, setStats] = useState<ArtworkUnmatchedStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    getArtworkUnmatchedStats()
      .then(setStats)
      .catch((e) => showToast(getApiErrorMessage(e, 'Failed to load unmatched artwork'), 'error'))
      .finally(() => setLoading(false))
  }, [showToast])

  // One row per item, with badges for every artwork type it's missing.
  const items = useMemo(() => (stats ? mergeByItem({
    logo: flattenArtwork(stats.logo),
    background: flattenArtwork(stats.background),
    squareart: flattenArtwork(stats.squareart),
  }) : []), [stats])

  const visibleItems = useMemo(() => {
    const lower = query.trim().toLowerCase()
    return lower ? items.filter((it) => it.title.toLowerCase().includes(lower)) : items
  }, [items, query])

  // Paged rendering so a large unmatched list doesn't mount every card at once (same
  // pattern as the My Drive browser).
  useEffect(() => {
    setVisibleCount(PAGE_SIZE)
  }, [query, items, PAGE_SIZE])

  const shownItems = useMemo(() => visibleItems.slice(0, visibleCount), [visibleItems, visibleCount])

  useEffect(() => {
    const el = sentinelRef.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setVisibleCount((c) => Math.min(c + PAGE_SIZE, visibleItems.length))
      }
    }, { rootMargin: '600px' })
    io.observe(el)
    return () => io.disconnect()
  }, [visibleItems.length, visibleCount, PAGE_SIZE])

  const goToDetection = () => {
    localStorage.setItem('posterflow.posterManager.activeTab', 'unmatched')
    navigate('/poster-manager')
  }

  if (loading) {
    return <p className="muted" style={{ padding: '1rem 0' }}>Loading unmatched artwork…</p>
  }

  if (!stats || !stats.last_run) {
    return (
      <div className="unmatched-maker-empty">
        <AlertCircle size={44} />
        <h3>No unmatched artwork results</h3>
        <p>Run Unmatched Detection (with the Artwork scope) in Asset Manager to populate this list.</p>
        <button className="btn-toolbar btn-primary" type="button" onClick={goToDetection}>
          <RefreshCw size={16} /> Go to Detection
        </button>
      </div>
    )
  }

  return (
    <>
      {items.length === 0 ? (
        <div className="unmatched-maker-empty">
          <AlertCircle size={44} />
          <h3>All matched</h3>
          <p>Your last detection run found no items missing artwork.</p>
        </div>
      ) : (
        <>
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
            <div style={{ marginTop: 8 }}>
              {shownItems.map((it) => (
                <ArtworkUnmatchedCard
                  key={it.key}
                  item={it}
                  syncTargetIndex={selected ? selected.index : null}
                  scopeLabel={selected ? selected.label : null}
                />
              ))}
              {visibleCount < visibleItems.length && (
                <div ref={sentinelRef} style={{ padding: '0.75rem 0', textAlign: 'center' }}>
                  <span className="muted" style={{ fontSize: '0.8rem' }}>
                    Showing {shownItems.length.toLocaleString()} of {visibleItems.length.toLocaleString()} — scroll for more
                  </span>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </>
  )
}
