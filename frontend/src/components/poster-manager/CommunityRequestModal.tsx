import { useState, useEffect, useCallback, useRef } from 'react'
import { AlertCircle, Check, ExternalLink, Loader2, Star, LogOut, User } from 'lucide-react'
import { type TmdbCandidate, searchUnmatchedTmdb } from '../../api/client'
import { submitCommunityRequest } from '../../api/client'
import { useToast } from '../Toast'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'

type TmdbSearchType = 'movie' | 'show' | 'collection' | 'person'

// Required, single-select community poster style. The stored tag values keep the
// "… Style" suffix for consistency with existing requests; the card derives the badge from them.
const POSTER_STYLES: { value: string; label: string }[] = [
  { value: 'CL2K Style', label: 'CL2K' },
  { value: 'MM2K Style', label: 'MM2K' },
]

// Optional extra style preferences (multi-select)
const EXTRA_TAGS = [
  'Anime Movie',
  'Anime TV',
]

function getTmdbLink(candidate: TmdbCandidate): string {
  if (candidate.media_type === 'movie') return `https://www.themoviedb.org/movie/${candidate.tmdb_id}`
  if (candidate.media_type === 'collection') return `https://www.themoviedb.org/collection/${candidate.tmdb_id}`
  if (candidate.media_type === 'person') return `https://www.themoviedb.org/person/${candidate.tmdb_id}`
  return `https://www.themoviedb.org/tv/${candidate.tmdb_id}`
}

/** Validate that a string is a plausible Discord username (2–32 non-whitespace chars, no @everyone/@here) */
function isValidDiscordUsername(value: string): boolean {
  const v = value.trim()
  return v.length >= 2 && v.length <= 32 && !/^@?(everyone|here)$/i.test(v)
}

type CommunityRequestModalProps = {
  title: string
  year: number | null
  tmdbType: TmdbSearchType
  seasonNumbers?: number[]
  tmdbApiKeyConfigured: boolean
  onClose: () => void
}

export default function CommunityRequestModal({
  title,
  year,
  tmdbType,
  seasonNumbers,
  tmdbApiKeyConfigured,
  onClose,
}: CommunityRequestModalProps) {
  const { showToast } = useToast()
  const { isConnected, username, token, connecting, connectError, login, logout } = useDiscordAuth()
  const [candidates, setCandidates] = useState<TmdbCandidate[] | null>(null)
  const [personCandidates, setPersonCandidates] = useState<TmdbCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<TmdbCandidate | null>(null)
  const [isCustomRequest, setIsCustomRequest] = useState(false)
  const [posterStyle, setPosterStyle] = useState<string | null>(null)
  const [extraTags, setExtraTags] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [pingDiscordId, setPingDiscordId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  // Set when the user hits submit without picking a TMDB match (and not custom);
  // surfaces an inline warning + highlights the results. Auto-clears once resolved.
  const [pickWarning, setPickWarning] = useState(false)
  const candidatesRef = useRef<HTMLDivElement>(null)

  const effectiveName = username ?? ''

  const cleanTitle = year ? title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : title

  // Combine main + person candidates, deduplicating by tmdb_id
  const allCandidates: TmdbCandidate[] = (() => {
    const main = candidates ?? []
    // Only append person results that aren't already in the main list
    const mainIds = new Set(main.map((c) => `${c.media_type}:${c.tmdb_id}`))
    const extra = personCandidates.filter((c) => !mainIds.has(`${c.media_type}:${c.tmdb_id}`))
    return [...main, ...extra]
  })()

  // Auto-search on open
  useEffect(() => {
    if (!tmdbApiKeyConfigured) return
    setLoading(true)
    // Run main search + person search in parallel (skip person search if already searching persons)
    const mainSearch = searchUnmatchedTmdb({ title: cleanTitle, year, type: tmdbType })
    const personSearch = tmdbType !== 'person'
      ? searchUnmatchedTmdb({ title: cleanTitle, year: null, type: 'person' })
      : Promise.resolve({ candidates: [] as TmdbCandidate[] })

    Promise.all([mainSearch, personSearch])
      .then(([mainResult, personResult]) => {
        setCandidates(mainResult.candidates)
        setPersonCandidates(personResult.candidates)
        // Auto-select first result if it's a single unambiguous match (main type only)
        if (mainResult.candidates.length === 1) setSelected(mainResult.candidates[0])
      })
      .catch(() => { setCandidates([]); setPersonCandidates([]) })
      .finally(() => setLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleExtraTag = useCallback((tag: string) => {
    setExtraTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))
  }, [])

  // Determine if the submit should be blocked because of missing TMDB selection
  const hasResults = allCandidates.length > 0
  const tmdbSelectionRequired = tmdbApiKeyConfigured && hasResults && !selected && !isCustomRequest
  // Only show the warning while the selection is actually still missing — picking a
  // match or checking "custom request" flips tmdbSelectionRequired and hides it.
  const showPickWarning = pickWarning && tmdbSelectionRequired

  const handleSubmit = useCallback(async () => {
    if (submitting) return
    // Block + warn if there are TMDB results but the user never picked one (or
    // marked it custom). Surface the results so they can choose.
    if (tmdbSelectionRequired) {
      setPickWarning(true)
      showToast('Pick the correct TMDB match below, or check “This is a custom request.”', 'error')
      candidatesRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
      return
    }
    const trimmedName = effectiveName.trim()
    if (!trimmedName || !token) return
    setSubmitting(true)
    try {
      const isSeason = seasonNumbers != null && seasonNumbers.length > 0
      const isMultiSeason = seasonNumbers != null && seasonNumbers.length > 1
      let notesValue = notes.trim() || null
      let seasonNumber: number | null = null
      if (isSeason) {
        if (!isMultiSeason) {
          seasonNumber = seasonNumbers![0]
        } else {
          const seasonsStr = `Seasons: ${seasonNumbers!.join(', ')}`
          notesValue = notesValue ? `${seasonsStr}\n\n${notesValue}` : seasonsStr
        }
      }
      // Use TMDB match if selected, otherwise fall back to the original asset title/year
      const trimmedPingId = pingDiscordId.trim()
      const validPingId = trimmedPingId && isValidDiscordUsername(trimmedPingId) ? trimmedPingId : null
      const styleTags = [posterStyle, ...extraTags].filter(Boolean) as string[]
      const result = await submitCommunityRequest({
        tmdb_id: selected?.tmdb_id ?? null,
        media_type: isSeason ? 'season' : (selected?.media_type ?? tmdbType),
        title: selected?.title ?? cleanTitle,
        year: selected?.year ?? year,
        season_number: seasonNumber,
        poster_path: selected?.poster_url ?? null,
        imdb_id: selected?.imdb_id ?? null,
        tvdb_id: selected?.tvdb_id ?? null,
        notes: notesValue,
        style_tags: styleTags.length > 0 ? styleTags : undefined,
        requested_by: trimmedName,
        ping_discord_id: validPingId,
        discord_token: token,
      })

      setSubmitted(true)
      showToast(
        result.status === 'already_requested'
          ? 'Already requested!'
          : 'Request submitted!',
        result.status === 'already_requested' ? 'info' : 'success',
      )
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail ?? 'Failed to submit request', 'error')
    } finally {
      setSubmitting(false)
    }
  }, [selected, submitting, effectiveName, notes, posterStyle, extraTags, pingDiscordId, token, showToast, tmdbSelectionRequired])

  // Auto-close shortly after a successful submit. Cleared on unmount so a pending
  // close can't fire after the modal is gone (which leaked stray onClose calls into tests).
  useEffect(() => {
    if (!submitted) return
    const id = setTimeout(onClose, 1200)
    return () => clearTimeout(id)
  }, [submitted, onClose])

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="modal-content schedule-modal community-req-modal">
        <div className="modal-header">
          <h2>Request Community Poster</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="creq-rate-limit-notice">
          Limited to 5 new requests per day.
        </div>

        <div className="modal-body">
          {/* Item being requested */}
          <div className="creq-item-label">
            Requesting a poster for:
            <strong className="creq-item-title"> {cleanTitle}</strong>
            {year && <span className="creq-item-year"> ({year})</span>}
            {seasonNumbers && seasonNumbers.length > 0 ? (
              <span className="tmdb-type-badge tmdb-type-badge--season">
                {seasonNumbers.length === 1
                  ? `Season ${seasonNumbers[0]}`
                  : `Seasons: ${seasonNumbers.join(', ')}`}
              </span>
            ) : (
              <span className={`tmdb-type-badge tmdb-type-badge--${tmdbType === 'show' ? 'show' : tmdbType}`}>
                {tmdbType}
              </span>
            )}
          </div>

          {/* TMDB match picker */}
          <div className="creq-section-label">Pick the correct TMDB match:</div>

          {showPickWarning && (
            <div className="creq-pick-warning" role="alert">
              <AlertCircle size={14} />
              <span>Pick the correct TMDB match below, or check <strong>“This is a custom request.”</strong></span>
            </div>
          )}

          {!tmdbApiKeyConfigured ? (
            <div className="tmdb-candidates-warning">
              <AlertCircle size={14} />
              <span>No TMDB API key configured. Add it in <strong>Settings → General → API Keys</strong>.</span>
            </div>
          ) : loading ? (
            <div className="creq-loading">
              <Loader2 size={16} className="spin-icon" />
              <span>Searching TMDB…</span>
            </div>
          ) : allCandidates.length === 0 ? (
            <div className="creq-no-results">
              No TMDB results found.
            </div>
          ) : (
            <div className={`creq-candidates${showPickWarning ? ' creq-candidates--warn' : ''}`} ref={candidatesRef}>
              {allCandidates.map((c, i) => {
                const isSelected = selected?.tmdb_id === c.tmdb_id && selected?.media_type === c.media_type
                const isPerson = c.media_type === 'person'
                const link = getTmdbLink(c)
                return (
                  <button
                    key={i}
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

          {/* Custom request checkbox — shown when there are results but nothing selected, or always when no results */}
          {tmdbApiKeyConfigured && !loading && (
            <label className="creq-custom-request-label">
              <input
                type="checkbox"
                checked={isCustomRequest || allCandidates.length === 0}
                disabled={allCandidates.length === 0}
                onChange={(e) => setIsCustomRequest(e.target.checked)}
              />
              This is a custom request — no matching TMDB item available
            </label>
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
                onClick={() => setPosterStyle((prev) => (prev === s.value ? null : s.value))}
              >
                {s.label}
              </button>
            ))}
          </div>

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
            disabled={submitting || submitted || !isConnected || !effectiveName.trim() || !posterStyle}
            title={
              !posterStyle ? 'Select a poster style (CL2K or MM2K)' :
              tmdbSelectionRequired ? 'Pick a TMDB match or check "custom request"' :
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
