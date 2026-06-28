import { useState, useCallback } from 'react'
import { AlertCircle, Check, Copy, ExternalLink, Loader2, Search, Star } from 'lucide-react'
import { type TmdbCandidate, searchUnmatchedTmdb } from '../../api/client'
import { mediaTypeToTmdbFilter } from '../../api/makerTools'
import { type FallbackItem } from '../../api/posterManager'
import { type ClaimStatus } from '../../hooks/useCommunityClaimStatus'
import { useToast } from '../Toast'
import CommunityStatusBadge from './CommunityStatusBadge'
import ArrMissingBadge from './ArrMissingBadge'
import CommunityRequestModal from './CommunityRequestModal'

type PosterStyleTmdbSearchProps = {
  item: FallbackItem
  tmdbApiKeyConfigured: boolean
  seasons?: (number | null)[]
  claimStatus?: ClaimStatus | null
}

function getTmdbLink(candidate: TmdbCandidate): string {
  if (candidate.media_type === 'movie') return `https://www.themoviedb.org/movie/${candidate.tmdb_id}`
  if (candidate.media_type === 'collection') return `https://www.themoviedb.org/collection/${candidate.tmdb_id}`
  return `https://www.themoviedb.org/tv/${candidate.tmdb_id}`
}

export default function PosterStyleTmdbSearch({ item, tmdbApiKeyConfigured, seasons, claimStatus }: PosterStyleTmdbSearchProps) {
  const { showToast } = useToast()
  const [isExpanded, setIsExpanded] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [candidates, setCandidates] = useState<TmdbCandidate[] | null>(null)
  const [isNoKey, setIsNoKey] = useState(false)
  const [copiedLink, setCopiedLink] = useState<string | null>(null)
  const [copiedTitle, setCopiedTitle] = useState<string | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [showRequestModal, setShowRequestModal] = useState(false)

  // Strip trailing (YYYY) from title when year is already provided separately,
  // e.g. "INVINCIBLE (2021)" + year 2021 → search "INVINCIBLE" with year 2021
  const cleanTitle = item.year
    ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
    : item.title

  const handleSearch = useCallback(async () => {
    if (isLoading) return

    if (isExpanded) {
      setIsExpanded(false)
      return
    }

    setIsExpanded(true)

    if (!tmdbApiKeyConfigured) {
      setIsNoKey(true)
      return
    }

    setIsNoKey(false)

    if (candidates !== null) return

    setIsLoading(true)
    try {
      const result = await searchUnmatchedTmdb({
        title: cleanTitle,
        year: item.year,
        type: item.type,
        tmdb_id: item.tmdb_id,
        tvdb_id: item.tvdb_id,
        imdb_id: item.imdb_id,
      })
      setCandidates(result.candidates)
    } catch {
      setCandidates([])
    } finally {
      setIsLoading(false)
    }
  }, [isExpanded, isLoading, candidates, item, tmdbApiKeyConfigured])

  const handleCopyLink = useCallback(async (link: string) => {
    try {
      await navigator.clipboard.writeText(link)
      setCopiedLink(link)
      setTimeout(() => setCopiedLink(null), 2000)
      showToast('Link copied')
    } catch {
      try {
        const el = document.createElement('textarea')
        el.value = link
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        setCopiedLink(link)
        setTimeout(() => setCopiedLink(null), 2000)
        showToast('Link copied')
      } catch (err) {
        console.error('Failed to copy link:', err)
        showToast('Failed to copy link', 'error')
      }
    }
  }, [showToast])

  const handleCopyTitle = useCallback(async (candidate: TmdbCandidate, key: string) => {
    const text = candidate.year ? `${candidate.title} (${candidate.year})` : candidate.title
    try {
      await navigator.clipboard.writeText(text)
      setCopiedTitle(key)
      setTimeout(() => setCopiedTitle(null), 2000)
      showToast('Title copied')
    } catch {
      try {
        const el = document.createElement('textarea')
        el.value = text
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        setCopiedTitle(key)
        setTimeout(() => setCopiedTitle(null), 2000)
        showToast('Title copied')
      } catch (err) {
        console.error('Failed to copy title:', err)
        showToast('Failed to copy title', 'error')
      }
    }
  }, [showToast])

  return (
    <div className={`unmatched-item-with-tmdb${isExpanded ? ' expanded' : ''}`}>
      <div className="unmatched-item">
        <div className="unmatched-item-top">
          <div className="unmatched-item-meta">
            <span className="item-title">{cleanTitle}</span>
            {item.year && <span className="item-year">({item.year})</span>}
            <span className={`unmatched-cat-badge unmatched-cat-badge--${
              item.type === 'movie' ? 'movie' : item.type === 'collection' ? 'collection' : 'series'
            }`}>
              {item.type === 'movie' ? 'Movie' : item.type === 'collection' ? 'Collection' : 'Show'}
            </span>
            <CommunityStatusBadge status={claimStatus ?? null} />
            <ArrMissingBadge available={item.available} />
          </div>
          <div className="unmatched-item-actions">
            <button
              className={`tmdb-search-btn${isExpanded ? ' active' : ''}`}
              onClick={handleSearch}
              title="Search TMDB for this item"
            >
              {isLoading ? <Loader2 size={13} className="spin-icon" /> : <Search size={13} />}
              <span>TMDB</span>
            </button>
            <button
              type="button"
              className="maker-nav-btn"
              title="Search in Maker Tools (opens in a new tab)"
              onClick={() => {
                const filter = mediaTypeToTmdbFilter(item.type)
                const q = item.year ? `${cleanTitle} ${item.year}` : cleanTitle
                const params = new URLSearchParams({ tmdbSearch: q })
                if (filter) params.set('type', filter)
                if (item.tmdb_id) params.set('tmdbId', String(item.tmdb_id))
                if (item.tvdb_id) params.set('tvdbId', String(item.tvdb_id))
                if (item.imdb_id) params.set('imdbId', item.imdb_id)
                window.open(`/maker-tools?${params.toString()}`, '_blank', 'noopener,noreferrer')
              }}
            >
              <Search size={13} />
              <span>Maker</span>
            </button>
            <button
              type="button"
              className="community-request-btn"
              title="Request this poster from the community"
              onClick={() => setShowRequestModal(true)}
            >
              <Star size={13} />
              <span>Request</span>
            </button>
          </div>
        </div>
      </div>

      {seasons && seasons.length > 0 && (
        <div className="item-seasons-row">
          {seasons.map((s, i) => (
            <span key={i} className="unmatched-cat-badge unmatched-cat-badge--season">
              {s === 0 ? 'Specials' : `Season ${s}`}
            </span>
          ))}
        </div>
      )}

      {isExpanded && (
        <>
        <div className="tmdb-candidates-panel">
          {isNoKey ? (
            <div className="tmdb-candidates-warning">
              <AlertCircle size={14} />
              <span>
                No TMDB API key configured. Add it in <strong>Settings → General → API Keys</strong>.
              </span>
            </div>
          ) : isLoading ? (
            <div className="tmdb-candidates-loading">
              <Loader2 size={16} className="spin-icon" />
              <span>Searching TMDB…</span>
            </div>
          ) : !candidates || candidates.length === 0 ? (
            <div className="tmdb-candidates-empty">No TMDB results found</div>
          ) : (
            candidates.map((candidate, cidx) => {
              const link = getTmdbLink(candidate)
              const isCopied = copiedLink === link
              const isTitleCopied = copiedTitle === link
              const previewSrc = candidate.poster_url
                ? candidate.poster_url.replace('/w185/', '/w342/')
                : null
              return (
                <div key={cidx} className={`tmdb-candidate-item${candidate.auto_matched ? ' tmdb-candidate-item--matched' : ''}`}>
                  {previewSrc ? (
                    <button
                      className="tmdb-candidate-poster-btn"
                      onClick={() => setPreviewUrl(previewSrc)}
                      title="Click to preview poster"
                    >
                      <img
                        src={candidate.poster_url!}
                        alt=""
                        className="tmdb-candidate-poster"
                        loading="lazy"
                      />
                    </button>
                  ) : (
                    <div className="tmdb-candidate-poster tmdb-candidate-poster--empty" />
                  )}
                  <div className="tmdb-candidate-info">
                    <div className="tmdb-candidate-title-row">
                      <span className="candidate-title">{candidate.title}</span>
                      {candidate.year && <span className="candidate-year">({candidate.year})</span>}
                      <span className={`tmdb-type-badge tmdb-type-badge--${candidate.media_type}`}>
                        {candidate.media_type}
                      </span>
                      {candidate.auto_matched && (
                        <span className="tmdb-matched-badge" title="Matched from your *arr metadata by id">
                          <Check size={11} /> Matched
                        </span>
                      )}
                    </div>
                    <div className="tmdb-candidate-link-row">
                      <span className="tmdb-link-text">{link}</span>
                      <a
                        href={link}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="tmdb-icon-btn"
                        title="Open in TMDB"
                      >
                        <ExternalLink size={13} />
                        <span>Open</span>
                      </a>
                      <button
                        type="button"
                        className={`tmdb-copy-btn${isCopied ? ' copied' : ''}`}
                        onClick={() => handleCopyLink(link)}
                        title={isCopied ? 'Copied!' : 'Copy link'}
                      >
                        {isCopied ? <Check size={13} /> : <Copy size={13} />}
                        <span>{isCopied ? 'Copied' : 'Copy'}</span>
                      </button>
                      <button
                        type="button"
                        className={`tmdb-copy-btn${isTitleCopied ? ' copied' : ''}`}
                        onClick={() => handleCopyTitle(candidate, link)}
                        title={isTitleCopied ? 'Copied!' : 'Copy title & year'}
                      >
                        {isTitleCopied ? <Check size={13} /> : <Copy size={13} />}
                        <span>{isTitleCopied ? 'Copied' : 'Title'}</span>
                      </button>
                    </div>
                  </div>
                </div>
              )
            })
          )}
        </div>
        {!isNoKey && (
          <p className="tmdb-attribution" style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: '#666' }}>
            This product uses the TMDB API but is not endorsed or certified by TMDB.{' '}
            <a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>themoviedb.org</a>
          </p>
        )}
        </>
      )}

      {showRequestModal && (
        <CommunityRequestModal
          title={item.title}
          year={item.year ?? null}
          tmdbType={item.type === 'show' ? 'show' : item.type === 'collection' ? 'collection' : 'movie'}
          seasonNumbers={item.season != null ? [item.season] : undefined}
          tmdbApiKeyConfigured={tmdbApiKeyConfigured}
          onClose={() => setShowRequestModal(false)}
          tmdbId={item.tmdb_id}
          tvdbId={item.tvdb_id}
          imdbId={item.imdb_id}
        />
      )}

      {previewUrl && (
        <div className="poster-preview-overlay" onClick={() => setPreviewUrl(null)}>
          <div className="poster-preview-modal" onClick={(e) => e.stopPropagation()}>
            <button className="poster-preview-close" onClick={() => setPreviewUrl(null)}>×</button>
            <img src={previewUrl} alt="Poster preview" className="poster-preview-img" />
          </div>
        </div>
      )}
    </div>
  )
}
