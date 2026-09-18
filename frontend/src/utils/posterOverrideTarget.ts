import type { ArtworkSearchType, LibraryItem, OverrideArtSlot, PosterOverride } from '../api/posterManager'

export type ArtSlot = OverrideArtSlot

// Artwork index types → override slots (the index says "squareart", the slot is "square").
export const ART_SLOT_OF: Record<ArtworkSearchType, ArtSlot> = { logo: 'logo', background: 'background', squareart: 'square' }
export const ART_SLOT_LABELS: Record<ArtSlot, string> = { logo: 'Logo', background: 'Background', square: 'Square' }

// The library item (and slot) a drive file gets pinned to: a poster/season, or an artwork
// piece. Artwork targets take their slot from each picked file's type.
export interface OverrideTarget {
  domain?: 'poster' | 'artwork'
  media_type: 'movie' | 'show' | 'collection'
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  title: string
  year?: number | null
  // Shows only: null = main poster, 0 = specials, N = that season.
  season?: number | null
  // Season numbers the caller already knows about (chips in the picker).
  seasons?: number[]
}

// One drive file from a search hit, ready to pin. artwork_type is set for artwork hits.
export interface DrivePosterPick {
  poster_name: string
  drive_id: string
  drive_name: string
  file_path: string
  artwork_type?: ArtworkSearchType | null
}

export const pickSlot = (pick: DrivePosterPick): ArtSlot | null =>
  pick.artwork_type ? ART_SLOT_OF[pick.artwork_type] : null

// A note shown on a search hit: a file some override already pins.
export interface FileBadge {
  kind: 'pinned'
  text: string
  tip?: string
}

// "Breaking Bad (2008) - Season 2" / "Heat (1995) - Logo": what an override is pinned to.
export function overrideLabel(ov: PosterOverride): string {
  let label = ov.title
  if (ov.year) label += ` (${ov.year})`
  if ((ov.domain ?? 'poster') === 'artwork') {
    if (ov.scope === 'set') label += ' - Artwork set'
    else if (ov.slot) label += ` - ${ART_SLOT_LABELS[ov.slot]}`
  } else if (ov.scope === 'set') {
    label += ' - Whole set'
  } else if (ov.season != null) {
    label += ov.season === 0 ? ' - Specials' : ` - Season ${ov.season}`
  }
  return label
}

// "Pinned · <item>" badges for every file-level override, keyed by file; `exclude` skips the
// caller's own picks so they read "Using" instead.
export function pinnedFileBadges(overrides: PosterOverride[], exclude: Set<number> = new Set()): Record<string, FileBadge[]> {
  const badges: Record<string, FileBadge[]> = {}
  for (const ov of overrides) {
    if (!ov.file || exclude.has(ov.id)) continue
    ;(badges[ov.file] ??= []).push({
      kind: 'pinned',
      text: `Pinned · ${overrideLabel(ov)}`,
      tip: `Already pinned as ${overrideLabel(ov)}. Using it here as well is fine; one file can serve several slots.`,
    })
  }
  return badges
}

const EXT_RE = /\.[a-z0-9]{2,5}$/i
const TAG_RE = /\s*\{(?:tmdb|tvdb|imdb)-[^}]*\}/gi
const ARTWORK_SUFFIX_RE = /\s*-\s*(?:logo|background|squareart)s?\s*$/i
const SEASON_RE = /(?:[-\s_]+)?Season\s*(\d{1,4})/i
const SPECIALS_RE = /(?:\s*-\s*|_)Specials\b/i

// Title/year/season out of a drive file name like "Show (2020) {tmdb-1} - Season 2" or
// "Show (2020) {tmdb-1} - background". The query keeps its "(2020)": the library search
// reads a trailing year itself and ranks by it.
export function parsePosterName(name: string): { query: string; season: number | null } {
  let stem = name.replace(EXT_RE, '').replace(TAG_RE, '').replace(ARTWORK_SUFFIX_RE, '')
  let season: number | null = null
  if (SPECIALS_RE.test(stem)) {
    season = 0
    stem = stem.replace(SPECIALS_RE, '')
  } else {
    const m = stem.match(SEASON_RE)
    if (m && m.index != null) {
      season = parseInt(m[1], 10)
      stem = stem.slice(0, m.index)
    }
  }
  return { query: stem.trim(), season }
}

type ItemIdentity = { tmdb_id?: number | null; title: string; year?: number | null }

// Same library item: a shared TMDB id decides when both have one, else title + year.
export function sameItem(a: ItemIdentity, b: ItemIdentity): boolean {
  if (a.tmdb_id && b.tmdb_id) return a.tmdb_id === b.tmdb_id
  return a.title.trim().toLowerCase() === b.title.trim().toLowerCase() && (a.year ?? null) === (b.year ?? null)
}

export function libraryItemToTarget(item: LibraryItem, season: number | null, domain: 'poster' | 'artwork' = 'poster'): OverrideTarget {
  return {
    domain,
    media_type: item.media_type,
    tmdb_id: item.tmdb_id ?? null,
    tvdb_id: item.tvdb_id ?? null,
    imdb_id: item.imdb_id ?? null,
    title: item.title,
    year: item.year ?? null,
    season: domain === 'poster' && item.media_type === 'show' ? season : null,
    seasons: domain === 'poster' && item.media_type === 'show' ? item.seasons : undefined,
  }
}

export function libraryItemMatchesTarget(item: LibraryItem, t: OverrideTarget): boolean {
  return item.media_type === t.media_type && sameItem(item, t)
}

export function overrideTargetLabel(t: OverrideTarget, slot?: ArtSlot | null): string {
  let label = t.title
  if (t.year) label += ` (${t.year})`
  if ((t.domain ?? 'poster') === 'artwork') {
    if (slot) label += ` - ${ART_SLOT_LABELS[slot]}`
  } else if (t.media_type === 'show' && t.season != null) {
    label += t.season === 0 ? ' - Specials' : ` - Season ${t.season}`
  }
  return label
}

// The stored slot override for this exact item: the poster/season slot, or (artwork) the
// named piece — drive-level or file-level.
export function overrideMatchesTarget(ov: PosterOverride, t: OverrideTarget, slot?: ArtSlot | null): boolean {
  const domain = t.domain ?? 'poster'
  if ((ov.domain ?? 'poster') !== domain || ov.scope !== 'slot' || ov.media_type !== t.media_type) return false
  if (domain === 'artwork') {
    if ((ov.slot ?? null) !== (slot ?? null)) return false
  } else if ((ov.season ?? null) !== (t.media_type === 'show' ? t.season ?? null : null)) {
    return false
  }
  return sameItem(ov, t)
}

export function overridePayloadFor(t: OverrideTarget, pick: DrivePosterPick): Omit<PosterOverride, 'id'> {
  const domain = pick.artwork_type ? 'artwork' : 'poster'
  return {
    media_type: t.media_type,
    tmdb_id: t.tmdb_id ?? null,
    tvdb_id: t.tvdb_id ?? null,
    imdb_id: t.imdb_id ?? null,
    title: t.title,
    year: t.year ?? null,
    domain,
    scope: 'slot',
    season: domain === 'poster' && t.media_type === 'show' ? t.season ?? null : null,
    slot: domain === 'artwork' ? pickSlot(pick) : null,
    drive_id: pick.drive_id,
    file: pick.file_path,
  }
}

export const fileBaseName = (path: string): string => path.split(/[\\/]/).pop() ?? path
