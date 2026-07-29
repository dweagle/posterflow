import { describe, expect, it } from 'vitest'
import {
  parseRange,
  conventionSuffix,
  seasonSuffixes,
  isYearRange,
  yearSuffixes,
  buildConventionItems,
  parseTagName,
  buildTagItems,
  baseIsCollection,
  MAX_RANGE,
  type Variant,
  type RawVariant,
  type TextSets,
} from '../../src/lib/photopeaBatch'

describe('parseRange', () => {
  it('treats a bare number as a count (1..N)', () => {
    expect(parseRange('8')).toEqual({ start: 1, end: 8 })
  })
  it('parses an explicit range including 0', () => {
    expect(parseRange('0-5')).toEqual({ start: 0, end: 5 })
    expect(parseRange('3-6')).toEqual({ start: 3, end: 6 })
  })
  it('blank means all', () => {
    expect(parseRange('')).toEqual({ start: null, end: null })
    expect(parseRange('   ')).toEqual({ start: null, end: null })
  })
  it('non-numeric is treated as all', () => {
    expect(parseRange('abc')).toEqual({ start: null, end: null })
  })
})

describe('conventionSuffix', () => {
  it('maps season / specials / base-poster roles', () => {
    expect(conventionSuffix({ t: 's', n: 1, p: [0] })).toBe(' - Season 1')
    expect(conventionSuffix({ t: 's', n: 0, p: [0] })).toBe(' - Specials')
    expect(conventionSuffix({ t: 'main', p: [0] })).toBe('')
    expect(conventionSuffix({ t: 'show', p: [0] })).toBe('')
    expect(conventionSuffix({ t: 'c', p: [0] })).toBe('')
  })
})

describe('seasonSuffixes (count/range mode)', () => {
  const seasons = [1, 2, 3, 4, 5]
  const withSpecials = [{ lab: 'SP' as const }, { lab: 'C' as const }, { lab: 'CLS' as const }]

  it('0-5 → seasons 1–5 + Specials, never Collection/CLS', () => {
    expect(seasonSuffixes(seasons, withSpecials, 0, 5)).toEqual([
      ' - Season 1', ' - Season 2', ' - Season 3', ' - Season 4', ' - Season 5', ' - Specials',
    ])
  })
  it('1-5 → seasons only (no Specials because 0 not in range)', () => {
    expect(seasonSuffixes(seasons, withSpecials, 1, 5)).toEqual([
      ' - Season 1', ' - Season 2', ' - Season 3', ' - Season 4', ' - Season 5',
    ])
  })
  it('blank/all → all seasons + Specials', () => {
    expect(seasonSuffixes(seasons, withSpecials, null, null)).toEqual([
      ' - Season 1', ' - Season 2', ' - Season 3', ' - Season 4', ' - Season 5', ' - Specials',
    ])
  })
  it('a subrange excludes out-of-range seasons and Specials', () => {
    expect(seasonSuffixes(seasons, withSpecials, 2, 4)).toEqual([
      ' - Season 2', ' - Season 3', ' - Season 4',
    ])
  })
  it('never emits Collection or Complete-Limited-Series suffixes', () => {
    const all = seasonSuffixes(seasons, withSpecials, null, null).join('|')
    // C → base name (''), CLS → ' - Season 1' — neither must appear from a single
    expect(all).not.toContain('Collection')
    // the only ' - Season 1' present is the real season 1, and there is exactly one
    expect(seasonSuffixes(seasons, withSpecials, null, null).filter((s) => s === ' - Season 1')).toHaveLength(1)
  })
})

describe('isYearRange', () => {
  it('treats 4-digit bounds as years', () => {
    expect(isYearRange(2015, 2020)).toBe(true)   // 2015-2020
    expect(isYearRange(1, 2020)).toBe(true)       // bare "2020" → start:1,end:2020
  })
  it('treats small counts/ranges as numbered seasons', () => {
    expect(isYearRange(1, 8)).toBe(false)         // "8"
    expect(isYearRange(2, 11)).toBe(false)        // "2-11"
    expect(isYearRange(0, 5)).toBe(false)         // "0-5"
    expect(isYearRange(null, null)).toBe(false)   // blank
  })
})

describe('yearSuffixes (Season YYYY mode)', () => {
  const years = [2015, 2016, 2017, 2018, 2019, 2020]

  it('a year range emits one "Season YYYY" per year, sorted', () => {
    expect(yearSuffixes(years, 2016, 2018)).toEqual([
      ' - Season 2016', ' - Season 2017', ' - Season 2018',
    ])
  })
  it('bare "2020" (start:1) yields every available year up to 2020', () => {
    expect(yearSuffixes(years, 1, 2020)).toEqual([
      ' - Season 2015', ' - Season 2016', ' - Season 2017',
      ' - Season 2018', ' - Season 2019', ' - Season 2020',
    ])
  })
  it('never adds Specials (years have none)', () => {
    expect(yearSuffixes(years, null, null).join('|')).not.toContain('Specials')
  })
})

describe('buildConventionItems', () => {
  // s1 at [0], s0 at [2], main at [3]; Season 1 text at [4,0], Season 2 at [4,1]; Specials at [5]
  const variants: Variant[] = [
    { t: 's', n: 1, p: [0] },
    { t: 's', n: 0, p: [2] },
    { t: 'main', p: [3] },
    { t: 'c', p: [6] },
  ]
  const seasonText = [{ n: 1, p: [4, 0] }, { n: 2, p: [4, 1] }]
  const specialsText = [{ p: [5] }]
  const items = buildConventionItems(variants, seasonText, specialsText)
  const byName = (suffix: string) => items.find((i) => i.suffix === suffix)!
  const on = (item: { changes: { p: number[]; v: boolean }[] }, p: number[]) => {
    const hit = [...item.changes].reverse().find((c) => JSON.stringify(c.p) === JSON.stringify(p))
    return hit?.v
  }

  it('suffixes each variant correctly', () => {
    expect(items.map((i) => i.suffix)).toEqual([' - Season 1', ' - Specials', '', ''])
  })
  it('s1 shows its layer + Season 1 text, hides Season 2 and Specials', () => {
    const it1 = byName(' - Season 1')
    expect(on(it1, [0])).toBe(true)         // s1 layer on
    expect(on(it1, [4, 0])).toBe(true)      // Season 1 text on
    expect(on(it1, [4, 1])).toBe(false)     // Season 2 text off
    expect(on(it1, [5])).toBe(false)        // Specials text off
    expect(on(it1, [2])).toBe(false)        // other convention leaf (s0) off
  })
  it('s0 shows Specials text and hides all season text', () => {
    const it0 = byName(' - Specials')
    expect(on(it0, [2])).toBe(true)         // s0 layer on
    expect(on(it0, [5])).toBe(true)         // Specials text on
    expect(on(it0, [4, 0])).toBe(false)     // Season 1 text off
    expect(on(it0, [4, 1])).toBe(false)     // Season 2 text off
  })
  it('main and collection are base posters — no season/specials text', () => {
    for (const item of items.filter((i) => i.suffix === '')) {
      expect(on(item, [4, 0])).toBe(false)
      expect(on(item, [4, 1])).toBe(false)
      expect(on(item, [5])).toBe(false)
    }
    expect(on(byName(''), [3])).toBe(true) // main's own layer on
  })
})

describe('parseTagName (jsx tag language)', () => {
  it('maps aliases (show/movie/poster/main and the PSD name) to one main tag', () => {
    for (const nm of ['show', 'movie', 'poster', 'main', 'from (2022)']) {
      const r = parseTagName(nm, 'from (2022)')
      expect(r.tags).toHaveLength(1)
      expect(r.tags[0].kind).toBe('main')
    }
  })
  it('parses single seasons, specials, c and cls', () => {
    expect(parseTagName('s3', '').tags[0]).toMatchObject({ kind: 's', n: 3 })
    expect(parseTagName('s0', '').tags[0]).toMatchObject({ kind: 's', n: 0 })
    expect(parseTagName('c', '').tags[0]).toMatchObject({ kind: 'c' })
    expect(parseTagName('cls', '').tags[0]).toMatchObject({ kind: 'cls', n: 1 })
  })
  it('expands ranges, including Specials from 0', () => {
    expect(parseTagName('s1-4', '').tags.map((t) => t.n)).toEqual([1, 2, 3, 4])
    expect(parseTagName('s0-2', '').tags.map((t) => t.n)).toEqual([0, 1, 2])
  })
  it('rejects backwards and oversized ranges with a reason', () => {
    expect(parseTagName('s5-3', '').reject).toMatch(/count upwards/)
    expect(parseTagName('s1-' + (MAX_RANGE + 2), '').reject).toMatch(/more than/)
    expect(parseTagName('s5-3', '').tags).toHaveLength(0)
  })
  it('season years work like seasons (s2027)', () => {
    expect(parseTagName('s2027', '').tags[0]).toMatchObject({ kind: 's', n: 2027 })
  })
  it('unknown names produce no tags and no reject', () => {
    const r = parseTagName('background art', '')
    expect(r.tags).toHaveLength(0)
    expect(r.reject).toBeNull()
  })
})

describe('baseIsCollection', () => {
  it('detects collection PSD names, ignoring {tmdb} tags', () => {
    expect(baseIsCollection('Back to the Future Collection')).toBe(true)
    expect(baseIsCollection('Back to the Future Collection {tmdb-264}')).toBe(true)
    expect(baseIsCollection('From (2022) {tmdb-124364}')).toBe(false)
  })
})

describe('buildTagItems', () => {
  const texts: TextSets = {
    season: [{ n: 1, p: [8, 0] }, { n: 2, p: [8, 1] }],
    specials: [{ p: [9] }],
    cls: [{ p: [10] }],
    collection: [{ p: [11] }],
  }
  const on = (item: { changes: { p: number[]; v: boolean }[] }, p: number[]) => {
    let last: boolean | undefined
    for (const ch of item.changes) if (JSON.stringify(ch.p) === JSON.stringify(p)) last = ch.v
    return last
  }

  it('sorts main → seasons ascending (cls beside s1) → specials → collection', () => {
    const variants: RawVariant[] = [
      { nm: 'c', p: [0], v: true },
      { nm: 's2', p: [1], v: true },
      { nm: 'show', p: [2], v: true },
      { nm: 's0', p: [3], v: true },
      { nm: 'cls', p: [4], v: true },
    ]
    const { items } = buildTagItems(variants, texts, 'From (2022)')
    expect(items.map((i) => i.suffix)).toEqual(['', ' - Season 1', ' - Season 2', ' - Specials', ' - Collection'])
  })

  it('a range layer exports once per season with the right text each time', () => {
    const variants: RawVariant[] = [{ nm: 's1-2', p: [0], v: true }]
    const { items, warnings } = buildTagItems(variants, texts, 'X')
    expect(items.map((i) => i.suffix)).toEqual([' - Season 1', ' - Season 2'])
    expect(on(items[0], [8, 0])).toBe(true)   // Season 1 text on for the first
    expect(on(items[0], [8, 1])).toBe(false)
    expect(on(items[1], [8, 1])).toBe(true)   // Season 2 text on for the second
    expect(on(items[1], [0])).toBe(true)      // same source layer visible both times
    expect(warnings).toHaveLength(0)
  })

  it('cls shows the CLS text (not season text) and names the Season 1 file', () => {
    const variants: RawVariant[] = [{ nm: 'cls', p: [0], v: true }]
    const { items } = buildTagItems(variants, texts, 'X')
    expect(items[0].suffix).toBe(' - Season 1')
    expect(on(items[0], [10])).toBe(true)
    expect(on(items[0], [8, 0])).toBe(false)
  })

  it('c shows the COLLECTION text, is flagged, and suffix depends on the PSD name', () => {
    const variants: RawVariant[] = [{ nm: 'c', p: [0], v: true }]
    const show = buildTagItems(variants, texts, 'From (2022)')
    expect(show.items[0].suffix).toBe(' - Collection')
    expect(show.items[0].collection).toBe(true)
    expect(on(show.items[0], [11])).toBe(true)
    const coll = buildTagItems(variants, texts, 'Back to the Future Collection {tmdb-264}')
    expect(coll.items[0].suffix).toBe('')
  })

  it('duplicate tags pick the topmost visible candidate and warn', () => {
    const variants: RawVariant[] = [
      { nm: 's1', p: [0], v: false },
      { nm: 's01', p: [1], v: true },
    ]
    const { items, warnings } = buildTagItems(variants, texts, 'X')
    expect(items).toHaveLength(1)
    expect(on(items[0], [1])).toBe(true)     // the visible one exported
    expect(on(items[0], [0])).toBe(false)
    expect(warnings.some((w) => /topmost visible/.test(w))).toBe(true)
  })

  it('warns when s1 and cls both exist (same output file)', () => {
    const variants: RawVariant[] = [
      { nm: 's1', p: [0], v: true },
      { nm: 'cls', p: [1], v: true },
    ]
    const { warnings } = buildTagItems(variants, texts, 'X')
    expect(warnings.some((w) => /s1.*cls|cls.*s1/i.test(w))).toBe(true)
  })

  it('rejected ranges surface as warnings, not items', () => {
    const variants: RawVariant[] = [{ nm: 's9-1', p: [0], v: true }]
    const { items, warnings } = buildTagItems(variants, texts, 'X')
    expect(items).toHaveLength(0)
    expect(warnings).toHaveLength(1)
  })
})
