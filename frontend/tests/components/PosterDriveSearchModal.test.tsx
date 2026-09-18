import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const searchLibraryItems = vi.fn()
const getPosterOverrides = vi.fn()
const savePosterOverride = vi.fn()
const searchPosters = vi.fn()
vi.mock('../../src/api/posterManager', () => ({
  searchLibraryItems: (...args: unknown[]) => searchLibraryItems(...args),
  getPosterOverrides: (...args: unknown[]) => getPosterOverrides(...args),
  savePosterOverride: (...args: unknown[]) => savePosterOverride(...args),
  deletePosterOverride: vi.fn(),
}))
const searchArtwork = vi.fn()
vi.mock('../../src/api/client', () => ({
  searchPosters: (...args: unknown[]) => searchPosters(...args),
  searchArtwork: (...args: unknown[]) => searchArtwork(...args),
}))
vi.mock('../../src/api/http', () => ({ API_URL: '' }))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import PosterDriveSearchModal from '../../src/components/PosterDriveSearchModal'

const target = {
  media_type: 'show' as const, tmdb_id: 1396, title: 'Breaking Bad', year: 2008, season: 2, seasons: [1, 2],
}
const libraryShow = {
  media_type: 'show', title: 'Breaking Bad', year: 2008, tmdb_id: 1396, tvdb_id: 81189, imdb_id: null,
  seasons: [0, 1, 2, 3, 4], source: 'Sonarr',
}
const FILE = '/drives/cl/Any Poster.jpg'
const hit = {
  poster_name: 'Any Poster', drive_count: 1,
  drives: [{ drive_id: 'cl-1', drive_name: 'CL Drive', drive_type: 'cl2k', style_type: 'CL2K', is_custom: false, poster_id: 1, image_url: '/i', file_path: FILE }],
}

describe('PosterDriveSearchModal with a target', () => {
  afterEach(() => {
    cleanup()
    searchLibraryItems.mockReset()
    getPosterOverrides.mockReset()
    savePosterOverride.mockReset()
    searchPosters.mockReset()
    searchArtwork.mockReset()
  })

  it('artwork targets lock to the artwork index and pin each hit to its own piece', async () => {
    getPosterOverrides.mockResolvedValue([{
      id: 5, media_type: 'show', tmdb_id: 1396, title: 'Breaking Bad', year: 2008, domain: 'artwork',
      scope: 'slot', slot: 'logo', drive_id: 'art-a', file: '/drives/art/logos/Old Logo.png',
    }])
    searchArtwork.mockResolvedValue({ query: 'Breaking Bad', count: 2, items: [
      { artwork_name: 'Old Logo', artwork_type: 'logo', drive_count: 1, drives: [{ drive_id: 'art-a', drive_name: 'Art A', drive_type: 'artwork', is_custom: false, artwork_id: 1, image_url: '/i1', file_path: '/drives/art/logos/Old Logo.png' }] },
      { artwork_name: 'New Backdrop', artwork_type: 'background', drive_count: 1, drives: [{ drive_id: 'art-a', drive_name: 'Art A', drive_type: 'artwork', is_custom: false, artwork_id: 2, image_url: '/i2', file_path: '/drives/art/backgrounds/New Backdrop.jpg' }] },
    ] })
    savePosterOverride.mockResolvedValue({ id: 6, file: '/drives/art/backgrounds/New Backdrop.jpg' })
    const user = userEvent.setup()
    render(<PosterDriveSearchModal initialQuery="Breaking Bad" target={{ ...target, domain: 'artwork' }} onClose={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Pick Artwork' })).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByRole('button', { name: /Season/ })).toBeNull()
    expect(searchLibraryItems).not.toHaveBeenCalled()
    await waitFor(() => expect(searchArtwork).toHaveBeenCalledWith('Breaking Bad'))

    // The pinned logo reads "Using"; a background hit pins the background piece.
    expect((await screen.findByRole('button', { name: 'Stop using Old Logo' })).className).toContain('active')
    await user.click(screen.getByRole('button', { name: 'Use New Backdrop' }))
    await waitFor(() => expect(savePosterOverride).toHaveBeenCalledWith(expect.objectContaining({
      domain: 'artwork', slot: 'background', season: null, drive_id: 'art-a', file: '/drives/art/backgrounds/New Backdrop.jpg',
    })))
  })

  it('reads "In use" for the slot\'s current file and flags files other overrides pin', async () => {
    const CURRENT = '/drives/cl/Breaking Bad (2008) - Season 2.jpg'
    const S3 = '/drives/cl/Breaking Bad (2008) - Season 3.jpg'
    getPosterOverrides.mockResolvedValue([
      // This target's own pick for Season 2 → "Using".
      { id: 1, media_type: 'show', tmdb_id: 1396, title: 'Breaking Bad', year: 2008, scope: 'slot', season: 2, drive_id: 'cl-1', file: FILE },
      // A different slot's pick → "Pinned · …" on that file.
      { id: 2, media_type: 'show', tmdb_id: 1396, title: 'Breaking Bad', year: 2008, scope: 'slot', season: 3, drive_id: 'cl-1', file: S3 },
    ])
    searchLibraryItems.mockResolvedValue({ query: 'Breaking Bad', count: 1, total: 1, items: [libraryShow] })
    const row = (name: string, id: number, file: string) => ({ poster_name: name, drive_count: 1, drives: [{ ...hit.drives[0], poster_id: id, file_path: file }] })
    searchPosters.mockResolvedValue({ query: 'Breaking Bad', count: 3, items: [
      hit, row('Breaking Bad (2008) - Season 2', 2, CURRENT), row('Breaking Bad (2008) - Season 3', 3, S3),
    ] })
    render(
      <PosterDriveSearchModal
        initialQuery="Breaking Bad"
        target={target}
        inUse={[{ file: CURRENT, season: 2, slot: null }, { file: S3, season: 3, slot: null }]}
        onClose={vi.fn()}
      />,
    )

    // Own pick → removable "Using" button.
    expect((await screen.findByRole('button', { name: 'Stop using Any Poster' })).className).toContain('active')
    // The Season 2 slot is being picked: its current file reads "In use" and offers no Use button.
    expect(screen.getByText('In use')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Use Breaking Bad (2008) - Season 2' })).toBeNull()
    // Another slot's current file keeps its Use button (no extra note), and its pin is flagged.
    expect(screen.getByRole('button', { name: 'Use Breaking Bad (2008) - Season 3' })).toBeTruthy()
    expect(screen.queryByText(/In use ·/)).toBeNull()
    expect(screen.getByText('Pinned · Breaking Bad (2008) - Season 3')).toBeTruthy()
    expect(screen.queryByText('Pinned · Breaking Bad (2008) - Season 2')).toBeNull()
  })

  it("offers the library's seasons for the show, not just the drives' or a free number", async () => {
    searchLibraryItems.mockResolvedValue({ query: 'Breaking Bad', count: 1, total: 1, items: [libraryShow] })
    getPosterOverrides.mockResolvedValue([])
    searchPosters.mockResolvedValue({ query: 'Breaking Bad', count: 1, items: [hit] })
    savePosterOverride.mockResolvedValue({ id: 1, file: FILE })
    const user = userEvent.setup()
    render(<PosterDriveSearchModal initialQuery="Breaking Bad" target={target} onClose={vi.fn()} />)

    await waitFor(() => expect(searchLibraryItems).toHaveBeenCalledWith('Breaking Bad'))
    expect(await screen.findByRole('button', { name: 'Season 4' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Specials' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Season 2' }).className).toContain('active')
    expect(screen.queryByRole('spinbutton')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Season 4' }))
    await user.click(await screen.findByRole('button', { name: 'Use Any Poster' }))
    await waitFor(() => expect(savePosterOverride).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'show', tmdb_id: 1396, season: 4, drive_id: 'cl-1', file: FILE,
    })))
  })

  it("keeps the caller's seasons when the show is not in the library", async () => {
    searchLibraryItems.mockResolvedValue({ query: 'Breaking Bad', count: 0, total: 0, items: [] })
    getPosterOverrides.mockResolvedValue([])
    searchPosters.mockResolvedValue({ query: 'Breaking Bad', count: 0, items: [] })
    render(<PosterDriveSearchModal initialQuery="Breaking Bad" target={target} onClose={vi.fn()} />)

    await waitFor(() => expect(searchLibraryItems).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Season 1' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Season 3' })).toBeNull()
  })
})
