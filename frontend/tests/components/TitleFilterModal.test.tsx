import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TitleFilterModal from '../../src/components/tools/TitleFilterModal'

const mockGetArrTitles = vi.fn()

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}))

vi.mock('../../src/api/tools', () => ({
  getArrTitles: (...args: unknown[]) => mockGetArrTitles(...args),
}))

const library = [
  { id: 1, title: 'Alpha', year: 2020 },
  { id: 2, title: 'Alphabet City', year: 1984 },
  { id: 3, title: 'Beta', year: null },
  { id: 4, title: 'The Alpha Test', year: 2021 },
]

const renderModal = (value = '') => {
  const onApply = vi.fn()
  const onClose = vi.fn()
  render(<TitleFilterModal toolId="sonarr_rename" instance="Sonarr 4K" value={value} noun="series" nouns="series" onClose={onClose} onApply={onApply} />)
  return { onApply, onClose }
}
const titles = (dialog: HTMLElement) => within(dialog).queryAllByRole('listitem').map((li) => li.textContent)

describe('TitleFilterModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetArrTitles.mockResolvedValue({ instance: 'Sonarr 4K', items: library })
  })

  afterEach(() => cleanup())

  it('shows which titles the text matches, as a run would, and uses a clicked title', async () => {
    const user = userEvent.setup()
    const { onApply } = renderModal()
    const dialog = screen.getByRole('dialog', { name: 'Title filter' })
    expect(within(dialog).getByText(/Loading the series in Sonarr 4K/)).toBeTruthy()
    await within(dialog).findByRole('list', { name: 'Matching titles' })
    expect(mockGetArrTitles).toHaveBeenCalledWith('sonarr_rename', 'Sonarr 4K')
    expect(titles(dialog)).toEqual(['Alpha 2020', 'Alphabet City 1984', 'Beta', 'The Alpha Test 2021'])
    expect(within(dialog).getByText('No filter: all 4 series')).toBeTruthy()

    // The title contains the text, capitals aside. The year is not part of it.
    const text = within(dialog).getByLabelText('Title filter text') as HTMLInputElement
    await user.type(text, 'ALPHA')
    expect(titles(dialog)).toEqual(['Alpha 2020', 'Alphabet City 1984', 'The Alpha Test 2021'])
    expect(within(dialog).getByText('3 of 4 series match')).toBeTruthy()
    await user.clear(text)
    await user.type(text, '2020')
    expect(titles(dialog)).toEqual([])
    expect(within(dialog).getByText(/No title in Sonarr 4K contains "2020"\. A run would find nothing to do\./)).toBeTruthy()

    // A clicked title becomes the text, and the list shows what that still matches.
    await user.clear(text)
    await user.type(text, 'alphab')
    await user.click(within(dialog).getByRole('button', { name: /Alphabet City/ }))
    expect(text.value).toBe('Alphabet City')
    expect(within(dialog).getByText('1 of 4 series match')).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenCalledWith('Alphabet City')
  })

  it('opens with the filter as it stands, applies on Enter, and can clear it', async () => {
    const user = userEvent.setup()
    const { onApply, onClose } = renderModal('  beta ')
    const dialog = screen.getByRole('dialog', { name: 'Title filter' })
    await within(dialog).findByRole('list', { name: 'Matching titles' })
    expect(titles(dialog)).toEqual(['Beta'])
    const text = within(dialog).getByLabelText('Title filter text') as HTMLInputElement
    await user.type(text, '{Enter}')
    expect(onApply).toHaveBeenLastCalledWith('beta')
    await user.clear(text)
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenLastCalledWith('')
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('lists a long library a hundred at a time, and says so when the arr cannot be read', async () => {
    const user = userEvent.setup()
    mockGetArrTitles.mockResolvedValueOnce({ instance: 'Sonarr 4K', items: Array.from({ length: 230 }, (_, n) => ({ id: n + 1, title: `Show ${n + 1}`, year: 2000 })) })
    renderModal()
    const dialog = screen.getByRole('dialog', { name: 'Title filter' })
    await within(dialog).findByRole('list', { name: 'Matching titles' })
    expect(titles(dialog)).toHaveLength(100)
    expect(within(dialog).getByText('Showing 100 of 230.')).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Show 100 more' }))
    expect(titles(dialog)).toHaveLength(200)
    await user.click(within(dialog).getByRole('button', { name: 'Show 30 more' }))
    expect(titles(dialog)).toHaveLength(230)
    cleanup()
    mockGetArrTitles.mockRejectedValueOnce(new Error('down'))
    renderModal()
    expect((await screen.findByRole('alert')).textContent).toBe('Could not load the series in Sonarr 4K')
  })
})
