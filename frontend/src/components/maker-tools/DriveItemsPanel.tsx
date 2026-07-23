import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, Search, X } from 'lucide-react'
import {
  getApiErrorMessage,
  getArtworkScopeItems,
  searchTmdb,
  type ArtworkScopeItem,
  type TmdbSearchFilter,
} from '../../api/client'
import { useToast } from '../Toast'
import { ArtworkUnmatchedCard, type MergedArtworkItem } from './ArtworkUnmatchedMakerPanel'

// tmdb_id -> poster url ('' = looked up, none found). Module-level so filter toggles and
// re-mounts don't refetch.
const posterCache = new Map<number, string>()

// The scope only holds artwork files (no posters), so each card resolves its TMDB poster
// lazily when it scrolls near the viewport — bounded by what's actually viewed, not the
// full drive.
function LazyPosterCard({ item, syncTargetIndex, scopeLabel }: {
  item: MergedArtworkItem
  syncTargetIndex: number | null
  scopeLabel: string | null
}) {
  const holder = useRef<HTMLDivElement | null>(null)
  const [posterUrl, setPosterUrl] = useState<string | null>(
    item.tmdb_id != null ? posterCache.get(item.tmdb_id) ?? null : '',
  )

  useEffect(() => {
    if (posterUrl !== null || item.tmdb_id == null) return
    const el = holder.current
    if (!el) return
    const io = new IntersectionObserver((entries) => {
      if (!entries.some((e) => e.isIntersecting)) return
      io.disconnect()
      const filter: TmdbSearchFilter = item.mediaType === 'tv' ? 'tv' : item.mediaType === 'collection' ? 'collection' : 'movie'
      searchTmdb(item.title, filter, { tmdb_id: item.tmdb_id })
        .then((res) => {
          const url = res[0]?.poster_url || ''
          posterCache.set(item.tmdb_id!, url)
          setPosterUrl(url)
        })
        .catch(() => setPosterUrl(''))
    }, { rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div ref={holder}>
      {/* The card seeds internal state from the item on mount, so remount once when the
          poster resolves (fetch fires ~400px before visibility, so this lands pre-interaction). */}
      <ArtworkUnmatchedCard
        key={posterUrl ? 'with-poster' : 'no-poster'}
        item={posterUrl ? { ...item, poster_url: posterUrl } : item}
        syncTargetIndex={syncTargetIndex}
        scopeLabel={scopeLabel}
      />
    </div>
  )
}

type Props = {
  syncTargetIndex: number | null
  scopeLabel: string | null
}

// Map a scope item onto the shared unmatched-card shape (poster_url unknown — the scope holds
// artwork files, not posters; the card falls back to a type icon).
function toMergedItem(it: ArtworkScopeItem, index: number): MergedArtworkItem {
  return {
    key: `scope-${index}`,
    title: it.title,
    year: it.year,
    category: it.media_type === 'tv' ? 'series' : it.media_type === 'collection' ? 'collection' : 'movie',
    tmdbType: it.media_type === 'tv' ? 'show' : it.media_type === 'collection' ? 'collection' : 'movie',
    mediaType: it.media_type,
    tmdb_id: it.tmdb_id,
    tvdb_id: it.tvdb_id,
    imdb_id: it.imdb_id,
    poster_url: null,
    missing: it.missing,
  }
}

const PAGE_SIZE = 50   // cards rendered at a time; more stream in as the list scrolls

export default function DriveItemsPanel({ syncTargetIndex, scopeLabel }: Props) {
  const { showToast } = useToast()
  const [items, setItems] = useState<ArtworkScopeItem[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [missingOnly, setMissingOnly] = useState(true)
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const sentinelRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (syncTargetIndex == null) {
      setItems(null)
      return
    }
    let cancelled = false
    setLoading(true)
    getArtworkScopeItems(syncTargetIndex)
      .then((res) => { if (!cancelled) setItems(res.items) })
      .catch((e) => { if (!cancelled) showToast(getApiErrorMessage(e, 'Failed to scan the drive'), 'error') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [syncTargetIndex, showToast])

  const merged = useMemo(() => (items ?? []).map(toMergedItem), [items])
  const gapCount = useMemo(() => merged.filter((it) => it.missing.length > 0).length, [merged])

  const visibleItems = useMemo(() => {
    const lower = query.trim().toLowerCase()
    return merged.filter((it) => {
      if (missingOnly && it.missing.length === 0) return false
      if (lower && !it.title.toLowerCase().includes(lower)) return false
      return true
    })
  }, [merged, query, missingOnly])

  // Render in pages so a large drive doesn't mount thousands of cards at once; the sentinel
  // below the list streams in the next page as it approaches the viewport.
  useEffect(() => {
    setVisibleCount(PAGE_SIZE)
  }, [query, missingOnly, syncTargetIndex, items])

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
  }, [visibleItems.length, visibleCount])

  if (syncTargetIndex == null) {
    return (
      <div className="unmatched-maker-empty">
        <AlertCircle size={44} />
        <h3>No artwork scope selected</h3>
        <p>Pick an artwork scope in the toolbar to browse its items.</p>
      </div>
    )
  }

  if (loading || items === null) {
    return <p className="muted" style={{ padding: '1rem 0' }}>Scanning {scopeLabel || 'the drive'}…</p>
  }

  if (items.length === 0) {
    return (
      <div className="unmatched-maker-empty">
        <AlertCircle size={44} />
        <h3>Drive is empty</h3>
        <p>No artwork found in {scopeLabel || 'this scope'} yet — add some from the Find &amp; Add or Batch Pull tabs.</p>
      </div>
    )
  }

  return (
    <>
      <div className="tmdb-search-bar unmatched-maker-search">
        <Search size={18} className="tmdb-search-icon" />
        <input
          type="text"
          className="tmdb-search-input"
          placeholder="Filter drive items by title…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {query && (
          <button type="button" className="unmatched-maker-search-clear" onClick={() => setQuery('')} title="Clear filter">
            <X size={15} />
          </button>
        )}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap', margin: '0.4rem 0' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer' }}>
          <input type="checkbox" checked={missingOnly} onChange={(e) => setMissingOnly(e.target.checked)} />
          Only items missing artwork
        </label>
        <span className="muted" style={{ fontSize: '0.8rem' }}>
          {merged.length.toLocaleString()} items in the drive · {gapCount.toLocaleString()} with gaps
          {visibleItems.length !== (missingOnly ? gapCount : merged.length) ? ` · showing ${visibleItems.length}` : ''}
        </span>
      </div>

      {visibleItems.length === 0 ? (
        <p className="tmdb-empty">{missingOnly && !query ? 'Nothing is missing artwork — the drive is complete.' : 'No items match your filter.'}</p>
      ) : (
        <div style={{ marginTop: 8 }}>
          {shownItems.map((it) => (
            <LazyPosterCard
              key={it.key}
              item={it}
              syncTargetIndex={syncTargetIndex}
              scopeLabel={scopeLabel}
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
  )
}
