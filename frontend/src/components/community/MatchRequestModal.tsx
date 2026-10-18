import { useState, useCallback, useEffect, useRef } from 'react'
import { AlertCircle, Check, ExternalLink, Link2, Loader2, Search, User, X } from 'lucide-react'
import { type TmdbCandidate, searchUnmatchedTmdb, tmdbCandidateKey, tmdbCandidateLink, isTmdbKeyMissingError } from '../../api/posterManager'
import type { CommunityRequest, RequestMatch } from '../../api/community'

type SearchType = RequestMatch['media_type']

const SEARCH_TYPE_OPTIONS: { value: SearchType; label: string }[] = [
  { value: 'movie', label: 'Movie' },
  { value: 'show', label: 'TV Show' },
  { value: 'collection', label: 'Collection' },
  { value: 'person', label: 'Person' },
]

// A season request belongs to a series, so it searches shows; the row keeps its season type.
export function searchTypeFor(mediaType: CommunityRequest['media_type']): SearchType {
  return mediaType === 'season' ? 'show' : mediaType
}

export function matchFromCandidate(c: TmdbCandidate): RequestMatch {
  return {
    tmdb_id: c.tmdb_id ?? null,
    tvdb_id: c.tvdb_id ?? null,
    imdb_id: c.imdb_id ?? null,
    title: c.title,
    year: c.year ?? null,
    poster_path: c.poster_url || null,
    media_type: c.media_type,
  }
}

type Props = {
  request: CommunityRequest
  submitting: boolean
  onCancel: () => void
  onConfirm: (match: RequestMatch) => void
}

/**
 * Server-owner tool for a request that came in with no TMDB/TVDB id: search
 * TMDB (TVDB fallback for shows), pick the entry, and the edge function fills
 * in the ids, poster, canonical title and year, then refreshes the thread.
 */
export default function MatchRequestModal({ request, submitting, onCancel, onConfirm }: Props) {
  const isSeason = request.media_type === 'season'
  const [query, setQuery] = useState(request.title)
  const [searchType, setSearchType] = useState<SearchType>(() => searchTypeFor(request.media_type))
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<TmdbCandidate[]>([])
  const [selected, setSelected] = useState<TmdbCandidate | null>(null)

  const runSearch = useCallback(async (q: string, type: SearchType) => {
    const title = q.trim()
    if (!title) return
    setSearching(true)
    setSearchError(null)
    setSelected(null)
    try {
      const result = await searchUnmatchedTmdb({ title, year: null, type, tvdb_fallback: true })
      setCandidates(result.candidates)
      if (result.candidates.length === 1) setSelected(result.candidates[0])
    } catch (err) {
      setCandidates([])
      setSearchError(
        isTmdbKeyMissingError(err)
          ? 'No TMDB API key configured. Add it in Settings → General → API Keys.'
          : 'Search failed. Try again.',
      )
    } finally {
      setSearching(false)
      setHasSearched(true)
    }
  }, [])

  // Search the request's own title as soon as the modal opens; usually the owner only has to pick.
  const autoSearched = useRef(false)
  useEffect(() => {
    if (autoSearched.current) return
    autoSearched.current = true
    void runSearch(request.title, searchTypeFor(request.media_type))
  }, [request.title, request.media_type, runSearch])

  const typeChanges = selected != null && !isSeason && selected.media_type !== request.media_type
  const canConfirm = !!selected && !submitting && !searching
  const resultType = selected ? (isSeason ? 'season' : selected.media_type) : null

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !submitting) onCancel() }}>
      <div className="modal-content schedule-modal community-req-modal">
        <div className="modal-header">
          <h2>Match Request</h2>
          <button className="modal-close" onClick={onCancel} disabled={submitting}>×</button>
        </div>

        <div className="modal-body">
          <div className="creq-item-label">
            <strong className="creq-item-title">{request.title}</strong>
            {request.year && <span className="creq-item-year"> ({request.year})</span>}
            <span className={`tmdb-type-badge tmdb-type-badge--${request.media_type}`}>{request.media_type}</span>
            {request.requested_by && <> · requested by <strong>{request.requested_by}</strong></>}
          </div>
          <div className="creq-field-hint">
            This request has no TMDB or TVDB id. Pick the matching entry to fill in its ids, poster and title.
            The Discord thread is updated too.
          </div>
          {isSeason && <div className="creq-field-hint">Season request: pick the series it belongs to.</div>}

          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>Search TMDB</div>
          <div className="new-creq-search-row">
            <div className="new-creq-search-bar">
              <Search size={15} className="search-bar-icon" />
              <input
                type="text"
                className="new-creq-search-input"
                placeholder="Search TMDB…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void runSearch(query, searchType) }}
                maxLength={200}
              />
              {query && (
                <button type="button" className="search-clear-btn" onClick={() => setQuery('')} title="Clear">
                  <X size={14} />
                </button>
              )}
            </div>
            <select
              className="new-creq-type-select"
              aria-label="Search type"
              value={searchType}
              disabled={isSeason}
              onChange={(e) => setSearchType(e.target.value as SearchType)}
            >
              {SEARCH_TYPE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
            <button
              type="button"
              className="new-creq-search-btn"
              onClick={() => void runSearch(query, searchType)}
              disabled={searching || !query.trim()}
            >
              {searching ? <Loader2 size={14} className="spin-icon" /> : <Search size={14} />}
              Search
            </button>
          </div>

          {searchError && (
            <div className="tmdb-candidates-warning" role="alert" style={{ marginTop: '0.5rem' }}>
              <AlertCircle size={14} />
              <span>{searchError}</span>
            </div>
          )}

          {hasSearched && !searchError && (
            candidates.length === 0 ? (
              <div className="creq-no-results" style={{ marginTop: '0.75rem' }}>
                No results. Try another spelling or type.
              </div>
            ) : (
              <>
                <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>Pick the correct match:</div>
                <div className={`creq-candidates${candidates.length === 1 ? ' creq-candidates--single' : ''}`}>
                  {candidates.map((c) => {
                    const isSelected = selected != null && tmdbCandidateKey(selected) === tmdbCandidateKey(c)
                    const isPerson = c.media_type === 'person'
                    const link = tmdbCandidateLink(c)
                    return (
                      <button
                        key={tmdbCandidateKey(c)}
                        type="button"
                        className={`creq-candidate${isSelected ? ' selected' : ''}`}
                        onClick={() => setSelected(isSelected ? null : c)}
                      >
                        {c.poster_url ? (
                          <img src={c.poster_url} alt="" className="creq-candidate-poster" loading="lazy" />
                        ) : (
                          <div className={`creq-candidate-poster creq-candidate-poster--empty${isPerson ? ' creq-candidate-poster--person' : ''}`}>
                            {isPerson && <User size={22} style={{ opacity: 0.4 }} />}
                          </div>
                        )}
                        <div className="creq-candidate-info">
                          <div className="creq-candidate-title">
                            {c.title}
                            {c.year && <span className="creq-candidate-year"> ({c.year})</span>}
                          </div>
                          {isPerson && <span className="tmdb-type-badge tmdb-type-badge--person">Person</span>}
                          <a
                            href={link.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="creq-tmdb-link"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <ExternalLink size={11} />
                            {link.label}
                          </a>
                        </div>
                        {isSelected && <Check size={16} className="creq-selected-check" />}
                      </button>
                    )
                  })}
                </div>
              </>
            )
          )}

          {selected && resultType && (
            <div className="creq-item-label" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
              Will become:
              <strong className="creq-item-title"> {selected.title}</strong>
              {selected.year && <span className="creq-item-year"> ({selected.year})</span>}
              <span className={`tmdb-type-badge tmdb-type-badge--${resultType}`}>{resultType}</span>
              {typeChanges && (
                <div className="creq-field-hint" style={{ color: '#ff9800' }}>
                  Type changes from {request.media_type} to {selected.media_type}.
                </div>
              )}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button
            className="btn-primary"
            onClick={() => { if (selected) onConfirm(matchFromCandidate(selected)) }}
            disabled={!canConfirm}
            title={!selected ? 'Pick a match first' : undefined}
          >
            {submitting ? <Loader2 size={13} className="spin-icon" /> : <Link2 size={13} />}
            {' '}Match
          </button>
        </div>
      </div>
    </div>
  )
}
