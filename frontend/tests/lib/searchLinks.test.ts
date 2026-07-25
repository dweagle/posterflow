import { describe, it, expect } from 'vitest'
import { googleSearchUrl, tpdbSearchUrl } from '../../src/utils/searchLinks'

describe('tpdbSearchUrl', () => {
  // Series reach this helper as 'tv' (TMDB search results) or 'show' (unmatched/community APIs).
  it.each(['tv', 'show', 'season'])('scopes %s to the shows section', (mediaType) => {
    expect(tpdbSearchUrl('Catfish: The TV Show', mediaType))
      .toBe('https://theposterdb.com/search?term=Catfish%3A%20The%20TV%20Show&section=shows')
  })

  it('scopes movies and collections to their own sections', () => {
    expect(tpdbSearchUrl('Alien', 'movie')).toBe('https://theposterdb.com/search?term=Alien&section=movies')
    expect(tpdbSearchUrl('Alien Collection', 'collection'))
      .toBe('https://theposterdb.com/search?term=Alien%20Collection&section=collections')
  })

  it('leaves unknown or missing types unscoped', () => {
    expect(tpdbSearchUrl('Sigourney Weaver', 'person')).toBe('https://theposterdb.com/search?term=Sigourney%20Weaver')
    expect(tpdbSearchUrl('Alien')).toBe('https://theposterdb.com/search?term=Alien')
  })
})

describe('googleSearchUrl', () => {
  it('appends the year when present', () => {
    expect(googleSearchUrl('Alien', 1979)).toBe('https://www.google.com/search?q=Alien%201979')
    expect(googleSearchUrl('Alien')).toBe('https://www.google.com/search?q=Alien')
  })
})
