import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { getTmdbOverview } from '../../src/api/client'
import { resetCardMetaCache, tmdbMetaKey, useTmdbMetaMap } from '../../src/hooks/useCardOverview'

vi.mock('../../src/api/client', () => ({ getTmdbOverview: vi.fn() }))
const mocked = vi.mocked(getTmdbOverview)
const META = { overview: '', poster_url: null, original_language: 'ja', anime: true }

describe('useTmdbMetaMap', () => {
  beforeEach(() => {
    resetCardMetaCache()
    mocked.mockReset()
  })

  it('fetches each listed title once and keys the result by type and id', async () => {
    mocked.mockResolvedValue(META)
    const items = [
      { tmdb_id: 1429, media_type: 'tv' },
      { tmdb_id: 1429, media_type: 'tv' },     // a season of the same show
      { tmdb_id: null, media_type: 'movie' },  // id-less request
      { tmdb_id: 5, media_type: null },        // collection: nothing to ask
    ]
    const { result } = renderHook(() => useTmdbMetaMap(items, true))

    await waitFor(() => expect(result.current.get(tmdbMetaKey(1429, 'tv'))).toEqual(META))
    expect(mocked).toHaveBeenCalledTimes(1)
    expect(mocked).toHaveBeenCalledWith(1429, 'tv')
    expect(result.current.size).toBe(1)
  })

  it('does nothing while disabled', () => {
    const { result } = renderHook(() => useTmdbMetaMap([{ tmdb_id: 1, media_type: 'movie' }], false))
    expect(mocked).not.toHaveBeenCalled()
    expect(result.current.size).toBe(0)
  })

  it('leaves a failed lookup out so that title stays visible', async () => {
    mocked.mockRejectedValueOnce(new Error('down')).mockResolvedValue(META)
    const items = [{ tmdb_id: 1, media_type: 'movie' }, { tmdb_id: 2, media_type: 'movie' }]
    const { result } = renderHook(() => useTmdbMetaMap(items, true))

    await waitFor(() => expect(result.current.size).toBe(1))
    expect(result.current.has(tmdbMetaKey(2, 'movie'))).toBe(true)
  })

  it('serves an already resolved title on the first render without refetching', async () => {
    mocked.mockResolvedValue(META)
    const items = [{ tmdb_id: 7, media_type: 'movie' }]
    const first = renderHook(() => useTmdbMetaMap(items, true))
    await waitFor(() => expect(first.result.current.size).toBe(1))

    const second = renderHook(() => useTmdbMetaMap(items, true))
    expect(second.result.current.size).toBe(1)
    expect(mocked).toHaveBeenCalledTimes(1)
  })
})
