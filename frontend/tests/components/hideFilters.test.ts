import { describe, expect, it } from 'vitest'
import {
  isHiddenByMeta,
  isHiddenByStyle,
  loadHideFilters,
  NO_HIDE_FILTERS,
  saveHideFilters,
  tmdbMetaType,
} from '../../src/components/community/hideFilters'

const KEY = 'test.hideFilters'
const LEGACY = 'test.noMm2k'
const meta = (over: { anime?: boolean; original_language?: string | null }) => ({
  overview: '', poster_url: null, original_language: 'en', anime: false, ...over,
})

describe('hide filter storage', () => {
  it('starts clean without stored state', () => {
    expect(loadHideFilters(KEY, LEGACY)).toEqual(NO_HIDE_FILTERS)
  })

  it('migrates the old No MM2K flag', () => {
    localStorage.setItem(LEGACY, '1')
    expect(loadHideFilters(KEY, LEGACY)).toEqual({ ...NO_HIDE_FILTERS, mm2k: true })
  })

  it('round-trips the choices and clears the legacy key', () => {
    localStorage.setItem(LEGACY, '1')
    saveHideFilters(KEY, { mm2k: false, anime: true, nonEnglish: true }, LEGACY)
    expect(localStorage.getItem(LEGACY)).toBeNull()
    expect(loadHideFilters(KEY, LEGACY)).toEqual({ mm2k: false, anime: true, nonEnglish: true })
  })

  it('removes the key when nothing is hidden', () => {
    saveHideFilters(KEY, { ...NO_HIDE_FILTERS, anime: true })
    saveHideFilters(KEY, NO_HIDE_FILTERS)
    expect(localStorage.getItem(KEY)).toBeNull()
  })

  it('ignores malformed storage', () => {
    localStorage.setItem(KEY, '{nope')
    expect(loadHideFilters(KEY)).toEqual(NO_HIDE_FILTERS)
  })
})

describe('isHiddenByMeta', () => {
  const all = { mm2k: false, anime: true, nonEnglish: true }

  it('keeps a title the lookup has not classified visible', () => {
    expect(isHiddenByMeta(all, undefined)).toBe(false)
  })

  it('hides anime only when that filter is on', () => {
    expect(isHiddenByMeta({ ...NO_HIDE_FILTERS, anime: true }, meta({ anime: true }))).toBe(true)
    expect(isHiddenByMeta({ ...NO_HIDE_FILTERS, nonEnglish: true }, meta({ anime: true }))).toBe(false)
  })

  it('hides by original language, never by a missing one', () => {
    const f = { ...NO_HIDE_FILTERS, nonEnglish: true }
    expect(isHiddenByMeta(f, meta({ original_language: 'ja' }))).toBe(true)
    expect(isHiddenByMeta(f, meta({ original_language: 'en' }))).toBe(false)
    expect(isHiddenByMeta(f, meta({ original_language: null }))).toBe(false)
  })
})

describe('isHiddenByStyle', () => {
  it('hides an app-wide hidden style regardless of the menu', () => {
    expect(isHiddenByStyle('CL2K', NO_HIDE_FILTERS, new Set(['CL2K']))).toBe(true)
  })

  it('hides MM2K only through the menu, and never an untagged item', () => {
    const f = { ...NO_HIDE_FILTERS, mm2k: true }
    expect(isHiddenByStyle('MM2K', f, new Set())).toBe(true)
    expect(isHiddenByStyle('CL2K', f, new Set())).toBe(false)
    expect(isHiddenByStyle(null, f, new Set())).toBe(false)
  })
})

describe('tmdbMetaType', () => {
  it('maps shows and seasons to tv and leaves collections and people out', () => {
    expect(tmdbMetaType('movie')).toBe('movie')
    expect(tmdbMetaType('show')).toBe('tv')
    expect(tmdbMetaType('season')).toBe('tv')
    expect(tmdbMetaType('collection')).toBeNull()
    expect(tmdbMetaType('person')).toBeNull()
  })
})
