import { describe, expect, it } from 'vitest'
import {
  COLLECTION_ALL_MOVIES_NOTE,
  ORIGINAL_LANGUAGE_NOTE,
  hasCollectionMoviesNote,
  hasRequestFlag,
  withRequestFlags,
} from '../../src/components/community/posterStyles'

describe('request flag note lines', () => {
  it('returns notes untouched when no flag is set', () => {
    expect(withRequestFlags('free text', {})).toBe('free text')
    expect(withRequestFlags(null, { originalLanguage: false })).toBeNull()
  })

  it('stacks flag lines above the free text', () => {
    expect(withRequestFlags('free text', { allCollectionMovies: true, originalLanguage: true }))
      .toBe(`${COLLECTION_ALL_MOVIES_NOTE}\n${ORIGINAL_LANGUAGE_NOTE}\n\nfree text`)
    expect(withRequestFlags(null, { originalLanguage: true })).toBe(ORIGINAL_LANGUAGE_NOTE)
  })

  it('keeps a Seasons line first', () => {
    const notes = withRequestFlags('Seasons: 1, 2\n\nfree text', { originalLanguage: true })
    expect(notes).toBe(`Seasons: 1, 2\n${ORIGINAL_LANGUAGE_NOTE}\n\nfree text`)
    expect(hasRequestFlag(notes, ORIGINAL_LANGUAGE_NOTE)).toBe(true)
  })

  it('only reads flags from the leading lines', () => {
    expect(hasRequestFlag(`free text\n${ORIGINAL_LANGUAGE_NOTE}`, ORIGINAL_LANGUAGE_NOTE)).toBe(false)
    expect(hasRequestFlag(`${COLLECTION_ALL_MOVIES_NOTE}\n\n${ORIGINAL_LANGUAGE_NOTE}`, ORIGINAL_LANGUAGE_NOTE)).toBe(false)
    expect(hasCollectionMoviesNote('collection', `${ORIGINAL_LANGUAGE_NOTE}\n${COLLECTION_ALL_MOVIES_NOTE}`)).toBe(true)
    expect(hasCollectionMoviesNote('movie', COLLECTION_ALL_MOVIES_NOTE)).toBe(false)
    expect(hasRequestFlag(null, ORIGINAL_LANGUAGE_NOTE)).toBe(false)
  })
})
