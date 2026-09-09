import { describe, it, expect } from 'vitest'
import { appleTvArtworkUrl, cleanAppleTvQuery, googleSearchUrl, tpdbSearchUrl } from '../../src/utils/searchLinks'

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

describe('cleanAppleTvQuery', () => {
  it.each([
    ['Spider-Man: No Way Home', 'Spider Man No Way Home'],
    ['Mission: Impossible – Dead Reckoning', 'Mission Impossible Dead Reckoning'],
    ["Schitt's Creek", 'Schitts Creek'],
    ['Schitt’s Creek', 'Schitts Creek'],
    ['M*A*S*H', 'MASH'],
    ['S.W.A.T.', 'SWAT'],
    ['Fast & Furious', 'Fast Furious'],
    ['Face/Off', 'Face Off'],
    ['  Mr.   Robot  ', 'Mr Robot'],
    ['Amélie', 'Amélie'],
    ['9-1-1', '9 1 1'],
  ])('%s → %s', (input, expected) => {
    expect(cleanAppleTvQuery(input)).toBe(expected)
  })
})

describe('appleTvArtworkUrl', () => {
  it('cleans the query and scopes movies and series', () => {
    expect(appleTvArtworkUrl('Spider-Man: No Way Home', '143441', 'movie'))
      .toBe('https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=Spider%20Man%20No%20Way%20Home&storefront=143441&type=movies')
    expect(appleTvArtworkUrl("Schitt's Creek", '143455', 'tv'))
      .toBe('https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=Schitts%20Creek&storefront=143455&type=tv')
  })

  it('leaves unknown types unscoped', () => {
    expect(appleTvArtworkUrl('Alien', '143441'))
      .toBe('https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=Alien&storefront=143441')
  })
})
