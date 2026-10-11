import { describe, expect, it } from 'vitest'
import { AxiosError, AxiosHeaders } from 'axios'
import { isTmdbKeyMissingError } from '../../src/api/posterManager'

const axios400 = (detail: string) =>
  new AxiosError('Request failed', '400', undefined, undefined, {
    status: 400, statusText: 'Bad Request', headers: {}, config: { headers: new AxiosHeaders() }, data: { detail },
  })

describe('isTmdbKeyMissingError', () => {
  it('matches the search endpoint rejecting a missing key', () => {
    expect(isTmdbKeyMissingError(axios400('TMDB API key is not configured. Add it in Settings.'))).toBe(true)
  })

  it('ignores other 400s and non-axios errors', () => {
    expect(isTmdbKeyMissingError(axios400('Title is required'))).toBe(false)
    expect(isTmdbKeyMissingError(new Error('boom'))).toBe(false)
  })
})
