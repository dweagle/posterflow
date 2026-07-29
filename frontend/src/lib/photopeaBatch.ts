// Pure logic for the Photopea plugin's batch export — extracted here so it can be unit-tested.
// The plugin panels (frontend/public/photopea-plugin.html and photopea-posterflow/photopea-posterflow.html)
// run as self-contained HTML inside Photopea and can't import at runtime, so they carry inline copies of
// these same functions. Keep those copies in sync with this module; the tests here are the spec.

export type VariantType = 's' | 'main' | 'show' | 'c'
export interface Variant { t: VariantType; n?: number; p: number[] }
export interface TextLayer { n?: number; p: number[] }
export interface Single { lab: 'SP' | 'C' | 'CLS' }
export interface Change { p: number[]; v: boolean }
export interface BatchItem { suffix: string; changes: Change[] }

/** Parse the season-count/range input: "8" → seasons 1–8, "3-6" → that range, blank → all. */
export function parseRange(str: string): { start: number | null; end: number | null } {
  const s = (str || '').trim()
  if (!s) return { start: null, end: null }
  if (s.indexOf('-') >= 0) {
    const parts = s.split('-')
    const a = parseInt(parts[0], 10)
    const b = parseInt(parts[1], 10)
    return { start: isNaN(a) ? 1 : a, end: isNaN(b) ? 9999 : b }
  }
  const n = parseInt(s, 10)
  return isNaN(n) ? { start: null, end: null } : { start: 1, end: n }
}

/** Filename suffix for a convention variant: s0 → " - Specials", sN → " - Season N", else base ("" ). */
export const conventionSuffix = (v: Variant): string =>
  v.t === 's' ? (v.n === 0 ? ' - Specials' : ' - Season ' + v.n) : ''

/**
 * Season-count/range mode selection: the suffixes exported for a single poster's SEASONS group.
 * Seasons within [start,end]; Specials only when 0 is in range (or "all"); NEVER Collection / CLS.
 */
export function seasonSuffixes(
  seasonNumbers: number[],
  singles: Single[],
  start: number | null,
  end: number | null,
): string[] {
  const out: string[] = []
  seasonNumbers.slice().sort((a, b) => a - b).forEach((n) => {
    if (isNaN(n)) return
    if ((start != null && n < start) || (end != null && n > end)) return
    out.push(' - Season ' + n)
  })
  if ((start == null) || (start <= 0 && (end == null || end >= 0))) {
    if (singles.some((s) => s.lab === 'SP')) out.push(' - Specials')
  }
  return out
}

/** A batch range targets the year layers (Season YYYY) when its bounds look like 4-digit years. */
export function isYearRange(start: number | null, end: number | null): boolean {
  return (start != null && start >= 1900) || (end != null && end >= 1900)
}

/**
 * Season-YYYY mode: the suffixes for the year layers (Season 2015…) within [start,end]. Same range
 * filter as numbered seasons, so files match the "Season N" convention; never Specials/Collection.
 */
export function yearSuffixes(years: number[], start: number | null, end: number | null): string[] {
  const out: string[] = []
  years.slice().sort((a, b) => a - b).forEach((n) => {
    if (isNaN(n)) return
    if ((start != null && n < start) || (end != null && n > end)) return
    out.push(' - Season ' + n)
  })
  return out
}

const showText = (changes: Change[], t: TextLayer, on: boolean) => {
  changes.push({ p: t.p, v: on })
  if (on) for (let i = 1; i < t.p.length; i++) changes.push({ p: t.p.slice(0, i), v: true })
}

// ---------------------------------------------------------------------------
// Name-tag mode (amber bolt) — the tag language, ported from CL2K batch export.jsx
// ---------------------------------------------------------------------------

export type TagKind = 'main' | 's' | 'c' | 'cls'
export interface Tag { key: string; kind: TagKind; n: number; sort: number }
/** A candidate layer from the scan, in scan order (topmost first): normalized name + path + raw visible. */
export interface RawVariant { nm: string; p: number[]; v: boolean }
export interface TextSets { season: TextLayer[]; specials: TextLayer[]; cls: TextLayer[]; collection: TextLayer[] }
export interface TagItem { suffix: string; changes: Change[]; collection: boolean; key: string }

/** A range tag (s1-8) may expand to at most this many exports, so a typo can't flood a batch. */
export const MAX_RANGE = 30

export const normName = (s: string): string =>
  String(s).toLowerCase().replace(/^\s+|\s+$/g, '').replace(/\s+/g, ' ')

/** Does the PSD base name already denote a collection ("Title Collection", "Title Collection {tmdb-1}")?
 *  Then the c export needs no " - Collection" suffix — the name already says it. */
export const baseIsCollection = (base: string): boolean =>
  /collection$/i.test(String(base).replace(/\s*\{[^}]*\}\s*/g, ' ').replace(/\s+/g, ' ').trim())

const seasonTag = (n: number): Tag =>
  n === 0 ? { key: 's0', kind: 's', n: 0, sort: 100000 } : { key: 's' + n, kind: 's', n, sort: n }

/**
 * Parse one layer name into export tags. Aliases (show/movie/poster/main or the PSD's own name) → main;
 * s{N} → one season (0 = Specials); s{A}-{B} → one tag per season in the range (rejected with a reason
 * when backwards or wider than MAX_RANGE); c → Collection; cls → Complete Limited Series (Season 1 file).
 */
export function parseTagName(nm: string, baseNorm: string): { tags: Tag[]; reject: string | null } {
  if (nm === 'show' || nm === 'movie' || nm === 'poster' || nm === 'main' || (!!baseNorm && nm === baseNorm))
    return { tags: [{ key: 'show', kind: 'main', n: -1, sort: 0 }], reject: null }
  if (nm === 'c') return { tags: [{ key: 'c', kind: 'c', n: -1, sort: 200000 }], reject: null }
  if (nm === 'cls') return { tags: [{ key: 'cls', kind: 'cls', n: 1, sort: 1.5 }], reject: null }
  const m = /^s(\d{1,4})(?:-(\d{1,4}))?$/.exec(nm)
  if (m) {
    const start = parseInt(m[1], 10)
    if (!m[2]) return { tags: [seasonTag(start)], reject: null }
    const end = parseInt(m[2], 10)
    if (end <= start) return { tags: [], reject: '"' + nm + '" was skipped: a range has to count upwards, like "s' + start + '-' + (start + 1) + '"' }
    if (end - start + 1 > MAX_RANGE) return { tags: [], reject: '"' + nm + '" was skipped: it covers ' + (end - start + 1) + ' seasons, more than the ' + MAX_RANGE + ' a single range may expand to' }
    const out: Tag[] = []
    for (let v = start; v <= end; v++) out.push(seasonTag(v))
    return { tags: out, reject: null }
  }
  return { tags: [], reject: null }
}

/**
 * Turn scanned candidate layers into export items. Duplicate tags resolve to the topmost VISIBLE
 * candidate (topmost overall if none visible) with a warning. Items are sorted main → seasons
 * ascending (cls beside Season 1) → Specials → Collection. Each item shows its layer (+ ancestors),
 * hides every other candidate, and pairs the right text layer (Season N / Specials / Complete
 * Limited Series / Collection), hiding the rest. `collection: true` marks the c export so the panels
 * can run the collection-logo placement around it.
 */
export function buildTagItems(
  variants: RawVariant[],
  texts: TextSets,
  baseName: string,
): { items: TagItem[]; warnings: string[] } {
  const warnings: string[] = []
  const baseNorm = normName(baseName)
  const byKey: Record<string, { layer: RawVariant; tag: Tag }[]> = {}
  const keyOrder: string[] = []
  variants.forEach((rv) => {
    const parsed = parseTagName(rv.nm, baseNorm)
    if (parsed.reject) warnings.push(parsed.reject)
    parsed.tags.forEach((tag) => {
      if (!byKey[tag.key]) { byKey[tag.key] = []; keyOrder.push(tag.key) }
      byKey[tag.key].push({ layer: rv, tag })
    })
  })

  const chosen: { layer: RawVariant; tag: Tag }[] = []
  const seen: Record<string, boolean> = {}
  const warnedCombos: Record<string, boolean> = {}
  keyOrder.forEach((k) => {
    const cands = byKey[k]
    let pick = cands[0]
    if (cands.length > 1) {
      const vis = cands.find((c) => c.layer.v)
      if (vis) pick = vis
      const names: string[] = []
      cands.forEach((c) => { if (names.indexOf(c.layer.nm) < 0) names.push(c.layer.nm) })
      const combo = names.join('|')
      if (names.length > 1 && !warnedCombos[combo]) {
        warnedCombos[combo] = true
        warnings.push(cands.length + ' layers are tagged "' + names.join('" / "') + '" — exported ' +
          (vis ? 'the topmost visible one' : 'the topmost one (none were visible)') + ' and ignored the rest.')
      }
    }
    seen[k] = true
    chosen.push(pick)
  })
  if (seen['cls'] && seen['s1']) {
    warnings.push('Both "s1" and "cls" are tagged — they export to the same " - Season 1" file, so the later one overwrites the other.')
  }

  chosen.sort((a, b) => a.tag.sort - b.tag.sort)
  const collectionBase = baseIsCollection(baseName)

  const items = chosen.map(({ layer, tag }) => {
    const changes: Change[] = []
    variants.forEach((o) => changes.push({ p: o.p, v: false }))
    changes.push({ p: layer.p, v: true })
    for (let i = 1; i < layer.p.length; i++) changes.push({ p: layer.p.slice(0, i), v: true })
    const onOff = (list: TextLayer[], onFor: (t: TextLayer) => boolean) =>
      list.forEach((t) => (onFor(t) ? showText(changes, t, true) : changes.push({ p: t.p, v: false })))
    if (tag.kind === 's' && tag.n >= 1) {
      onOff(texts.season, (t) => t.n === tag.n)
      onOff(texts.specials, () => false); onOff(texts.cls, () => false); onOff(texts.collection, () => false)
    } else if (tag.kind === 's') {          // s0 = Specials
      onOff(texts.season, () => false); onOff(texts.specials, () => true)
      onOff(texts.cls, () => false); onOff(texts.collection, () => false)
    } else if (tag.kind === 'cls') {
      onOff(texts.season, () => false); onOff(texts.specials, () => false)
      onOff(texts.cls, () => true); onOff(texts.collection, () => false)
    } else if (tag.kind === 'c') {
      onOff(texts.season, () => false); onOff(texts.specials, () => false)
      onOff(texts.cls, () => false); onOff(texts.collection, () => true)
    } else {                                 // main
      onOff(texts.season, () => false); onOff(texts.specials, () => false)
      onOff(texts.cls, () => false); onOff(texts.collection, () => false)
    }
    const suffix = tag.kind === 'main' ? ''
      : tag.kind === 'c' ? (collectionBase ? '' : ' - Collection')
      : tag.kind === 'cls' ? ' - Season 1'
      : tag.n === 0 ? ' - Specials' : ' - Season ' + tag.n
    return { suffix, changes, collection: tag.kind === 'c', key: tag.key }
  })
  return { items, warnings }
}

/**
 * Parse the tags-mode filter input: blank → null (run everything); otherwise a set of tag keys —
 * tokens like "c", "cls", "main"/"show", "s3", "3", "s1-8" (comma/space separated).
 */
export function parseTagFilter(str: string): Record<string, 1> | null {
  const toks = String(str || '').toLowerCase().split(/[\s,]+/).filter(Boolean)
  if (!toks.length) return null
  const keys: Record<string, 1> = {}
  toks.forEach((t) => {
    if (t === 'c') keys.c = 1
    else if (t === 'cls') keys.cls = 1
    else if (t === 'main' || t === 'show' || t === 'movie' || t === 'poster') keys.show = 1
    else {
      const m = /^s?(\d{1,4})(?:-s?(\d{1,4}))?$/.exec(t)
      if (m) {
        const a = parseInt(m[1], 10)
        const b = m[2] ? parseInt(m[2], 10) : a
        const lo = Math.min(a, b), hi = Math.max(a, b)
        for (let n = lo; n <= hi && n - lo < 60; n++) keys['s' + n] = 1
      }
    }
  })
  return keys
}

/**
 * Convention mode (LEGACY shape, kept for the blue bolt's typed variants): for each s0/s1/…/main/show/c
 * variant, show its layer (+ ancestors), hide the other convention leaves, and switch on the matching
 * "Season N"/"Specials" text (hiding the rest). main/show/c hide all season & specials text.
 */
export function buildConventionItems(
  variants: Variant[],
  seasonText: TextLayer[],
  specialsText: TextLayer[],
): BatchItem[] {
  return variants.map((v) => {
    const changes: Change[] = []
    variants.forEach((o) => changes.push({ p: o.p, v: false }))
    changes.push({ p: v.p, v: true })
    for (let i = 1; i < v.p.length; i++) changes.push({ p: v.p.slice(0, i), v: true })
    if (v.t === 's' && (v.n ?? -1) >= 1) {
      seasonText.forEach((t) => showText(changes, t, t.n === v.n))
      specialsText.forEach((t) => changes.push({ p: t.p, v: false }))
    } else if (v.t === 's' && v.n === 0) {
      seasonText.forEach((t) => changes.push({ p: t.p, v: false }))
      specialsText.forEach((t) => showText(changes, t, true))
    } else {
      seasonText.forEach((t) => changes.push({ p: t.p, v: false }))
      specialsText.forEach((t) => changes.push({ p: t.p, v: false }))
    }
    return { suffix: conventionSuffix(v), changes }
  })
}
