import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, act } from '@testing-library/react'
import { useSettingsCore } from '../../src/hooks/useSettingsCore'

/**
 * Saving the timezone is the one write that re-points the scheduler, so the two failure modes
 * are pinned: a rejected POST must reach the user, and a zone the server stored but could not
 * apply must show up as the zone still in force rather than as a cheerful "saved".
 */

const getSettingsMock = vi.hoisted(() => vi.fn())
const saveBulkSettingsMock = vi.hoisted(() => vi.fn())

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getSettings: (...args: unknown[]) => getSettingsMock(...args),
  saveBulkSettings: (...args: unknown[]) => saveBulkSettingsMock(...args),
}))

function renderCore() {
  const showToast = vi.fn()
  const setSaving = vi.fn()
  const setMediaSettings = vi.fn()
  const { result } = renderHook(() =>
    useSettingsCore({ showToast, setSaving, setMediaSettings })
  )
  return { result, showToast }
}

afterEach(() => {
  cleanup()
  getSettingsMock.mockReset()
  saveBulkSettingsMock.mockReset()
})

describe('useSettingsCore timezone', () => {
  it('reads the stored zone and the zone in force', async () => {
    getSettingsMock.mockResolvedValue({ timezone: 'Europe/Amsterdam', effective_timezone: 'Europe/Amsterdam' })
    const { result } = renderCore()

    await act(async () => {
      await result.current.fetchSettings()
    })

    expect(result.current.appTimezone).toBe('Europe/Amsterdam')
    expect(result.current.effectiveTimezone).toBe('Europe/Amsterdam')
  })

  it('posts the zone on screen and reflects the zone the server resolved', async () => {
    getSettingsMock
      .mockResolvedValueOnce({ timezone: '', effective_timezone: 'UTC' })
      .mockResolvedValueOnce({ timezone: 'Asia/Tokyo', effective_timezone: 'Asia/Tokyo' })
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result, showToast } = renderCore()

    await act(async () => {
      await result.current.fetchSettings()
    })
    expect(result.current.effectiveTimezone).toBe('UTC')

    // The picker prefills the browser's zone, so the value saved is the one passed in, not
    // the stored state — and it has to be the value the picker is showing.
    let saved: boolean | undefined
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Asia/Tokyo')
    })

    expect(saveBulkSettingsMock).toHaveBeenCalledWith({ timezone: 'Asia/Tokyo' })
    expect(saved).toBe(true)
    expect(result.current.appTimezone).toBe('Asia/Tokyo')
    expect(result.current.effectiveTimezone).toBe('Asia/Tokyo')
    expect(showToast).toHaveBeenCalledWith('Timezone saved')
  })

  it('reports a zone the server stored but did not apply', async () => {
    // The bulk POST answers 200 even for garbage, so the mismatch is the only signal.
    getSettingsMock
      .mockResolvedValueOnce({ timezone: 'Europe/Amsterdam', effective_timezone: 'Europe/Amsterdam' })
      .mockResolvedValueOnce({ timezone: 'Not/AZone', effective_timezone: 'Europe/Amsterdam' })
    saveBulkSettingsMock.mockResolvedValue(undefined)
    const { result, showToast } = renderCore()

    await act(async () => {
      await result.current.fetchSettings()
    })
    let saved: boolean | undefined
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Not/AZone')
    })

    expect(saved).toBe(false)
    expect(showToast).toHaveBeenCalledWith(
      'Server rejected "Not/AZone" — still running in Europe/Amsterdam',
      'error',
    )
    // The bad zone is not adopted as the saved one.
    expect(result.current.appTimezone).toBe('Europe/Amsterdam')
    expect(result.current.effectiveTimezone).toBe('Europe/Amsterdam')
  })

  it('surfaces a rejected save instead of silently doing nothing', async () => {
    getSettingsMock.mockResolvedValue({ timezone: 'Europe/Amsterdam', effective_timezone: 'Europe/Amsterdam' })
    saveBulkSettingsMock.mockRejectedValue({ response: { data: { detail: 'Invalid timezone' } } })
    const { result, showToast } = renderCore()

    await act(async () => {
      await result.current.fetchSettings()
    })
    let saved: boolean | undefined
    await act(async () => {
      saved = await result.current.handleSaveAppTimezone('Not/AZone')
    })

    expect(saved).toBe(false)
    expect(showToast).toHaveBeenCalledWith('Invalid timezone', 'error')
    expect(result.current.appTimezone).toBe('Europe/Amsterdam')
  })
})