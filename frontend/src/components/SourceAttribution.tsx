import type { CSSProperties } from 'react'
import tmdbLogo from '../assets/service-icons/tmdb.png'
import tvdbLogo from '../assets/service-icons/tvdb.png'

type Props = {
  source: 'tmdb' | 'tvdb'
  className?: string
  style?: CSSProperties
}

// Each provider's own published attribution wording. TMDB's notice is mandatory under their API
// terms; TheTVDB's is the sample text from their API information page, which pairs the sentence
// with a linked logo. The logo itself is the required direct link in both cases.
const SOURCES = {
  tmdb: {
    logo: tmdbLogo,
    name: 'TMDB',
    href: 'https://www.themoviedb.org',
    notice: 'This product uses the TMDB API but is not endorsed or certified by TMDB.',
  },
  tvdb: {
    logo: tvdbLogo,
    name: 'TheTVDB',
    href: 'https://www.thetvdb.com',
    notice: 'Metadata provided by TheTVDB. Please consider subscribing.',
  },
} as const

/** Attribution for a metadata source: its linked logo plus the provider's own notice text. */
export default function SourceAttribution({ source, className, style }: Props) {
  const { logo, name, href, notice } = SOURCES[source]
  return (
    <p className={`source-attribution${className ? ` ${className}` : ''}`} style={style}>
      <a href={href} target="_blank" rel="noopener noreferrer" title={notice}>
        <img src={logo} alt={name} />
      </a>
      <span>{notice}</span>
    </p>
  )
}
