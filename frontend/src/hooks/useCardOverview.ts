import { useEffect, useMemo, useState } from 'react'
import { getTmdbOverview } from '../api/client'
import type { TmdbCardMeta } from '../api/makerTools'

// Card metadata shared across cards; keyed "type:id", failed fetches evicted so they can retry
const metaCache = new Map<string, Promise<TmdbCardMeta>>()
// Settled lookups, so a list-level filter can read many titles without awaiting each
const resolvedMeta = new Map<string, TmdbCardMeta>()

const EMPTY_META: TmdbCardMeta = { overview: '', poster_url: null, original_language: null, anime: false }

export function tmdbMetaKey(tmdbId: number, mediaType: string): string {
  return `${mediaType}:${tmdbId}`
}

function fetchCardMeta(tmdbId: number, mediaType: string): Promise<TmdbCardMeta> {
  const key = tmdbMetaKey(tmdbId, mediaType)
  let pending = metaCache.get(key)
  if (!pending) {
    pending = getTmdbOverview(tmdbId, mediaType)
    metaCache.set(key, pending)
    pending.then((meta) => { resolvedMeta.set(key, meta) }, () => metaCache.delete(key))
  }
  return pending
}

/** Test seam: forget every cached lookup. */
export function resetCardMetaCache(): void {
  metaCache.clear()
  resolvedMeta.clear()
}

/**
 * A card's description — and TMDB poster URL — fetched only when the source data didn't carry one.
 *
 * TMDB search results and resolved unmatched candidates already include an overview. Cards built
 * from unmatched detection are assembled from the library sources instead — ids and maybe a poster,
 * no TMDB text — so those look it up here rather than making every scan fetch descriptions for
 * the whole library. The same details response carries poster_path, so media-server-sourced cards
 * (no public poster URL of their own) get a TMDB CDN poster from the same single request.
 * Pass enabled=false when the host wants no description at all; that also skips the lookup.
 */
export function useCardOverview(item: { tmdb_id: number; media_type: string; overview?: string }, enabled = true): { overview: string; posterUrl: string | null } {
  const own = enabled ? item.overview ?? '' : ''
  const [fetched, setFetched] = useState<TmdbCardMeta>(EMPTY_META)
  const needsFetch = enabled && !own && (item.tmdb_id ?? 0) > 0

  useEffect(() => {
    if (!needsFetch) {
      setFetched(EMPTY_META)
      return
    }
    let alive = true
    fetchCardMeta(item.tmdb_id, item.media_type)
      .then((meta) => { if (alive) setFetched(meta) })
      .catch(() => { /* non-blocking: the card just shows no description */ })
    return () => { alive = false }
  }, [needsFetch, item.tmdb_id, item.media_type])

  return { overview: own || fetched.overview, posterUrl: fetched.poster_url }
}

/**
 * A title's original language (ISO 639-1) from the same cached details read, or null while
 * loading, when the lookup fails, or when there is nothing to ask (no id, disabled, collection).
 */
export function useOriginalLanguage(tmdbId: number | null | undefined, mediaType: string, enabled = true): string | null {
  const [code, setCode] = useState<string | null>(null)
  const needsFetch = enabled && (tmdbId ?? 0) > 0 && mediaType !== 'collection'

  useEffect(() => {
    if (!needsFetch) {
      setCode(null)
      return
    }
    let alive = true
    fetchCardMeta(tmdbId as number, mediaType)
      .then((meta) => { if (alive) setCode(meta.original_language) })
      .catch(() => { /* non-blocking: the card just omits the language */ })
    return () => { alive = false }
  }, [needsFetch, tmdbId, mediaType])

  return code
}

/**
 * Meta for every listed title with a TMDB id, keyed by tmdbMetaKey, for list-level filters.
 * Titles still loading or failed are absent. Fetches only while enabled, and re-renders once per
 * settled batch so filtered cards drop out together instead of one by one.
 */
export function useTmdbMetaMap(
  items: { tmdb_id: number | null | undefined; media_type: string | null }[],
  enabled: boolean,
): Map<string, TmdbCardMeta> {
  const [version, setVersion] = useState(0)
  // A string signature keeps the effect quiet when the caller rebuilds an identical list
  const signature = enabled
    ? items.flatMap((it) => ((it.tmdb_id ?? 0) > 0 && it.media_type ? [tmdbMetaKey(it.tmdb_id as number, it.media_type)] : [])).join('|')
    : ''
  const keys = useMemo(() => (signature ? Array.from(new Set(signature.split('|'))) : []), [signature])

  useEffect(() => {
    const missing = keys.filter((key) => !resolvedMeta.has(key))
    if (missing.length === 0) return
    let alive = true
    Promise.allSettled(missing.map((key) => {
      const sep = key.indexOf(':')
      return fetchCardMeta(Number(key.slice(sep + 1)), key.slice(0, sep))
    })).then(() => { if (alive) setVersion((v) => v + 1) })
    return () => { alive = false }
  }, [keys])

  return useMemo(() => {
    const map = new Map<string, TmdbCardMeta>()
    for (const key of keys) {
      const meta = resolvedMeta.get(key)
      if (meta) map.set(key, meta)
    }
    return map
  }, [keys, version])
}
