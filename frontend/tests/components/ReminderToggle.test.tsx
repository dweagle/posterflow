import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReminderToggle from '../../src/components/maker-tools/ReminderToggle'
import { RemindersProvider } from '../../src/contexts/RemindersContext'
import { deletePosterReminder, getPosterReminders, savePosterReminder, type PosterReminder } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getPosterReminders: vi.fn(),
  savePosterReminder: vi.fn(),
  deletePosterReminder: vi.fn(),
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const mockedList = vi.mocked(getPosterReminders)
const mockedSave = vi.mocked(savePosterReminder)
const mockedDelete = vi.mocked(deletePosterReminder)

const item = {
  tmdb_id: 153312,
  media_type: 'tv' as const,
  title: 'Tulsa King',
  year: '2022',
  tvdb_id: 405297,
  imdb_id: 'tt16358384',
  poster_url: 'https://img/x.jpg',
  homepage: 'https://www.themoviedb.org/tv/153312',
}

const existing: PosterReminder = {
  id: 7, kind: 'poster', media_type: 'tv', tmdb_id: 153312, tvdb_id: 405297, imdb_id: 'tt16358384',
  title: 'Tulsa King', year: '2022', poster_url: 'https://img/x.jpg', homepage: '', note: 'Redo the title',
  created_at: '2026-09-07T00:00:00+00:00', updated_at: null,
}

const ADD = 'Add reminder for Tulsa King (2022)'
const EDIT = 'Edit reminder for Tulsa King (2022)'

const mount = () => render(
  <RemindersProvider>
    <ReminderToggle kind="poster" item={item} />
  </RemindersProvider>,
)

describe('ReminderToggle', () => {
  beforeEach(() => {
    mockedList.mockReset()
    mockedSave.mockReset()
    mockedDelete.mockReset()
  })
  afterEach(() => { cleanup() })

  it('renders nothing outside a RemindersProvider', () => {
    const { container } = render(<ReminderToggle kind="poster" item={item} />)
    expect(container.innerHTML).toBe('')
  })

  it('clicking the bell opens the note popover and saves a reminder', async () => {
    mockedList.mockResolvedValue([])
    mockedSave.mockResolvedValue({ ...existing, note: 'Wait for better key art' })
    mount()
    const bell = await screen.findByRole('button', { name: ADD })
    expect(bell.getAttribute('aria-pressed')).toBe('false')

    await userEvent.click(bell)
    const dialog = screen.getByRole('dialog', { name: 'Add reminder' })
    expect(dialog.textContent).toContain('Tulsa King (2022)')
    await userEvent.type(screen.getByRole('textbox'), 'Wait for better key art')
    await userEvent.click(screen.getByRole('button', { name: 'Add reminder' }))

    expect(mockedSave).toHaveBeenCalledWith({
      kind: 'poster', media_type: 'tv', tmdb_id: 153312, tvdb_id: 405297, imdb_id: 'tt16358384',
      title: 'Tulsa King', year: '2022', poster_url: 'https://img/x.jpg',
      homepage: 'https://www.themoviedb.org/tv/153312', note: 'Wait for better key art',
    })
    const pressed = await screen.findByRole('button', { name: EDIT })
    expect(pressed.getAttribute('aria-pressed')).toBe('true')
    expect(pressed.className).toContain('active')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('cancel saves nothing and leaves the bell unflagged', async () => {
    mockedList.mockResolvedValue([])
    mount()
    await userEvent.click(await screen.findByRole('button', { name: ADD }))
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mockedSave).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: ADD }).getAttribute('aria-pressed')).toBe('false')
  })

  it('a flagged item shows the amber bell; its popover is prefilled and can save or remove', async () => {
    mockedList.mockResolvedValue([existing])
    mockedSave.mockResolvedValue({ ...existing, note: 'Redo the title, then re-export' })
    mockedDelete.mockResolvedValue(undefined)
    mount()
    const bell = await screen.findByRole('button', { name: EDIT })
    expect(bell.getAttribute('data-tooltip')).toContain('Redo the title')

    await userEvent.click(bell)
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(box.value).toBe('Redo the title')
    await userEvent.type(box, ', then re-export')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(mockedSave).toHaveBeenCalledWith(expect.objectContaining({ note: 'Redo the title, then re-export' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())

    await userEvent.click(screen.getByRole('button', { name: EDIT }))
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(mockedDelete).toHaveBeenCalledWith(7)
    expect(await screen.findByRole('button', { name: ADD })).toBeTruthy()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('the popover hangs from its top-right corner under the bell, opening inward', async () => {
    mockedList.mockResolvedValue([])
    vi.stubGlobal('innerWidth', 1200)
    vi.stubGlobal('innerHeight', 800)
    const rect = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 880, right: 900, top: 380, bottom: 400, width: 20, height: 20, x: 880, y: 380, toJSON: () => ({}),
    } as DOMRect)
    mount()
    await userEvent.click(await screen.findByRole('button', { name: ADD }))
    const dialog = screen.getByRole('dialog', { name: 'Add reminder' })
    // right edge = bell's right (900) → left = 900 - 300; top = bell's bottom + 6
    expect(dialog.style.left).toBe('600px')
    expect(dialog.style.top).toBe('406px')
    rect.mockRestore()
  })

  it('a different item of the same kind is not flagged by another item\'s reminder', async () => {
    mockedList.mockResolvedValue([existing])
    render(
      <RemindersProvider>
        <ReminderToggle kind="poster" item={{ ...item, tmdb_id: 1 }} />
        <ReminderToggle kind="artwork" item={item} />
      </RemindersProvider>,
    )
    await waitFor(() => expect(mockedList).toHaveBeenCalled())
    const bells = screen.getAllByRole('button')
    expect(bells.map((b) => b.getAttribute('aria-pressed'))).toEqual(['false', 'false'])
  })
})
