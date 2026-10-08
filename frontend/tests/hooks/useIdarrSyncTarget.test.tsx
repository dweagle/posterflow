import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  getMakerIdarrConfig: vi.fn(),
}))

vi.mock('../../src/api/client', () => ({
  getMakerIdarrConfig: mocks.getMakerIdarrConfig,
}))

const TARGETS = [
  { personal_drive_id: 'drive-a', source_dir: '/sync/a', label: 'Posters', scope_token: 'tok-a' },
  { personal_drive_id: 'drive-b', source_dir: '/sync/b', label: 'Artwork', scope_token: 'tok-b', is_asset_drive: true },
  { personal_drive_id: 'drive-c', source_dir: '/sync/c', label: 'PSDs', scope_token: 'tok-c', is_psd_drive: true },
  { personal_drive_id: 'drive-d', source_dir: '/sync/d', label: 'Posters 4K', scope_token: 'tok-d' },
]

// The store is module-level, so each test gets a fresh module.
async function loadModule() {
  vi.resetModules()
  return import('../../src/hooks/useIdarrSyncTarget')
}

beforeEach(() => {
  localStorage.clear()
  mocks.getMakerIdarrConfig.mockResolvedValue({ sync_targets: TARGETS })
})

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe('resolveSyncTargetIndex', () => {
  it('matches the scope id, and the pre-save drive/dir/label id of a target that since gained a token', async () => {
    const { resolveSyncTargetIndex } = await loadModule()
    expect(resolveSyncTargetIndex(TARGETS, 'scope:tok-b')).toBe(1)
    expect(resolveSyncTargetIndex(TARGETS, 'drive-b::/sync/b::Artwork')).toBe(1)
    expect(resolveSyncTargetIndex(TARGETS, 'scope:gone')).toBe(-1)
    expect(resolveSyncTargetIndex(TARGETS, null)).toBe(-1)
  })
})

describe('resolvePosterSyncTargetIndex', () => {
  it('keeps a stored poster drive and falls back to the first poster drive for artwork, PSD or nothing', async () => {
    const { resolvePosterSyncTargetIndex } = await loadModule()
    expect(resolvePosterSyncTargetIndex(TARGETS, 'scope:tok-d')).toBe(3)
    expect(resolvePosterSyncTargetIndex(TARGETS, 'scope:tok-b')).toBe(0)
    expect(resolvePosterSyncTargetIndex(TARGETS, 'scope:tok-c')).toBe(0)
    expect(resolvePosterSyncTargetIndex(TARGETS, null)).toBe(0)
    expect(resolvePosterSyncTargetIndex([TARGETS[1], TARGETS[2]], 'scope:tok-b')).toBe(-1)
  })
})

describe('useIdarrSyncTarget', () => {
  it('fetches the targets once for every consumer and falls back to the first target', async () => {
    const { useIdarrSyncTarget } = await loadModule()
    const a = renderHook(() => useIdarrSyncTarget())
    const b = renderHook(() => useIdarrSyncTarget())

    await waitFor(() => expect(a.result.current.loaded).toBe(true))
    expect(mocks.getMakerIdarrConfig).toHaveBeenCalledTimes(1)
    expect(a.result.current.options.map((o) => o.label)).toEqual(['Posters', 'Artwork', 'PSDs', 'Posters 4K'])
    expect(a.result.current.posterOptions.map((o) => o.label)).toEqual(['Posters', 'Posters 4K'])
    expect(a.result.current.selectedValue).toBe('scope:tok-a')
    expect(b.result.current.selectedValue).toBe('scope:tok-a')
  })

  it('shares one selection: picking in one consumer updates the others and localStorage', async () => {
    const { useIdarrSyncTarget, IDARR_SYNC_TARGET_STORAGE_KEY } = await loadModule()
    const sidebar = renderHook(() => useIdarrSyncTarget())
    const requests = renderHook(() => useIdarrSyncTarget())
    await waitFor(() => expect(sidebar.result.current.loaded).toBe(true))

    act(() => { sidebar.result.current.setSelectedValue('scope:tok-b') })

    expect(requests.result.current.selectedValue).toBe('scope:tok-b')
    expect(requests.result.current.selectedIndex).toBe(1)
    expect(requests.result.current.selectedLabel).toBe('Artwork')
    expect(localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)).toBe('scope:tok-b')
  })

  it('starts from the stored selection and follows a change made in another tab', async () => {
    localStorage.setItem('posterflow.idarr.selectedSyncTarget', 'scope:tok-b')
    const { useIdarrSyncTarget, IDARR_SYNC_TARGET_STORAGE_KEY } = await loadModule()
    const { result } = renderHook(() => useIdarrSyncTarget())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.selectedIndex).toBe(1)

    localStorage.setItem(IDARR_SYNC_TARGET_STORAGE_KEY, 'scope:tok-a')
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: IDARR_SYNC_TARGET_STORAGE_KEY, newValue: 'scope:tok-a' }))
    })
    expect(result.current.selectedIndex).toBe(0)
  })

  it('poster-only pickers skip artwork and PSD drives and fall back when the shared pick is one of them', async () => {
    const { useIdarrSyncTarget } = await loadModule()
    const { result } = renderHook(() => useIdarrSyncTarget())
    await waitFor(() => expect(result.current.loaded).toBe(true))

    act(() => { result.current.setSelectedValue('scope:tok-d') })
    expect(result.current.selectedPosterIndex).toBe(3)
    expect(result.current.selectedPosterValue).toBe('scope:tok-d')

    act(() => { result.current.setSelectedValue('scope:tok-b') })
    expect(result.current.selectedIndex).toBe(1)
    expect(result.current.selectedPosterIndex).toBe(0)
    expect(result.current.selectedPosterValue).toBe('scope:tok-a')

    act(() => { result.current.setSelectedValue('scope:tok-c') })
    expect(result.current.selectedIndex).toBe(2)
    expect(result.current.selectedPosterIndex).toBe(0)
    expect(result.current.posterOptions.map((o) => o.index)).toEqual([0, 3])
  })

  it('exposes no options and a -1 index when the config cannot be loaded', async () => {
    mocks.getMakerIdarrConfig.mockRejectedValue(new Error('offline'))
    const { useIdarrSyncTarget } = await loadModule()
    const { result } = renderHook(() => useIdarrSyncTarget())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.options).toEqual([])
    expect(result.current.posterOptions).toEqual([])
    expect(result.current.selectedIndex).toBe(-1)
    expect(result.current.selectedPosterIndex).toBe(-1)
    expect(result.current.selectedValue).toBe('')
    expect(result.current.selectedPosterValue).toBe('')
  })
})
