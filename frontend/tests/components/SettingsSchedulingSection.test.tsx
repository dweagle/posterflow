import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SettingsSchedulingSection from '../../src/components/settings/SettingsSchedulingSection'

/**
 * The timezone control is the only place a user can SET the application timezone, so the two
 * ways it can quietly lie are pinned here: an unset field that looks empty (the old
 * placeholder approach persisted "" and handed schedules back to the server's host zone) and
 * a dropdown that cannot render the value it is holding.
 */

const zoneState = {
  browser: 'America/New_York',
  options: ['Europe/Amsterdam', 'Asia/Tokyo'],
}

vi.mock('../../src/utils/datetime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/utils/datetime')>()),
  browserTimeZone: () => zoneState.browser,
  timeZoneOptions: () => zoneState.options,
}))

const noop = () => undefined
const FIELD = 'Application Timezone'

type Overrides = { appTimezone?: string; effectiveTimezone?: string }

function renderSection(overrides: Overrides = {}) {
  const onSaveAppTimezone = vi.fn()

  const Harness = () => (
      <SettingsSchedulingSection
        schedules={[]}
        onAddSchedule={noop}
        onToggleScheduleEnabled={noop}
        onEditSchedule={noop}
        onRemoveSchedule={noop}
        getScheduleSummary={() => null}
        appTimezone={overrides.appTimezone ?? ''}
        effectiveTimezone={overrides.effectiveTimezone ?? ''}
        onSaveAppTimezone={onSaveAppTimezone}
        saving={false}
      />
  )

  const { container } = render(<Harness />)
  return { container, onSaveAppTimezone }
}

describe('SettingsSchedulingSection timezone control', () => {
  beforeEach(() => {
    zoneState.browser = 'America/New_York'
    zoneState.options = ['Europe/Amsterdam', 'Asia/Tokyo']
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('prefills from the browser zone when nothing is saved, and saves that value untouched', async () => {
    // A placeholder would leave value === '' here and persist '' on save, silently reverting
    // to the server host zone.
    const { onSaveAppTimezone } = renderSection({ appTimezone: '', effectiveTimezone: 'UTC' })

    const control = screen.getByLabelText(FIELD) as HTMLSelectElement
    expect(control.value).toBe('America/New_York')
    expect(screen.getByText(/Prefilled from your browser zone \(America\/New_York\)/)).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Save Timezone' }))
    expect(onSaveAppTimezone).toHaveBeenCalledWith('America/New_York')
  })

  it('names the zone actually in force when it differs from the field', () => {
    // Berlin user, nothing saved, server on UTC: without this the field looks authoritative.
    renderSection({ appTimezone: '', effectiveTimezone: 'UTC' })
    expect(screen.getByText(/the server is running in UTC/)).toBeTruthy()
  })

  it('says nothing about a mismatch once the field matches the zone in force', () => {
    const { container } = renderSection({
      appTimezone: 'Europe/Amsterdam',
      effectiveTimezone: 'Europe/Amsterdam',
    })
    expect(container.textContent).not.toContain('the server is running in')
  })

  it('reports the zone picked from the list', async () => {
    const { onSaveAppTimezone } = renderSection({ appTimezone: '' })

    const control = screen.getByLabelText(FIELD)
    expect(control.tagName).toBe('SELECT')
    await userEvent.selectOptions(control, 'Asia/Tokyo')
    expect((control as HTMLSelectElement).value).toBe('Asia/Tokyo')

    await userEvent.click(screen.getByRole('button', { name: 'Save Timezone' }))
    expect(onSaveAppTimezone).toHaveBeenCalledWith('Asia/Tokyo')
  })

  it('keeps a saved zone that Intl does not list selectable', () => {
    // Legacy aliases and zones newer than the engine's database must not blank the control.
    renderSection({ appTimezone: 'US/Eastern' })
    const control = screen.getByLabelText(FIELD) as HTMLSelectElement
    expect(control.value).toBe('US/Eastern')
  })

  it('offers the host timezone so a saved zone can be cleared', async () => {
    // Once a zone is stored the field can never go blank again, so without an explicit
    // entry the host fallback the docs describe is only reachable before the first save.
    const { onSaveAppTimezone } = renderSection({
      appTimezone: 'Asia/Tokyo',
      effectiveTimezone: 'Asia/Tokyo',
    })

    await userEvent.selectOptions(screen.getByLabelText(FIELD), '')

    const control = screen.getByLabelText(FIELD) as HTMLSelectElement
    expect(control.value).toBe('')
    expect(screen.getByRole('option', { name: 'Use host timezone' })).toBeTruthy()
    expect(screen.getByText('Schedules will follow the host timezone.')).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: 'Save Timezone' }))
    expect(onSaveAppTimezone).toHaveBeenCalledWith('')
  })

  it('does not call the host choice a prefill or claim a mismatch against it', () => {
    // The zone in force IS the thing being changed away from, so naming it as a pending
    // mismatch would describe the current state as if it were the outcome.
    const { container } = renderSection({
      appTimezone: 'Asia/Tokyo',
      effectiveTimezone: 'Asia/Tokyo',
    })
    fireEvent.change(screen.getByLabelText(FIELD), { target: { value: '' } })

    expect(container.textContent).not.toContain('Prefilled from your browser zone')
    expect(container.textContent).not.toContain('the server is running in')
  })

  it('falls back to free text when Intl cannot list zones', async () => {
    zoneState.options = []
    const { onSaveAppTimezone } = renderSection({ appTimezone: '' })

    // No empty dropdown: a textbox, still prefilled, still editable.
    expect(screen.queryByRole('combobox')).toBeNull()
    const control = screen.getByLabelText(FIELD) as HTMLInputElement
    expect(control.tagName).toBe('INPUT')
    expect(control.value).toBe('America/New_York')

    // A blank field now means "use the host timezone" rather than snapping back, so the
    // host fallback stays reachable even when Intl cannot list zones.
    await userEvent.clear(control)
    expect(control.value).toBe('')

    fireEvent.change(control, { target: { value: 'Etc/UTC' } })
    expect(control.value).toBe('Etc/UTC')

    await userEvent.click(screen.getByRole('button', { name: 'Save Timezone' }))
    expect(onSaveAppTimezone).toHaveBeenCalledWith('Etc/UTC')
  })
})