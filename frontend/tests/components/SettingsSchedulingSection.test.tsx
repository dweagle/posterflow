import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import SettingsSchedulingSection from '../../src/components/settings/SettingsSchedulingSection'

const zoneState = { browser: 'America/New_York' }

vi.mock('../../src/utils/datetime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/utils/datetime')>()),
  browserTimeZone: () => zoneState.browser,
}))

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getTimezones: vi.fn().mockResolvedValue(['UTC', 'America/New_York', 'Asia/Kolkata', 'Asia/Tokyo']),
}))

async function renderSection(appTimezone: string, effectiveTimezone: string, saving = false) {
  const onSaveAppTimezone = vi.fn()
  render(
    <SettingsSchedulingSection
      schedules={[]}
      onAddSchedule={vi.fn()}
      onToggleScheduleEnabled={vi.fn()}
      onEditSchedule={vi.fn()}
      onRemoveSchedule={vi.fn()}
      getScheduleSummary={() => null}
      appTimezone={appTimezone}
      effectiveTimezone={effectiveTimezone}
      onSaveAppTimezone={onSaveAppTimezone}
      saving={saving}
    />,
  )
  await screen.findByRole('option', { name: 'Asia/Tokyo' })
  const select = screen.getByLabelText('Schedule Timezone') as HTMLSelectElement
  const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
  return { onSaveAppTimezone, select, save }
}

afterEach(() => {
  cleanup()
  zoneState.browser = 'America/New_York'
})

describe('SettingsSchedulingSection timezone', () => {
  it('shows the host option and the zone in force when nothing is saved', async () => {
    const { select, save } = await renderSection('', 'America/New_York')

    expect(select.value).toBe('')
    expect(select.selectedOptions[0].textContent).toBe('Host timezone (TZ)')
    expect(screen.getByText(/Schedules currently run in America\/New_York\./)).toBeTruthy()
    expect(screen.queryByText(/Your browser is in/)).toBeNull()
    expect(save.disabled).toBe(true)
  })

  it('mentions the browser zone only when it differs from the zone in force', async () => {
    zoneState.browser = 'America/Chicago'
    await renderSection('', 'America/New_York')

    expect(screen.getByText(/Your browser is in America\/Chicago\./)).toBeTruthy()
  })

  it('does not treat an alias of the same zone as a different browser zone', async () => {
    zoneState.browser = 'Asia/Calcutta'
    await renderSection('Asia/Kolkata', 'Asia/Kolkata')

    expect(screen.queryByText(/Your browser is in/)).toBeNull()
  })

  it('lists the zones the server offers', async () => {
    const { select } = await renderSection('', 'UTC')

    expect(Array.from(select.options).map((option) => option.value)).toEqual(
      ['', 'UTC', 'America/New_York', 'Asia/Kolkata', 'Asia/Tokyo'],
    )
  })

  it('saves the picked zone', async () => {
    const { onSaveAppTimezone, select, save } = await renderSection('', 'UTC')

    fireEvent.change(select, { target: { value: 'Asia/Tokyo' } })
    expect(save.disabled).toBe(false)
    fireEvent.click(save)

    expect(onSaveAppTimezone).toHaveBeenCalledWith('Asia/Tokyo')
  })

  it('saves blank to go back to the host timezone', async () => {
    const { onSaveAppTimezone, select, save } = await renderSection('Asia/Tokyo', 'Asia/Tokyo')
    expect(select.value).toBe('Asia/Tokyo')

    fireEvent.change(select, { target: { value: '' } })
    fireEvent.click(save)

    expect(onSaveAppTimezone).toHaveBeenCalledWith('')
  })

  it('keeps a saved zone selected when the server no longer lists it', async () => {
    const { select } = await renderSection('US/Eastern', 'America/New_York')

    expect(select.value).toBe('US/Eastern')
  })

  it('disables Save while a save is running', async () => {
    const { select, save } = await renderSection('', 'UTC', true)

    fireEvent.change(select, { target: { value: 'Asia/Tokyo' } })

    expect(save.disabled).toBe(true)
  })
})
