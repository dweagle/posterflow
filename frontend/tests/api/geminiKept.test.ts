import { describe, expect, it } from 'vitest'
import { keptToTmdbImage, withKeptImages, type KeptGeminiImage, type TmdbImagesResponse } from '../../src/api/makerTools'

const kept = (over: Partial<KeptGeminiImage> = {}): KeptGeminiImage => ({
  ref: 'gemini:tmdb-550/poster-abcdefabcdef.jpg', role: 'poster', source: 'tmdb', season: null, operation: 'remove_text',
  width: 1000, height: 1500, download_name: 'Fight Club (1999) {tmdb-550} - textless.jpg', url_full: '/f', url_thumb: '/f', ...over,
})
const img = (file_path: string) => ({ file_path, width: 10, height: 15, language: 'en', vote_average: 1, url_thumb: 't', url_full: 'f' })
const data = (): TmdbImagesResponse => ({ posters: [img('/a.jpg')], backdrops: [img('/b.jpg')], logos: [img('/l.png')] } as TmdbImagesResponse)

describe('withKeptImages', () => {
  it('pins kept results for the tab and season to the front of the matching list', () => {
    const out = withKeptImages(data(), [
      kept(),
      kept({ ref: 'gemini:tmdb-550/background-abcdefabcdef.jpg', role: 'background' }),
      kept({ ref: 'gemini:tmdb-550/logo-abcdefabcdef.png', role: 'logo' }),
      kept({ ref: 'gemini:tmdb-550/poster-111111111111.jpg', source: 'tvdb' }),       // other tab
      kept({ ref: 'gemini:tmdb-550/poster-222222222222.jpg', season: 2 }),           // season list only
    ], 'tmdb', null)
    expect(out.posters.map((p) => p.file_path)).toEqual(['gemini:tmdb-550/poster-abcdefabcdef.jpg', '/a.jpg'])
    expect(out.backdrops.map((p) => p.file_path)).toEqual(['gemini:tmdb-550/background-abcdefabcdef.jpg', '/b.jpg'])
    expect(out.logos.map((p) => p.file_path)).toEqual(['gemini:tmdb-550/logo-abcdefabcdef.png', '/l.png'])
    expect(out.posters[0].gemini).toBe(true)
    expect(out.posters[0].language).toBeNull()   // reads as TL in the grid
  })

  it('season lists only get that season, and nothing is pinned twice', () => {
    const season2 = kept({ ref: 'gemini:tmdb-550/poster-222222222222.jpg', season: 2 })
    const out = withKeptImages(data(), [kept(), season2], 'tmdb', 2)
    expect(out.posters.map((p) => p.file_path)).toEqual(['gemini:tmdb-550/poster-222222222222.jpg', '/a.jpg'])
    const again = withKeptImages(out, [kept(), season2], 'tmdb', 2)
    expect(again.posters.map((p) => p.file_path)).toEqual(['gemini:tmdb-550/poster-222222222222.jpg', '/a.jpg'])
  })

  it('returns the same object when nothing applies', () => {
    const d = data()
    expect(withKeptImages(d, [kept({ source: 'apple' })], 'tmdb', null)).toBe(d)
    expect(keptToTmdbImage(kept()).download_name).toContain('textless')
  })
})
