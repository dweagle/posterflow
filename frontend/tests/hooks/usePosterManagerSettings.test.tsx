import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useRef, useState } from 'react'
import { usePosterManagerSettings } from '../../src/hooks/usePosterManagerSettings'
import type { PosterConfig } from '../../src/api/client'

/**
 * Saving a blank destination must land on the default, matching what the setup wizard
 * promises. The API ignores an empty destination instead of storing it, so a cleared
 * field would otherwise silently keep the old path.
 */

const savePosterConfig = vi.fn()

vi.mock('../../src/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../src/api/client')>()
  return {
    ...actual,
    savePosterConfig: (config: PosterConfig) => savePosterConfig(config),
    getPosterConfig: vi.fn(),
    getDrives: vi.fn(),
  }
})

const baseConfig = (destination: string): PosterConfig =>
  ({ destination, action_type: 'copy', asset_folders: true }) as PosterConfig

function useHarness(initial: PosterConfig) {
  const [config, setConfig] = useState<PosterConfig | null>(initial)
  const [, setDrives] = useState([])
  const [, setHasUnsavedChanges] = useState(false)
  const [, setSaving] = useState(false)
  const originalConfigRef = useRef<PosterConfig | null>(null)
  const api = usePosterManagerSettings({
    config,
    setConfig,
    setDrives: setDrives as never,
    originalConfigRef,
    setHasUnsavedChanges,
    setSaving,
    showToast: vi.fn(),
  })
  return { config, ...api }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('usePosterManagerSettings save', () => {
  it('saves a blank destination as the default', async () => {
    const { result } = renderHook(() => useHarness(baseConfig('')))

    await act(async () => {
      await result.current.handleSaveConfig()
    })

    expect(savePosterConfig).toHaveBeenCalledWith(
      expect.objectContaining({ destination: '/config/posters/assets' }),
    )
    // The field reflects what was actually stored, rather than staying blank.
    expect(result.current.config?.destination).toBe('/config/posters/assets')
  })

  it('treats a whitespace-only destination as blank', async () => {
    const { result } = renderHook(() => useHarness(baseConfig('   ')))

    await act(async () => {
      await result.current.handleSaveConfig()
    })

    expect(savePosterConfig).toHaveBeenCalledWith(
      expect.objectContaining({ destination: '/config/posters/assets' }),
    )
  })

  it('leaves a real destination untouched', async () => {
    const { result } = renderHook(() => useHarness(baseConfig('/kometa/config/assets')))

    await act(async () => {
      await result.current.handleSaveConfig()
    })

    expect(savePosterConfig).toHaveBeenCalledWith(
      expect.objectContaining({ destination: '/kometa/config/assets' }),
    )
  })

  it('keeps the other settings when resolving the default', async () => {
    const { result } = renderHook(() =>
      useHarness({ ...baseConfig(''), action_type: 'hardlink', asset_folders: false } as PosterConfig),
    )

    await act(async () => {
      await result.current.handleSaveConfig()
    })

    expect(savePosterConfig).toHaveBeenCalledWith(
      expect.objectContaining({ action_type: 'hardlink', asset_folders: false }),
    )
  })
})
