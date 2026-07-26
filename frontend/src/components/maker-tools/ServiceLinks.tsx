import tmdbIcon from '../../assets/service-icons/tmdb.png'
import imdbIcon from '../../assets/service-icons/imdb.png'
import tvdbIcon from '../../assets/service-icons/tvdb.png'
import appleTvIcon from '../../assets/service-icons/appletv.png'
import googleIcon from '../../assets/service-icons/google.png'
import tpdbIcon from '../../assets/service-icons/tpdb.png'
import { googleSearchUrl, tpdbSearchUrl } from '../../utils/searchLinks'

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
  /** Apple TV storefront id. The maker card resolves it from the title's country of origin;
   *  callers that don't get the US default. */
  appleTvStorefront?: string
  /** Fired on hover/focus of the Apple TV link, so a caller can resolve the storefront lazily. */
  onAppleTvIntent?: () => void
}

/** External-service links for a title, as one row of icon buttons. Shared by the maker card and
 *  the artwork finder card so both stay in step. */
export default function ServiceLinks({ item, appleTvStorefront = '143441', onAppleTvIntent }: Props) {
  const hasTmdb = (item.tmdb_id ?? 0) > 0
  const typeParam = item.media_type === 'tv' ? '&type=tv' : item.media_type === 'movie' ? '&type=movies' : ''

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
          href={`https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=${encodeURIComponent(item.title)}&storefront=${appleTvStorefront}${typeParam}`}
          target="_blank"
          rel="noreferrer"
          onMouseEnter={onAppleTvIntent}
          onFocus={onAppleTvIntent}
          title="Find Apple TV artwork"
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
