import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AxiosError } from 'axios'

const mockSearch = vi.fn()
vi.mock('../../src/api/posterManager', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/posterManager')>()),
  searchUnmatchedTmdb: (...args: unknown[]) => mockSearch(...args),
}))

import MatchRequestModal, { matchFromCandidate, searchTypeFor } from '../../src/components/community/MatchRequestModal'
import type { CommunityRequest } from '../../src/api/community'
import type { TmdbCandidate } from '../../src/api/posterManager'

const request = (over: Partial<CommunityRequest> = {}): CommunityRequest => ({
  id: 'req-1', tmdb_id: null, media_type: 'movie', title: 'Inceptoin', year: 2010, season_number: null,
  poster_path: null, imdb_id: null, tvdb_id: null, status: 'pending', claimed_by: null, claimed_by_discord_id: null,
  fulfilled_by: null, requested_by: 'them', requested_by_discord_id: '2', discord_thread_url: null,
  created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z', ...over,
})

const candidate = (over: Partial<TmdbCandidate> = {}): TmdbCandidate => ({
  tmdb_id: 27205, tvdb_id: null, imdb_id: 'tt1375666', title: 'Inception', year: 2010,
  poster_url: 'https://image.tmdb.org/t/p/w342/inc.jpg', overview: '', popularity: 1,
  media_type: 'movie', match_reason: 'fuzzy_title', ...over,
})

describe('MatchRequestModal', () => {
  beforeEach(() => {
    mockSearch.mockResolvedValue({ candidates: [] })
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('searches the request title on open and confirms the picked candidate', async () => {
    mockSearch.mockResolvedValue({ candidates: [candidate(), candidate({ tmdb_id: 99, title: 'Inception: Encore', year: 2012 })] })
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(<MatchRequestModal request={request()} submitting={false} onCancel={vi.fn()} onConfirm={onConfirm} />)

    await waitFor(() => expect(screen.getByText('Inception: Encore')).toBeTruthy())
    expect(mockSearch).toHaveBeenCalledWith({ title: 'Inceptoin', year: null, type: 'movie', tvdb_fallback: true })
    const match = screen.getByRole('button', { name: /^match$/i })
    expect(match.hasAttribute('disabled')).toBe(true)

    await user.click(screen.getByText('Inception', { selector: '.creq-candidate-title' }).closest('button')!)
    expect(screen.getByText(/will become/i)).toBeTruthy()
    await user.click(match)
    expect(onConfirm).toHaveBeenCalledWith({
      tmdb_id: 27205, tvdb_id: null, imdb_id: 'tt1375666', title: 'Inception', year: 2010,
      poster_path: 'https://image.tmdb.org/t/p/w342/inc.jpg', media_type: 'movie',
    })
  })

  it('a season request searches shows with the type locked and auto-selects a lone hit', async () => {
    mockSearch.mockResolvedValue({ candidates: [candidate({ tmdb_id: 1399, title: 'Game of Thrones', year: 2011, media_type: 'show', tvdb_id: 121361 })] })
    const onConfirm = vi.fn()
    const user = userEvent.setup()
    render(
      <MatchRequestModal
        request={request({ media_type: 'season', title: 'Game of Throne', season_number: 2 })}
        submitting={false}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )

    await waitFor(() => expect(screen.getByText('Game of Thrones', { selector: '.creq-candidate-title' })).toBeTruthy())
    expect(mockSearch).toHaveBeenCalledWith({ title: 'Game of Throne', year: null, type: 'show', tvdb_fallback: true })
    expect((screen.getByRole('combobox', { name: /search type/i }) as HTMLSelectElement).disabled).toBe(true)
    expect(screen.getByText(/pick the series it belongs to/i)).toBeTruthy()

    const match = screen.getByRole('button', { name: /^match$/i })
    await waitFor(() => expect(match.hasAttribute('disabled')).toBe(false))
    await user.click(match)
    expect(onConfirm).toHaveBeenCalledWith(expect.objectContaining({ tmdb_id: 1399, tvdb_id: 121361, media_type: 'show' }))
  })

  it('flags a media type change for non-season requests', async () => {
    mockSearch.mockResolvedValue({ candidates: [candidate({ media_type: 'show' })] })
    render(<MatchRequestModal request={request()} submitting={false} onCancel={vi.fn()} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/type changes from movie to show/i)).toBeTruthy())
  })

  it('shows no-results and missing-key states', async () => {
    const { unmount } = render(<MatchRequestModal request={request()} submitting={false} onCancel={vi.fn()} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/no results/i)).toBeTruthy())
    unmount()

    mockSearch.mockRejectedValue(new AxiosError('bad', 'ERR_BAD_REQUEST', undefined, undefined, {
      status: 400, statusText: 'Bad Request', headers: {}, config: { headers: {} } as never,
      data: { detail: 'TMDB API key is not configured.' },
    }))
    render(<MatchRequestModal request={request()} submitting={false} onCancel={vi.fn()} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/no tmdb api key/i))
  })

  it('disables Match and Cancel while submitting', async () => {
    mockSearch.mockResolvedValue({ candidates: [candidate()] })
    const onCancel = vi.fn()
    render(<MatchRequestModal request={request()} submitting onCancel={onCancel} onConfirm={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Inception', { selector: '.creq-candidate-title' })).toBeTruthy())
    expect(screen.getByRole('button', { name: /^match$/i }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: /^cancel$/i }).hasAttribute('disabled')).toBe(true)
  })

  it('helpers map season requests to a show search and candidates to match fields', () => {
    expect(searchTypeFor('season')).toBe('show')
    expect(searchTypeFor('collection')).toBe('collection')
    expect(matchFromCandidate(candidate({ tmdb_id: null, tvdb_id: 5, poster_url: null, imdb_id: null, source: 'tvdb', media_type: 'show' }))).toEqual({
      tmdb_id: null, tvdb_id: 5, imdb_id: null, title: 'Inception', year: 2010, poster_path: null, media_type: 'show',
    })
  })
})
