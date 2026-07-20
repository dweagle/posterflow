import { useState, useCallback, useRef, useEffect } from 'react'
import { AlertCircle, Check, ExternalLink, Loader2, Search, Star, LogOut, User, X } from 'lucide-react'
import { type TmdbCandidate, searchUnmatchedTmdb } from '../../api/client'
import { submitCommunityRequest } from '../../api/client'
import { useToast } from '../Toast'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'
import { POSTER_STYLES, EXTRA_TAGS, isValidDiscordUsername, getStoredPosterStyle, setStoredPosterStyle, useAlreadyMadeWarning } from '../community/posterStyles'

type TmdbSearchType = 'movie' | 'show' | 'collection' | 'person'

const SEARCH_TYPE_OPTIONS: { value: TmdbSearchType; label: string }[] = [
  { value: 'movie', label: 'Movie' },
  { value: 'show', label: 'TV Show' },
  { value: 'collection', label: 'Collection' },
  { value: 'person', label: 'Person' },
]

function getTmdbLink(candidate: TmdbCandidate): string {
  if (candidate.media_type === 'movie') return `https://www.themoviedb.org/movie/${candidate.tmdb_id}`
  if (candidate.media_type === 'collection') return `https://www.themoviedb.org/collection/${candidate.tmdb_id}`
  if (candidate.media_type === 'person') return `https://www.themoviedb.org/person/${candidate.tmdb_id}`
  return `https://www.themoviedb.org/tv/${candidate.tmdb_id}`
}

type NewCommunityRequestModalProps = {
  tmdbApiKeyConfigured: boolean
  onClose: () => void
}

export default function NewCommunityRequestModal({
  tmdbApiKeyConfigured,
  onClose,
}: NewCommunityRequestModalProps) {
  const { showToast } = useToast()
  const { isConnected, username, token, connecting, connectError, login, logout } = useDiscordAuth()

  // Search state
  const [searchQuery, setSearchQuery] = useState('')
  const [searchType, setSearchType] = useState<TmdbSearchType>('movie')
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [candidates, setCandidates] = useState<TmdbCandidate[]>([])

  // Selection / form state
  const [selected, setSelected] = useState<TmdbCandidate | null>(null)
  const [customTitle, setCustomTitle] = useState('')
  const [customYear, setCustomYear] = useState('')
  // Seed from the remembered CL2K/MM2K choice so it sticks between requests.
  const [posterStyle, setPosterStyle] = useState<string | null>(getStoredPosterStyle)
  const [extraTags, setExtraTags] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [pingDiscordId, setPingDiscordId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  // Set when a search returned matches but the user submitted without picking one
  // and without confirming it's custom — surfaces a warning + highlights the results.
  const [isCustomRequest, setIsCustomRequest] = useState(false)
  const [pickWarning, setPickWarning] = useState(false)

  const searchInputRef = useRef<HTMLInputElement>(null)
  const candidatesRef = useRef<HTMLDivElement>(null)

  const effectiveName = username ?? ''

  const handleSearch = useCallback(async () => {
    const query = searchQuery.trim()
    if (!query || searching) return
    setSearching(true)
    setHasSearched(false)
    setCandidates([])
    setSelected(null)
    // A fresh search invalidates any earlier "custom" confirmation / warning.
    setIsCustomRequest(false)
    setPickWarning(false)
    try {
      const result = await searchUnmatchedTmdb({ title: query, year: null, type: searchType })
      setCandidates(result.candidates)
      if (result.candidates.length === 1) setSelected(result.candidates[0])
    } catch {
      setCandidates([])
    } finally {
      setSearching(false)
      setHasSearched(true)
    }
  }, [searchQuery, searchType, searching])

  const handleSearchKeyDown = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSearch()
  }, [handleSearch])

  const toggleExtraTag = useCallback((tag: string) => {
    setExtraTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))
  }, [])

  // Derive effective title/year for submission
  const effectiveTitle = selected?.title ?? customTitle.trim()
  const effectiveYear = selected?.year ?? (customYear.trim() ? parseInt(customYear.trim(), 10) : null)

  // Non-blocking notice when this item was already fulfilled in the chosen style.
  const alreadyMade = useAlreadyMadeWarning(
    selected
      ? { tmdb_id: selected.tmdb_id, media_type: selected.media_type, title: selected.title }
      : effectiveTitle
        ? { media_type: searchType, title: effectiveTitle }
        : null,
    posterStyle,
  )

  // A search surfaced matches, but the user hasn't picked one or confirmed it's
  // custom — e.g. they typed a custom title while a real match sat in the results.
  const tmdbSelectionMissed =
    tmdbApiKeyConfigured && hasSearched && candidates.length > 0 && !selected && !isCustomRequest
  const showPickWarning = pickWarning && tmdbSelectionMissed

  const canSubmit =
    !submitting &&
    !submitted &&
    isConnected &&
    !!token &&
    !!effectiveName.trim() &&
    !!effectiveTitle &&
    !!posterStyle

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || !token) return
    // Block + warn if matches were found but none was picked (or marked custom).
    if (tmdbSelectionMissed) {
      setPickWarning(true)
      showToast('Pick the correct TMDB match below, or check “None of these fit — custom request.”', 'error')
      candidatesRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      return
    }
    setSubmitting(true)
    try {
      const trimmedPingId = pingDiscordId.trim()
      const validPingId = trimmedPingId && isValidDiscordUsername(trimmedPingId) ? trimmedPingId : null
      const styleTags = [posterStyle, ...extraTags].filter(Boolean) as string[]
      const result = await submitCommunityRequest({
        tmdb_id: selected?.tmdb_id ?? null,
        media_type: selected?.media_type ?? searchType,
        title: effectiveTitle,
        year: effectiveYear,
        poster_path: selected?.poster_url ?? null,
        imdb_id: selected?.imdb_id ?? null,
        tvdb_id: selected?.tvdb_id ?? null,
        notes: notes.trim() || null,
        style_tags: styleTags.length > 0 ? styleTags : undefined,
        requested_by: effectiveName.trim(),
        ping_discord_id: validPingId,
        discord_token: token,
      })
      setSubmitted(true)
      showToast(
        result.status === 'already_requested' ? 'Already requested!' : 'Request submitted!',
        result.status === 'already_requested' ? 'info' : 'success',
      )
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail ?? 'Failed to submit request', 'error')
    } finally {
      setSubmitting(false)
    }
  }, [
    canSubmit, tmdbSelectionMissed, selected, searchType, effectiveTitle, effectiveYear,
    notes, posterStyle, extraTags, pingDiscordId, effectiveName, token, showToast,
  ])

  // Auto-close shortly after a successful submit. Cleared on unmount so a pending
  // close can't fire after the modal is gone (avoids stray onClose calls).
  useEffect(() => {
    if (!submitted) return
    const id = setTimeout(onClose, 1200)
    return () => clearTimeout(id)
  }, [submitted, onClose])

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-content schedule-modal community-req-modal">
        <div className="modal-header">
          <h2>New Community Request</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="creq-rate-limit-notice">
          Limited to 5 new requests per day.
        </div>

        <div className="modal-body">
          {/* TMDB search bar */}
          <div className="creq-section-label">Search TMDB</div>

          {!tmdbApiKeyConfigured ? (
            <div className="tmdb-candidates-warning">
              <AlertCircle size={14} />
              <span>No TMDB API key configured. Add it in <strong>Settings → General → API Keys</strong>.</span>
            </div>
          ) : (
            <div className="new-creq-search-row">
              <div className="new-creq-search-bar">
                <Search size={15} className="search-bar-icon" />
                <input
                  ref={searchInputRef}
                  type="text"
                  className="new-creq-search-input"
                  placeholder="Search TMDB…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  maxLength={200}
                />
                {searchQuery && (
                  <button
                    type="button"
                    className="search-clear-btn"
                    onClick={() => { setSearchQuery(''); setCandidates([]); setHasSearched(false); setSelected(null) }}
                    title="Clear"
                  >
                    <X size={14} />
                  </button>
                )}
              </div>
              <select
                className="new-creq-type-select"
                value={searchType}
                onChange={(e) => {
                  setSearchType(e.target.value as TmdbSearchType)
                  setCandidates([])
                  setHasSearched(false)
                  setSelected(null)
                }}
              >
                {SEARCH_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <button
                type="button"
                className="new-creq-search-btn"
                onClick={handleSearch}
                disabled={searching || !searchQuery.trim()}
              >
                {searching ? <Loader2 size={14} className="spin-icon" /> : <Search size={14} />}
                Search
              </button>
            </div>
          )}

          {/* Search results / TMDB match picker */}
          {tmdbApiKeyConfigured && hasSearched && (
            <>
              <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
                Pick the correct TMDB match:
              </div>
              {showPickWarning && (
                <div className="creq-pick-warning" role="alert">
                  <AlertCircle size={14} />
                  <span>Pick the correct TMDB match below, or check <strong>“None of these fit.”</strong></span>
                </div>
              )}
              {candidates.length === 0 ? (
                <div className="creq-no-results">No TMDB results found.</div>
              ) : (
                <div className={`creq-candidates${showPickWarning ? ' creq-candidates--warn' : ''}`} ref={candidatesRef}>
                  {candidates.map((c, i) => {
                    const isSelected = selected?.tmdb_id === c.tmdb_id && selected?.media_type === c.media_type
                    const isPerson = c.media_type === 'person'
                    const link = getTmdbLink(c)
                    return (
                      <button
                        key={i}
                        type="button"
                        className={`creq-candidate${isSelected ? ' selected' : ''}`}
                        onClick={() => { setSelected(isSelected ? null : c); setPickWarning(false) }}
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
                          {isPerson && (
                            <span className="tmdb-type-badge tmdb-type-badge--person">Person</span>
                          )}
                          <a
                            href={link}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="creq-tmdb-link"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <ExternalLink size={11} />
                            TMDB
                          </a>
                        </div>
                        {isSelected && <Check size={16} className="creq-selected-check" />}
                      </button>
                    )
                  })}
                </div>
              )}
              {candidates.length > 0 && !selected && (
                <label className="creq-custom-request-label">
                  <input
                    type="checkbox"
                    checked={isCustomRequest}
                    onChange={(e) => setIsCustomRequest(e.target.checked)}
                  />
                  None of these fit — submit as a custom request
                </label>
              )}
            </>
          )}

          {/* Title/year inputs — shown when no TMDB result is selected */}
          {!selected && (
            <div className="new-creq-custom-fields">
              <div className="new-creq-custom-row">
                <div className="new-creq-custom-field">
                  <label className="creq-section-label" style={{ marginTop: 0 }}>
                    Title <span className="request-required">required</span>
                  </label>
                  <input
                    type="text"
                    className="request-notes-textarea"
                    style={{ resize: 'none', height: 'auto', padding: '0.5rem 0.75rem' }}
                    placeholder="Title of the movie, show, or collection"
                    value={customTitle}
                    onChange={(e) => setCustomTitle(e.target.value)}
                    maxLength={200}
                  />
                </div>
                <div className="new-creq-custom-year">
                  <label className="creq-section-label" style={{ marginTop: 0 }}>
                    Year <span className="request-optional">(optional)</span>
                  </label>
                  <input
                    type="text"
                    className="request-notes-textarea"
                    style={{ resize: 'none', height: 'auto', padding: '0.5rem 0.75rem' }}
                    placeholder="e.g. 2024"
                    value={customYear}
                    onChange={(e) => setCustomYear(e.target.value.replace(/\D/g, '').slice(0, 4))}
                    maxLength={4}
                  />
                </div>
              </div>
            </div>
          )}

          {/* Show selected item label */}
          {selected && (
            <div className="creq-item-label" style={{ marginTop: '0.75rem' }}>
              Requesting a poster for:
              <strong className="creq-item-title"> {selected.title}</strong>
              {selected.year && <span className="creq-item-year"> ({selected.year})</span>}
              <span className={`tmdb-type-badge tmdb-type-badge--${selected.media_type === 'show' ? 'show' : selected.media_type}`}>
                {selected.media_type}
              </span>
            </div>
          )}

          {/* Poster style — required, single choice */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Poster style <span className="request-required">required</span>
          </div>
          <div className="request-style-tags">
            {POSTER_STYLES.map((s) => (
              <button
                key={s.value}
                type="button"
                className={`request-style-tag${posterStyle === s.value ? ' selected' : ''}`}
                onClick={() => setPosterStyle((prev) => {
                  const next = prev === s.value ? null : s.value
                  if (next) setStoredPosterStyle(next)
                  return next
                })}
              >
                {s.label}
              </button>
            ))}
          </div>

          {alreadyMade && (
            <div className="tmdb-candidates-warning" role="alert" style={{ marginTop: '0.5rem' }}>
              <AlertCircle size={14} />
              <span>{alreadyMade}</span>
            </div>
          )}

          {/* Extra style preferences — optional */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Style preferences <span className="request-optional">(optional)</span>
          </div>
          <div className="request-style-tags">
            {EXTRA_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={`request-style-tag${extraTags.includes(tag) ? ' selected' : ''}`}
                onClick={() => toggleExtraTag(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          {/* Discord identity */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Discord account <span className="request-required">required</span>
          </div>
          {isConnected && username ? (
            <div className="creq-discord-connected">
              <span className="creq-discord-username">✓ {username}</span>
              <button type="button" className="creq-discord-logout" onClick={logout} title="Disconnect Discord">
                <LogOut size={12} />
                Disconnect
              </button>
            </div>
          ) : (
            <div className="creq-discord-connect-row">
              <button
                type="button"
                className="creq-discord-btn"
                onClick={login}
                disabled={connecting}
              >
                {connecting ? <Loader2 size={13} className="spin-icon" /> : null}
                {connecting ? 'Connecting…' : 'Connect with Discord'}
              </button>
            </div>
          )}
          {connectError && <div className="creq-discord-error">{connectError}</div>}

          {/* Ping a user */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Ping a Discord user <span className="request-optional">(optional)</span>
          </div>
          <input
            type="text"
            className="request-notes-textarea"
            style={{ resize: 'none', height: 'auto', padding: '0.5rem 0.75rem' }}
            placeholder="Discord username (e.g. dweagle79)"
            value={pingDiscordId}
            onChange={(e) => setPingDiscordId(e.target.value.slice(0, 32))}
            maxLength={32}
          />
          {pingDiscordId.length > 0 && !isValidDiscordUsername(pingDiscordId) && (
            <div className="creq-field-hint" style={{ color: '#ff9800' }}>
              Must be 2–32 characters. @everyone and @here are not allowed.
            </div>
          )}

          {/* Notes */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Notes <span className="request-optional">(optional)</span>
          </div>
          <textarea
            className="request-notes-textarea"
            placeholder="Any special instructions, season numbers, extra context…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            maxLength={500}
          />
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary"
            onClick={handleSubmit}
            disabled={!canSubmit}
            title={
              !isConnected ? 'Connect with Discord to submit a request' :
              !effectiveTitle ? 'Enter a title or select a TMDB match' :
              !posterStyle ? 'Select a poster style (CL2K or MM2K)' :
              undefined
            }
          >
            {submitting ? (
              <Loader2 size={14} className="spin-icon" />
            ) : submitted ? (
              <Check size={14} />
            ) : (
              <Star size={14} />
            )}
            {submitted ? 'Submitted!' : 'Request Poster'}
          </button>
        </div>
      </div>
    </div>
  )
}
