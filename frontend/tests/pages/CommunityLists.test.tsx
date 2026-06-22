import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// ── Shared mocks ──────────────────────────────────────────────────────────────

const mockLogin = vi.fn()
const mockShowToast = vi.fn()
const mockSubmitListItems = vi.fn()
const mockGetListItems = vi.fn()
const mockGetListOwners = vi.fn()
const mockGetRequests = vi.fn()
const mockUpdateListItem = vi.fn()
const mockRefreshCount = vi.fn()

let mockDiscordAuth: Record<string, unknown> = {}

vi.mock('../../src/hooks/useDiscordAuth', () => ({
  useDiscordAuth: () => mockDiscordAuth,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('../../src/contexts/UnmatchedContext', () => ({
  useUnmatched: () => ({ refreshCommunityRequestCount: mockRefreshCount }),
}))

// Heavy leaf — not needed for these behaviours.
vi.mock('../../src/components/maker-tools/TmdbItemCard', () => ({
  default: () => null,
  derivePsdConfig: () => ({ exportFolder: '', templatePath: '', openPhotopea: false, imageExportFolder: '' }),
}))

vi.mock('../../src/api/community', () => ({
  submitCommunityListItems: (...a: unknown[]) => mockSubmitListItems(...a),
  getCommunityListItems: (...a: unknown[]) => mockGetListItems(...a),
  getCommunityListOwners: (...a: unknown[]) => mockGetListOwners(...a),
  getCommunityRequests: (...a: unknown[]) => mockGetRequests(...a),
}))

vi.mock('../../src/api/client', () => ({
  submitCommunityListItems: (...a: unknown[]) => mockSubmitListItems(...a),
  getSettings: () => Promise.resolve({}),
  saveSettings: () => Promise.resolve({}),
  getMakerIdarrConfig: () => Promise.resolve({ sync_targets: [] }),
  uploadMakerIdarrFiles: () => Promise.resolve({ uploaded: [], uploaded_count: 0 }),
  startIdarr: () => Promise.resolve({}),
}))

vi.mock('../../src/api/makerTools', () => ({
  checkTmdbPosterAvailability: () => Promise.resolve({}),
}))

// Avoid TMDB lookups from the style modal's per-row child.
vi.mock('../../src/components/poster-manager/PosterStyleTmdbSearch', () => ({
  default: () => null,
}))
vi.mock('../../src/components/poster-manager/SortControls', () => ({
  default: () => null,
}))

import PosterStyleModal from '../../src/components/poster-manager/PosterStyleModal'
import ListsView from '../../src/components/community/ListsView'
import type { FallbackItem } from '../../src/api/posterManager'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ── Publish entry point: PosterStyleModal "Add to Lists" ───────────────────────

describe('PosterStyleModal — Add to Lists', () => {
  const items: FallbackItem[] = [
    { title: 'Movie A', year: 2020, type: 'movie', tmdb_id: 27205, imdb_id: 'tt1375666', poster_url: 'https://image.tmdb.org/t/p/original/a.jpg' },
    // Seasons-only series → one season card carrying both seasons.
    { title: 'Show B', year: 2019, type: 'show', season: 3 },
    { title: 'Show B', year: 2019, type: 'show', season: 4 },
    // Show with main poster + a season → collapses to a single show card.
    { title: 'Show C', year: 2018, type: 'show', season: null, tvdb_id: 999, poster_url: 'https://image.tmdb.org/t/p/original/c.jpg' },
    { title: 'Show C', year: 2018, type: 'show', season: 1 },
  ]

  // The claim-status hook fetches community requests + lists on mount.
  beforeEach(() => {
    mockGetRequests.mockResolvedValue({ requests: [] })
    mockGetListItems.mockResolvedValue({ items: [], total: 0 })
    mockGetListOwners.mockResolvedValue({ owners: [] })
  })

  function renderModal() {
    return render(
      <PosterStyleModal
        preferredStyle="CL2K"
        fallbackStyle="MM2K"
        items={items}
        tmdbApiKeyConfigured={false}
        onClose={vi.fn()}
        onDownload={vi.fn()}
      />,
    )
  }

  it('prompts Discord login when not connected', async () => {
    mockDiscordAuth = { isConnected: false, token: null, login: mockLogin }
    renderModal()
    await userEvent.click(screen.getByRole('button', { name: /Add to Lists/i }))
    expect(mockLogin).toHaveBeenCalledTimes(1)
    expect(mockSubmitListItems).not.toHaveBeenCalled()
  })

  it('publishes style-fallback items tagged with the preferred style', async () => {
    mockDiscordAuth = { isConnected: true, token: 'tok', login: mockLogin }
    mockSubmitListItems.mockResolvedValue({ inserted: 3, skipped: 0 })
    renderModal()
    await userEvent.click(screen.getByRole('button', { name: /Add to Lists/i }))

    await waitFor(() => expect(mockSubmitListItems).toHaveBeenCalledTimes(1))
    expect(mockSubmitListItems).toHaveBeenCalledWith({
      discord_token: 'tok',
      items: [
        { media_type: 'movie', title: 'Movie A', year: 2020, season_number: null, tmdb_id: 27205, tvdb_id: null, imdb_id: 'tt1375666', poster_path: 'https://image.tmdb.org/t/p/original/a.jpg', style_tag: 'CL2K', source: 'style_fallback' },
        { media_type: 'season', title: 'Show B', year: 2019, season_number: null, tmdb_id: null, tvdb_id: null, imdb_id: null, poster_path: null, style_tag: 'CL2K', source: 'style_fallback', notes: 'Seasons: 3, 4' },
        { media_type: 'show', title: 'Show C', year: 2018, season_number: null, tmdb_id: null, tvdb_id: 999, imdb_id: null, poster_path: 'https://image.tmdb.org/t/p/original/c.jpg', style_tag: 'CL2K', source: 'style_fallback' },
      ],
    })
    expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('Added 3 items'), 'success')
  })
})

// ── Lists tab: claim then complete a list item ─────────────────────────────────

describe('ListsView — claim and complete', () => {
  const openItem = {
    id: 'item-1',
    tmdb_id: null,
    media_type: 'movie',
    title: 'Lonely Movie',
    year: 2021,
    season_number: null,
    poster_path: null,
    imdb_id: null,
    tvdb_id: null,
    style_tag: 'CL2K',
    source: 'unmatched',
    notes: null,
    added_by: 'alice',
    added_by_discord_id: 'discord-alice',
    status: 'open',
    claimed_by: null,
    claimed_by_discord_id: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
  }

  beforeEach(() => {
    mockDiscordAuth = {
      isConnected: true,
      isMaker: true,
      isOwner: false,
      discordUserId: 'discord-me',
      updateListItem: mockUpdateListItem,
    }
    mockGetListItems.mockResolvedValue({ items: [openItem], total: 1 })
    mockGetListOwners.mockResolvedValue({ owners: [{ id: 'discord-alice', name: 'alice', count: 1 }] })
  })

  it('claims an open item, then completes it (removing the card)', async () => {
    mockUpdateListItem
      .mockResolvedValueOnce({ status: 'in_progress', claimed_by: 'me', claimed_by_discord_id: 'discord-me' })
      .mockResolvedValueOnce({ status: 'fulfilled', fulfilled_by: 'me' })

    render(<ListsView />)

    await waitFor(() => expect(screen.getByText('Lonely Movie')).toBeTruthy())
    expect(screen.getByText(/From/)).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: /^Claim$/i }))
    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith('item-1', 'claim'))

    // Card stays, now shows it as claimed and offers Complete.
    await waitFor(() => expect(screen.getByText(/Claimed by/)).toBeTruthy())

    await userEvent.click(screen.getByRole('button', { name: /^Complete$/i }))
    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith('item-1', 'complete'))

    // Completed item drops off the list.
    await waitFor(() => expect(screen.queryByText('Lonely Movie')).toBeNull())
  })
})

// ── Lists tab: owner remove + bulk clear ───────────────────────────────────────

describe('ListsView — owner remove & bulk clear', () => {
  const alicesItem = {
    id: 'item-1', tmdb_id: null, media_type: 'movie', title: 'Alice Movie', year: 2021,
    season_number: null, poster_path: null, imdb_id: null, tvdb_id: null, style_tag: 'CL2K',
    source: 'unmatched', notes: null, added_by: 'alice', added_by_discord_id: 'discord-alice',
    status: 'open', claimed_by: null, claimed_by_discord_id: null,
    created_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z',
  }
  const myItem = { ...alicesItem, id: 'item-mine', title: 'My Movie', added_by: 'me', added_by_discord_id: 'discord-me' }

  it('lets the server owner remove an item they did not publish', async () => {
    mockDiscordAuth = { isConnected: true, isMaker: false, isOwner: true, discordUserId: 'discord-me', updateListItem: mockUpdateListItem }
    mockGetListItems.mockResolvedValue({ items: [alicesItem], total: 1 })
    mockGetListOwners.mockResolvedValue({ owners: [{ id: 'discord-alice', name: 'alice', count: 1 }] })
    mockUpdateListItem.mockResolvedValue({ ok: true, removed: true })

    render(<ListsView />)
    await waitFor(() => expect(screen.getByText('Alice Movie')).toBeTruthy())

    // Owner sees Remove on an item they didn't publish.
    await userEvent.click(screen.getByRole('button', { name: /^Remove$/i }))
    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith('item-1', 'remove'))
    await waitFor(() => expect(screen.queryByText('Alice Movie')).toBeNull())
  })

  it("bulk-removes only the connected user's own items", async () => {
    mockDiscordAuth = { isConnected: true, isMaker: false, isOwner: false, discordUserId: 'discord-me', updateListItem: mockUpdateListItem }
    // First load returns both; after the bulk remove the view reloads with just Alice's.
    mockGetListItems
      .mockResolvedValueOnce({ items: [alicesItem, myItem], total: 2 })
      .mockResolvedValue({ items: [alicesItem], total: 1 })
    mockGetListOwners.mockResolvedValue({ owners: [
      { id: 'discord-alice', name: 'alice', count: 1 },
      { id: 'discord-me', name: 'me', count: 1 },
    ] })
    mockUpdateListItem.mockResolvedValue({ ok: true, removed: 1 })

    render(<ListsView />)
    await waitFor(() => expect(screen.getByText('My Movie')).toBeTruthy())

    // Open the confirm, then confirm (modal button is the last matching one).
    await userEvent.click(screen.getByRole('button', { name: /Remove My Items/i }))
    expect(screen.getByRole('heading', { name: /Remove all your items/i })).toBeTruthy()
    const buttons = screen.getAllByRole('button', { name: /Remove My Items/i })
    await userEvent.click(buttons[buttons.length - 1])

    await waitFor(() => expect(mockUpdateListItem).toHaveBeenCalledWith(null, 'remove_mine'))
    // Only the user's own item is gone; another publisher's remains.
    await waitFor(() => expect(screen.queryByText('My Movie')).toBeNull())
    expect(screen.getByText('Alice Movie')).toBeTruthy()
  })
})
