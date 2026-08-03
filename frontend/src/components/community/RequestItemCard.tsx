import { ReactNode } from 'react'
import { ExternalLink, ImageOff, Upload } from 'lucide-react'
import TmdbItemCard, { type PsdConfig } from '../maker-tools/TmdbItemCard'
import { type PosterAvailability } from '../../api/makerTools'

export type CardMediaType = 'movie' | 'show' | 'season' | 'collection' | 'person'

// Single-letter stock preview shown when a card has no poster — a custom item or
// one the requester never matched to a TMDB entry. Colored per type via CSS.
const PLACEHOLDER_LETTER: Record<CardMediaType, string> = {
  movie: 'M',
  show: 'S',
  season: 'S',
  collection: 'C',
  person: 'P',
}

// Format a request/list timestamp the same way across both Community tabs.
function formatCardDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    + ' · '
    + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

// Lives in posterStyles.ts now (the claim-status hook needs it without pulling
// in this component); re-exported so existing importers keep working.
export { getStyleLabel } from './posterStyles'

function tmdbLinkFor(mediaType: CardMediaType, tmdbId: number | null): string {
  if (!tmdbId) return ''
  if (mediaType === 'movie') return `https://www.themoviedb.org/movie/${tmdbId}`
  if (mediaType === 'collection') return `https://www.themoviedb.org/collection/${tmdbId}`
  if (mediaType === 'person') return `https://www.themoviedb.org/person/${tmdbId}`
  return `https://www.themoviedb.org/tv/${tmdbId}`
}

function tvdbLinkFor(tvdbId: number | null): string {
  return tvdbId ? `https://thetvdb.com/dereferrer/series/${tvdbId}` : ''
}

type RequestItemCardProps = {
  id: string
  posterPath: string | null
  title: string
  year: number | null
  seasonLabels?: string[]
  mediaType: CardMediaType
  styleLabel: 'CL2K' | 'MM2K' | null
  status: string
  /** Small badges shown in the title row after the status badge (overlap chips). */
  titleExtras?: ReactNode
  /** Optional whole-card accent, e.g. 'available' → green (poster is in a drive). */
  accent?: 'available' | null
  notes?: string | null
  createdAt: string
  imdbId: string | null
  tvdbId: number | null
  tmdbId: number | null
  /** View-specific lines (requested/added/claimed/fulfilled) shown under the id links. */
  infoLines?: ReactNode
  /** Footer buttons rendered inside `.request-actions` — supplied per view. */
  actions: ReactNode
  // Maker tools (shared with the requests card)
  isMaker: boolean
  showMakerTools: boolean
  psdConfig: PsdConfig
  posterAvailability?: PosterAvailability
  posterAvailabilityChecked?: boolean
  /** Bump to auto-close this card's TMDB image gallery (e.g. when the item is completed). */
  collapseSignal?: number
  // Drag/drop poster upload (only wired when isMaker)
  dragOver: boolean
  onDragEnter?: () => void
  onDragLeave?: () => void
  onDrop?: (e: React.DragEvent) => void
  dropLabel?: string
}

/**
 * Shared community card used by both the Requests and Lists tabs so list cards
 * are visually identical to request cards, including the maker tooling. Each
 * view supplies its own `infoLines` and footer `actions`.
 */
export default function RequestItemCard({
  id,
  posterPath,
  title,
  year,
  seasonLabels,
  mediaType,
  styleLabel,
  status,
  titleExtras,
  accent,
  notes,
  createdAt,
  imdbId,
  tvdbId,
  tmdbId,
  infoLines,
  actions,
  isMaker,
  showMakerTools,
  psdConfig,
  posterAvailability,
  posterAvailabilityChecked,
  collapseSignal,
  dragOver,
  onDragEnter,
  onDragLeave,
  onDrop,
  dropLabel = 'Drop poster(s) here',
}: RequestItemCardProps) {
  const tmdbLink = tmdbLinkFor(mediaType, tmdbId)
  const tvdbLink = tvdbLinkFor(tvdbId)
  const tmdbMediaType = mediaType === 'movie' ? 'movie' : mediaType === 'collection' ? 'collection' : 'tv'

  return (
    <div className="community-request-wrapper">
      <div
        className={`community-request-item${dragOver ? ' drag-over' : ''}${accent ? ` community-request-item--${accent}` : ''}`}
        onDragOver={isMaker ? (e) => { e.preventDefault(); onDragEnter?.() } : undefined}
        onDragLeave={isMaker ? () => onDragLeave?.() : undefined}
        onDrop={isMaker ? (e) => onDrop?.(e) : undefined}
      >
        <div className="request-poster">
          {posterPath ? (
            <img src={posterPath} alt="" loading="lazy" />
          ) : tmdbId != null ? (
            <div
              className="request-poster-placeholder request-poster-noposter"
              title="No poster available on TMDB yet"
              aria-label="No poster available"
            >
              <ImageOff size={22} />
              <span className="request-poster-noposter-label">No poster</span>
            </div>
          ) : (
            <div
              className={`request-poster-placeholder type-${mediaType}`}
              title="No poster — custom item or no TMDB match"
              aria-label={`${mediaType} placeholder`}
            >
              {PLACEHOLDER_LETTER[mediaType] ?? '?'}
            </div>
          )}
        </div>

        <div className="request-info">
          <div className="request-title-row">
            <span className="request-title">{title}</span>
            {year && <span className="request-year">({year})</span>}
            {(seasonLabels ?? []).map((lbl, i) => <span key={i} className="request-season">{lbl}</span>)}
            <span className={`request-type-badge type-${mediaType}`}>{mediaType}</span>
            {styleLabel && <span className={`request-style-badge style-${styleLabel.toLowerCase()}`}>{styleLabel}</span>}
            <span className={`request-status-badge status-${status}`}>
              {status === 'in_progress' ? 'in progress' : status}
            </span>
            {titleExtras}
          </div>
          {(tmdbLink || tvdbLink) && (
            <div className="request-id-links">
              {tmdbLink && (
                <a className="request-tmdb-link" href={tmdbLink} target="_blank" rel="noopener noreferrer" title="Open on TMDB">
                  <ExternalLink size={11} />
                  TMDB
                </a>
              )}
              {tvdbLink && (
                <a className="request-tmdb-link request-tvdb-link" href={tvdbLink} target="_blank" rel="noopener noreferrer" title="Open on TheTVDB">
                  <ExternalLink size={11} />
                  TVDB
                </a>
              )}
            </div>
          )}
          {infoLines}
          {notes && <div className="request-notes">{notes}</div>}
          <div className="request-timestamp">{formatCardDate(createdAt)}</div>
        </div>

        <div className="request-maker-actions-group">
          {showMakerTools && (
            <div className="request-maker-tools-panel">
              <TmdbItemCard
                item={{
                  tmdb_id: tmdbId ?? 0,
                  media_type: tmdbMediaType,
                  title,
                  year: year ? String(year) : '',
                  overview: '',
                  poster_url: posterPath || '',
                  homepage: tmdbLink,
                  imdb_id: imdbId,
                  tvdb_id: tvdbId ?? null,
                }}
                psdConfig={psdConfig}
                posterStyle={styleLabel ?? undefined}
                posterAvailability={posterAvailability}
                posterAvailabilityChecked={posterAvailabilityChecked}
                collapseSignal={collapseSignal}
                hidePoster
                hideTitle
                galleryPortalId={`gallery-portal-${id}`}
              />
              <div className={`request-drop-zone${dragOver ? ' drop-active' : ''}`}>
                <Upload size={22} />
                <span>{dropLabel}</span>
              </div>
            </div>
          )}

          <div className="request-actions">{actions}</div>
        </div>
      </div>

      {/* Gallery panel portals here when Browse Images is open */}
      <div id={`gallery-portal-${id}`} />
    </div>
  )
}
