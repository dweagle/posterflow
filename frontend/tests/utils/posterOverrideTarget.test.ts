import { describe, expect, it } from 'vitest'
import {
  libraryItemToTarget,
  overrideLabel,
  overrideMatchesTarget,
  overridePayloadFor,
  parsePosterName,
  pinnedFileBadges,
} from '../../src/utils/posterOverrideTarget'

describe('parsePosterName', () => {
  it.each([
    ['Breaking Bad (2008) {tmdb-1396} - Season 2', 'Breaking Bad (2008)', 2],
    ['Breaking Bad (2008)_Season03', 'Breaking Bad (2008)', 3],
    ['Breaking Bad (2008) - Specials', 'Breaking Bad (2008)', 0],
    ['Heat (1995) {imdb-tt0113277}.jpg', 'Heat (1995)', null],
    ['Season of the Witch (2011)', 'Season of the Witch (2011)', null],
    ['Breaking Bad (2008) {tmdb-1396} - background', 'Breaking Bad (2008)', null],
    ['Heat (1995) - Logo', 'Heat (1995)', null],
    ['Heat (1995) {tmdb-949} - squareart', 'Heat (1995)', null],
  ])('%s -> %s / season %s', (name, query, season) => {
    expect(parsePosterName(name)).toEqual({ query, season })
  })
})

describe('libraryItemToTarget', () => {
  const item = {
    media_type: 'show' as const, title: 'Breaking Bad', year: 2008, tmdb_id: 1396, tvdb_id: 81189,
    imdb_id: 'tt0903747', seasons: [0, 1], source: 'Sonarr',
  }

  it('carries the ids, the chosen season and the known seasons for shows only', () => {
    expect(libraryItemToTarget(item, 1)).toEqual({
      domain: 'poster', media_type: 'show', tmdb_id: 1396, tvdb_id: 81189, imdb_id: 'tt0903747',
      title: 'Breaking Bad', year: 2008, season: 1, seasons: [0, 1],
    })
    expect(libraryItemToTarget(item, 1, 'artwork')).toMatchObject({ domain: 'artwork', season: null, seasons: undefined })
    const movie = libraryItemToTarget({ ...item, media_type: 'movie' }, 1)
    expect(movie.season).toBeNull()
    expect(movie.seasons).toBeUndefined()
  })
})

describe('overrideLabel / pinnedFileBadges', () => {
  const base = { id: 1, media_type: 'show' as const, title: 'Breaking Bad', year: 2008, scope: 'slot' as const, drive_id: 'cl-1' }

  it('names what an override is pinned to', () => {
    expect(overrideLabel({ ...base, season: 2 })).toBe('Breaking Bad (2008) - Season 2')
    expect(overrideLabel({ ...base, season: 0 })).toBe('Breaking Bad (2008) - Specials')
    expect(overrideLabel({ ...base, scope: 'set' })).toBe('Breaking Bad (2008) - Whole set')
    expect(overrideLabel({ ...base, domain: 'artwork', slot: 'square' })).toBe('Breaking Bad (2008) - Square')
    expect(overrideLabel({ ...base, year: null, title: 'Jump Street', media_type: 'collection' })).toBe('Jump Street')
  })

  it('badges file picks by file and skips the excluded ids', () => {
    const badges = pinnedFileBadges([
      { ...base, id: 1, season: 2, file: '/d/a.jpg' },
      { ...base, id: 2, season: 3, file: '/d/a.jpg' },
      { ...base, id: 3, season: null, file: null },
      { ...base, id: 4, season: 1, file: '/d/b.jpg' },
    ], new Set([4]))
    expect(Object.keys(badges)).toEqual(['/d/a.jpg'])
    expect(badges['/d/a.jpg'].map((b) => b.text)).toEqual([
      'Pinned · Breaking Bad (2008) - Season 2',
      'Pinned · Breaking Bad (2008) - Season 3',
    ])
  })
})

describe('overrideMatchesTarget / overridePayloadFor', () => {
  const pick = { poster_name: 'x', drive_id: 'cl-1', drive_name: 'CL', file_path: '/d/x.jpg' }

  it('round-trips a payload back onto its target and keys by tmdb id first', () => {
    const target = libraryItemToTarget({
      media_type: 'show', title: 'Breaking Bad', year: 2008, tmdb_id: 1396, tvdb_id: 81189,
      imdb_id: 'tt0903747', seasons: [1, 2], source: 'Sonarr',
    }, 2)
    const payload = { id: 1, ...overridePayloadFor(target, pick) }
    expect(payload).toMatchObject({ scope: 'slot', domain: 'poster', season: 2, file: '/d/x.jpg', drive_id: 'cl-1' })
    expect(overrideMatchesTarget(payload, target)).toBe(true)
    expect(overrideMatchesTarget(payload, { ...target, season: null })).toBe(false)
    expect(overrideMatchesTarget({ ...payload, tmdb_id: 999 }, target)).toBe(false)
  })

  it('maps an artwork pick to the artwork domain and the piece matching its type', () => {
    const target = libraryItemToTarget({
      media_type: 'show', title: 'Breaking Bad', year: 2008, tmdb_id: 1396, tvdb_id: null, imdb_id: null,
      seasons: [1], source: 'Sonarr',
    }, 1)
    const artPick = { ...pick, artwork_type: 'squareart' as const }
    const payload = { id: 3, ...overridePayloadFor(target, artPick) }
    expect(payload).toMatchObject({ domain: 'artwork', scope: 'slot', slot: 'square', season: null, file: '/d/x.jpg' })
    expect(overrideMatchesTarget(payload, { ...target, domain: 'artwork' }, 'square')).toBe(true)
    expect(overrideMatchesTarget(payload, { ...target, domain: 'artwork' }, 'logo')).toBe(false)
    expect(overrideMatchesTarget(payload, target)).toBe(false)
  })

  it('falls back to title + year without ids', () => {
    const target = { media_type: 'movie' as const, title: 'Heat', year: 1995 }
    const payload = { id: 2, ...overridePayloadFor(target, pick) }
    expect(overrideMatchesTarget(payload, { ...target, title: 'heat ' })).toBe(true)
    expect(overrideMatchesTarget(payload, { ...target, year: 1996 })).toBe(false)
  })
})
