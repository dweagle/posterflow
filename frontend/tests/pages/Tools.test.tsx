import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Tools from '../../src/pages/Tools'

const mockShowToast = vi.fn()
const mockNavigate = vi.fn()

vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => mockNavigate,
}))
const mockGetTools = vi.fn()
const mockRunTool = vi.fn()
const mockGetJob = vi.fn()
const mockGetPlexMetadataPaths = vi.fn()
const mockSavePlexMetadataPaths = vi.fn()
const mockCheckPlexMetadataPath = vi.fn()
const mockCreateToolProfile = vi.fn()
const mockUpdateToolProfile = vi.fn()
const mockDeleteToolProfile = vi.fn()
const mockGetRenameTagged = vi.fn()
const mockGetArrOptions = vi.fn()
const mockGetArrTitles = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: () => 'api error',
  getJob: (...args: unknown[]) => mockGetJob(...args),
}))

vi.mock('../../src/api/tools', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/tools')>()),
  getTools: (...args: unknown[]) => mockGetTools(...args),
  runTool: (...args: unknown[]) => mockRunTool(...args),
  getPlexMetadataPaths: (...args: unknown[]) => mockGetPlexMetadataPaths(...args),
  savePlexMetadataPaths: (...args: unknown[]) => mockSavePlexMetadataPaths(...args),
  checkPlexMetadataPath: (...args: unknown[]) => mockCheckPlexMetadataPath(...args),
  createToolProfile: (...args: unknown[]) => mockCreateToolProfile(...args),
  updateToolProfile: (...args: unknown[]) => mockUpdateToolProfile(...args),
  deleteToolProfile: (...args: unknown[]) => mockDeleteToolProfile(...args),
  getRenameTagged: (...args: unknown[]) => mockGetRenameTagged(...args),
  getArrOptions: (...args: unknown[]) => mockGetArrOptions(...args),
  getArrTitles: (...args: unknown[]) => mockGetArrTitles(...args),
}))

const renameDefaults = {
  count: 25, batch: false, tag_name: 'renamed', ignore_tag: 'rename-ignore', scope: 'files' as const, max_renames: 0, scan_to_limit: false, ignore_case: false, ignore_punctuation: false,
  allow_irregular: false, hold_tba: true, refresh_first: false, title_filter: '', notify: false, notify_all: false,
}
const ALL_KINDS = ['poster', 'background', 'logo', 'squareart']
const one = (id: number, settings: Record<string, unknown>, instance: string | null = null) => [{ id, name: 'Default', instance, is_default: true, settings }]
const groups = [
  { id: 'plex', label: 'Plex', instances: ['plex'], tools: [
    { id: 'plex_cleanup', label: 'Clean up Plex uploads', description: 'Removes superseded uploads.', job_type: 'Tools', group: 'plex', confirm: null, dry_run: true, kinds: ALL_KINDS, run_label: 'Clean', available: false, reason: 'Set the Plex Metadata folder for at least one server first.', profiles: one(1, { kinds: ALL_KINDS }), defaults: { kinds: ALL_KINDS } },
    { id: 'plex_reset_posters', label: 'Reset Plex images', description: 'Puts images back.', job_type: 'Tools', group: 'plex', confirm: 'Yes, reset them', dry_run: true, kinds: ALL_KINDS, run_label: 'Reset', available: true, reason: '', profiles: one(2, { kinds: ALL_KINDS }), defaults: { kinds: ALL_KINDS } },
  ] },
  { id: 'jellyfin', label: 'Jellyfin', instances: ['jf'], tools: [
    { id: 'jellyfin_clean_image_cache', label: 'Clean Jellyfin image cache', description: 'Asks the server to clean its cache.', job_type: 'Tools', group: 'jellyfin', confirm: null, dry_run: false, kinds: [], run_label: 'Run', available: true, reason: '', profiles: [], defaults: null },
    { id: 'jellyfin_reset_posters', label: 'Reset Jellyfin images', description: 'Deletes images and refetches.', job_type: 'Tools', group: 'jellyfin', confirm: 'Yes, reset them', dry_run: true, kinds: ['poster', 'background', 'logo'], run_label: 'Reset', available: true, reason: '', profiles: one(3, { kinds: ['poster', 'background', 'logo'] }), defaults: { kinds: ['poster', 'background', 'logo'] } },
  ] },
  { id: 'sonarr', label: 'Sonarr', instances: ['sonarr-1'], tools: [
    { id: 'sonarr_rename', label: 'Rename Sonarr files', description: 'Asks Sonarr to rename drifted files.', job_type: 'Tools', group: 'sonarr', confirm: 'Yes, rename them', dry_run: true, kinds: [], run_label: 'Run', available: true, reason: '', per_instance: true, instances: ['sonarr-1'], profiles: one(4, renameDefaults, 'sonarr-1'), defaults: renameDefaults },
  ] },
  { id: 'radarr', label: 'Radarr', instances: [], tools: [
    { id: 'radarr_rename', label: 'Rename Radarr files', description: 'Asks Radarr to rename drifted files.', job_type: 'Tools', group: 'radarr', confirm: 'Yes, rename them', dry_run: true, kinds: [], run_label: 'Run', available: false, reason: 'No Radarr instance is configured.', per_instance: true, instances: [] as string[], profiles: [] as ReturnType<typeof one>, defaults: renameDefaults },
  ] },
]

const renderPage = () => render(<MemoryRouter><Tools /></MemoryRouter>)

describe('Tools page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetTools.mockResolvedValue({ groups })
    mockGetPlexMetadataPaths.mockResolvedValue({ instances: [], paths: {} })
    mockUpdateToolProfile.mockImplementation(async (toolId: string, id: number, updates: { name?: string; settings?: Record<string, unknown> }) => {
      // A profile made during the test (id 9) is not in the fixture.
      const current = groups.flatMap((g) => g.tools).find((t) => t.id === toolId)!.profiles.find((p) => p.id === id) ?? { id, name: '', instance: null, is_default: false, settings: {} }
      return { ...current, ...(updates.name ? { name: updates.name } : {}), ...(updates.settings ? { settings: updates.settings } : {}) }
    })
    mockCreateToolProfile.mockImplementation(async (_toolId: string, name: string, settings: Record<string, unknown>, instance?: string) => ({ id: 9, name, instance: instance ?? null, is_default: false, settings }))
    mockDeleteToolProfile.mockResolvedValue(undefined)
    mockRunTool.mockResolvedValue({ success: true, job_id: 5, message: 'queued' })
    mockGetJob.mockResolvedValue({ id: 5, job_type: 'Overlays', status: 'completed', progress: 100, message: 'Poster reset complete: 4 reset, 0 failed', error: null })
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
  })

  it('lists one bundle per server type and gates the tool that is not ready', async () => {
    renderPage()
    const card = await screen.findByRole('region', { name: 'Clean up Plex uploads' })
    expect(within(screen.getByRole('region', { name: 'Plex tools' })).getByText('plex')).toBeTruthy()
    const jellyfin = screen.getByRole('region', { name: 'Jellyfin tools' })
    expect(within(jellyfin).getByText(/nothing to prune/)).toBeTruthy()
    expect(within(jellyfin).queryByRole('button', { name: 'Dry run' })).toBeTruthy()  // the reset offers one
    expect(within(screen.getByRole('region', { name: 'Clean Jellyfin image cache' })).queryByRole('button', { name: 'Dry run' })).toBeNull()
    expect((within(card).getByRole('button', { name: 'Clean' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByRole('button', { name: 'Dry run' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByRole('checkbox', { name: 'Logos' }) as HTMLInputElement).disabled).toBe(true)  // the kind switches sleep with the tool
    expect(within(card).getByText(/Set the Plex Metadata folder/)).toBeTruthy()
    const reset = screen.getByRole('region', { name: 'Reset Plex images' })
    expect((within(reset).getByRole('button', { name: 'Reset' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole('region', { name: 'Sonarr tools' })).toBeNull()  // lives on the Arr Renamer tab
  })

  it('offers the Scheduling and Discord shortcuts in the toolbar, like the other pages', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('button', { name: 'Scheduling' }))
    expect(localStorage.getItem('posterflow.settings.activeTab')).toBe('scheduling')
    expect(mockNavigate).toHaveBeenCalledWith('/settings')
    await user.click(screen.getByRole('button', { name: 'Discord' }))
    expect(localStorage.getItem('posterflow.settings.activeTab')).toBe('notifications')
  })

  it('switches to the Arr Renamer tab, remembers it, and shows only the arr cards there', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    const arrTab = screen.getByRole('tab', { name: 'Arr Renamer' })
    expect(arrTab.getAttribute('aria-selected')).toBe('false')
    await user.click(arrTab)
    expect(arrTab.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('region', { name: 'Sonarr tools' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Radarr tools' })).toBeNull()  // no instance: no card
    expect(screen.queryByRole('region', { name: 'Plex tools' })).toBeNull()
    expect(localStorage.getItem('posterflow.tools.activeTab')).toBe('arr')

    cleanup()
    renderPage()  // the saved tab comes back
    expect(await screen.findByRole('region', { name: 'Sonarr tools' })).toBeTruthy()
    await user.click(screen.getByRole('tab', { name: 'Media Servers' }))
    expect(await screen.findByRole('region', { name: 'Jellyfin tools' })).toBeTruthy()
  })

  it('tells the Arr Renamer tab apart when no arr is configured', async () => {
    const user = userEvent.setup()
    mockGetTools.mockResolvedValue({ groups: groups.map((g) => (g.id === 'sonarr' ? { ...g, instances: [] } : g)) })
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    expect(await screen.findByText(/No Sonarr or Radarr instance is configured/)).toBeTruthy()
  })

  it('gives every rename setting an info tip that does not touch the setting itself', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Sonarr rename settings' })
    const tips = card.querySelectorAll('.tools-field .toolbar-info')
    expect(tips).toHaveLength(15)  // Sonarr has the TBA setting too
    expect(within(card).getAllByLabelText(/Only items whose title contains this text/)[0]).toBeTruthy()
    // The tip sits inside the checkbox's label: clicking it leaves the box alone.
    const batches = within(card).getByLabelText('Whole library in batches') as HTMLInputElement
    expect(batches.checked).toBe(false)
    await user.click(within(card).getAllByLabelText(/Process the whole library in one run/)[0])
    expect(batches.checked).toBe(false)
  })

  // Two Radarr instances, each with a profile list of its own.
  const twoRadarrs = () => {
    const profiles = [...one(5, renameDefaults, 'Radarr'), ...one(6, { ...renameDefaults, count: 5 }, 'Radarr4K')]
    return { groups: groups.map((g) => (g.id === 'radarr' ? { ...g, instances: ['Radarr', 'Radarr4K'], tools: g.tools.map((t) => ({ ...t, available: true, reason: '', instances: ['Radarr', 'Radarr4K'], profiles })) } : g)) }
  }

  it('shows one settings block per instance, each with its own values', async () => {
    const user = userEvent.setup()
    mockGetTools.mockResolvedValue(twoRadarrs())
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Radarr rename settings' })
    expect(within(card).getByRole('group', { name: 'Radarr rename settings' })).toBeTruthy()
    expect((within(card).getByLabelText('Items per run (Radarr)') as HTMLInputElement).value).toBe('25')
    expect((within(card).getByLabelText('Items per run (Radarr4K)') as HTMLInputElement).value).toBe('5')
    // A single instance (Sonarr) needs no name on its fields.
    const sonarr = screen.getByRole('region', { name: 'Sonarr rename settings' })
    expect(within(sonarr).getByLabelText('Items per run')).toBeTruthy()
    // Sonarr can keep to TBA titles; movies have no episode titles, so Radarr is not offered it.
    expect(within(within(sonarr).getByLabelText('Rename')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Files', 'Folders', 'Files and folders', 'TBA titles only'])
    expect(within(within(card).getByLabelText('Rename (Radarr)')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Files', 'Folders', 'Files and folders'])
    expect(within(sonarr).getAllByLabelText(/TBA titles only: just the files whose episode title was TBA/).length).toBeGreaterThan(0)
    expect(within(card).queryByLabelText(/TBA titles only: just the files/)).toBeNull()
    // Radarr has no TBA setting: each of its blocks keeps an empty row in its place, so the two cards stay level.
    expect(card.querySelectorAll('.tools-check-spacer')).toHaveLength(2)
    expect(sonarr.querySelectorAll('.tools-check-spacer')).toHaveLength(0)
    expect(within(card).queryByLabelText(/Hold back TBA titles/)).toBeNull()
    expect(within(sonarr).queryByRole('heading', { level: 5 })).toBeNull()
    expect(within(sonarr).queryByRole('button', { name: /^Dry run / })).toBeNull()
    // Each named instance runs on its own, with its own profile: the run names both, the dry run too.
    mockRunTool.mockResolvedValue({ success: true, job_id: 77, message: 'queued' })
    expect(within(card).getByRole('button', { name: 'Run Radarr4K' })).toBeTruthy()
    await user.click(within(card).getByRole('button', { name: 'Dry run Radarr4K' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('radarr_rename', { dry_run: true, confirm: true, profile_id: 6, instance: 'Radarr4K' }))
    expect(mockShowToast).toHaveBeenCalledWith('Rename Radarr files on Radarr4K (dry run) started (Job ID: 77)', 'success')
    // The card's own buttons run every instance and say so; a single-instance card keeps the plain labels.
    const all = screen.getByRole('region', { name: 'Rename Radarr files' })
    expect(within(all).getByRole('button', { name: 'Run all' })).toBeTruthy()
    expect(within(all).getByRole('button', { name: 'Dry run all' })).toBeTruthy()
    const single = screen.getByRole('region', { name: 'Rename Sonarr files' })
    expect(within(single).getByRole('button', { name: 'Run' })).toBeTruthy()
  })

  it('keeps the profiles of each instance apart: its own list, its own save, its own run', async () => {
    const user = userEvent.setup()
    mockGetTools.mockResolvedValue(twoRadarrs())
    mockRunTool.mockResolvedValue({ success: true, job_id: 77, message: 'queued' })
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Radarr rename settings' })
    const hd = within(card).getByRole('group', { name: 'Radarr rename settings' })
    const uhd = within(card).getByRole('group', { name: 'Radarr4K rename settings' })
    expect((within(hd).getByLabelText('Radarr profile') as HTMLSelectElement).value).toBe('5')
    expect((within(uhd).getByLabelText('Radarr4K profile') as HTMLSelectElement).value).toBe('6')

    // An edit to one instance is that instance's alone: its Save lights, its runs wait, the other is untouched.
    const count = within(uhd).getByLabelText('Items per run (Radarr4K)') as HTMLInputElement
    await user.clear(count)
    await user.type(count, '3')
    expect((within(uhd).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false)
    expect((within(hd).getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByRole('button', { name: 'Run Radarr4K' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByRole('button', { name: 'Run Radarr' }) as HTMLButtonElement).disabled).toBe(false)
    const all = screen.getByRole('region', { name: 'Rename Radarr files' })
    expect((within(all).getByRole('button', { name: 'Run all' }) as HTMLButtonElement).title).toBe('Save the profile first')

    // A new profile from what is shown belongs to that instance, and becomes the one it shows.
    await user.click(within(uhd).getByRole('button', { name: 'New Radarr4K profile' }))
    await user.type(screen.getByLabelText('Profile name'), 'Slow{Enter}')
    await waitFor(() => expect(mockCreateToolProfile).toHaveBeenCalledWith('radarr_rename', 'Slow', { ...renameDefaults, count: 3 }, 'Radarr4K'))
    const picker = within(uhd).getByLabelText('Radarr4K profile') as HTMLSelectElement
    await waitFor(() => expect(picker.value).toBe('9'))
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)', 'Slow'])
    expect(within(within(hd).getByLabelText('Radarr profile')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)'])
    expect(localStorage.getItem('posterflow.tools.profile')).toBe('{"radarr_rename|Radarr4K":9}')

    // Run all: every instance with the profile it shows, named in the question and in the message.
    await user.click(within(all).getByRole('button', { name: 'Run all' }))
    expect(screen.getByText(/Profiles: Radarr: Default, Radarr4K: Slow\./)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Yes, rename them' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('radarr_rename', { dry_run: false, confirm: true, profiles: { Radarr: 5, Radarr4K: 9 } }))
    expect(mockShowToast).toHaveBeenCalledWith('Rename Radarr files (Radarr: Default, Radarr4K: Slow) started (Job ID: 77)', 'success')
  })

  it('opens the list of items carrying each tag from its field, once the tag is usable', async () => {
    const user = userEvent.setup()
    mockGetRenameTagged.mockImplementation(async (_tool: string, instance: string, tag: string) => ({ instance, tag, items: [{ id: 4, title: 'Delta', year: 2019, tagged: true }] }))
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Sonarr rename settings' })
    const manage = within(card).getByRole('button', { name: 'Manage ignored items' }) as HTMLButtonElement
    const tag = within(card).getByLabelText('Ignore tag') as HTMLInputElement
    await user.clear(tag)
    expect(manage.disabled).toBe(true)
    expect(manage.title).toBe('Set an ignore tag first')
    await user.type(tag, 'skip me')
    expect(manage.disabled).toBe(true)
    expect(manage.title).toBe('The ignore tag can only have letters, digits and -')
    await user.clear(tag)
    await user.type(tag, 'rename-ignore')
    await user.click(manage)
    const dialog = await screen.findByRole('dialog', { name: 'Ignored series' })
    expect(mockGetRenameTagged).toHaveBeenCalledWith('sonarr_rename', 'sonarr-1', 'rename-ignore')
    expect(await within(dialog).findByRole('checkbox', { name: 'Delta 2019' })).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Ignored series' })).toBeNull()

    // The cycle tag has its own button and list.
    const checked = within(card).getByRole('button', { name: 'Manage checked items' }) as HTMLButtonElement
    const cycle = within(card).getByLabelText('Cycle tag') as HTMLInputElement
    await user.clear(cycle)
    expect(checked.disabled).toBe(true)
    expect(checked.title).toBe('Set a cycle tag first')
    await user.type(cycle, 'renamed')
    await user.click(checked)
    expect(await screen.findByRole('dialog', { name: 'Checked series' })).toBeTruthy()
    expect(mockGetRenameTagged).toHaveBeenLastCalledWith('sonarr_rename', 'sonarr-1', 'renamed')
  })

  it('scans until the rename limit only once a limit is set, and then sets the count and batches aside', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Sonarr rename settings' })
    const scan = within(card).getByLabelText('Scan until the rename limit') as HTMLInputElement
    const count = within(card).getByLabelText('Items per run') as HTMLInputElement
    const batches = within(card).getByLabelText('Whole library in batches') as HTMLInputElement
    expect(scan.disabled).toBe(true)  // no rename limit yet
    const limit = within(card).getByLabelText('Rename limit') as HTMLInputElement
    await user.clear(limit)
    await user.type(limit, '5')
    expect(scan.disabled).toBe(false)
    expect(count.disabled).toBe(false)
    await user.click(scan)
    expect(scan.checked).toBe(true)
    expect(count.disabled).toBe(true)
    expect(batches.disabled).toBe(true)
    await user.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockUpdateToolProfile).toHaveBeenCalled())
    expect(mockUpdateToolProfile.mock.calls[0][2].settings).toMatchObject({ max_renames: 5, scan_to_limit: true })
    // A limit back at 0 sets the option aside again.
    await user.clear(limit)
    expect(scan.checked).toBe(false)
    expect(scan.disabled).toBe(true)
    expect(count.disabled).toBe(false)
  })

  it('edits and saves the Sonarr profile, then runs the saved profile after confirming', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const card = await screen.findByRole('region', { name: 'Sonarr rename settings' })
    expect((within(card).getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByLabelText('sonarr-1 profile') as HTMLSelectElement).value).toBe('4')
    const count = within(card).getByLabelText('Items per run') as HTMLInputElement
    expect(count.value).toBe('25')
    await user.clear(count)
    await user.type(count, '10')
    await user.selectOptions(within(card).getByLabelText('Rename'), 'both')
    const limit = within(card).getByLabelText('Rename limit') as HTMLInputElement
    await user.clear(limit)
    await user.type(limit, '5')
    await user.click(within(card).getByLabelText('Ignore capitals'))
    await user.click(within(card).getByLabelText('Hold back TBA titles'))
    await user.type(within(card).getByLabelText('Title filter'), 'Alien')
    await user.click(within(card).getByLabelText('Notify after runs'))
    // Unsaved edits: the run buttons wait for the save.
    const rename = screen.getByRole('region', { name: 'Rename Sonarr files' })
    const run = within(rename).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(run.title).toBe('Save the profile first')
    await user.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockUpdateToolProfile).toHaveBeenCalledWith('sonarr_rename', 4, {
      settings: { ...renameDefaults, count: 10, scope: 'both', max_renames: 5, ignore_case: true, hold_tba: false, title_filter: 'Alien', notify: true },
    }))
    expect(mockShowToast).toHaveBeenCalledWith('Profile Default of sonarr-1 saved', 'success')
    expect((within(card).getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true)
    expect((within(card).getByLabelText('Items per run') as HTMLInputElement).value).toBe('10')  // the saved profile shows

    // Reset to Defaults is a draft like any other edit; Cancel throws it away.
    await user.click(within(card).getByRole('button', { name: 'Reset to Defaults' }))
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect((within(card).getByLabelText('Items per run') as HTMLInputElement).value).toBe('25')
    expect((within(card).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false)
    await user.click(within(card).getByRole('button', { name: 'Cancel' }))
    expect((within(card).getByLabelText('Items per run') as HTMLInputElement).value).toBe('10')

    expect(run.disabled).toBe(false)
    await user.click(run)
    expect(mockRunTool).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Yes, rename them' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('sonarr_rename', { dry_run: false, confirm: true, profiles: { 'sonarr-1': 4 } }))
    expect(mockShowToast).toHaveBeenCalledWith('Rename Sonarr files started (Job ID: 5)', 'success')  // one profile: not named
  })

  it('runs a Jellyfin tool that needs no confirmation', async () => {
    const user = userEvent.setup()
    renderPage()
    const card = await screen.findByRole('region', { name: 'Clean Jellyfin image cache' })
    await user.click(within(card).getByRole('button', { name: 'Run' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('jellyfin_clean_image_cache', { dry_run: false, confirm: true }))
    expect(mockShowToast).toHaveBeenCalledWith('Clean Jellyfin image cache started (Job ID: 5)', 'success')
  })

  it('confirms a destructive run, then reports the outcome and unlocks the button', async () => {
    const user = userEvent.setup()
    renderPage()
    const reset = await screen.findByRole('region', { name: 'Reset Plex images' })
    await user.click(within(reset).getByRole('button', { name: 'Reset' }))
    expect(mockRunTool).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Yes, reset them' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('plex_reset_posters', { dry_run: false, confirm: true, profile_id: 2 }))
    expect(mockShowToast).toHaveBeenCalledWith('Reset Plex images started (Job ID: 5)', 'success')
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Reset Plex images finished: Poster reset complete: 4 reset, 0 failed', 'success'))
    await waitFor(() => expect(mockGetTools).toHaveBeenCalledTimes(2))  // availability refreshed after the job
    expect((within(reset).getByRole('button', { name: 'Reset' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('switches image types in the profile, saves it, and runs the saved profile', async () => {
    const user = userEvent.setup()
    renderPage()
    const reset = await screen.findByRole('region', { name: 'Reset Plex images' })
    const byKind = within(reset).getByRole('group', { name: 'Reset Plex images by image kind' })
    // A profile picker, one pair of buttons, one switch per kind under it, all on to start, and a Save pair.
    expect((within(byKind).getByLabelText('Reset Plex images profile') as HTMLSelectElement).value).toBe('2')
    expect(within(byKind).getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual([
      'New Reset Plex images profile', 'Rename Reset Plex images profile', 'Delete Reset Plex images profile', ' Reset', ' Dry run', 'SaveSaving…Saved', ' Cancel',
    ])
    const switches = within(byKind).getAllByRole('checkbox') as HTMLInputElement[]
    expect(switches.map((c) => c.getAttribute('aria-label'))).toEqual(['Posters', 'Backgrounds', 'Logos', 'Square art'])
    expect(switches.every((c) => c.checked)).toBe(true)
    expect(within(byKind).getByText('Image types')).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Clean Jellyfin image cache' })).queryByRole('group')).toBeNull()  // no kinds, no profiles there
    expect(within(screen.getByRole('region', { name: 'Reset Jellyfin images' })).queryByRole('checkbox', { name: 'Square art' })).toBeNull()
    // Leave only Logos on: the last switch stays on, the run waits for the save.
    await user.click(within(byKind).getByRole('checkbox', { name: 'Posters' }))
    await user.click(within(byKind).getByRole('checkbox', { name: 'Backgrounds' }))
    await user.click(within(byKind).getByRole('checkbox', { name: 'Square art' }))
    const logos = within(byKind).getByRole('checkbox', { name: 'Logos' }) as HTMLInputElement
    expect(logos.checked && logos.disabled).toBe(true)
    const resetBtn = within(byKind).getByRole('button', { name: 'Reset' }) as HTMLButtonElement
    expect(resetBtn.disabled).toBe(true)
    expect(resetBtn.title).toBe('Save the profile first')
    await user.click(within(byKind).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockUpdateToolProfile).toHaveBeenCalledWith('plex_reset_posters', 2, { settings: { kinds: ['logo'] } }))
    expect(mockShowToast).toHaveBeenCalledWith('Profile Default saved', 'success')
    // The reset asks first and names the profile and its kinds.
    await user.click(resetBtn)
    expect(mockRunTool).not.toHaveBeenCalled()
    expect(screen.getByText(/Profile: Default \(Logos\)\./)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Yes, reset them' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('plex_reset_posters', { dry_run: false, confirm: true, profile_id: 2 }))
    expect(mockShowToast).toHaveBeenCalledWith('Reset Plex images started (Job ID: 5)', 'success')
  })

  it('makes a new profile from the settings shown, renames it, switches back, and deletes it', async () => {
    const user = userEvent.setup()
    renderPage()
    const reset = await screen.findByRole('region', { name: 'Reset Plex images' })
    const byKind = within(reset).getByRole('group', { name: 'Reset Plex images by image kind' })
    const picker = within(byKind).getByLabelText('Reset Plex images profile') as HTMLSelectElement
    expect((within(byKind).getByRole('button', { name: 'Delete Reset Plex images profile' }) as HTMLButtonElement).disabled).toBe(true)  // the only one stays
    // New: the unsaved switches go into the new profile, which is then the one shown.
    await user.click(within(byKind).getByRole('checkbox', { name: 'Posters' }))
    await user.click(within(byKind).getByRole('button', { name: 'New Reset Plex images profile' }))
    expect(screen.getByRole('heading', { name: 'New Profile' })).toBeTruthy()
    await user.type(screen.getByLabelText('Profile name'), 'No posters{Enter}')
    await waitFor(() => expect(mockCreateToolProfile).toHaveBeenCalledWith('plex_reset_posters', 'No posters', { kinds: ['background', 'logo', 'squareart'] }, undefined))
    await waitFor(() => expect(picker.value).toBe('9'))
    expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)', 'No posters'])
    expect(mockUpdateToolProfile).not.toHaveBeenCalled()
    expect((within(byKind).getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true)  // nothing left unsaved
    expect((within(byKind).getByRole('checkbox', { name: 'Posters' }) as HTMLInputElement).checked).toBe(false)
    expect(localStorage.getItem('posterflow.tools.profile')).toBe('{"plex_reset_posters":9}')
    // Rename.
    await user.click(within(byKind).getByRole('button', { name: 'Rename Reset Plex images profile' }))
    const name = screen.getByLabelText('Profile name') as HTMLInputElement
    expect(name.value).toBe('No posters')
    await user.clear(name)
    await user.type(name, 'Art only')
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockUpdateToolProfile).toHaveBeenCalledWith('plex_reset_posters', 9, { name: 'Art only' }))
    await waitFor(() => expect(within(picker).getAllByRole('option').map((o) => o.textContent)).toEqual(['Default (default)', 'Art only']))
    // The catalog is re-read after a run: it holds the new profile too.
    mockGetTools.mockResolvedValue({ groups: groups.map((g) => (g.id !== 'plex' ? g : { ...g, tools: g.tools.map((t) => (t.id !== 'plex_reset_posters' ? t : {
      ...t, profiles: [...t.profiles, { id: 9, name: 'Art only', instance: null, is_default: false, settings: { kinds: ['background', 'logo', 'squareart'] } }],
    })) })) })
    // With two profiles a run names the one it uses.
    await user.click(within(byKind).getByRole('button', { name: 'Dry run' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('plex_reset_posters', { dry_run: true, confirm: true, profile_id: 9 }))
    expect(mockShowToast).toHaveBeenCalledWith('Reset Plex images (Art only) (dry run) started (Job ID: 5)', 'success')
    // Back to Default, then delete the new one.
    await user.selectOptions(picker, '2')
    expect((within(byKind).getByRole('checkbox', { name: 'Posters' }) as HTMLInputElement).checked).toBe(true)
    await user.selectOptions(picker, '9')
    await user.click(within(byKind).getByRole('button', { name: 'Delete Reset Plex images profile' }))
    expect(screen.getByText(/Profile Art only of Reset Plex images is removed/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(mockDeleteToolProfile).toHaveBeenCalledWith('plex_reset_posters', 9))
    await waitFor(() => expect(mockGetTools).toHaveBeenCalledTimes(3))  // once after the dry run's job, once after the delete
    expect(mockShowToast).toHaveBeenCalledWith('Profile Art only deleted', 'success')
    expect(localStorage.getItem('posterflow.tools.profile')).toBe('{}')
  })

  it('shows a tool that is still busy from before the page opened, and frees it when its job ends', async () => {
    mockGetTools.mockResolvedValue({ groups: groups.map((g) => (g.id === 'sonarr' ? { ...g, tools: g.tools.map((t) => ({ ...t, active_job: 9 })) } : g)) })
    mockGetJob.mockResolvedValueOnce({ id: 9, job_type: 'Tools', status: 'running', progress: 40, message: 'Sonarr rename: Alpha', error: null })
      .mockResolvedValue({ id: 9, job_type: 'Tools', status: 'completed', progress: 100, message: 'Rename Sonarr files complete', error: null })
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await waitFor(() => expect(mockGetJob).toHaveBeenCalledWith(9))  // watched as soon as the catalog arrives, whatever tab is open
    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    const rename = await screen.findByRole('region', { name: 'Rename Sonarr files' })
    expect((within(rename).getByRole('button', { name: 'Running…' }) as HTMLButtonElement).disabled).toBe(true)
    expect(mockRunTool).not.toHaveBeenCalled()
    // The next poll finds it finished: the outcome is reported and the button comes back.
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Rename Sonarr files finished: Rename Sonarr files complete', 'success'), { timeout: 5000 })
    expect((within(rename).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect(mockGetJob).toHaveBeenCalledTimes(2)
  })

  it('runs a dry run without asking', async () => {
    const user = userEvent.setup()
    renderPage()
    const reset = await screen.findByRole('region', { name: 'Reset Plex images' })
    await user.click(within(reset).getByRole('button', { name: 'Dry run' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('plex_reset_posters', { dry_run: true, confirm: true, profile_id: 2 }))
    expect(mockShowToast).toHaveBeenCalledWith('Reset Plex images (dry run) started (Job ID: 5)', 'success')
  })

  it('takes a Plex Metadata folder per server, checks it, and saves it', async () => {
    const user = userEvent.setup()
    mockGetPlexMetadataPaths.mockResolvedValue({ instances: ['plex'], paths: {} })
    mockCheckPlexMetadataPath.mockResolvedValue({ ok: true, reason: 'Connected to plex and found the Metadata folder.', verified_title: 'Show' })
    mockSavePlexMetadataPaths.mockImplementation(async (paths: Record<string, string>) => ({ success: true, paths }))
    renderPage()
    const card = await screen.findByRole('region', { name: 'Plex Metadata folders' })
    expect((within(card).getByRole('button', { name: 'Saved' }) as HTMLButtonElement).disabled).toBe(true)
    await user.type(within(card).getByLabelText('plex'), '/plex/Metadata')
    await user.click(within(card).getByRole('button', { name: 'Check' }))
    await waitFor(() => expect(mockCheckPlexMetadataPath).toHaveBeenCalledWith('plex', '/plex/Metadata'))
    expect(await within(card).findByText(/Connected to plex and found the Metadata folder/)).toBeTruthy()
    await user.click(within(card).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockSavePlexMetadataPaths).toHaveBeenCalledWith({ plex: '/plex/Metadata' }))
    expect(mockShowToast).toHaveBeenCalledWith('Plex Metadata folders saved', 'success')
    await waitFor(() => expect(mockGetTools).toHaveBeenCalledTimes(2))  // availability re-read after the save
  })

  it('folds the Plex setup tips under the Metadata folders', async () => {
    const user = userEvent.setup()
    mockGetPlexMetadataPaths.mockResolvedValue({ instances: ['plex'], paths: {} })
    renderPage()
    const card = await screen.findByRole('region', { name: 'Plex Metadata folders' })
    const details = card.querySelector('details.tools-setup') as HTMLDetailsElement
    expect(details.open).toBe(false)
    const body = details.querySelector('.tools-setup-body') as HTMLElement
    expect(body.textContent).toMatch(/EXTRA_GROUPS/)
    expect(body.textContent).toMatch(/umask to 002/)
    expect(body.textContent).toMatch(/Application Support/)
    await user.click(within(card).getByText('Setup tips'))
    expect(details.open).toBe(true)
  })

  // The upgrade search tools beside the rename ones: one Sonarr instance, two Radarr instances.
  const upgradeDefaults = {
    count: 5, mode: 'all' as const, search_unit: 'seasons' as const, season_threshold: 50, tag_name: 'upgrade-searched', ignore_tag: 'upgrade-ignore',
    monitored: 'yes' as const, only_tags: '', quality_profiles: '', min_age_days: 0, max_grabs: 0,
    skip_queued: true, title_filter: '', notify: false, notify_grabs_only: false,
  }
  const withUpgraders = () => {
    const tool = (group: string, label: string, instances: string[], profiles: unknown[]) => ({
      id: `${group}_upgrade`, label: `Search ${label} for upgrades`, description: `Has ${label} search for upgrades.`, job_type: 'Tools', group, confirm: 'Yes, search', destructive: false, dry_run: true, kinds: [] as string[],
      run_label: 'Search', available: true, reason: '', per_instance: true, instances, profiles, defaults: upgradeDefaults, active_job: null,
    })
    const radarrs = ['Radarr', 'Radarr4K']
    return { groups: groups.map((g) => {
      if (g.id === 'sonarr') return { ...g, tools: [...g.tools, tool('sonarr', 'Sonarr', ['sonarr-1'], one(41, upgradeDefaults, 'sonarr-1'))] }
      if (g.id === 'radarr') {
        return { ...g, instances: radarrs, tools: [
          ...g.tools.map((t) => ({ ...t, available: true, reason: '', instances: radarrs, profiles: [...one(5, renameDefaults, 'Radarr'), ...one(6, renameDefaults, 'Radarr4K')] })),
          tool('radarr', 'Radarr', radarrs, [...one(51, upgradeDefaults, 'Radarr'), ...one(52, { ...upgradeDefaults, count: 1, mode: 'cutoff' }, 'Radarr4K')]),
        ] }
      }
      return g
    }) }
  }
  const openUpgrader = async (user: ReturnType<typeof userEvent.setup>) => {
    mockGetTools.mockResolvedValue(withUpgraders())
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    await user.click(screen.getByRole('tab', { name: 'Arr Upgrader' }))
    return screen.findByRole('region', { name: 'Sonarr search settings' })
  }

  it('keeps the upgrade tools on the Arr Upgrader tab and the rename tools on theirs', async () => {
    const user = userEvent.setup()
    const sonarr = await openUpgrader(user)
    expect(localStorage.getItem('posterflow.tools.activeTab')).toBe('upgrade')
    expect(screen.getByRole('region', { name: 'Search Sonarr for upgrades' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Rename Sonarr files' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Sonarr rename settings' })).toBeNull()
    expect((within(sonarr).getByLabelText('Items per run') as HTMLInputElement).value).toBe('5')
    expect((within(sonarr).getByLabelText('Search tag') as HTMLInputElement).value).toBe('upgrade-searched')
    expect(within(within(sonarr).getByLabelText('Search')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Everything', 'Cutoff unmet', 'Missing'])
    expect(within(within(sonarr).getByLabelText('Search by')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Monitored seasons', 'Whole series'])
    expect((within(sonarr).getByLabelText('Search by') as HTMLSelectElement).value).toBe('seasons')
    expect(within(sonarr).queryByLabelText('Monitored')).toBeNull()  // Sonarr itself keeps to what is monitored
    // Each Radarr instance has a block of its own, with its own values and buttons. A movie has no seasons.
    const radarr = screen.getByRole('region', { name: 'Radarr search settings' })
    const uhd = within(radarr).getByRole('group', { name: 'Radarr4K search settings' })
    expect((within(uhd).getByLabelText('Items per run (Radarr4K)') as HTMLInputElement).value).toBe('1')
    expect((within(uhd).getByLabelText('Search (Radarr4K)') as HTMLSelectElement).value).toBe('cutoff')
    expect(within(radarr).queryByLabelText('Search by (Radarr)')).toBeNull()
    expect(within(radarr).queryByLabelText('Season threshold (Radarr)')).toBeNull()
    expect(within(within(uhd).getByLabelText('Monitored (Radarr4K)')).getAllByRole('option').map((o) => o.textContent)).toEqual(['Only monitored', 'Monitored or not', 'Only unmonitored'])
    expect(radarr.querySelectorAll('.tools-check-spacer')).toHaveLength(2)  // one row per block, level with Sonarr's two season settings
    expect(within(radarr).getByRole('button', { name: 'Search Radarr4K' })).toBeTruthy()

    await user.click(screen.getByRole('tab', { name: 'Arr Renamer' }))
    expect(await screen.findByRole('region', { name: 'Rename Sonarr files' })).toBeTruthy()
    expect(screen.queryByRole('region', { name: 'Search Sonarr for upgrades' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Sonarr search settings' })).toBeNull()
  })

  it('offers the season settings only where they apply', async () => {
    const user = userEvent.setup()
    const sonarr = await openUpgrader(user)
    const by = within(sonarr).getByLabelText('Search by') as HTMLSelectElement
    const threshold = within(sonarr).getByLabelText('Season threshold') as HTMLInputElement
    expect(by.disabled).toBe(false)
    expect(threshold.disabled).toBe(false)
    await user.selectOptions(by, 'series')
    expect(threshold.disabled).toBe(true)
    expect(threshold.title).toBe('Applies to Everything searched by Monitored seasons')
    await user.selectOptions(by, 'seasons')
    expect(threshold.disabled).toBe(false)
    // The Wanted lists name the episodes themselves.
    await user.selectOptions(within(sonarr).getByLabelText('Search'), 'cutoff')
    expect(by.disabled).toBe(true)
    expect(by.title).toBe('Cutoff unmet and Missing search the listed episodes')
    expect(threshold.disabled).toBe(true)
    // Quiet runs can only be muted once runs notify at all.
    const grabsOnly = within(sonarr).getByLabelText('Only notify on grabs') as HTMLInputElement
    expect(grabsOnly.disabled).toBe(true)
    await user.click(within(sonarr).getByLabelText('Notify after runs'))
    expect(grabsOnly.disabled).toBe(false)
  })

  it('saves an upgrade profile and asks before a search, without the red of a destructive tool', async () => {
    const user = userEvent.setup()
    mockRunTool.mockResolvedValue({ success: true, job_id: 88, message: 'queued' })
    mockUpdateToolProfile.mockImplementation(async (_toolId: string, id: number, updates: { settings?: Record<string, unknown> }) => (
      { id, name: 'Default', instance: 'sonarr-1', is_default: true, settings: updates.settings }))
    const sonarr = await openUpgrader(user)
    const row = screen.getByRole('region', { name: 'Search Sonarr for upgrades' })
    const threshold = within(sonarr).getByLabelText('Season threshold') as HTMLInputElement
    await user.clear(threshold)
    await user.type(threshold, '80')
    await user.type(within(sonarr).getByLabelText('Only tags'), 'kids, 4k')
    await user.type(within(sonarr).getByLabelText('Quality profiles'), 'Ultra-HD')
    await user.type(within(sonarr).getByLabelText('Minimum age in days'), '30')
    await user.type(within(sonarr).getByLabelText('Grab limit'), '2')
    const search = within(row).getByRole('button', { name: 'Search' }) as HTMLButtonElement
    expect(search.title).toBe('Save the profile first')
    expect(search.className).toContain('btn-primary')
    await user.click(within(sonarr).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockUpdateToolProfile).toHaveBeenCalledWith('sonarr_upgrade', 41, {
      settings: { ...upgradeDefaults, season_threshold: 80, only_tags: 'kids, 4k', quality_profiles: 'Ultra-HD', min_age_days: 30, max_grabs: 2 },
    }))
    await waitFor(() => expect(search.disabled).toBe(false))
    await user.click(within(row).getByRole('button', { name: 'Dry run' }))  // a dry run searches nothing: no question
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('sonarr_upgrade', { dry_run: true, confirm: true, profiles: { 'sonarr-1': 41 } }))
    await user.click(search)
    expect(mockRunTool).toHaveBeenCalledTimes(1)
    const yes = await screen.findByRole('button', { name: 'Yes, search' })
    expect(yes.className).toContain('btn-primary')
    await user.click(yes)
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('sonarr_upgrade', { dry_run: false, confirm: true, profiles: { 'sonarr-1': 41 } }))

    // One Radarr instance on its own, and the reset names the search settings.
    const radarr = screen.getByRole('region', { name: 'Radarr search settings' })
    await user.click(within(radarr).getByRole('button', { name: 'Search Radarr4K' }))
    expect(screen.getByText(/Search Radarr for upgrades on Radarr4K/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Yes, search' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('radarr_upgrade', { dry_run: false, confirm: true, profile_id: 52, instance: 'Radarr4K' }))
    await user.click(within(radarr).getByRole('button', { name: 'Reset to Defaults (Radarr4K)' }))
    expect(await screen.findByText('Reset Search Settings to Defaults?')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Reset' }))
    expect((within(radarr).getByLabelText('Items per run (Radarr4K)') as HTMLInputElement).value).toBe('5')
  })

  it('opens the searched and ignored lists of an upgrader from its tag fields', async () => {
    const user = userEvent.setup()
    mockGetRenameTagged.mockImplementation(async (_tool: string, instance: string, tag: string) => ({ instance, tag, items: [{ id: 4, title: 'Delta', year: 2019, tagged: true }] }))
    const sonarr = await openUpgrader(user)
    const searched = within(sonarr).getByRole('button', { name: 'Manage searched items' }) as HTMLButtonElement
    const tag = within(sonarr).getByLabelText('Search tag') as HTMLInputElement
    await user.clear(tag)
    expect(searched.title).toBe('Set a search tag first')
    await user.type(tag, 'upgrade-searched')
    await user.click(searched)
    const dialog = await screen.findByRole('dialog', { name: 'Searched series' })
    expect(mockGetRenameTagged).toHaveBeenCalledWith('sonarr_upgrade', 'sonarr-1', 'upgrade-searched')
    expect(within(dialog).getByText(/searched these series this cycle/)).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await user.click(within(sonarr).getByRole('button', { name: 'Manage ignored items' }))
    expect(await screen.findByRole('dialog', { name: 'Ignored series' })).toBeTruthy()
    expect(screen.getByText(/The upgrader never searches these series/)).toBeTruthy()
    expect(mockGetRenameTagged).toHaveBeenLastCalledWith('sonarr_upgrade', 'sonarr-1', 'upgrade-ignore')
  })

  it('picks the only tags and the quality profiles of an upgrader from what the arr has', async () => {
    const user = userEvent.setup()
    mockGetArrOptions.mockResolvedValue({
      instance: 'sonarr-1',
      tags: [{ name: 'anime', count: 18 }, { name: 'kids', count: 4 }, { name: 'upgrade-searched', count: 9 }],
      quality_profiles: [{ name: 'WEB-1080p', count: 285 }, { name: 'Anime - WEB-1080p', count: 19 }],
    })
    const sonarr = await openUpgrader(user)
    await user.click(within(sonarr).getByRole('button', { name: 'Manage only tags' }))
    const tags = await screen.findByRole('dialog', { name: 'Only tags' })
    expect(mockGetArrOptions).toHaveBeenCalledWith('sonarr_upgrade', 'sonarr-1')
    const rows = await within(tags).findAllByRole('listitem')
    expect(rows.map((li) => li.textContent)).toEqual(['anime 18 series', 'kids 4 series'])  // the search tag itself is no filter
    await user.click(within(tags).getByRole('checkbox', { name: /anime/ }))
    await user.click(within(tags).getByRole('button', { name: 'Apply' }))
    expect(screen.queryByRole('dialog', { name: 'Only tags' })).toBeNull()
    // The field is filled and the profile waits to be saved, like any other edit.
    expect((within(sonarr).getByLabelText('Only tags') as HTMLInputElement).value).toBe('anime')
    expect((within(sonarr).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false)

    await user.click(within(sonarr).getByRole('button', { name: 'Manage quality profiles' }))
    const profiles = await screen.findByRole('dialog', { name: 'Quality profiles' })
    await user.click(await within(profiles).findByRole('checkbox', { name: /^Anime - WEB-1080p/ }))
    await user.click(within(profiles).getByRole('checkbox', { name: /^WEB-1080p/ }))
    await user.click(within(profiles).getByRole('button', { name: 'Apply' }))
    expect((within(sonarr).getByLabelText('Quality profiles') as HTMLInputElement).value).toBe('WEB-1080p, Anime - WEB-1080p')
    // What is typed is what the list opens with.
    await user.click(within(sonarr).getByRole('button', { name: 'Manage only tags' }))
    expect((await within(await screen.findByRole('dialog', { name: 'Only tags' })).findByRole('checkbox', { name: /anime/ }) as HTMLInputElement).checked).toBe(true)
  })

  it('finds a title for the title filter of the renamer and of the upgrader in the arr itself', async () => {
    const user = userEvent.setup()
    mockGetArrTitles.mockResolvedValue({ instance: 'sonarr-1', items: [{ id: 1, title: 'Alpha', year: 2020 }, { id: 2, title: 'Alphabet City', year: 1984 }, { id: 3, title: 'Beta', year: 2021 }] })
    mockGetTools.mockResolvedValue(withUpgraders())
    renderPage()
    await screen.findByRole('region', { name: 'Plex tools' })
    for (const [tab, card, toolId] of [['Arr Renamer', 'Sonarr rename settings', 'sonarr_rename'], ['Arr Upgrader', 'Sonarr search settings', 'sonarr_upgrade']]) {
      await user.click(screen.getByRole('tab', { name: tab }))
      const region = await screen.findByRole('region', { name: card })
      const field = within(region).getByLabelText('Title filter') as HTMLInputElement
      await user.type(field, 'alph')
      await user.click(within(region).getByRole('button', { name: 'Find titles' }))
      const dialog = await screen.findByRole('dialog', { name: 'Title filter' })
      expect(mockGetArrTitles).toHaveBeenLastCalledWith(toolId, 'sonarr-1')
      // It opens with what is typed, and lists what that matches.
      expect((within(dialog).getByLabelText('Title filter text') as HTMLInputElement).value).toBe('alph')
      expect((await within(dialog).findAllByRole('listitem')).map((li) => li.textContent)).toEqual(['Alpha 2020', 'Alphabet City 1984'])
      await user.click(within(dialog).getByRole('button', { name: /Alphabet City/ }))
      await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
      expect(screen.queryByRole('dialog', { name: 'Title filter' })).toBeNull()
      // The field is filled and the profile waits to be saved, like any other edit.
      expect(field.value).toBe('Alphabet City')
      expect((within(region).getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false)
    }
  })
})
