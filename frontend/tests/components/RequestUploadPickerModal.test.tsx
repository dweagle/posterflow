import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const listPosterExports = vi.fn()
const fetchPosterExportFile = vi.fn()
vi.mock('../../src/api/makerTools', () => ({
  listPosterExports: (...args: unknown[]) => listPosterExports(...args),
  fetchPosterExportFile: (...args: unknown[]) => fetchPosterExportFile(...args),
  getPosterExportUrl: (f: { name: string; source: string }, thumb = false) =>
    `/exports/${encodeURIComponent(f.name)}?source=${f.source}${thumb ? '&thumb=1' : ''}`,
}))
vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}))

import RequestUploadPickerModal, { ageLabel, autoSelect, requestedSeasonsFromNotes, requestedSlots, seasonLabel, sortForRequest } from '../../src/components/community/RequestUploadPickerModal'
import type { CommunityRequest } from '../../src/api/community'
import type { PosterExportFile } from '../../src/api/makerTools'

const request = {
  id: 'req-1', tmdb_id: 157239, media_type: 'season', title: 'Alien: Earth', year: 2025, season_number: 1,
  poster_path: null, imdb_id: null, tvdb_id: null, status: 'in_progress', claimed_by: 'me', claimed_by_discord_id: '1',
  fulfilled_by: null, requested_by: 'them', requested_by_discord_id: '2', discord_thread_url: null,
  created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z',
} as CommunityRequest

const file = (over: Partial<PosterExportFile>): PosterExportFile => ({
  name: 'x.jpg', source: 'poster', size: 1, mtime: 1_700_000_000, season: null, match: null, ...over,
})
const FILES: PosterExportFile[] = [
  file({ name: 'Other (2020) {tmdb-1}.jpg', mtime: 1_700_000_300 }),
  file({ name: 'Alien Earth (2025) {tmdb-157239}.jpg', match: 'item', mtime: 1_700_000_200 }),
  file({ name: 'Alien Earth (2025) {tmdb-157239} - Season 1.jpg', match: 'exact', season: 1, mtime: 1_700_000_100 }),
]
const folders = [{ source: 'poster' as const, folder: '/exports', exists: true }]

describe('RequestUploadPickerModal', () => {
  afterEach(() => {
    cleanup()
    listPosterExports.mockReset()
    fetchPosterExportFile.mockReset()
  })

  it('queries the request identity, opens on Matching with exact matches first, and pre-selects the requested season', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)

    await waitFor(() => expect(screen.getByRole('button', { name: /^Matching/ })).toBeTruthy())
    expect(listPosterExports).toHaveBeenCalledWith({ tmdb_id: 157239, tvdb_id: null, title: 'Alien: Earth', year: 2025, seasons: [1] })

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

  it('a whole-show request pre-selects the newest export of every slot', async () => {
    const files = [
      ...FILES,
      file({ name: 'Alien Earth (2025) {tmdb-157239} (2).jpg', match: 'exact', mtime: 1_700_000_250 }),   // newer plain poster
      file({ name: 'Alien Earth (2025) {tmdb-157239} - Specials.jpg', match: 'item', season: 0, mtime: 1_700_000_050 }),
    ]
    listPosterExports.mockResolvedValue({ folders, files, truncated: false })
    const show = { ...request, media_type: 'show', season_number: null } as CommunityRequest
    render(<RequestUploadPickerModal request={show} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)

    await screen.findByText(/Selected the newest export for 3 posters/)
    expect(listPosterExports).toHaveBeenCalledWith(expect.objectContaining({ seasons: [] }))
    const pressed = screen.getAllByRole('button', { pressed: true }).map((b) => b.getAttribute('aria-label'))
    expect(pressed.sort()).toEqual([
      'Alien Earth (2025) {tmdb-157239} (2).jpg',
      'Alien Earth (2025) {tmdb-157239} - Season 1.jpg',
      'Alien Earth (2025) {tmdb-157239} - Specials.jpg',
    ])
    expect(screen.queryByText(/not requested/)).toBeNull()
    expect(screen.getByRole('button', { name: /to Discord/ }).textContent).toBe('Post 3 to Discord')
  })

  it('All exports shows every file and posting hands the selected ones over as File objects', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false })
    fetchPosterExportFile.mockImplementation(async (f: PosterExportFile) => new File(['bytes'], f.name, { type: 'image/jpeg' }))
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
    const files = onPost.mock.calls[0][0] as File[]
    expect(files.map((f) => f.name)).toEqual(['Alien Earth (2025) {tmdb-157239} - Season 1.jpg', 'Other (2020) {tmdb-1}.jpg'])
    expect(files[0]).toBeInstanceOf(File)
    expect(fetchPosterExportFile).toHaveBeenCalledTimes(2)
  })

  it('falls back to All exports when nothing matches and offers the OS dialog', async () => {
    listPosterExports.mockResolvedValue({ folders, files: [FILES[0]], truncated: false })
    const onPickFromComputer = vi.fn()
    const user = userEvent.setup()
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={onPickFromComputer} />)

    await screen.findByRole('button', { name: 'Other (2020) {tmdb-1}.jpg' })
    expect(screen.getByRole('button', { name: /^All exports/ }).className).toBe('active')

    await user.click(screen.getByRole('button', { name: /From computer/ }))
    expect(onPickFromComputer).toHaveBeenCalledTimes(1)
  })

  it('explains when no export folder is configured or the folder is missing', async () => {
    listPosterExports.mockResolvedValue({ folders: [], files: [], truncated: false })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText(/No export folder is set/)
    expect(screen.queryByRole('button', { name: /^Matching/ })).toBeNull()
    cleanup()

    listPosterExports.mockResolvedValue({ folders: [{ source: 'poster', folder: '/gone', exists: false }], files: [], truncated: false })
    render(<RequestUploadPickerModal request={request} onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    await screen.findByText('Folder not found on the server: /gone')
  })

  it('labels the primary button after the upload action', async () => {
    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false })
    render(<RequestUploadPickerModal request={request} action="idarr" onClose={vi.fn()} onPost={vi.fn()} onPickFromComputer={vi.fn()} />)
    expect((await screen.findByRole('button', { name: /to IDarr/ })).textContent).toBe('Add 1 to IDarr')
    cleanup()

    listPosterExports.mockResolvedValue({ folders, files: FILES, truncated: false })
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

  it('requestedSlots follows the request type, season number, and multi-season notes', () => {
    expect(requestedSlots({ media_type: 'movie', season_number: null, notes: null })).toEqual(new Set([null]))
    expect(requestedSlots({ media_type: 'collection', season_number: null, notes: null })).toEqual(new Set([null]))
    expect(requestedSlots({ media_type: 'season', season_number: 3, notes: null })).toEqual(new Set([3]))
    expect(requestedSlots({ media_type: 'season', season_number: null, notes: 'Seasons: 0, 2\nStyle notes' })).toEqual(new Set([0, 2]))
    expect(requestedSlots({ media_type: 'season', season_number: null, notes: 'just notes' })).toBe('all')
    expect(requestedSlots({ media_type: 'show', season_number: null, notes: null })).toBe('all')
    expect(requestedSeasonsFromNotes('Seasons: 1, x, 4')).toEqual([1, 4])
    expect(requestedSeasonsFromNotes(null)).toEqual([])
  })

  it('autoSelect takes the newest same-item file per slot, limited to the requested slots', () => {
    const files = [
      file({ name: 'plain-old', match: 'exact', mtime: 1 }),
      file({ name: 'plain-new', match: 'exact', mtime: 3 }),
      file({ name: 's1', match: 'item', season: 1, mtime: 2 }),
      file({ name: 's2-old', match: 'item', season: 2, mtime: 1 }),
      file({ name: 's2-new', match: 'item', season: 2, mtime: 9 }),
      file({ name: 'stranger', match: null, season: 1, mtime: 99 }),
    ]
    expect(autoSelect(files, 'all').map((f) => f.name).sort()).toEqual(['plain-new', 's1', 's2-new'])
    expect(autoSelect(files, new Set([null])).map((f) => f.name)).toEqual(['plain-new'])
    expect(autoSelect(files, new Set([1, 2])).map((f) => f.name).sort()).toEqual(['s1', 's2-new'])
    expect(autoSelect(files, new Set([5]))).toEqual([])
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
