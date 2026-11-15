import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// The Requests and Lists pages hand an upload to Posterflow's server (useDiscordAuth's uploadForClaim)
// with what Upload is set to on screen. These tests press the real buttons on the real pages.

const mockShowToast = vi.fn()
const mockUploadForClaim = vi.fn()
const mockGetRequests = vi.fn()
const mockGetListItems = vi.fn()
const mockNotifyRun = vi.fn()
let mockSettings: Record<string, string> = {}
let mockPicks: { source: string; name: string }[] = []

vi.mock('../../src/hooks/useDiscordAuth', () => ({
  useDiscordAuth: () => ({
    isConnected: true, isMaker: true, isOwner: false, isMod: false, username: 'me', discordUserId: 'discord-me', token: 't',
    connecting: false, connectError: null, login: vi.fn(), logout: vi.fn(),
    uploadForClaim: mockUploadForClaim, updateRequestStatus: vi.fn(), updateListItem: vi.fn(), matchRequest: vi.fn(), upgradeRequest: vi.fn(),
  }),
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: mockShowToast }) }))
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../src/contexts/AppEventsContext', () => ({ useAppEvents: () => ({ refreshCommunityRequestCount: vi.fn() }) }))
vi.mock('../../src/utils/idarrTargetedRun', () => ({ notifyIdarrTargetedRun: (...a: unknown[]) => mockNotifyRun(...a) }))
vi.mock('../../src/components/maker-tools/TmdbItemCard', () => {
  const psd = { exportFolder: '', templatePath: '', imageExportFolder: '', exportFolderMm2k: '', templatePathMm2k: '', imageExportFolderMm2k: '', openPhotopea: false }
  return { default: () => null, EMPTY_PSD_CONFIG: psd, derivePsdConfig: () => psd }
})
vi.mock('../../src/api/community', () => ({
  getCommunityRequests: (...a: unknown[]) => mockGetRequests(...a),
  getCommunityListItems: (...a: unknown[]) => mockGetListItems(...a),
  getCommunityListOwners: () => Promise.resolve({ owners: [] }),
  getCommunityClaimIndex: () => Promise.resolve({ requests: [], list_items: [] }),
}))
vi.mock('../../src/api/client', () => ({
  getSettings: () => Promise.resolve(mockSettings),
  saveSettings: () => Promise.resolve({}),
  getMakerIdarrConfig: () => Promise.resolve({ sync_targets: [
    { personal_drive_id: 'a', source_dir: '/sync/a', label: 'Posters', scope_token: 'tok-a' },
    { personal_drive_id: 'd', source_dir: '/sync/d', label: 'Posters 4K', scope_token: 'tok-d' },
  ] }),
}))
vi.mock('../../src/api/makerTools', () => ({
  checkTmdbPosterAvailability: () => Promise.resolve({}),
  posterCheckKey: (item: { tmdb_id?: number | null }) => (item.tmdb_id ? `tmdb-${item.tmdb_id}` : null),
}))
// The picker has its own tests; here it hands over what it would have ticked.
vi.mock('../../src/components/community/RequestUploadPickerModal', () => ({
  default: ({ onPost }: { onPost: (picks: unknown[]) => void }) => <button type="button" onClick={() => onPost(mockPicks)}>picker-post</button>,
}))

const RESULT = { posted: 1, messages: 1, idarr: 0, drive: '', idarr_job: null, warnings: [], did: 'posted 1 poster to Lonely Movie (2021)' }
const row = {
  id: 'row-1', tmdb_id: 5, media_type: 'movie', title: 'Lonely Movie', year: 2021, season_number: null, poster_path: null, imdb_id: null,
  tvdb_id: null, notes: null, status: 'in_progress', claimed_by: 'me', claimed_by_discord_id: 'discord-me', fulfilled_by: null,
  created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
}
const jpg = () => new File(['bytes'], 'Lonely Movie (2021) {tmdb-5}.jpg', { type: 'image/jpeg' })
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement

// The upload settings live in a module-level store, so each test loads the pages afresh.
async function loadPage(name: 'requests' | 'lists') {
  vi.resetModules()
  return name === 'requests'
    ? (await import('../../src/pages/CommunityRequests')).default
    : (await import('../../src/components/community/ListsView')).default
}

beforeEach(() => {
  localStorage.clear()
  mockSettings = {}
  mockPicks = []
  mockGetRequests.mockResolvedValue({ requests: [{ ...row, requested_by: 'them', requested_by_discord_id: 'discord-them', discord_thread_url: 'https://discord.test/t', style_tags: ['CL2K'] }], total: 1 })
  mockGetListItems.mockResolvedValue({ items: [{ ...row, style_tag: 'CL2K', source: 'unmatched', poster_list_wanters: [] }], total: 1 })
  mockUploadForClaim.mockResolvedValue(RESULT)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Requests page: Upload', () => {
  it('sends files from the computer to the server with Discord only, and shows Posted', async () => {
    const Page = await loadPage('requests')
    render(<Page />)
    await screen.findByText('Lonely Movie')
    const file = jpg()
    await userEvent.click(screen.getByRole('button', { name: /^Upload$/ }))   // no export folder set: straight to the file dialog
    await userEvent.upload(fileInput(), file)

    await waitFor(() => expect(mockUploadForClaim).toHaveBeenCalledTimes(1))
    expect(mockUploadForClaim).toHaveBeenCalledWith('requests', 'row-1', [file], { action: 'discord', drive: 'scope:tok-a' }, expect.any(Function))
    await screen.findByRole('button', { name: /^Posted!$/ })
    expect(mockNotifyRun).not.toHaveBeenCalled()
    expect(mockShowToast).not.toHaveBeenCalled()
  })

  it('sends what the switches and the drive picker show, and watches the IDarr run the server started', async () => {
    mockSettings = { idarr_quick_add_community: 'true', community_upload_action: 'idarr' }
    localStorage.setItem('posterflow.idarr.selectedSyncTarget', 'scope:tok-d')
    mockUploadForClaim.mockResolvedValue({ ...RESULT, posted: 0, idarr: 2, drive: 'Posters 4K', idarr_job: { id: 7, index: 1, sync_after: true }, warnings: ['The IDarr run did not start: busy'] })
    const Page = await loadPage('requests')
    render(<Page />)
    await screen.findByText('Lonely Movie')
    await waitFor(() => expect(screen.getByRole('button', { name: /^Upload$/ }).getAttribute('data-tooltip')).toMatch(/IDarr/))   // the settings have loaded
    await userEvent.click(screen.getByRole('button', { name: /^Upload$/ }))
    await userEvent.upload(fileInput(), [jpg(), jpg()])

    await waitFor(() => expect(mockUploadForClaim).toHaveBeenCalledTimes(1))
    expect(mockUploadForClaim.mock.calls[0][3]).toEqual({ action: 'idarr', drive: 'scope:tok-d' })
    await screen.findByRole('button', { name: /^Added 2 to IDarr!$/ })
    expect(mockNotifyRun).toHaveBeenCalledWith(7, true, 1)
    expect(mockShowToast).toHaveBeenCalledWith('The IDarr run did not start: busy', 'error')
  })

  it('sends the exports ticked in the picker by name, with nothing downloaded', async () => {
    mockSettings = { poster_export_folder: '/exports' }
    mockPicks = [{ source: 'poster', name: 'Lonely Movie (2021) {tmdb-5}.jpg' }]
    const Page = await loadPage('requests')
    render(<Page />)
    await screen.findByText('Lonely Movie')
    await waitFor(async () => {
      await userEvent.click(screen.getByRole('button', { name: /^Upload$/ }))
      expect(screen.getByRole('button', { name: 'picker-post' })).toBeTruthy()   // an export folder is set: the picker opens
    })
    await userEvent.click(screen.getByRole('button', { name: 'picker-post' }))

    await waitFor(() => expect(mockUploadForClaim).toHaveBeenCalledWith('requests', 'row-1', mockPicks, { action: 'discord', drive: 'scope:tok-a' }, expect.any(Function)))
    await screen.findByRole('button', { name: /^Posted!$/ })
  })

  it('counts the Discord messages of a long upload on the button, as before', async () => {
    let finish: (r: typeof RESULT) => void = () => {}
    let report: (p: { message: number; of: number }) => void = () => {}
    mockUploadForClaim.mockImplementation((_w, _id, _files, _settings, onProgress) => {
      report = onProgress
      return new Promise((resolve) => { finish = resolve })
    })
    const Page = await loadPage('requests')
    render(<Page />)
    await screen.findByText('Lonely Movie')
    await userEvent.click(screen.getByRole('button', { name: /^Upload$/ }))
    await userEvent.upload(fileInput(), Array.from({ length: 12 }, jpg))

    const button = await screen.findByRole('button', { name: /^Uploading…$/ })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    act(() => report({ message: 1, of: 2 }))
    await screen.findByRole('button', { name: /^Uploading 1\/2…$/ })
    act(() => report({ message: 2, of: 2 }))
    expect((await screen.findByRole('button', { name: /^Uploading 2\/2…$/ }) as HTMLButtonElement).disabled).toBe(true)
    await act(async () => { finish({ ...RESULT, posted: 12, messages: 2 }) })
    await screen.findByRole('button', { name: /^Posted 12!$/ })
  })

  it('shows the server\'s reason on the card and offers a retry', async () => {
    mockUploadForClaim.mockRejectedValue(new Error('Lonely Movie (2021) has no Discord thread.'))
    const Page = await loadPage('requests')
    render(<Page />)
    await screen.findByText('Lonely Movie')
    await userEvent.click(screen.getByRole('button', { name: /^Upload$/ }))
    await userEvent.upload(fileInput(), jpg())

    const retry = await screen.findByRole('button', { name: /^Retry$/ })
    expect(retry.getAttribute('data-tooltip')).toBe('Lonely Movie (2021) has no Discord thread.')
  })
})

describe('Lists tab: Upload', () => {
  it('has no Upload while Adds to IDarr is off', async () => {
    const Lists = await loadPage('lists')
    render(<Lists />)
    await screen.findByText('Lonely Movie')
    expect(screen.queryByRole('button', { name: /upload/i })).toBeNull()
  })

  it('sends a list item\'s files to the server as a list item, in the drive on screen', async () => {
    mockSettings = { idarr_quick_add_community: 'true' }
    mockUploadForClaim.mockResolvedValue({ ...RESULT, posted: 0, idarr: 1, drive: 'Posters', idarr_job: { id: 9, index: 0, sync_after: false } })
    const Lists = await loadPage('lists')
    render(<Lists />)
    await screen.findByText('Lonely Movie')
    const file = jpg()
    await userEvent.click(await screen.findByRole('button', { name: /upload/i }))
    await userEvent.upload(fileInput(), file)

    await waitFor(() => expect(mockUploadForClaim).toHaveBeenCalledWith('list-items', 'row-1', [file], { action: 'discord_idarr', drive: 'scope:tok-a' }))
    await waitFor(() => expect(mockNotifyRun).toHaveBeenCalledWith(9, false, 0))
    expect(mockShowToast).not.toHaveBeenCalled()
  })

  it('toasts the server\'s reason when the add fails', async () => {
    mockSettings = { idarr_quick_add_community: 'true' }
    mockUploadForClaim.mockRejectedValue(new Error('IDarr add failed: Cannot create or access source folder'))
    const Lists = await loadPage('lists')
    render(<Lists />)
    await screen.findByText('Lonely Movie')
    await userEvent.click(await screen.findByRole('button', { name: /upload/i }))
    await userEvent.upload(fileInput(), jpg())
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('IDarr add failed: Cannot create or access source folder', 'error'))
  })
})
