import { describe, expect, it } from 'vitest'
import { posterLayerNames } from '../../src/api/makerTools'

describe('posterLayerNames (tag → export payload)', () => {
  it('aligns each poster to its tag, blank for untagged', () => {
    const posters = ['/a.jpg', '/b.jpg', '/c.jpg']
    const tags = { '/a.jpg': 's1', '/c.jpg': 's0' }
    expect(posterLayerNames(posters, tags)).toEqual(['s1', '', 's0'])
  })

  it('all untagged → all blank (default title-based names)', () => {
    expect(posterLayerNames(['/a.jpg', '/b.jpg'], {})).toEqual(['', ''])
  })

  it('empty selection → empty list', () => {
    expect(posterLayerNames([], { '/a.jpg': 's1' })).toEqual([])
  })

  it('preserves order so it maps to poster_paths index', () => {
    const posters = ['/x.jpg', '/y.jpg']
    expect(posterLayerNames(posters, { '/y.jpg': 'main', '/x.jpg': 'show' })).toEqual(['show', 'main'])
  })
})

describe('getLocalArtworkImageUrl', () => {
  it('is API_URL-prefixed (img tags resolve bare paths against the page origin) and URL-encoded', async () => {
    const { getLocalArtworkImageUrl } = await import('../../src/api/makerTools')
    const { API_URL } = await import('../../src/api/http')
    expect(getLocalArtworkImageUrl('backgrounds/Dune (2021) - background.jpg'))
      .toBe(`${API_URL}/api/artwork-finder/local-image?path=backgrounds%2FDune%20(2021)%20-%20background.jpg&source=folder`)
    // Paths only mean anything against their own root, so the source rides along.
    expect(getLocalArtworkImageUrl('film-square.jpg', 'bundled'))
      .toBe(`${API_URL}/api/artwork-finder/local-image?path=film-square.jpg&source=bundled`)
    expect(API_URL).not.toBe('')
  })
})

describe('defaultTextLogoFields', () => {
  it('uppercases the title, strips a trailing "Collection", and starts the suffix off', async () => {
    const { defaultTextLogoFields } = await import('../../src/api/makerTools')
    expect(defaultTextLogoFields({ title: 'James Bond Collection' }))
      .toEqual({ top: '', main: 'JAMES BOND', suffix: '' })
    expect(defaultTextLogoFields({ title: 'Dune' }))
      .toEqual({ top: '', main: 'DUNE', suffix: '' })
    expect(defaultTextLogoFields({ title: 'Collection' }).main).toBe('COLLECTION')
  })

  it('keeps the whole title on the main line — no auto-split to the top', async () => {
    const { defaultTextLogoFields } = await import('../../src/api/makerTools')
    expect(defaultTextLogoFields({ title: 'The Lord of the Rings Collection' }))
      .toEqual({ top: '', main: 'THE LORD OF THE RINGS', suffix: '' })
    expect(defaultTextLogoFields({ title: 'Star Wars: The Force Awakens' }))
      .toEqual({ top: '', main: 'STAR WARS: THE FORCE AWAKENS', suffix: '' })
  })
})

describe('reminderMatchesItem', () => {
  const base = {
    id: 1, kind: 'poster' as const, media_type: 'tv' as const, tmdb_id: 10, tvdb_id: null, imdb_id: null,
    title: 'Tulsa King', year: '2022', poster_url: null, homepage: null, note: '', created_at: null, updated_at: null,
  }

  it('matches on TMDB id within the same kind and media type', async () => {
    const { reminderMatchesItem } = await import('../../src/api/makerTools')
    const item = { tmdb_id: 10, media_type: 'tv' as const, title: 'Other', year: '' }
    expect(reminderMatchesItem(base, 'poster', item)).toBe(true)
    expect(reminderMatchesItem(base, 'artwork', item)).toBe(false)
    expect(reminderMatchesItem(base, 'poster', { ...item, media_type: 'movie' })).toBe(false)
    expect(reminderMatchesItem(base, 'poster', { ...item, tmdb_id: 11 })).toBe(false)
  })

  it('falls back to title + year only when neither side has a TMDB id', async () => {
    const { reminderMatchesItem } = await import('../../src/api/makerTools')
    const idless = { ...base, tmdb_id: null }
    expect(reminderMatchesItem(idless, 'poster', { tmdb_id: 0, media_type: 'tv', title: 'tulsa king', year: '2022' })).toBe(true)
    expect(reminderMatchesItem(idless, 'poster', { tmdb_id: 0, media_type: 'tv', title: 'Tulsa King', year: '2023' })).toBe(false)
    // An id on one side only never matches by title — that would fuse two different items.
    expect(reminderMatchesItem(idless, 'poster', { tmdb_id: 10, media_type: 'tv', title: 'Tulsa King', year: '2022' })).toBe(false)
    expect(reminderMatchesItem(base, 'poster', { tmdb_id: 0, media_type: 'tv', title: 'Tulsa King', year: '2022' })).toBe(false)
  })
})
