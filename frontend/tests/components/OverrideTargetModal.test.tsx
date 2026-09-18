import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const searchLibraryItems = vi.fn()
const savePosterOverride = vi.fn()
vi.mock('../../src/api/posterManager', () => ({
  searchLibraryItems: (...args: unknown[]) => searchLibraryItems(...args),
  savePosterOverride: (...args: unknown[]) => savePosterOverride(...args),
  getDriveImageUrl: (path: string) => `/img?path=${path}`,
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import OverrideTargetModal from '../../src/components/poster-manager/OverrideTargetModal'

const show = {
  media_type: 'show', title: 'Breaking Bad', year: 2008, tmdb_id: 1396, tvdb_id: 81189, imdb_id: 'tt0903747',
  seasons: [0, 1, 2, 3], source: 'Sonarr', poster_url: null, thumb_url: null,
}
const movie = {
  media_type: 'movie', title: 'Heat', year: 1995, tmdb_id: 949, tvdb_id: null, imdb_id: 'tt0113277',
  seasons: [], source: 'Radarr', poster_url: null, thumb_url: null,
}
const response = (items: unknown[]) => ({ query: '', count: items.length, total: 2, items })

describe('OverrideTargetModal', () => {
  afterEach(() => {
    cleanup()
    searchLibraryItems.mockReset()
    savePosterOverride.mockReset()
  })

  it('searches the library with the poster name and pins the parsed season', async () => {
    searchLibraryItems.mockResolvedValue(response([show]))
    savePosterOverride.mockResolvedValue({ id: 1 })
    const onClose = vi.fn()
    const onSaved = vi.fn()
    const user = userEvent.setup()
    const pick = {
      poster_name: 'Breaking Bad (2008) {tmdb-1396} - Season 2',
      drive_id: 'cl-1', drive_name: 'CL Drive', file_path: '/drives/cl/Breaking Bad (2008) - Season 2.jpg',
    }
    render(<OverrideTargetModal pick={pick} onClose={onClose} onSaved={onSaved} />)

    await waitFor(() => expect(searchLibraryItems).toHaveBeenCalledWith('Breaking Bad (2008)', { refresh: false }))
    const row = await screen.findByRole('option', { name: /Breaking Bad/ })
    expect(row.textContent).toContain('Sonarr')
    await user.click(row)

    // The season parsed from the file name is preselected among the show's own seasons.
    expect(screen.getByRole('button', { name: 'Season 2' }).className).toContain('active')
    expect(screen.getByRole('button', { name: 'Season 3' })).toBeTruthy()
    expect(screen.queryByRole('spinbutton')).toBeNull()

    await user.click(screen.getByRole('button', { name: /Use for Breaking Bad \(2008\) - Season 2/ }))
    await waitFor(() => expect(savePosterOverride).toHaveBeenCalledTimes(1))
    expect(savePosterOverride).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'show', tmdb_id: 1396, tvdb_id: 81189, imdb_id: 'tt0903747', title: 'Breaking Bad', year: 2008,
      domain: 'poster', scope: 'slot', season: 2, drive_id: 'cl-1', file: pick.file_path,
    }))
    expect(onSaved).toHaveBeenCalled()
    expect(onClose).toHaveBeenCalled()
  })

  it('pins a movie without any season and filters by type', async () => {
    searchLibraryItems.mockResolvedValue(response([movie, show]))
    savePosterOverride.mockResolvedValue({ id: 2 })
    const user = userEvent.setup()
    const pick = { poster_name: 'Heat (1995)', drive_id: 'mm-1', drive_name: 'MM Drive', file_path: '/drives/mm/Heat (1995).jpg' }
    render(<OverrideTargetModal pick={pick} onClose={vi.fn()} />)

    expect((screen.getByRole('button', { name: 'Use for…' }) as HTMLButtonElement).disabled).toBe(true)
    await screen.findByRole('option', { name: /Heat/ })
    await user.click(screen.getByRole('button', { name: 'Movies' }))
    expect(screen.queryByRole('option', { name: /Breaking Bad/ })).toBeNull()

    await user.click(screen.getByRole('option', { name: /Heat/ }))
    expect(screen.queryByRole('button', { name: 'Show poster' })).toBeNull()

    await user.click(screen.getByRole('button', { name: /Use for Heat \(1995\)/ }))
    await waitFor(() => expect(savePosterOverride).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'movie', tmdb_id: 949, season: null, file: pick.file_path,
    })))
  })

  it('offers only the seasons the library has and falls back to the show poster', async () => {
    searchLibraryItems.mockResolvedValue(response([{ ...show, seasons: [1, 2] }]))
    const user = userEvent.setup()
    const pick = {
      poster_name: 'Breaking Bad (2008) - Season 9',
      drive_id: 'cl-1', drive_name: 'CL Drive', file_path: '/drives/cl/Breaking Bad (2008) - Season 9.jpg',
    }
    render(<OverrideTargetModal pick={pick} onClose={vi.fn()} />)

    await user.click(await screen.findByRole('option', { name: /Breaking Bad/ }))
    expect(screen.getByRole('button', { name: 'Show poster' }).className).toContain('active')
    expect(screen.queryByRole('button', { name: 'Season 9' })).toBeNull()
    expect(screen.getAllByRole('button', { name: /^Season \d+$/ }).map((b) => b.textContent)).toEqual(['Season 1', 'Season 2'])
    expect(screen.getByRole('button', { name: /Use for Breaking Bad \(2008\)$/ })).toBeTruthy()
  })

  it('pins an artwork hit to the matching piece with no season choice', async () => {
    searchLibraryItems.mockResolvedValue(response([show]))
    savePosterOverride.mockResolvedValue({ id: 3 })
    const user = userEvent.setup()
    const pick = {
      poster_name: 'Breaking Bad (2008)', drive_id: 'art-a', drive_name: 'Art Drive A',
      file_path: '/drives/art/squareart/Breaking Bad (2008).png', artwork_type: 'squareart' as const,
    }
    render(<OverrideTargetModal pick={pick} onClose={vi.fn()} />)

    expect(screen.getByText(/Art Drive A · Square/)).toBeTruthy()
    await user.click(await screen.findByRole('option', { name: /Breaking Bad/ }))
    expect(screen.queryByRole('button', { name: 'Show poster' })).toBeNull()

    await user.click(screen.getByRole('button', { name: /Use for Breaking Bad \(2008\) - Square/ }))
    await waitFor(() => expect(savePosterOverride).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'show', tmdb_id: 1396, domain: 'artwork', scope: 'slot', slot: 'square', season: null,
      drive_id: 'art-a', file: pick.file_path,
    })))
  })

  it('reports an empty library match and can refresh the cached list', async () => {
    searchLibraryItems.mockResolvedValue(response([]))
    const user = userEvent.setup()
    render(<OverrideTargetModal pick={{ poster_name: 'Nope (2001)', drive_id: 'mm-1', drive_name: 'MM', file_path: '/d/n.jpg' }} onClose={vi.fn()} />)

    expect(await screen.findByText(/No library items match \(2 items/)).toBeTruthy()
    await user.click(screen.getByRole('button', { name: /Refresh library/ }))
    await waitFor(() => expect(searchLibraryItems).toHaveBeenLastCalledWith('Nope (2001)', { refresh: true }))
  })

  it('shows the API error when the sources cannot be read', async () => {
    searchLibraryItems.mockRejectedValue({ response: { data: { detail: 'Library search failed' } } })
    render(<OverrideTargetModal pick={{ poster_name: 'Heat (1995)', drive_id: 'mm-1', drive_name: 'MM', file_path: '/d/h.jpg' }} onClose={vi.fn()} />)

    expect(await screen.findByText('Library search failed')).toBeTruthy()
  })
})
