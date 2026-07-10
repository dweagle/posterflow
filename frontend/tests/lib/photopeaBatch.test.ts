import { describe, expect, it } from 'vitest'
import {
  parseRange,
  conventionSuffix,
  seasonSuffixes,
  buildConventionItems,
  type Variant,
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
