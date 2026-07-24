// External "find this title elsewhere" links, opened in a new tab from maker + result cards.
// Google carries the year when present (helps disambiguate); ThePosterDB matches poster-set
// titles, which rarely include a year, so it searches the bare title.

export function googleSearchUrl(title: string, year?: number | string | null): string {
  const t = String(title ?? '').trim()
  const y = year != null && String(year).trim() ? ` ${String(year).trim()}` : ''
  return `https://www.google.com/search?q=${encodeURIComponent(`${t}${y}`.trim())}`
}

// ThePosterDB scopes results by a ?section= tab. Map our media types onto it; a season belongs
// to a show, so it points at shows. Unknown types (e.g. person) fall back to an unscoped search.
const TPDB_SECTION: Record<string, string> = {
  movie: 'movies',
  tv: 'shows',
  season: 'shows',
  collection: 'collections',
}

export function tpdbSearchUrl(title: string, mediaType?: string | null): string {
  const term = encodeURIComponent(String(title ?? '').trim())
  const section = mediaType ? TPDB_SECTION[mediaType] : undefined
  const base = `https://theposterdb.com/search?term=${term}`
  return section ? `${base}&section=${section}` : base
}
