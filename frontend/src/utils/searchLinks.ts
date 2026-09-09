// External "find this title elsewhere" links, opened in a new tab from maker + result cards.
// Google carries the year when present (helps disambiguate); ThePosterDB matches poster-set
// titles, which rarely include a year, so it searches the bare title.

export function googleSearchUrl(title: string, year?: number | string | null): string {
  const t = String(title ?? '').trim()
  const y = year != null && String(year).trim() ? ` ${String(year).trim()}` : ''
  return `https://www.google.com/search?q=${encodeURIComponent(`${t}${y}`.trim())}`
}

// ThePosterDB scopes results by a ?section= tab. Map our media types onto it; a season belongs
// to a show, so it points at shows. Series arrive as 'tv' from TMDB search results and as 'show'
// from the unmatched/community APIs — both are here so either caller gets a scoped search.
// Unknown types (e.g. person) fall back to an unscoped search.
const TPDB_SECTION: Record<string, string> = {
  movie: 'movies',
  tv: 'shows',
  show: 'shows',
  season: 'shows',
  collection: 'collections',
}

export function tpdbSearchUrl(title: string, mediaType?: string | null): string {
  const term = encodeURIComponent(String(title ?? '').trim())
  const section = mediaType ? TPDB_SECTION[mediaType] : undefined
  const base = `https://theposterdb.com/search?term=${term}`
  return section ? `${base}&section=${section}` : base
}

// Ben Dodson's Apple TV artwork finder queries the iTunes Search API, which matches far better
// on bare words: hyphens and dashes split words ("Spider-Man" → "Spider Man"), apostrophes join
// them ("Schitt's" → "Schitts"), and all other punctuation is dropped ("M*A*S*H" → "MASH").
export function cleanAppleTvQuery(title: string): string {
  return String(title ?? '')
    .replace(/[-‐-―−/\\_]/g, ' ')
    .replace(/['‘’]/g, '')
    .replace(/[^\p{L}\p{N}\s]/gu, '')
    .replace(/\s+/g, ' ')
    .trim()
}

const APPLE_TV_TYPE: Record<string, string> = { movie: 'movies', tv: 'tv' }

export function appleTvArtworkUrl(title: string, storefront: string, mediaType?: string | null): string {
  const query = encodeURIComponent(cleanAppleTvQuery(title))
  const type = mediaType ? APPLE_TV_TYPE[mediaType] : undefined
  const base = `https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=${query}&storefront=${storefront}`
  return type ? `${base}&type=${type}` : base
}
