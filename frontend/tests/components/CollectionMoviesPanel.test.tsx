import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CollectionMoviesPanel from '../../src/components/community/CollectionMoviesPanel'

const mockGetCollectionMovies = vi.fn()
const mockCheckAvailability = vi.fn()

vi.mock('../../src/api/makerTools', () => ({
  getCollectionMovies: (...a: unknown[]) => mockGetCollectionMovies(...a),
  checkTmdbPosterAvailability: (...a: unknown[]) => mockCheckAvailability(...a),
  posterCheckKey: (item: { tmdb_id?: number | null }) => (item.tmdb_id ? `tmdb-${item.tmdb_id}` : null),
}))

// Heavy leaf — a title stub is enough to count cards.
vi.mock('../../src/components/maker-tools/TmdbItemCard', () => ({
  default: ({ item }: { item: { title: string } }) => <div data-testid="movie-card">{item.title}</div>,
}))

const movie = (tmdb_id: number, title: string, year: string) => ({
  tmdb_id, title, year, media_type: 'movie', overview: '', poster_url: '', homepage: '', imdb_id: null, tvdb_id: null,
})

const toggle = () => screen.getByRole('button', { name: /movies in this collection/i })

describe('CollectionMoviesPanel', () => {
  beforeEach(() => {
    mockGetCollectionMovies.mockResolvedValue([movie(301, 'Alien', '1979'), movie(302, 'Aliens', '1986')])
    mockCheckAvailability.mockResolvedValue({})
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('stays folded and fetches nothing until opened', () => {
    render(<CollectionMoviesPanel tmdbId={8091} />)
    expect(toggle()).toBeTruthy()
    expect(mockGetCollectionMovies).not.toHaveBeenCalled()
    expect(screen.queryAllByTestId('movie-card')).toHaveLength(0)
  })

  it('loads the collection once and shows one maker card per movie', async () => {
    const user = userEvent.setup()
    render(<CollectionMoviesPanel tmdbId={8091} />)
    await user.click(toggle())
    await waitFor(() => expect(screen.getAllByTestId('movie-card')).toHaveLength(2))
    expect(mockGetCollectionMovies).toHaveBeenCalledWith(8091)
    expect(screen.getByText('Alien')).toBeTruthy()
    expect(screen.getByRole('button', { name: /movies in this collection \(2\)/i })).toBeTruthy()
    // Fold and reopen: no second fetch.
    await user.click(toggle())
    expect(screen.queryAllByTestId('movie-card')).toHaveLength(0)
    await user.click(toggle())
    expect(screen.getAllByTestId('movie-card')).toHaveLength(2)
    expect(mockGetCollectionMovies).toHaveBeenCalledTimes(1)
  })

  it('checks drive availability for the loaded movies', async () => {
    const user = userEvent.setup()
    render(<CollectionMoviesPanel tmdbId={8091} />)
    await user.click(toggle())
    await waitFor(() => expect(mockCheckAvailability).toHaveBeenCalledOnce())
    expect(mockCheckAvailability).toHaveBeenCalledWith([
      expect.objectContaining({ tmdb_id: 301, media_type: 'movie' }),
      expect.objectContaining({ tmdb_id: 302, media_type: 'movie' }),
    ])
  })

  it('shows the error and retries on demand', async () => {
    mockGetCollectionMovies.mockRejectedValueOnce({ response: { data: { detail: 'TMDB is down' } } })
    const user = userEvent.setup()
    render(<CollectionMoviesPanel tmdbId={8091} />)
    await user.click(toggle())
    await waitFor(() => expect(screen.getByText('TMDB is down')).toBeTruthy())
    await user.click(screen.getByRole('button', { name: /retry/i }))
    await waitFor(() => expect(screen.getAllByTestId('movie-card')).toHaveLength(2))
    expect(mockGetCollectionMovies).toHaveBeenCalledTimes(2)
  })

  it('folds when the collapse signal bumps', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<CollectionMoviesPanel tmdbId={8091} collapseSignal={0} />)
    await user.click(toggle())
    await waitFor(() => expect(screen.getAllByTestId('movie-card')).toHaveLength(2))
    rerender(<CollectionMoviesPanel tmdbId={8091} collapseSignal={1} />)
    expect(screen.queryAllByTestId('movie-card')).toHaveLength(0)
  })

  it('portals the button into the card slot and expands the movies outside it', async () => {
    const user = userEvent.setup()
    render(
      <>
        <div id="slot-8091" data-testid="slot" />
        <CollectionMoviesPanel tmdbId={8091} toggleSlotId="slot-8091" />
      </>,
    )
    const slot = screen.getByTestId('slot')
    await waitFor(() => expect(slot.querySelector('button')).toBeTruthy())
    await user.click(toggle())
    await waitFor(() => expect(screen.getAllByTestId('movie-card')).toHaveLength(2))
    expect(slot.querySelectorAll('[data-testid="movie-card"]')).toHaveLength(0)
  })
})
