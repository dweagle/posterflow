import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import UnmatchedItemsModal, { type UnmatchedModalType } from '../../src/components/poster-manager/UnmatchedItemsModal'
import type { UnmatchedStats } from '../../src/api/client'

/**
 * The modal heading names the asset type it was opened for. One modal serves every type,
 * so poster wording must not leak into a logo/backdrop/square art view.
 */

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  addUnmatchedIgnoreItem: vi.fn().mockResolvedValue({ items: [] }),
}))

vi.mock('../../src/hooks/useDiscordAuth', () => ({
  useDiscordAuth: () => ({ isConnected: false, token: null, login: vi.fn() }),
}))

vi.mock('../../src/hooks/useCommunityClaimStatus', () => ({
  useCommunityClaimStatus: () => ({ getStatus: () => undefined }),
}))

const posterStats = (): UnmatchedStats => ({
  summary: {
    movies: { total: 10, unmatched: 1, percent_complete: 90 },
    series: { total: 5, unmatched: 1, percent_complete: 80 },
    seasons: { total: 8, unmatched: 1, percent_complete: 87.5 },
    collections: { total: 4, unmatched: 1, percent_complete: 75 },
    grand_total: { total: 19, unmatched: 4, percent_complete: 78.9 },
    by_library: {},
  },
  unmatched: {
    movies: [{ title: 'Inception', year: 2010, instance: 'Radarr' }],
    series: [{ title: 'Breaking Bad', year: 2008, missing_seasons: [1], missing_main_poster: true, instance: 'Sonarr' }],
    collections: [{ title: 'Marvel Collection', year: 0, instance: 'Plex' }],
  },
  last_run: '2026-07-15T00:00:00Z',
})

// Artwork carries no seasons — the backend zeroes that dimension.
const artworkStats = (): UnmatchedStats => {
  const stats = posterStats()
  stats.summary.seasons = { total: 0, unmatched: 0, percent_complete: 100 }
  stats.unmatched.series = [
    { title: 'Breaking Bad', year: 2008, missing_seasons: [], missing_main_poster: true, instance: 'Sonarr' },
  ]
  return stats
}

function renderModal(modalType: UnmatchedModalType, stats: UnmatchedStats, typeNoun?: string) {
  render(
    <UnmatchedItemsModal
      modalType={modalType}
      unmatchedStats={stats}
      modalDisplayLimit={50}
      tmdbApiKeyConfigured={false}
      onClose={vi.fn()}
      typeNoun={typeNoun}
      hideCommunity={Boolean(typeNoun && typeNoun !== 'Posters')}
    />,
  )
}

describe('UnmatchedItemsModal heading', () => {
  afterEach(() => {
    cleanup()
  })

  it('defaults to poster wording', () => {
    renderModal('movies', posterStats())
    expect(screen.getByText('Movies Missing Posters', { selector: 'h2' })).toBeTruthy()
  })

  it('names the artwork type for each media type', () => {
    renderModal('movies', artworkStats(), 'Logos')
    expect(screen.getByText('Movies Missing Logos', { selector: 'h2' })).toBeTruthy()

    cleanup()
    renderModal('collections', artworkStats(), 'Backdrops')
    expect(screen.getByText('Collections Missing Backdrops', { selector: 'h2' })).toBeTruthy()

    cleanup()
    renderModal('all', artworkStats(), 'Square Art')
    expect(screen.getByText('All Missing Square Art', { selector: 'h2' })).toBeTruthy()
  })

  it('keeps "Main" for posters, where season posters also exist', () => {
    renderModal('series', posterStats())
    expect(screen.getByText('Series Missing Main Posters', { selector: 'h2' })).toBeTruthy()
  })

  it('drops "Main" for artwork, which has no season dimension', () => {
    renderModal('series', artworkStats(), 'Logos')
    expect(screen.getByText('Series Missing Logos', { selector: 'h2' })).toBeTruthy()
    expect(screen.queryByText('Series Missing Main Logos')).toBeNull()
  })

  // 'movies' renders no season rows, so a row-derived flag would wrongly read false here.
  it('keeps "Main" on a poster series view even when the open view has no season rows', () => {
    renderModal('series', posterStats(), 'Posters')
    expect(screen.getByText('Series Missing Main Posters', { selector: 'h2' })).toBeTruthy()
  })
})

describe('UnmatchedItemsModal ignore button', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('adds the item to the ignore list and hides its row', async () => {
    const { addUnmatchedIgnoreItem } = await import('../../src/api/client')
    const user = userEvent.setup()
    renderModal('movies', posterStats())

    expect(screen.getByText('Inception')).toBeTruthy()
    await user.click(screen.getByTitle(/Ignore this item/))

    expect(addUnmatchedIgnoreItem).toHaveBeenCalledWith({
      media_type: 'movie',
      title: 'Inception',
      year: 2010,
      tmdb_id: null,
      tvdb_id: null,
      imdb_id: null,
    })
    await waitFor(() => expect(screen.queryByText('Inception')).toBeNull())
  })
})
