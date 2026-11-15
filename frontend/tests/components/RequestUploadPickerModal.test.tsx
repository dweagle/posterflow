import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const listPosterExports = vi.fn()
vi.mock('../../src/api/makerTools', () => ({
  listPosterExports: (...args: unknown[]) => listPosterExports(...args),
  getPosterExportUrl: (f: { name: string; source: string }, thumb = false) =>
    `/exports/${encodeURIComponent(f.name)}?source=${f.source}${thumb ? '&thumb=1' : ''}`,
}))
vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}))

import RequestUploadPickerModal, { ageLabel, seasonLabel, sortForRequest } from '../../src/components/community/RequestUploadPickerModal'
import type { CommunityRequest } from '../../src/api/community'
import type { PosterExportFile } from '../../src/api/makerTools'

const request = {
  id: 'req-1', tmdb_id: 157239, media_type: 'season', title: 'Alien: Earth', year: 2025, season_number: 1,
  poster_path: null, imdb_id: null, tvdb_id: null, status: 'in_progress', claimed_by: 'me', claimed_by_discord_id: '1',
  fulfilled_by: null, requested_by: 'them', requested_by_discord_id: '2', discord_thread_url: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
} as CommunityRequest

const file = (over: Partial<PosterExportFile>): PosterExportFile => ({
  name: 'x.jpg', source: 'poster', size: 1, mtime: 1_700_000_000, season: null, collection: false, match: null, requested: false, selected: false, ...over,
})
// What the server answers for the Season 1 request below: it says which poster is asked for and which export starts selected.
const FILES: PosterExportFile[] = [
  file({ name: 'Other (2020) {tmdb-1}.jpg', mtime: 1_700_000_300 }),
  file({ name: 'Alien Earth (2025) {tmdb-157239}.jpg', match: 'item', mtime: 1_700_000_200 }),
  file({ name: 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg', match: 'exact', season: 1, requested: true, selected: true, mtime: 1_700_000_100 }),
]
const folders = [{ source: 'poster' as const, folder: '/exports', exists: true }]

describe('RequestUploadPickerModal', () => {
  afterEach(() => {
    cleanup()
    listPosterExports.mockReset()
  })

  it('queries the request identity, opens on Matching with exact matches first, and pre-selects the requested season', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false, seasons: [1] })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^Matching/ })).toBeTruthy())
    expect(listPosterExports).toHaveBeenCalledWith({ tmdb_id: 157239, tvdb_id: null, title: 'Alien: Earth', year: 2025, media_type: 'season', season_number: 1 })

    const thumbs = screen.getAllByRole('button', { name: /\.jpg$/ })
    expect(thumbs.map((b) => b.getAttribute('aria-label'))).toEqual(['Alien Earth (2025) {tmdb-157239} - Season 1.jpg', 'Alien Earth (2025) {tmdb-157239}.jpg'])
    expect(thumbs.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    expect(screen.getByText('Season 1')).toBeTruthy()
    expect(screen.getByText(/not requested/)).toBeTruthy()
    expect(screen.getByText(/Selected the newest matching export/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /to Discord/ }).textContent).toBe('Post 1 to Discord')
    expect(screen.getByRole('button', { name: /^Matching/ }).textContent).toContain('2')
    expect(screen.getByRole('button', { name: /^All exports/ }).textContent).toContain('3')
  })

  it('a whole-show request starts with every export the server selected', async () => {
    const files = [
      FILES[0],
      file({ name: 'Alien Earth (2025) {tmdb-157239}.jpg', match: 'exact', requested: true, mtime: 1_700_000_200 }),   // an older plain poster
      file({ name: 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg', match: 'item', season: 1, requested: true, selected: true, mtime: 1_700_000_100 }),
      file({ name: 'Alien Earth (2025) {tmdb-157239} (2).jpg', match: 'exact', requested: true, selected: true, mtime: 1_700_000_250 }),
      file({ name: 'Alien Earth (2025) {tmdb-157239} - Specials.jpg', match: 'item', season: 0, requested: true, selected: true, mtime: 1_700_000_050 }),
    ]
    listPosterExports.mockResolvedValue({ folders, files, truncated: false, seasons: [] })
    const show = { ...request, media_type: 'show', season_number: null, notes: 'Seasons: 1\nany style' } as CommunityRequest
    render(<RequestUploadPickerModal request={show} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)

    await screen.findByText(/Selected the newest export for 3 posters/)
    // the request goes to the server as it is: the server reads the type, season and notes
    expect(listPosterExports).toHaveBeenCalledWith(expect.objectContaining({ media_type: 'show', season_number: null, notes: 'Seasons: 1\nany style' }))
    expect(screen.getByText(/Upload poster · Alien: Earth \(2025\)$/)).toBeTruthy()   // no season named for a whole show
    const pressed = screen.getAllByRole('button', { pressed: true }).map((b) => b.getAttribute('aria-label'))
    expect(pressed.sort()).toEqual([
      'Alien Earth (2025) {tmdb-157239} (2).jpg',
      'Alien Earth (2025) {tmdb-157239} - Season 1.jpg',
      'Alien Earth (2025) {tmdb-157239} - Specials.jpg',
    ])
    expect(screen.queryByText(/not requested/)).toBeNull()
    expect(screen.getByRole('button', { name: /to Discord/ }).textContent).toBe('Post 3 to Discord')
  })

  it('a collection poster exported from the same PSD never takes the place of the title\'s own poster', async () => {
    const files = [
      file({ name: 'Alien (1979) {tmdb-348} {imdb-tt0078748}.jpg', match: 'exact', requested: true, selected: true, mtime: 1_700_000_100 }),
      file({ name: 'Alien (1979) {tmdb-348} {imdb-tt0078748} - Collection.jpg', match: 'item', collection: true, mtime: 1_700_000_900 }),   // exported last
    ]
    listPosterExports.mockResolvedValue({ folders, files, truncated: false, seasons: [1] })
    const movie = { ...request, tmdb_id: 348, media_type: 'movie', title: 'Alien', year: 1979, season_number: null } as CommunityRequest
    render(<RequestUploadPickerModal request={movie} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)

    await screen.findByText(/Selected the newest matching export/)
    const thumbs = screen.getAllByRole('button', { name: /\.jpg$/ })
    expect(thumbs.map((b) => b.getAttribute('aria-label'))).toEqual(files.map((f) => f.name))   // the movie poster first, both listed
    expect(thumbs.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true', 'false'])
    expect(screen.getByText('Collection')).toBeTruthy()            // badged, so the two thumbnails are told apart
    expect(screen.getAllByText(/not requested/).length).toBe(1)
    expect(screen.getByRole('button', { name: /to Discord/ }).textContent).toBe('Post 1 to Discord')
  })

  it('names the seasons of a multi-season request once the server has read its notes', async () => {
    listPosterExports.mockResolvedValue({ folders, files: [], truncated: false, seasons: [2, 0] })
    const multi = { ...request, season_number: null, notes: 'Seasons: 2, 0' } as CommunityRequest
    render(<RequestUploadPickerModal request={multi} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText(/Upload poster · Alien: Earth \(2025\) · Season 2, Specials$/)
  })

  it('All exports shows every file and posting hands the selected ones over by name', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false, seasons: [1] })
    const onPost = vi.fn()
    const user = userEvent.setup()
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={onPost} onPickFromComputer={vi.fn()} />)

    await user.click(await screen.findByRole('button', { name: /^All exports/ }))
    expect(screen.getAllByRole('button', { name: /\.jpg$/ })).toHaveLength(3)

    // the requested season is pre-selected; deselecting it disables Post, adding another re-enables it
    const post = screen.getByRole('button', { name: /to Discord/ })
    expect(post.textContent).toBe('Post 1 to Discord')
    await user.click(screen.getByRole('button', { name: 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg' }))
    expect((post as HTMLButtonElement).disabled).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg' }))
    await user.click(screen.getByRole('button', { name: 'Other (2020) {tmdb-1}.jpg' }))
    expect(post.textContent).toBe('Post 2 to Discord')
    await user.click(post)

    await waitFor(() => expect(onPost).toHaveBeenCalledTimes(1))
    // the exports are on the server already: it is told which, and nothing is downloaded here
    const picks = onPost.mock.calls[0][0] as PosterExportFile[]
    expect(picks.map((f) => [f.source, f.name])).toEqual([['poster', 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg'], ['poster', 'Other (2020) {tmdb-1}.jpg']])
  })

  it('falls back to All exports when nothing matches and offers the OS dialog', async () => {
    listPosterExports.mockResolvedValue({ folders, files: [FILES[0]], truncated: false, seasons: [1] })
    const onPickFromComputer = vi.fn()
    const user = userEvent.setup()
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={onPickFromComputer} />)

    await screen.findByRole('button', { name: 'Other (2020) {tmdb-1}.jpg' })
    expect(screen.getByRole('button', { name: /^All exports/ }).className).toBe('active')

    await user.click(screen.getByRole('button', { name: /From computer/ }))
    expect(onPickFromComputer).toHaveBeenCalledTimes(1)
  })

  it('explains when no export folder is configured or the folder is missing', async () => {
    listPosterExports.mockResolvedValue({ folders: [], files: [], truncated: false, seasons: [1] })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText(/No export folder is set/)
    expect(screen.queryByRole('button', { name: /^Matching/ })).toBeNull()
    cleanup()

    listPosterExports.mockResolvedValue({ folders: [{ source: 'poster', folder: '/gone', exists: false }], files: [], truncated: false, seasons: [1] })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText('Folder not found on the server: /gone')
  })

  it('labels the primary button after the upload action', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false, seasons: [1] })
    render(<RequestUploadPickerModal request={request} action="idarr" onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    expect((await screen.findByRole('button', { name: /to IDarr/ })).textContent).toBe('Add 1 to IDarr')
    cleanup()

    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false, seasons: [1] })
    render(<RequestUploadPickerModal request={request} action="discord_idarr" onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    expect((await screen.findByRole('button', { name: /to Discord/ })).textContent).toBe('Post 1 to Discord + IDarr')
  })

  it('shows the list error and keeps the OS dialog available', async () => {
    listPosterExports.mockRejectedValue(new Error('boom'))
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText('Failed to list exports')
    expect(screen.getByRole('button', { name: /From computer/ })).toBeTruthy()
  })
})

describe('picker helpers', () => {
  it('seasonLabel names specials and seasons', () => {
    expect(seasonLabel(null)).toBeNull()
    expect(seasonLabel(0)).toBe('Specials')
    expect(seasonLabel(3)).toBe('Season 3')
  })

  it('ageLabel buckets by age', () => {
    const now = 1_700_000_000_000
    expect(ageLabel(1_700_000_000 - 5, now)).toBe('just now')
    expect(ageLabel(1_700_000_000 - 300, now)).toBe('5m ago')
    expect(ageLabel(1_700_000_000 - 7200, now)).toBe('2h ago')
    expect(ageLabel(1_700_000_000 - 3 * 86400, now)).toBe('3d ago')
    expect(ageLabel(1_700_000_000 - 30 * 86400, now)).toMatch(/\d/)
  })

  it('sortForRequest ranks exact, then same title, then the rest, newest first within a rank', () => {
    const sorted = sortForRequest([
      file({ name: 'c', match: null, mtime: 9 }),
      file({ name: 'b-old', match: 'exact', mtime: 1 }),
      file({ name: 'a', match: 'item', mtime: 5 }),
      file({ name: 'b-new', match: 'exact', mtime: 2 }),
    ])
    expect(sorted.map((f) => f.name)).toEqual(['b-new', 'b-old', 'a', 'c'])
  })
})
