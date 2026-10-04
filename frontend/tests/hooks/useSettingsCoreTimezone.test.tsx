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

afterEach(() => {
  cleanup()
  getSettingsMock.mockReset()
  saveBulkSettingsMock.mockReset()
})

describe('useSettingsCore timezone', () => {
  it('reads the saved zone and the zone in force', async () => {
    getSettingsMock.mockResolvedValue({ timezone: '', effective_timezone: 'America/New_York' })
    const { result } = renderCore()

    await act(async () => {
      await result.current.fetchSettings()
    })

    expect(result.current.appTimezone).toBe('')
    expect(result.current.effectiveTimezone).toBe('America/New_York')
  })

  it('saves the zone and reads back the one the server resolved', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    getSettingsMock.mockResolvedValue({ timezone: 'Asia/Tokyo', effective_timezone: 'Asia/Tokyo' })
    const { result, showToast } = renderCore()

    let saved = false
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Asia/Tokyo')
    })

    expect(saved).toBe(true)
    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ timezone: 'Asia/Tokyo' })
    expect(result.current.appTimezone).toBe('Asia/Tokyo')
    expect(result.current.effectiveTimezone).toBe('Asia/Tokyo')
    expect(showToast).toHaveBeenCalledWith('Timezone saved!')
  })

  it('shows the server message when the zone is rejected', async () => {
    saveBulkSettingsMock.mockRejectedValue({ response: { data: { detail: 'Unknown timezone: Not/AZone' } } })
    const { result, showToast } = renderCore()

    let saved = true
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Not/AZone')
    })

    expect(saved).toBe(false)
    expect(result.current.appTimezone).toBe('')
    expect(showToast).toHaveBeenCalledWith('Unknown timezone: Not/AZone', 'error')
    expect(getSettingsMock).not.toHaveBeenCalled()
  })

  it('still reports a save when the read-back fails', async () => {
    saveBulkSettingsMock.mockResolvedValue(undefined)
    getSettingsMock.mockRejectedValue(new Error('offline'))
    const { result, showToast } = renderCore()

    let saved = false
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Asia/Tokyo')
    })

    expect(saved).toBe(true)
    expect(result.current.appTimezone).toBe('Asia/Tokyo')
    expect(showToast).toHaveBeenCalledTimes(1)
  })
})
