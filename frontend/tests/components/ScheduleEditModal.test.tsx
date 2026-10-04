import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import ScheduleEditModal from '../../src/components/settings/ScheduleEditModal'
import type { Schedule } from '../../src/api/client'

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
  next_run: null,
}

function renderModal(effectiveTimezone: string) {
  return render(
    <ScheduleEditModal
      editingSchedule={{ schedule, index: 0 }}
      drives={[]}
      scheduleSaving={false}
      effectiveTimezone={effectiveTimezone}
      updateScheduleField={vi.fn()}
      onClose={vi.fn()}
      onSave={vi.fn()}
    />,
  )
}

afterEach(() => {
  cleanup()
  zoneState.browser = 'America/New_York'
})

describe('ScheduleEditModal timezone note', () => {
  it('stays quiet when the browser is in the schedule timezone', () => {
    renderModal('America/New_York')

    expect(screen.queryByText(/Schedule times are in/)).toBeNull()
  })

  it('stays quiet when the browser reports an alias of the schedule timezone', () => {
    zoneState.browser = 'Asia/Calcutta'
    renderModal('Asia/Kolkata')

    expect(screen.queryByText(/Schedule times are in/)).toBeNull()
  })

  it('names both zones when they differ', () => {
    zoneState.browser = 'America/Chicago'
    renderModal('America/New_York')

    expect(screen.getByText('Schedule times are in America/New_York. Your browser is in America/Chicago.')).toBeTruthy()
  })
})
