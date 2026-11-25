import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// A maker rejects with a typed reason and can undo a rejection, on the Requests page and on the Lists page.

const mockUpdateRequestStatus = vi.fn()
const mockUpdateListItem = vi.fn()
const mockGetRequests = vi.fn()
const mockGetListItems = vi.fn()
let mockIsMaker = true

vi.mock('../../src/hooks/useDiscordAuth', () => ({
  useDiscordAuth: () => ({
    isConnected: true, isMaker: mockIsMaker, isOwner: false, isMod: false, username: 'me', discordUserId: 'discord-me', token: 't',
    connecting: false, connectError: null, login: vi.fn(), logout: vi.fn(),
    uploadForClaim: vi.fn(), updateRequestStatus: mockUpdateRequestStatus, updateListItem: mockUpdateListItem, matchRequest: vi.fn(), upgradeRequest: vi.fn(),
  }),
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../src/contexts/AppEventsContext', () => ({ useAppEvents: () => ({ refreshCommunityRequestCount: vi.fn() }) }))
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
  getSettings: () => Promise.resolve({}),
  saveSettings: () => Promise.resolve({}),
  getMakerIdarrConfig: () => Promise.resolve({ sync_targets: [] }),
}))
vi.mock('../../src/api/makerTools', () => ({
  checkTmdbPosterAvailability: () => Promise.resolve({}),
  posterCheckKey: (item: { tmdb_id?: number | null }) => (item.tmdb_id ? `tmdb-${item.tmdb_id}` : null),
}))

import CommunityRequests from '../../src/pages/CommunityRequests'
import ListsView from '../../src/components/community/ListsView'

const row = {
  id: 'row-1', tmdb_id: 5, media_type: 'movie', title: 'Lonely Movie', year: 2021, season_number: null, poster_path: null, imdb_id: null,
  tvdb_id: null, notes: null, status: 'rejected', claimed_by: 'them', claimed_by_discord_id: 'discord-them', fulfilled_by: null,
  created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
}
const request = { ...row, requested_by: 'them', requested_by_discord_id: 'discord-them', discord_thread_url: null, style_tags: ['CL2K'] }
const listItem = { ...row, fulfilled_by: 'bob', style_tag: 'CL2K', source: 'unmatched', poster_list_wanters: [{ discord_id: 'discord-alice', name: 'alice' }] }

beforeEach(() => {
  localStorage.clear()
  mockIsMaker = true
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

const reasonBox = () => screen.getByRole('textbox', { name: 'Reason for rejecting' })
// The card's Reject opens the popup; the popup's own Reject is the last one on screen.
const confirmReject = () => { const all = screen.getAllByRole('button', { name: /^Reject$/ }); return all[all.length - 1] as HTMLButtonElement }

describe('Requests page: Reject', () => {
  const open = { ...request, status: 'pending', claimed_by: null, claimed_by_discord_id: null }

  it('asks for a reason before rejecting, then shows it on the card', async () => {
    mockGetRequests.mockResolvedValue({ requests: [open] })
    mockUpdateRequestStatus.mockResolvedValue({ status: 'rejected', claimed_by: null, fulfilled_by: null })
    render(<CommunityRequests />)
    await screen.findByText('Lonely Movie')

    await userEvent.click(screen.getByRole('button', { name: /^Reject$/ }))
    expect(screen.getByRole('heading', { name: 'Reject request' })).toBeTruthy()
    expect(mockUpdateRequestStatus).not.toHaveBeenCalled()
    expect(confirmReject().disabled).toBe(true)

    await userEvent.type(reasonBox(), '  Already in the drive  ')
    expect(confirmReject().disabled).toBe(false)
    await userEvent.click(confirmReject())

    await waitFor(() => expect(mockUpdateRequestStatus).toHaveBeenCalledWith('row-1', 'reject', 'Already in the drive'))
    expect(screen.queryByRole('heading', { name: 'Reject request' })).toBeNull()
    await screen.findByText(/Rejected: Already in the drive/)
  })

  it('rejects nothing when the popup is cancelled or left blank', async () => {
    mockGetRequests.mockResolvedValue({ requests: [open] })
    render(<CommunityRequests />)
    await screen.findByText('Lonely Movie')

    await userEvent.click(screen.getByRole('button', { name: /^Reject$/ }))
    await userEvent.type(reasonBox(), '   ')
    expect(confirmReject().disabled).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: /^Cancel$/ }))

    expect(screen.queryByRole('heading', { name: 'Reject request' })).toBeNull()
    expect(mockUpdateRequestStatus).not.toHaveBeenCalled()
  })
})

describe('Requests page: Unreject', () => {
  it('puts a rejected request back to open, on the same card', async () => {
    mockGetRequests.mockResolvedValue({ requests: [request] })
    mockUpdateRequestStatus.mockResolvedValue({ status: 'pending', claimed_by: null, fulfilled_by: null })
    render(<CommunityRequests />)
    await screen.findByText('Lonely Movie')
    expect(screen.queryByRole('button', { name: /^Claim$/ })).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /^Unreject$/ }))

    await waitFor(() => expect(mockUpdateRequestStatus).toHaveBeenCalledWith('row-1', 'unreject', undefined))
    // Open again on the same card, with no second read of the list.
    await screen.findByRole('button', { name: /^Claim$/ })
    expect(screen.queryByRole('button', { name: /^Unreject$/ })).toBeNull()
    expect(screen.getByText('Lonely Movie')).toBeTruthy()
    expect(mockGetRequests).toHaveBeenCalledTimes(1)
  })

  it('shows why a request could not be unrejected', async () => {
    mockGetRequests.mockResolvedValue({ requests: [request] })
    mockUpdateRequestStatus.mockRejectedValue(new Error('An active CL2K request for "Lonely Movie" already exists'))
    render(<CommunityRequests />)
    await screen.findByText('Lonely Movie')

    await userEvent.click(screen.getByRole('button', { name: /^Unreject$/ }))

    await screen.findByText('An active CL2K request for "Lonely Movie" already exists')
    expect(screen.getByRole('button', { name: /^Unreject$/ })).toBeTruthy()
  })

  it('offers no Unreject without the Maker role', async () => {
    mockIsMaker = false
    mockGetRequests.mockResolvedValue({ requests: [request] })
    render(<CommunityRequests />)
    await screen.findByText('Lonely Movie')
    expect(screen.queryByRole('button', { name: /^Unreject$/ })).toBeNull()
  })
})

describe('Lists page: Reject', () => {
  it('asks for a reason before rejecting an item', async () => {
    mockGetListItems.mockResolvedValue({ items: [{ ...listItem, status: 'open', claimed_by: null, claimed_by_discord_id: null, fulfilled_by: null }], total: 1 })
    mockUpdateListItem.mockResolvedValue({ status: 'rejected' })
    render(<ListsView />)
    await screen.findByText('Lonely Movie')

    await userEvent.click(screen.getByRole('button', { name: /^Reject$/ }))
    expect(screen.getByRole('heading', { name: 'Reject item' })).toBeTruthy()
    expect(confirmReject().disabled).toBe(true)
    await userEvent.type(reasonBox(), 'Not a real title')
    await userEvent.click(confirmReject())

    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith('row-1', 'reject', 'Not a real title'))
    await waitFor(() => expect(screen.queryByText('Lonely Movie')).toBeNull())
  })

  it('shows the saved reason on a rejected item', async () => {
    mockGetListItems.mockImplementation((params: { status: string }) =>
      Promise.resolve(params.status === 'rejected' ? { items: [{ ...listItem, reject_reason: 'Not a real title' }], total: 1 } : { items: [], total: 0 }))
    render(<ListsView />)

    await waitFor(() => expect(document.querySelector('.community-status-select')).toBeTruthy())
    await userEvent.selectOptions(document.querySelector('.community-status-select') as HTMLSelectElement, 'rejected')
    await screen.findByText('Lonely Movie')
    expect(screen.getByText(/Rejected by/).textContent).toBe('❌ Rejected by bob: Not a real title')
  })
})

describe('Lists page: Unreject', () => {
  it('lists rejected items under the Rejected status and puts one back on the list', async () => {
    mockGetListItems.mockImplementation((params: { status: string }) =>
      Promise.resolve(params.status === 'rejected' ? { items: [listItem], total: 1 } : { items: [], total: 0 }))
    mockUpdateListItem.mockResolvedValue({ status: 'open', claimed_by: null, claimed_by_discord_id: null })
    render(<ListsView />)

    await waitFor(() => expect(document.querySelector('.community-status-select')).toBeTruthy())
    await userEvent.selectOptions(document.querySelector('.community-status-select') as HTMLSelectElement, 'rejected')
    await screen.findByText('Lonely Movie')
    expect(mockGetListItems).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'rejected' }))
    expect(screen.getByText(/Rejected by/).textContent).toContain('bob')

    await userEvent.click(screen.getByRole('button', { name: /^Unreject$/ }))

    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith('row-1', 'unreject'))
    // Open again on the same card: Claim is offered and "Rejected by" is gone.
    await screen.findByRole('button', { name: /^Claim$/ })
    expect(screen.queryByRole('button', { name: /^Unreject$/ })).toBeNull()
    expect(screen.queryByText(/Rejected by/)).toBeNull()
  })

  it('offers no Unreject on an item nobody wants any more', async () => {
    mockGetListItems.mockImplementation((params: { status: string }) =>
      Promise.resolve(params.status === 'rejected' ? { items: [{ ...listItem, poster_list_wanters: [] }], total: 1 } : { items: [], total: 0 }))
    render(<ListsView />)

    await waitFor(() => expect(document.querySelector('.community-status-select')).toBeTruthy())
    await userEvent.selectOptions(document.querySelector('.community-status-select') as HTMLSelectElement, 'rejected')
    await screen.findByText('Lonely Movie')
    expect(screen.getByText(/No one wants this item any more/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Unreject$/ })).toBeNull()
  })

  it('keeps the Rejected status away from non-makers', async () => {
    mockIsMaker = false
    mockGetListItems.mockResolvedValue({ items: [], total: 0 })
    render(<ListsView />)
    await waitFor(() => expect(document.querySelector('.community-status-select')).toBeTruthy())
    const options = Array.from((document.querySelector('.community-status-select') as HTMLSelectElement).options).map((o) => o.value)
    expect(options).toEqual(['active', 'in_progress', 'fulfilled', 'all'])
  })
})
