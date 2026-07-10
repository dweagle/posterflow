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
