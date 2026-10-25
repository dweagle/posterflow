import type { TmdbCardMeta } from '../../api/makerTools'

export type HideFilterKey = 'mm2k' | 'anime' | 'nonEnglish'
export type HideFilters = Record<HideFilterKey, boolean>

export const NO_HIDE_FILTERS: HideFilters = { mm2k: false, anime: false, nonEnglish: false }

export const HIDE_FILTER_OPTIONS: { key: HideFilterKey; label: string; hint: string }[] = [
  { key: 'mm2k', label: 'MM2K', hint: 'Hide MM2K-style posters.' },
  { key: 'anime', label: 'Anime', hint: 'Hide anime. Uses the TMDB anime keyword, or Japanese animation when the keyword is missing.' },
  { key: 'nonEnglish', label: 'Non-English', hint: 'Hide titles whose original language on TMDB is not English.' },
]

export const anyHideFilter = (f: HideFilters): boolean => f.mm2k || f.anime || f.nonEnglish
// Anime and language come from TMDB; MM2K is a style tag on the item itself
export const needsTmdbMeta = (f: HideFilters): boolean => f.anime || f.nonEnglish

/** Stored filters, or the old single No MM2K flag when the user last saved under that key. */
export function loadHideFilters(storageKey: string, legacyNoMm2kKey?: string): HideFilters {
  try {
    const raw = localStorage.getItem(storageKey)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<Record<HideFilterKey, unknown>>
      return { mm2k: parsed.mm2k === true, anime: parsed.anime === true, nonEnglish: parsed.nonEnglish === true }
    }
    if (legacyNoMm2kKey && localStorage.getItem(legacyNoMm2kKey) === '1') return { ...NO_HIDE_FILTERS, mm2k: true }
  } catch { /* storage unavailable or malformed; start clean */ }
  return NO_HIDE_FILTERS
}

/** Persist while any filter is on; clear the key (and the legacy one) otherwise. */
export function saveHideFilters(storageKey: string, filters: HideFilters, legacyNoMm2kKey?: string): void {
  try {
    if (anyHideFilter(filters)) localStorage.setItem(storageKey, JSON.stringify(filters))
    else localStorage.removeItem(storageKey)
    if (legacyNoMm2kKey) localStorage.removeItem(legacyNoMm2kKey)
  } catch { /* storage unavailable; skip persistence */ }
}

/** TMDB namespace that carries a language and keywords; seasons ride their show's id. */
export function tmdbMetaType(mediaType: string | null | undefined): 'movie' | 'tv' | null {
  if (mediaType === 'movie') return 'movie'
  if (mediaType === 'show' || mediaType === 'season' || mediaType === 'tv') return 'tv'
  return null
}

export function isHiddenByStyle(style: string | null, filters: HideFilters, hiddenStyles: Set<string>): boolean {
  return !!style && (hiddenStyles.has(style) || (filters.mm2k && style === 'MM2K'))
}

/** A title the lookup hasn't classified (no id, still loading, failed) stays visible. */
export function isHiddenByMeta(filters: HideFilters, meta: TmdbCardMeta | undefined): boolean {
  if (!meta) return false
  if (filters.anime && meta.anime) return true
  return filters.nonEnglish && meta.original_language != null && meta.original_language !== 'en'
}
