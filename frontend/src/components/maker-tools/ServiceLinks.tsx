import tmdbIcon from '../../assets/service-icons/tmdb.png'
import imdbIcon from '../../assets/service-icons/imdb.png'
import tvdbIcon from '../../assets/service-icons/tvdb.png'
import appleTvIcon from '../../assets/service-icons/appletv.png'
import googleIcon from '../../assets/service-icons/google.png'
import tpdbIcon from '../../assets/service-icons/tpdb.png'
import { appleTvArtworkUrl, googleSearchUrl, tpdbSearchUrl } from '../../utils/searchLinks'
import type { AppleTvStorefront } from '../../hooks/useAppleTvStorefront'

export type ServiceLinkItem = {
  tmdb_id: number
  media_type: string
  title: string
  year?: string | number | null
  homepage?: string
  imdb_id?: string | null
  tvdb_id?: number | null
}

type Props = {
  item: ServiceLinkItem
  appleTv: AppleTvStorefront
}

// TMDB's terms require crediting JustWatch wherever its watch-provider data is shown.
function appleTvTitle({ iso, soldIn }: AppleTvStorefront): string {
  if (soldIn == null) return 'Find Apple TV artwork'
  const where = soldIn.length ? `Sold in ${soldIn.join(', ')}` : 'Not sold on any Apple TV Store'
  return `Find Apple TV artwork (${iso} store) · ${where} · via JustWatch`
}

/** External-service links for a title, as one row of icon buttons. Shared by the maker card and
 *  the artwork finder card so both stay in step. */
export default function ServiceLinks({ item, appleTv }: Props) {
  const hasTmdb = (item.tmdb_id ?? 0) > 0

  return (
    <div className="tmdb-result-logos">
      {item.homepage && (
        <a className="tmdb-result-link" href={item.homepage} target="_blank" rel="noreferrer" title="Open on TMDB">
          <img className="tmdb-link-icon" src={tmdbIcon} alt="TMDB" />
        </a>
      )}
      {item.imdb_id && (
        <a className="tmdb-result-link" href={`https://www.imdb.com/title/${item.imdb_id}/`} target="_blank" rel="noreferrer" title="Open on IMDB">
          <img className="tmdb-link-icon" src={imdbIcon} alt="IMDB" />
        </a>
      )}
      {item.tvdb_id && (
        <a className="tmdb-result-link" href={`https://thetvdb.com/?id=${item.tvdb_id}&tab=series`} target="_blank" rel="noreferrer" title="Open on TheTVDB">
          <img className="tmdb-link-icon" src={tvdbIcon} alt="TVDB" />
        </a>
      )}
      {(hasTmdb || item.tvdb_id) && (
        <a
          className="tmdb-result-link"
          href={appleTvArtworkUrl(item.title, appleTv.storefront, item.media_type)}
          target="_blank"
          rel="noreferrer"
          onMouseEnter={appleTv.ensure}
          onFocus={appleTv.ensure}
          title={appleTvTitle(appleTv)}
        >
          <img className="tmdb-link-icon" src={appleTvIcon} alt="Apple TV Art" />
        </a>
      )}
      <a
        className="tmdb-result-link"
        href={googleSearchUrl(item.title, item.year)}
        target="_blank"
        rel="noreferrer"
        title="Google search"
      >
        <img className="tmdb-link-icon" src={googleIcon} alt="Google" />
      </a>
      <a
        className="tmdb-result-link"
        href={tpdbSearchUrl(item.title, item.media_type)}
        target="_blank"
        rel="noreferrer"
        title="Search ThePosterDB"
      >
        <img className="tmdb-link-icon" src={tpdbIcon} alt="ThePosterDB" />
      </a>
    </div>
  )
}
