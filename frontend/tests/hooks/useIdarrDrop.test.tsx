import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  getMakerIdarrConfig: vi.fn(),
  quickAddFilesToIdarr: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('../../src/api/client', () => ({
  getMakerIdarrConfig: mocks.getMakerIdarrConfig,
  getApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}))
vi.mock('../../src/utils/idarrQuickAdd', () => ({
  quickAddFilesToIdarr: mocks.quickAddFilesToIdarr,
}))
vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mocks.showToast }),
}))

const TARGETS = [
  { personal_drive_id: 'a', source_dir: '/sync/a', label: 'Posters', scope_token: 'tok-a' },
  { personal_drive_id: 'b', source_dir: '/sync/b', label: 'Artwork', scope_token: 'tok-b', is_asset_drive: true },
]

// The scope store is module-level, so each test gets fresh modules.
async function loadHook() {
  vi.resetModules()
  return (await import('../../src/hooks/useIdarrDrop')).useIdarrDrop
}

const fileDrop = (files: File[] = [new File(['x'], 'Poster (2020).jpg', { type: 'image/jpeg' })]) => ({
  preventDefault: vi.fn(),
  stopPropagation: vi.fn(),
  dataTransfer: { types: ['Files'], files, dropEffect: 'none' },
}) as unknown as React.DragEvent

beforeEach(() => {
  localStorage.clear()
  mocks.getMakerIdarrConfig.mockResolvedValue({ sync_targets: TARGETS })
  mocks.quickAddFilesToIdarr.mockResolvedValue({ uploadedCount: 1, skippedCount: 0, jobId: 7, autoRenameError: null })
})

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

describe('useIdarrDrop', () => {
  it('drops into the shared scope and reports the add', async () => {
    localStorage.setItem('posterflow.idarr.selectedSyncTarget', 'scope:tok-b')
    const useIdarrDrop = await loadHook()
    const { result } = renderHook(() => useIdarrDrop())
    await waitFor(() => expect(result.current.enabled).toBe(true))
    expect(result.current.targetLabel).toBe('Artwork')

    const event = fileDrop()
    await act(async () => { result.current.dropProps.onDrop?.(event) })

    expect(mocks.quickAddFilesToIdarr).toHaveBeenCalledWith(1, event.dataTransfer.files)
    expect(mocks.showToast).toHaveBeenCalledWith('IDarr (Artwork): added 1 file(s)', 'success')
    expect(result.current.state).toBe('done')
    expect(result.current.dragOver).toBe(false)
  })

  it('a pinned target wins over the shared scope', async () => {
    localStorage.setItem('posterflow.idarr.selectedSyncTarget', 'scope:tok-b')
    const useIdarrDrop = await loadHook()
    const { result } = renderHook(() => useIdarrDrop({ syncTargetIndex: 0 }))
    await waitFor(() => expect(result.current.enabled).toBe(true))
    expect(result.current.targetLabel).toBe('Posters')

    await act(async () => { result.current.dropProps.onDrop?.(fileDrop()) })
    expect(mocks.quickAddFilesToIdarr).toHaveBeenCalledWith(0, expect.anything())
  })

  it('stays off when disabled, pinned to no scope, or there are no targets', async () => {
    mocks.getMakerIdarrConfig.mockResolvedValueOnce({ sync_targets: [] })
    const useIdarrDrop = await loadHook()
    const none = renderHook(() => useIdarrDrop())
    await waitFor(() => expect(mocks.getMakerIdarrConfig).toHaveBeenCalled())
    expect(none.result.current.enabled).toBe(false)
    expect(none.result.current.dropProps).toEqual({})

    const useIdarrDrop2 = await loadHook()
    const off = renderHook(() => useIdarrDrop2({ enabled: false }))
    const noScope = renderHook(() => useIdarrDrop2({ syncTargetIndex: null }))
    await waitFor(() => expect(mocks.getMakerIdarrConfig).toHaveBeenCalledTimes(2))
    expect(off.result.current.enabled).toBe(false)
    expect(noScope.result.current.enabled).toBe(false)
  })

  it('ignores drags that carry no files, so dragging a gallery image does not light the card', async () => {
    const useIdarrDrop = await loadHook()
    const { result } = renderHook(() => useIdarrDrop())
    await waitFor(() => expect(result.current.enabled).toBe(true))

    const imageDrag = { preventDefault: vi.fn(), dataTransfer: { types: ['text/uri-list'], files: [] } } as unknown as React.DragEvent
    act(() => { result.current.dropProps.onDragEnter?.(imageDrag) })
    expect(result.current.dragOver).toBe(false)
    expect(imageDrag.preventDefault).not.toHaveBeenCalled()

    const files = fileDrop()
    act(() => { result.current.dropProps.onDragEnter?.(files) })
    expect(result.current.dragOver).toBe(true)
    act(() => { result.current.dropProps.onDragLeave?.(files) })
    expect(result.current.dragOver).toBe(false)
  })

  it('toasts the failure and returns to idle when the add fails', async () => {
    mocks.quickAddFilesToIdarr.mockRejectedValueOnce(new Error('boom'))
    const useIdarrDrop = await loadHook()
    const { result } = renderHook(() => useIdarrDrop())
    await waitFor(() => expect(result.current.enabled).toBe(true))

    await act(async () => { result.current.dropProps.onDrop?.(fileDrop()) })
    expect(mocks.showToast).toHaveBeenCalledWith('Failed to add files to IDarr', 'error')
    expect(result.current.state).toBe('idle')
  })
})
