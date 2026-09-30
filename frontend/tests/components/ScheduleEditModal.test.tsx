import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import ScheduleEditModal from '../../src/components/settings/ScheduleEditModal'
import type { Schedule } from '../../src/api/client'

/**
 * A schedule's times are typed in the application zone; next_run comes back as an instant and
 * is rendered in the browser's zone. Those two disagree whenever the zones differ, so both
 * have to be named — an unlabelled timestamp is how "the job ran at the wrong hour" gets
 * reported as a bug.
 */

const zoneState = { browser: 'America/New_York' }

vi.mock('../../src/utils/datetime', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/utils/datetime')>()),
  browserTimeZone: () => zoneState.browser,
}))

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getMakerIdarrConfig: vi.fn().mockResolvedValue({ sync_targets: [] }),
  listWorkflows: vi.fn().mockResolvedValue([]),
}))

const NEXT_RUN = '2026-09-30T14:30:00Z'

const schedule: Schedule = {
  id: 1,
  job_type: 'gdrive_sync',
  name: 'Nightly',
  enabled: true,
  drive_id: null,
  drive_group: null,
  schedule_type: 'daily',
  schedule_value: '09:00',
  job_config: null,
  last_run: null,
  next_run: NEXT_RUN,
}

function renderModal(appTimezone: string, effectiveTimezone: string) {
  return render(
    <ScheduleEditModal
      editingSchedule={{ schedule, index: 0 }}
      drives={[]}
      scheduleSaving={false}
      appTimezone={appTimezone}
      effectiveTimezone={effectiveTimezone}
      updateScheduleField={vi.fn()}
      onClose={vi.fn()}
      onSave={vi.fn()}
    />,
  )
}

describe('ScheduleEditModal timezone labels', () => {
  beforeEach(() => {
    zoneState.browser = 'America/New_York'
  })
  afterEach(() => {
    cleanup()
  })

  it('renders next_run as a browser-local instant and names the zone it used', () => {
    renderModal('Europe/Amsterdam', 'Europe/Amsterdam')

    // Shared formatter: UTC instant in, browser-local string out.
    expect(screen.getByText(new Date(NEXT_RUN).toLocaleString())).toBeTruthy()
    expect(screen.getByText(/shown in your browser zone \(America\/New_York\)/)).toBeTruthy()
  })

  it('labels the typed times with the schedule zone and warns when the browser differs', () => {
    renderModal('Europe/Amsterdam', 'Europe/Amsterdam')

    expect(screen.getByText(/Schedule times are in Europe\/Amsterdam/)).toBeTruthy()
    expect(screen.getByText(/your browser is in America\/New_York/)).toBeTruthy()
  })

  it('falls back to the zone in force when nothing is stored, and stays quiet when they match', () => {
    zoneState.browser = 'Europe/Amsterdam'
    const { container } = renderModal('', 'Europe/Amsterdam')

    expect(screen.getByText(/Schedule times are in Europe\/Amsterdam/)).toBeTruthy()
    expect(container.textContent).not.toContain('your browser is in')
  })
})