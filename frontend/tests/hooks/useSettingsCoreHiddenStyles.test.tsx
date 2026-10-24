import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useSettingsCore } from '../../src/hooks/useSettingsCore'
import { isPosterStyleHidden, resetHiddenPosterStylesCache } from '../../src/hooks/useHiddenPosterStyles'

const getSettingsMock = vi.hoisted(() => vi.fn())
const saveBulkSettingsMock = vi.hoisted(() => vi.fn())

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
  saveBulkSettings: (...args: unknown[]) => saveBulkSettingsMock(...args),
}))

function renderCore() {
  const showToast = vi.fn()
  const { result } = renderHook(() =>
    useSettingsCore({ showToast, setSaving: vi.fn(), setMediaSettings: vi.fn() })
  )
  return { result, showToast }
}

afterEach(() => {
  cleanup()
  getSettingsMock.mockReset()
  saveBulkSettingsMock.mockReset()
  resetHiddenPosterStylesCache()
})

describe('useSettingsCore hidden poster styles', () => {
  it('reads the saved list', async () => {
    getSettingsMock.mockResolvedValue({ hidden_poster_styles: '["MM2K"]' })
    const { result } = renderCore()

    await act(async () => { await result.current.fetchSettings() })

    expect(result.current.hiddenPosterStyles).toEqual(['MM2K'])
  })

  it('saves a hidden style and updates the shared cache', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result, showToast } = renderCore()

    await act(async () => { await result.current.handleToggleHiddenPosterStyle('MM2K', true) })

    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ hidden_poster_styles: '["MM2K"]' })
    expect(result.current.hiddenPosterStyles).toEqual(['MM2K'])
    expect(isPosterStyleHidden('MM2K')).toBe(true)
    expect(showToast).toHaveBeenCalledWith('MM2K hidden across the app')
  })

  it('refuses to hide the last visible style', async () => {
    getSettingsMock.mockResolvedValue({ hidden_poster_styles: '["MM2K"]' })
    const { result, showToast } = renderCore()
    await act(async () => { await result.current.fetchSettings() })

    await act(async () => { await result.current.handleToggleHiddenPosterStyle('CL2K', true) })

    expect(saveBulkSettingsMock).not.toHaveBeenCalled()
    expect(result.current.hiddenPosterStyles).toEqual(['MM2K'])
    expect(showToast).toHaveBeenCalledWith('One poster style has to stay visible', 'error')
  })
})
