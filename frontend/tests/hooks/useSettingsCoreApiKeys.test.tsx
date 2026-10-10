import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useSettingsCore } from '../../src/hooks/useSettingsCore'

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

async function loadSettings(result: ReturnType<typeof renderCore>['result'], settings: Record<string, string>) {
  getSettingsMock.mockResolvedValue(settings)
  await act(async () => {
    await result.current.fetchSettings()
  })
}

afterEach(() => {
  cleanup()
  getSettingsMock.mockReset()
  saveBulkSettingsMock.mockReset()
})

describe('useSettingsCore API keys', () => {
  it('knows which keys are saved from the masked settings read', async () => {
    const { result } = renderCore()
    await loadSettings(result, {
      tmdb_api_key: '***masked***',
      tvdb_api_key: '***masked***',
      tvdb_pin: '',
      fanart_api_key: '',
    })

    expect(result.current.savedApiKeys).toEqual({ tmdb: true, tvdb: true, fanart: false })
    expect(result.current.tmdbApiKey).toBe('***masked***')
  })

  it('removes a saved key by writing a blank value', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result, showToast } = renderCore()
    await loadSettings(result, { fanart_api_key: '***masked***' })

    let removed = false
    await act(async () => {
      removed = await result.current.handleRemoveApiKey('fanart')
    })

    expect(removed).toBe(true)
    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ fanart_api_key: '' })
    expect(result.current.fanartApiKey).toBe('')
    expect(result.current.savedApiKeys.fanart).toBe(false)
    expect(showToast).toHaveBeenCalledWith('fanart.tv API key removed')
  })

  it('removes the TheTVDB PIN together with its key', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result } = renderCore()
    await loadSettings(result, { tvdb_api_key: '***masked***', tvdb_pin: '***masked***' })

    await act(async () => {
      await result.current.handleRemoveApiKey('tvdb')
    })

    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ tvdb_api_key: '', tvdb_pin: '' })
    expect(result.current.tvdbApiKey).toBe('')
    expect(result.current.tvdbPin).toBe('')
    expect(result.current.savedApiKeys.tvdb).toBe(false)
  })

  it('keeps the saved key when the removal request fails', async () => {
    saveBulkSettingsMock.mockRejectedValue(new Error('offline'))
    const { result, showToast } = renderCore()
    await loadSettings(result, { tmdb_api_key: '***masked***' })

    let removed = true
    await act(async () => {
      removed = await result.current.handleRemoveApiKey('tmdb')
    })

    expect(removed).toBe(false)
    expect(result.current.tmdbApiKey).toBe('***masked***')
    expect(result.current.savedApiKeys.tmdb).toBe(true)
    expect(showToast).toHaveBeenCalledWith('Failed to remove TMDB API key', 'error')
  })

  it('refuses to save a blank key and points at Remove when one is saved', async () => {
    const { result, showToast } = renderCore()
    await loadSettings(result, { tmdb_api_key: '***masked***' })

    let saved = true
    await act(async () => {
      saved = await result.current.handleSaveTmdbApiKey()
    })

    expect(saved).toBe(false)
    expect(saveBulkSettingsMock).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith('Enter a new key, or click Remove to clear the saved one.', 'info')
  })

  it('asks for a key when nothing is saved and nothing was typed', async () => {
    const { result, showToast } = renderCore()

    let saved = true
    await act(async () => {
      saved = await result.current.handleSaveFanartApiKey()
    })

    expect(saved).toBe(false)
    expect(saveBulkSettingsMock).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith('Enter a key first.', 'info')
  })

  it('marks a key saved after a successful save', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result } = renderCore()

    act(() => {
      result.current.setTmdbApiKey('abc123')
    })
    let saved = false
    await act(async () => {
      saved = await result.current.handleSaveTmdbApiKey()
    })

    expect(saved).toBe(true)
    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ tmdb_api_key: 'abc123' })
    expect(result.current.savedApiKeys.tmdb).toBe(true)
  })
})
