import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RenameTagModal from '../../src/components/tools/RenameTagModal'

const mockShowToast = vi.fn()
const mockGetRenameTagged = vi.fn()
const mockSaveRenameTagged = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}))

vi.mock('../../src/api/tools', () => ({
  getRenameTagged: (...args: unknown[]) => mockGetRenameTagged(...args),
  saveRenameTagged: (...args: unknown[]) => mockSaveRenameTagged(...args),
}))

const library = [
  { id: 1, title: 'Alpha', year: 2020, tagged: true },
  { id: 2, title: 'Beta', year: 2021, tagged: false },
  { id: 3, title: 'Beta Two', year: null, tagged: false },
]

const renderModal = (onClose = vi.fn(), kind: 'ignore' | 'cycle' = 'ignore', tag = 'rename-ignore') => {
  render(<RenameTagModal kind={kind} toolId="radarr_rename" instance="Radarr 4K" tag={tag} noun="movie" nouns="movies" onClose={onClose} />)
  return onClose
}

describe('RenameTagModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetRenameTagged.mockResolvedValue({ instance: 'Radarr 4K', tag: 'rename-ignore', items: library })
    mockSaveRenameTagged.mockResolvedValue({ success: true, added: 1, removed: 1 })
  })

  afterEach(() => cleanup())

  it('lists the ignored movies, finds others by search, and saves every change at once', async () => {
    const user = userEvent.setup()
    const onClose = renderModal()
    expect(screen.getByRole('dialog', { name: 'Ignored movies' })).toBeTruthy()
    expect(screen.getByText('Radarr 4K · tag rename-ignore')).toBeTruthy()
    const ignored = await screen.findByRole('list', { name: 'Ignored movies' })
    expect(mockGetRenameTagged).toHaveBeenCalledWith('radarr_rename', 'Radarr 4K', 'rename-ignore')
    expect(within(ignored).getAllByRole('checkbox')).toHaveLength(1)  // only the ignored one until a search
    expect((within(ignored).getByRole('checkbox', { name: /Alpha/ }) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)

    await user.type(screen.getByRole('searchbox', { name: 'Search movies' }), 'beta')
    const results = screen.getByRole('list', { name: 'Search results' })
    expect(within(results).getAllByRole('checkbox').map((box) => box.closest('label')?.textContent)).toEqual(['Beta 2021', 'Beta Two'])
    await user.click(within(results).getByRole('checkbox', { name: 'Beta 2021' }))
    await user.clear(screen.getByRole('searchbox', { name: 'Search movies' }))
    // Back on the ignored list: the new one shows, and an unticked one stays until Save.
    const list = screen.getByRole('list', { name: 'Ignored movies' })
    await user.click(within(list).getByRole('checkbox', { name: /Alpha/ }))
    expect(within(list).getAllByRole('checkbox').map((box) => (box as HTMLInputElement).checked)).toEqual([false, true])
    expect(screen.getByText('1 ignored · 2 unsaved')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockSaveRenameTagged).toHaveBeenCalledWith('radarr_rename', { instance: 'Radarr 4K', tag: 'rename-ignore', add: [2], remove: [1] }))
    expect(mockShowToast).toHaveBeenCalledWith('Radarr 4K: 1 movie now ignored, 1 no longer ignored', 'success')
    expect(onClose).toHaveBeenCalled()
  })

  it('says when nothing is ignored or nothing matches, and shows a load failure', async () => {
    const user = userEvent.setup()
    mockGetRenameTagged.mockResolvedValue({ instance: 'Radarr 4K', tag: 'rename-ignore', items: library.map((i) => ({ ...i, tagged: false })) })
    renderModal()
    expect(await screen.findByText('No movies carry the tag yet. Search to add one.')).toBeTruthy()
    await user.type(screen.getByRole('searchbox', { name: 'Search movies' }), 'zeta')
    expect(screen.getByText('Nothing in Radarr 4K matches "zeta".')).toBeTruthy()

    cleanup()
    mockGetRenameTagged.mockRejectedValue(new Error('boom'))
    renderModal()
    expect((await screen.findByRole('alert')).textContent).toBe('Could not load the movies in Radarr 4K')
  })

  it('lists the tagged items and search matches 100 at a time, with Show more for the rest', async () => {
    const user = userEvent.setup()
    const many = Array.from({ length: 230 }, (_, n) => ({ id: n + 1, title: `Movie ${n + 1}`, year: 2000, tagged: n < 120 }))
    mockGetRenameTagged.mockResolvedValue({ instance: 'Radarr 4K', tag: 'rename-ignore', items: many })
    renderModal()
    const tagged = () => within(screen.getByRole('list', { name: 'Ignored movies' })).getAllByRole('checkbox')
    await screen.findByRole('list', { name: 'Ignored movies' })
    expect(tagged()).toHaveLength(100)
    expect(screen.getByText('Showing 100 of 120.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Show 20 more' }))
    expect(tagged()).toHaveLength(120)
    expect(screen.queryByRole('button', { name: /^Show \d+ more$/ })).toBeNull()

    const search = screen.getByRole('searchbox', { name: 'Search movies' })
    const rows = () => within(screen.getByRole('list', { name: 'Search results' })).getAllByRole('checkbox')
    await user.type(search, 'movie')
    expect(rows()).toHaveLength(100)  // a new search starts at 100 again
    expect(screen.getByText('Showing 100 of 230.')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Show 100 more' }))
    expect(rows()).toHaveLength(200)
    await user.click(screen.getByRole('button', { name: 'Show 30 more' }))
    expect(rows()).toHaveLength(230)
    expect(screen.queryByText(/^Showing /)).toBeNull()
    await user.clear(search)
    expect(tagged()).toHaveLength(100)  // and so does clearing it
  })

  it('words the cycle tag as what the pass has checked', async () => {
    const user = userEvent.setup()
    const onClose = renderModal(vi.fn(), 'cycle', 'renamed')
    expect(screen.getByRole('dialog', { name: 'Checked movies' })).toBeTruthy()
    expect(screen.getByText('Radarr 4K · tag renamed')).toBeTruthy()
    expect(screen.getByText(/checked these movies this pass and skips them until the pass ends/)).toBeTruthy()
    const list = await screen.findByRole('list', { name: 'Checked movies' })
    expect(mockGetRenameTagged).toHaveBeenCalledWith('radarr_rename', 'Radarr 4K', 'renamed')
    expect(screen.getByText('1 checked')).toBeTruthy()
    await user.click(within(list).getByRole('checkbox', { name: /Alpha/ }))
    await user.type(screen.getByRole('searchbox', { name: 'Search movies' }), 'two')
    await user.click(screen.getByRole('checkbox', { name: 'Beta Two' }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockSaveRenameTagged).toHaveBeenCalledWith('radarr_rename', { instance: 'Radarr 4K', tag: 'renamed', add: [3], remove: [1] }))
    expect(mockShowToast).toHaveBeenCalledWith('Radarr 4K: 1 movie marked checked, 1 to check again', 'success')
    expect(onClose).toHaveBeenCalled()
  })

  it('keeps the dialog open when the save fails', async () => {
    const user = userEvent.setup()
    mockSaveRenameTagged.mockRejectedValue(new Error('boom'))
    const onClose = renderModal()
    const list = await screen.findByRole('list', { name: 'Ignored movies' })
    await user.click(within(list).getByRole('checkbox', { name: /Alpha/ }))
    await user.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Could not save the tags in Radarr 4K', 'error'))
    expect(onClose).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(false)
  })
})
