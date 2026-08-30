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

  it('persists a removal immediately and notifies the baseline', async () => {
    const onInstanceRemoved = vi.fn()
    const { result } = renderHook(() =>
      useSettingsMedia({ showToast: vi.fn(), setSaving: vi.fn(), onInstanceRemoved })
    )
    act(() => {
      result.current.setMediaSettings({
        plex_instances: [{ name: 'Plex', url: 'http://p', api_key: 't' }],
        sonarr_instances: [
          { name: 'S1', url: 'http://s1', api_key: 'k1' },
          { name: 'S2', url: 'http://s2', api_key: 'k2' },
        ],
        radarr_instances: [],
        media_server_media_source: '',
      })
    })

    await act(async () => result.current.confirmRemoveSonarrInstance(0))
    // Configured instance → goes through the confirm dialog first
    await act(async () => result.current.handleConfirmDelete())

    expect(result.current.mediaSettings.sonarr_instances).toHaveLength(1)
    expect(saveBulkSettings).toHaveBeenCalledWith({
      sonarr_instances: JSON.stringify([{ name: 'S2', url: 'http://s2', api_key: 'k2' }]),
    })
    expect(onInstanceRemoved).toHaveBeenCalledWith('sonarr_instances', 0)
  })

  it('reverts the removal when the save fails', async () => {
    saveBulkSettings.mockRejectedValueOnce(new Error('boom'))
    const { result } = renderMediaHook()
    act(() => {
      result.current.setMediaSettings({
        plex_instances: [],
        sonarr_instances: [{ name: 'S1', url: 'http://s1', api_key: 'k1' }],
        radarr_instances: [],
        media_server_media_source: '',
      })
    })

    await act(async () => result.current.confirmRemoveSonarrInstance(0))
    await act(async () => result.current.handleConfirmDelete())

    expect(result.current.mediaSettings.sonarr_instances).toHaveLength(1)
  })

  it('swaps a default name when the server type changes, but keeps custom names', () => {
    const { result } = renderMediaHook()
    // Default card is named "Plex" — switching type renames it
    act(() => result.current.updatePlexInstance(0, 'type', 'jellyfin'))
    expect(result.current.mediaSettings.plex_instances[0].name).toBe('Jellyfin')
    act(() => result.current.updatePlexInstance(0, 'type', 'plex'))
    expect(result.current.mediaSettings.plex_instances[0].name).toBe('Plex')

    // A user-entered name is never clobbered
    act(() => result.current.updatePlexInstance(0, 'name', 'Living Room'))
    act(() => result.current.updatePlexInstance(0, 'type', 'jellyfin'))
    expect(result.current.mediaSettings.plex_instances[0].name).toBe('Living Room')
  })

  it('numbers a swapped default name against siblings, not card position', () => {
    const { result } = renderMediaHook()
    act(() => {
      result.current.setMediaSettings({
        plex_instances: [
          { name: 'Plex', url: 'http://p', api_key: 't' },
          { name: 'Plex 2', url: '', api_key: '' },
        ],
        sonarr_instances: [],
        radarr_instances: [],
        media_server_media_source: '',
      })
    })
    // First Jellyfin gets the bare default even from the "Plex 2" card
    act(() => result.current.updatePlexInstance(1, 'type', 'jellyfin'))
    expect(result.current.mediaSettings.plex_instances[1].name).toBe('Jellyfin')
    // Switching back numbers against the existing "Plex"
    act(() => result.current.updatePlexInstance(1, 'type', 'plex'))
    expect(result.current.mediaSettings.plex_instances[1].name).toBe('Plex 2')
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
