import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useAppleTvStorefront } from '../../src/hooks/useAppleTvStorefront'

const mocks = vi.hoisted(() => ({
  getTmdbAppleStorefront: vi.fn(),
}))

vi.mock('../../src/api/client', () => ({
  getTmdbAppleStorefront: mocks.getTmdbAppleStorefront,
}))

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe('useAppleTvStorefront', () => {
  it('starts on the US store and resolves once on first intent', async () => {
    mocks.getTmdbAppleStorefront.mockResolvedValue({ storefront: '143444', iso: 'GB', sold_in: ['GB', 'AU'] })
    const { result } = renderHook(() => useAppleTvStorefront({ tmdb_id: 92588, media_type: 'tv' }))
    expect(result.current).toMatchObject({ storefront: '143441', iso: 'US', soldIn: null })

    act(() => { result.current.ensure(); result.current.ensure() })
    await waitFor(() => expect(result.current.iso).toBe('GB'))
    expect(result.current.storefront).toBe('143444')
    expect(result.current.soldIn).toEqual(['GB', 'AU'])
    expect(mocks.getTmdbAppleStorefront).toHaveBeenCalledTimes(1)
    expect(mocks.getTmdbAppleStorefront).toHaveBeenCalledWith(92588, 'tv')
  })

  it('never asks for collections or items without a TMDB id', () => {
    const a = renderHook(() => useAppleTvStorefront({ tmdb_id: 10, media_type: 'collection' }))
    const b = renderHook(() => useAppleTvStorefront({ tmdb_id: 0, media_type: 'movie' }))
    act(() => { a.result.current.ensure(); b.result.current.ensure() })
    expect(mocks.getTmdbAppleStorefront).not.toHaveBeenCalled()
  })

  it('keeps the US default when the lookup fails', async () => {
    mocks.getTmdbAppleStorefront.mockRejectedValue(new Error('boom'))
    const { result } = renderHook(() => useAppleTvStorefront({ tmdb_id: 550, media_type: 'movie' }))
    act(() => { result.current.ensure() })
    await waitFor(() => expect(mocks.getTmdbAppleStorefront).toHaveBeenCalledTimes(1))
    expect(result.current).toMatchObject({ storefront: '143441', iso: 'US', soldIn: null })
  })
})
