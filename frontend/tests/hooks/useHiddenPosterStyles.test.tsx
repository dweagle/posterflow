import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { getSettings } from '../../src/api/settings'
import {
  isPosterStyleHidden,
  parseHiddenPosterStyles,
  resetHiddenPosterStylesCache,
  setHiddenPosterStylesCache,
  useHiddenPosterStyles,
} from '../../src/hooks/useHiddenPosterStyles'

vi.mock('../../src/api/settings', () => ({ getSettings: vi.fn() }))
const mockedGetSettings = vi.mocked(getSettings)

describe('useHiddenPosterStyles', () => {
  beforeEach(() => {
    resetHiddenPosterStylesCache()
    mockedGetSettings.mockReset()
  })
  afterEach(() => {
    resetHiddenPosterStylesCache()
  })

  it('reads the hidden styles from settings once and shares them between readers', async () => {
    mockedGetSettings.mockResolvedValue({ hidden_poster_styles: '["MM2K"]' })
    const a = renderHook(() => useHiddenPosterStyles())
    const b = renderHook(() => useHiddenPosterStyles())

    await waitFor(() => expect(a.result.current.has('MM2K')).toBe(true))
    await waitFor(() => expect(b.result.current.has('MM2K')).toBe(true))
    expect(mockedGetSettings).toHaveBeenCalledTimes(1)
    expect(isPosterStyleHidden('mm2k')).toBe(true)
  })

  it('updates mounted readers when the settings page saves', async () => {
    mockedGetSettings.mockResolvedValue({})
    const { result } = renderHook(() => useHiddenPosterStyles())
    await waitFor(() => expect(mockedGetSettings).toHaveBeenCalled())

    act(() => { setHiddenPosterStylesCache(['MM2K']) })
    expect(result.current.has('MM2K')).toBe(true)
    act(() => { setHiddenPosterStylesCache([]) })
    expect(result.current.size).toBe(0)
  })

  it('treats a missing, malformed or non-list value as nothing hidden', () => {
    expect(parseHiddenPosterStyles(undefined).size).toBe(0)
    expect(parseHiddenPosterStyles('not json').size).toBe(0)
    expect(parseHiddenPosterStyles('{"a":1}').size).toBe(0)
    expect([...parseHiddenPosterStyles('["mm2k", 3]')]).toEqual(['MM2K'])
  })

  it('falls back to nothing hidden when settings cannot be read', async () => {
    mockedGetSettings.mockRejectedValue(new Error('offline'))
    const { result } = renderHook(() => useHiddenPosterStyles())
    await waitFor(() => expect(mockedGetSettings).toHaveBeenCalled())
    expect(result.current.size).toBe(0)
  })
})
