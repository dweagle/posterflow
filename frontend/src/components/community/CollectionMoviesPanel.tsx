import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { ChevronDown, ChevronRight, Film, Loader2 } from 'lucide-react'
import {
  checkTmdbPosterAvailability,
  getCollectionMovies,
  posterCheckKey,
  type PosterAvailability,
  type TmdbSearchResult,
} from '../../api/makerTools'
import TmdbItemCard, { type PsdConfig } from '../maker-tools/TmdbItemCard'

type Props = {
  tmdbId: number
  psdConfig?: PsdConfig
  posterStyle?: 'CL2K' | 'MM2K'
  /** Bump to fold the panel (e.g. when the request is completed). */
  collapseSignal?: number
  /** Element id the toggle button portals into (the request card's footer slot); inline when unset. */
  toggleSlotId?: string
}

type LoadState = 'idle' | 'loading' | 'loaded' | 'error'

// Foldable list of a collection's movies as maker cards, so a "+ all movies" request
// doesn't send the maker searching for each one. Loads from TMDB on first open.
export default function CollectionMoviesPanel({ tmdbId, psdConfig, posterStyle, collapseSignal, toggleSlotId }: Props) {
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<LoadState>('idle')
  const [movies, setMovies] = useState<TmdbSearchResult[]>([])
  const [error, setError] = useState<string | null>(null)
  const [availability, setAvailability] = useState<Record<string, PosterAvailability>>({})
  const [availabilityChecked, setAvailabilityChecked] = useState(false)
  const [toggleSlotEl, setToggleSlotEl] = useState<HTMLElement | null>(null)

  useEffect(() => {
    setToggleSlotEl(toggleSlotId ? document.getElementById(toggleSlotId) : null)
  }, [toggleSlotId])

  useEffect(() => {
    if (collapseSignal) setOpen(false)
  }, [collapseSignal])

  useEffect(() => {
    if (!open || state !== 'idle') return
    setState('loading')
    setError(null)
    getCollectionMovies(tmdbId)
      .then((list) => {
        setMovies(list)
        setState('loaded')
        const items = list
          .filter((m) => posterCheckKey(m) != null)
          .map((m) => ({ tmdb_id: m.tmdb_id, tvdb_id: m.tvdb_id, title: m.title, year: m.year, media_type: 'movie' as const }))
        if (items.length === 0) return
        checkTmdbPosterAvailability(items)
          .then((result) => { setAvailability(result); setAvailabilityChecked(true) })
          .catch(() => {})
      })
      .catch((err: unknown) => {
        const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        setError(detail ?? 'Could not load this collection from TMDB')
        setState('error')
      })
  }, [open, state, tmdbId])

  const toggle = (
    <button
      type="button"
      className={`request-collection-toggle${open ? ' open' : ''}`}
      onClick={() => setOpen((o) => !o)}
      aria-expanded={open}
    >
      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      <Film size={13} />
      <span>Movies in this collection{state === 'loaded' ? ` (${movies.length})` : ''}</span>
    </button>
  )

  return (
    <>
      {toggleSlotId ? (toggleSlotEl && createPortal(toggle, toggleSlotEl)) : toggle}

      {open && (
        <div className="request-collection-panel">
          {state === 'error' ? (
            <div className="request-collection-status request-collection-status--error">
              <span>{error}</span>
              <button type="button" className="btn-secondary" onClick={() => setState('idle')}>Retry</button>
            </div>
          ) : state !== 'loaded' ? (
            <div className="request-collection-status">
              <Loader2 size={14} className="spin-icon" />
              <span>Loading movies from TMDB…</span>
            </div>
          ) : movies.length === 0 ? (
            <div className="request-collection-status">TMDB lists no movies in this collection.</div>
          ) : (
            <div className="tmdb-results-grid request-collection-grid">
              {movies.map((m) => (
                <TmdbItemCard
                  key={m.tmdb_id}
                  item={m}
                  psdConfig={psdConfig}
                  posterStyle={posterStyle}
                  posterAvailability={availability[posterCheckKey(m) ?? '']}
                  posterAvailabilityChecked={availabilityChecked}
                  hideOverview
                />
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}
