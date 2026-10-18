import { describe, expect, it } from 'vitest'
import { languageName } from '../../src/utils/language'

describe('languageName', () => {
  it('spells out a known ISO 639-1 code', () => {
    expect(languageName('ko')).toBe('Korean')
    expect(languageName('JA')).toBe('Japanese')
  })

  it('falls back to the upper-cased code when the runtime has no name for it', () => {
    expect(languageName('xx')).toBe('XX')
  })

  it('is empty for no code', () => {
    expect(languageName(null)).toBe('')
    expect(languageName('')).toBe('')
  })
})
