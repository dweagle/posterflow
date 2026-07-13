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

/**
 * Convention mode: for each s0/s1/…/main/show/c variant, show its layer (+ ancestors), hide the other
 * convention leaves, and switch on the matching "Season N"/"Specials" text (hiding the rest).
 * main/show/c hide all season & specials text.
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
