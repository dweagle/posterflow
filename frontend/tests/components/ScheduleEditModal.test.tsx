import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

vi.mock('../../src/api/tools', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/tools')>()),
  getTools: vi.fn().mockResolvedValue({ groups: [
    { id: 'sonarr', label: 'Sonarr', instances: ['sonarr-1'], tools: [{ id: 'sonarr_rename', label: 'Rename Sonarr files', description: '', job_type: 'Tools', group: 'sonarr', per_instance: true, instances: ['sonarr-1', 'sonarr-4k'], confirm: 'Yes', dry_run: true, kinds: [], run_label: 'Run', available: true, reason: '' , profiles: [{ id: 10, name: 'Default', instance: 'sonarr-1', is_default: true, settings: {} }, { id: 12, name: 'Default', instance: 'sonarr-4k', is_default: true, settings: {} }, { id: 13, name: 'Slow', instance: 'sonarr-4k', is_default: false, settings: {} }], defaults: {}}] },
    { id: 'radarr', label: 'Radarr', instances: [], tools: [{ id: 'radarr_rename', label: 'Rename Radarr files', description: '', job_type: 'Tools', group: 'radarr', confirm: 'Yes', dry_run: true, kinds: [], run_label: 'Run', available: false, reason: 'No Radarr instance' , per_instance: true, instances: [], profiles: [], defaults: {}}] },
    { id: 'plex', label: 'Plex', instances: ['plex-1'], tools: [
      { id: 'plex_reset', label: 'Reset Plex images', description: '', job_type: 'Tools', group: 'plex', confirm: 'Yes', dry_run: true, kinds: ['poster', 'background', 'logo', 'squareart'], run_label: 'Reset', available: true, defaults: { kinds: ['poster', 'background', 'logo', 'squareart'] },
        profiles: [{ id: 1, name: 'Default', instance: null, is_default: true, settings: { kinds: ['poster', 'background', 'logo', 'squareart'] } }, { id: 2, name: 'Logos', instance: null, is_default: false, settings: { kinds: ['logo'] } }] },
      { id: 'jellyfin_cache', label: 'Clean Jellyfin image cache', description: '', job_type: 'Tools', group: 'jellyfin', confirm: null, dry_run: false, kinds: [], run_label: 'Run', available: true, profiles: [], defaults: null },
    ] },
  ] }),
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

describe('ScheduleEditModal tool schedules', () => {
  it('offers the Tools page tools and keeps the choice and the dry run in job_config', async () => {
    const updateScheduleField = vi.fn()
    render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"radarr_rename","dry_run":false}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const picker = (await screen.findByLabelText('Tool')) as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('radarr_rename'))
    expect(screen.getByRole('option', { name: 'Rename Radarr files (not set up)' })).toBeTruthy()
    fireEvent.change(picker, { target: { value: 'sonarr_rename' } })
    expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false}')
    fireEvent.click(screen.getByLabelText('Dry run only'))
    expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"radarr_rename","dry_run":true}')
  })

  it('lets a per-instance tool schedule one instance or all of them', async () => {
    const updateScheduleField = vi.fn()
    render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"sonarr_rename","dry_run":false}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const picker = (await screen.findByLabelText('Instance')) as HTMLSelectElement
    expect(picker.value).toBe('')  // every instance unless one is picked
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['All instances', 'sonarr-1', 'sonarr-4k'])
    // Every instance: each runs its own default profile, so there is no profile to pick.
    expect(screen.queryByLabelText('Profile')).toBeNull()
    expect(screen.getByText(/Every instance runs its own default profile/)).toBeTruthy()
    fireEvent.change(picker, { target: { value: 'sonarr-4k' } })
    expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false,"instance":"sonarr-4k"}')
    fireEvent.change(screen.getByLabelText('Tool'), { target: { value: 'plex_reset' } })
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"plex_reset","dry_run":false}')  // another tool: the instance goes
  })

  it('offers each instance its own profiles, and keeps a profile with the instance it belongs to', async () => {
    const updateScheduleField = vi.fn()
    const open = (job_config: string) => render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const { unmount } = open('{"tool_id":"sonarr_rename","dry_run":false,"instance":"sonarr-4k"}')
    const profile = (await screen.findByLabelText('Profile')) as HTMLSelectElement
    expect(within(profile).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)', 'Slow'])  // the 4K instance's own list
    expect(profile.value).toBe('12')
    expect(screen.getByText(/this profile of sonarr-4k/)).toBeTruthy()
    fireEvent.change(profile, { target: { value: '13' } })
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false,"profile_id":13,"instance":"sonarr-4k"}')
    unmount()

    // Another instance has other profiles: the stored one goes, and so it does with every instance.
    updateScheduleField.mockClear()
    const stored = '{"tool_id":"sonarr_rename","dry_run":false,"profile_id":13,"instance":"sonarr-4k"}'
    const second = open(stored)
    const instance = (await screen.findByLabelText('Instance')) as HTMLSelectElement
    expect(((await screen.findByLabelText('Profile')) as HTMLSelectElement).value).toBe('13')
    fireEvent.change(instance, { target: { value: 'sonarr-1' } })
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false,"instance":"sonarr-1"}')
    fireEvent.change(instance, { target: { value: '' } })
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false}')
    second.unmount()

    // A stored profile of another instance is dropped on opening, so the save is not refused over it.
    updateScheduleField.mockClear()
    open('{"tool_id":"sonarr_rename","dry_run":true,"profile_id":13,"instance":"sonarr-1"}')
    await screen.findByLabelText('Instance')
    await waitFor(() => expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":true,"instance":"sonarr-1"}'))
  })

  it('offers the profiles of a tool with settings and keeps the chosen one in job_config', async () => {
    const updateScheduleField = vi.fn()
    const { unmount } = render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"plex_reset","dry_run":false}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const picker = (await screen.findByLabelText('Profile')) as HTMLSelectElement
    expect(picker.value).toBe('1')  // none stored: the default profile
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)', 'Logos'])
    expect(screen.getByText(/this profile's saved settings/)).toBeTruthy()
    fireEvent.change(picker, { target: { value: '2' } })
    expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"plex_reset","dry_run":false,"profile_id":2}')
    expect(updateScheduleField).toHaveBeenCalledTimes(1)  // the stored config was fine as it was
    unmount()

    // The stored profile shows; a dry run keeps it; another tool drops it (its profiles are other ones).
    render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"plex_reset","dry_run":false,"profile_id":2}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const stored = (await screen.findByLabelText('Profile')) as HTMLSelectElement
    await waitFor(() => expect(stored.value).toBe('2'))
    fireEvent.click(screen.getByLabelText('Dry run only'))
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"plex_reset","dry_run":true,"profile_id":2}')
    fireEvent.change(screen.getByLabelText('Tool'), { target: { value: 'sonarr_rename' } })
    expect(updateScheduleField).toHaveBeenLastCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false}')
  })

  it('drops a stored profile that no longer exists, so the save is not refused, and shows no picker for a tool without profiles', async () => {
    const updateScheduleField = vi.fn()
    const { unmount } = render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"plex_reset","dry_run":true,"profile_id":99}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    const picker = (await screen.findByLabelText('Profile')) as HTMLSelectElement
    expect(picker.value).toBe('1')  // the default stands in
    await waitFor(() => expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"plex_reset","dry_run":true}'))
    unmount()

    render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"jellyfin_cache","dry_run":false}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={vi.fn()}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    await waitFor(() => expect((screen.getByLabelText('Tool') as HTMLSelectElement).value).toBe('jellyfin_cache'))
    expect(screen.queryByLabelText('Profile')).toBeNull()
  })

  it('stores the tool the picker shows when a schedule becomes a Tool one, so the save is not refused', async () => {
    const updateScheduleField = vi.fn()
    const { rerender } = render(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: null }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    await screen.findByLabelText('Tool')
    await waitFor(() => expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false}'))
    expect(updateScheduleField).toHaveBeenCalledTimes(1)
    // Switched from a workflow schedule: its workflow_id is not a tool either.
    updateScheduleField.mockClear()
    rerender(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"workflow_id":3}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    await waitFor(() => expect(updateScheduleField).toHaveBeenCalledWith('job_config', '{"tool_id":"sonarr_rename","dry_run":false}'))
    // A stored tool is left alone.
    updateScheduleField.mockClear()
    rerender(
      <ScheduleEditModal
        editingSchedule={{ schedule: { ...schedule, job_type: 'tools', job_config: '{"tool_id":"radarr_rename","dry_run":true}' }, index: 0 }}
        drives={[]}
        scheduleSaving={false}
        effectiveTimezone="America/New_York"
        updateScheduleField={updateScheduleField}
        onClose={vi.fn()}
        onSave={vi.fn()}
      />,
    )
    await waitFor(() => expect((screen.getByLabelText('Tool') as HTMLSelectElement).value).toBe('radarr_rename'))
    expect(updateScheduleField).not.toHaveBeenCalled()
  })
})
