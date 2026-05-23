import { useState, useEffect, useCallback } from 'react'
import { AlertCircle, Check, ExternalLink, Loader2, Star, LogOut } from 'lucide-react'
import { type TmdbCandidate, searchUnmatchedTmdb } from '../../api/client'
  import { submitCommunityRequest } from '../../api/client'
import { useToast } from '../Toast'
import { useDiscordAuth } from '../../hooks/useDiscordAuth'

type TmdbSearchType = 'movie' | 'show' | 'collection'

const STYLE_TAGS = [
  'MM2K Style',
  'CL2K Style',
  'Anime Movie',
  'Anime TV',
]

function getTmdbLink(candidate: TmdbCandidate): string {
  if (candidate.media_type === 'movie') return `https://www.themoviedb.org/movie/${candidate.tmdb_id}`
  if (candidate.media_type === 'collection') return `https://www.themoviedb.org/collection/${candidate.tmdb_id}`
  return `https://www.themoviedb.org/tv/${candidate.tmdb_id}`
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
  const { isConnected, username, discordUserId, connecting, connectError, login, logout } = useDiscordAuth()
  const [candidates, setCandidates] = useState<TmdbCandidate[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<TmdbCandidate | null>(null)
  const [styleTags, setStyleTags] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)

  const effectiveName = username ?? ''

  const cleanTitle = year ? title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : title

  // Auto-search on open
  useEffect(() => {
    if (!tmdbApiKeyConfigured) return
    setLoading(true)
    searchUnmatchedTmdb({ title: cleanTitle, year, type: tmdbType })
      .then((result) => {
        setCandidates(result.candidates)
        // Auto-select first result if it's a clear match
        if (result.candidates.length === 1) setSelected(result.candidates[0])
      })
      .catch(() => setCandidates([]))
      .finally(() => setLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleTag = useCallback((tag: string) => {
    setStyleTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))
  }, [])

  const handleSubmit = useCallback(async () => {
    if (submitting) return
    const trimmedName = effectiveName.trim()
    if (!trimmedName) return
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
        requested_by_discord_id: discordUserId ?? null,
      })

      setSubmitted(true)
      showToast(
        result.status === 'already_requested'
          ? 'Already requested!'
          : 'Request submitted!',
        result.status === 'already_requested' ? 'info' : 'success',
      )
      setTimeout(onClose, 1200)
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      showToast(detail ?? 'Failed to submit request', 'error')
    } finally {
      setSubmitting(false)
    }
  }, [selected, submitting, effectiveName, notes, styleTags, showToast, onClose])

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
          ) : !candidates || candidates.length === 0 ? (
            <div className="creq-no-results">
              No TMDB results found — this will be submitted as a custom request using the title above.
            </div>
          ) : (
            <div className="creq-candidates">
              {candidates.map((c, i) => {
                const isSelected = selected?.tmdb_id === c.tmdb_id && selected?.media_type === c.media_type
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
                      <div className="creq-candidate-poster creq-candidate-poster--empty" />
                    )}
                    <div className="creq-candidate-info">
                      <div className="creq-candidate-title">
                        {c.title}
                        {c.year && <span className="creq-candidate-year"> ({c.year})</span>}
                      </div>
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

          {/* Style tags */}
          <div className="creq-section-label" style={{ marginTop: '1.25rem' }}>
            Style preferences
          </div>
          <div className="request-style-tags">
            {STYLE_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={`request-style-tag${styleTags.includes(tag) ? ' selected' : ''}`}
                onClick={() => toggleTag(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          {/* Discord identity */}
          <div className="creq-section-label" style={{ marginTop: '1.25rem' }}>
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

          {/* Notes */}
          <div className="creq-section-label" style={{ marginTop: '1.25rem' }}>
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
            disabled={submitting || submitted || !isConnected || !effectiveName.trim()}
            title={!selected ? 'Submitting without a TMDB match' : undefined}
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
