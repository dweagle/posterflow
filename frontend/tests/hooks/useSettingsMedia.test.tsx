import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useSettingsMedia } from '../../src/hooks/useSettingsMedia'

/**
 * Arr-less installs: the last Sonarr/Radarr card must be removable, and the
 * media-server media source defaults on exactly when no arr is configured.
 */

const saveBulkSettings = vi.fn().mockResolvedValue(undefined)

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>()
  return {
    ...actual,
    saveBulkSettings: (settings: Record<string, string>) => saveBulkSettings(settings),
    getPlexLibraryConfigs: vi.fn().mockResolvedValue({ configs: [] }),
  }
})

function renderMediaHook() {
  return renderHook(() =>
    useSettingsMedia({ showToast: vi.fn(), setSaving: vi.fn() })
  )
}

afterEach(() => {
  cleanup()
  saveBulkSettings.mockClear()
})

describe('useSettingsMedia arr-less support', () => {
  it('allows removing the last Sonarr and Radarr instances', () => {
    const { result } = renderMediaHook()
    expect(result.current.mediaSettings.sonarr_instances).toHaveLength(1)

    act(() => result.current.confirmRemoveSonarrInstance(0))
    act(() => result.current.confirmRemoveRadarrInstance(0))

    expect(result.current.mediaSettings.sonarr_instances).toHaveLength(0)
    expect(result.current.mediaSettings.radarr_instances).toHaveLength(0)
  })

  it('auto-enables the media source when no arr is configured', () => {
    const { result } = renderMediaHook()
    // Default blank cards have no url/api_key, so nothing is "configured"
    expect(result.current.mediaServerMediaSourceAuto).toBe(true)
    expect(result.current.mediaServerMediaSourceEnabled).toBe(true)
  })

  it('auto-disables the media source when an arr is configured', () => {
    const { result } = renderMediaHook()
    act(() => {
      result.current.setMediaSettings({
        plex_instances: [{ name: 'Plex', url: 'http://p', api_key: 't' }],
        sonarr_instances: [{ name: 'Sonarr', url: 'http://s', api_key: 'k' }],
        radarr_instances: [],
        media_server_media_source: '',
      })
    })
    expect(result.current.mediaServerMediaSourceAuto).toBe(true)
    expect(result.current.mediaServerMediaSourceEnabled).toBe(false)
  })

  it('explicit setting overrides the auto default', () => {
    const { result } = renderMediaHook()
    act(() => {
      result.current.setMediaSettings({
        plex_instances: [],
        sonarr_instances: [{ name: 'Sonarr', url: 'http://s', api_key: 'k' }],
        radarr_instances: [],
        media_server_media_source: 'true',
      })
    })
    expect(result.current.mediaServerMediaSourceAuto).toBe(false)
    expect(result.current.mediaServerMediaSourceEnabled).toBe(true)
  })

  it('toggling persists an explicit value', async () => {
    const { result } = renderMediaHook()
    expect(result.current.mediaServerMediaSourceEnabled).toBe(true)

    await act(async () => {
      await result.current.toggleMediaServerMediaSource()
    })

    expect(saveBulkSettings).toHaveBeenCalledWith({ media_server_media_source: 'false' })
    expect(result.current.mediaSettings.media_server_media_source).toBe('false')
    expect(result.current.mediaServerMediaSourceEnabled).toBe(false)
  })
})
