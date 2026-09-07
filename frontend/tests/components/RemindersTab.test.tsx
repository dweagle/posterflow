import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import RemindersTab from '../../src/components/maker-tools/RemindersTab'
import { RemindersProvider } from '../../src/contexts/RemindersContext'
import { EMPTY_PSD_CONFIG } from '../../src/components/maker-tools/TmdbItemCard'
import { deletePosterReminder, getPosterReminders, updatePosterReminderNote, type PosterReminder } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getPosterReminders: vi.fn(),
  updatePosterReminderNote: vi.fn(),
  deletePosterReminder: vi.fn(),
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../src/components/maker-tools/TmdbItemCard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/components/maker-tools/TmdbItemCard')>()),
  default: ({ item }: { item: { title: string } }) => <div data-testid="maker-card">{item.title}</div>,
}))
vi.mock('../../src/components/maker-tools/ArtworkFinderCard', () => ({
  default: ({ item, scopeLabel }: { item: { title: string }; scopeLabel: string | null }) => (
    <div data-testid="artwork-card">{item.title} / {scopeLabel ?? 'no scope'}</div>
  ),
}))
vi.mock('../../src/components/maker-tools/useArtworkScopes', () => ({
  useArtworkScopes: () => ({
    scopes: [{ index: 2, value: 'scope:abc', label: 'Art Drive' }],
    posterScopes: [],
    selectedValue: 'scope:abc',
    selected: { index: 2, value: 'scope:abc', label: 'Art Drive' },
    onSelectScope: vi.fn(),
    scopesLoaded: true,
  }),
}))

const mockedList = vi.mocked(getPosterReminders)
const mockedUpdate = vi.mocked(updatePosterReminderNote)
const mockedDelete = vi.mocked(deletePosterReminder)

const poster: PosterReminder = {
  id: 1, kind: 'poster', media_type: 'movie', tmdb_id: 27205, tvdb_id: null, imdb_id: 'tt1375666',
  title: 'Inception', year: '2010', poster_url: '', homepage: '', note: 'Border looks off',
  created_at: '2026-09-01T12:00:00+00:00', updated_at: null,
}
const artwork: PosterReminder = {
  id: 2, kind: 'artwork', media_type: 'tv', tmdb_id: 1396, tvdb_id: 81189, imdb_id: null,
  title: 'Breaking Bad', year: '2008', poster_url: '', homepage: '', note: '',
  created_at: '2026-09-02T12:00:00+00:00', updated_at: null,
}

const mount = () => render(
  <MemoryRouter>
    <RemindersProvider>
      <RemindersTab psdConfig={EMPTY_PSD_CONFIG} />
    </RemindersProvider>
  </MemoryRouter>,
)

describe('RemindersTab', () => {
  beforeEach(() => {
    mockedList.mockReset()
    mockedUpdate.mockReset()
    mockedDelete.mockReset()
  })
  afterEach(() => { cleanup() })

  it('shows an empty state when nothing is flagged', async () => {
    mockedList.mockResolvedValue([])
    mount()
    expect(await screen.findByText('No poster reminders')).toBeTruthy()
  })

  it('lists poster reminders as real cards with the note, and artwork ones under their own subtab', async () => {
    mockedList.mockResolvedValue([poster, artwork])
    mount()
    expect(await screen.findByText('Border looks off')).toBeTruthy()
    expect(screen.getByTestId('maker-card').textContent).toBe('Inception')
    expect(screen.queryByTestId('artwork-card')).toBeNull()
    expect(screen.getByRole('tab', { name: /Posters/ }).textContent).toContain('1')

    await userEvent.click(screen.getByRole('tab', { name: /Artwork/ }))
    expect(screen.getByTestId('artwork-card').textContent).toBe('Breaking Bad / Art Drive')
    expect(screen.getByText('No note')).toBeTruthy()
    expect(screen.queryByTestId('maker-card')).toBeNull()
    // The artwork scope picker rides on the toolbar only for artwork reminders.
    expect(screen.getByText('Artwork scope:')).toBeTruthy()
  })

  it('edits a note inline', async () => {
    mockedList.mockResolvedValue([poster])
    mockedUpdate.mockResolvedValue({ ...poster, note: 'Border fixed, re-export' })
    mount()
    await screen.findByText('Border looks off')

    await userEvent.click(screen.getByRole('button', { name: 'Edit' }))
    const box = screen.getByRole('textbox') as HTMLTextAreaElement
    expect(box.value).toBe('Border looks off')
    await userEvent.clear(box)
    await userEvent.type(box, 'Border fixed, re-export')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(mockedUpdate).toHaveBeenCalledWith(1, 'Border fixed, re-export')
    expect(await screen.findByText('Border fixed, re-export')).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('removes a reminder from the list', async () => {
    mockedList.mockResolvedValue([poster])
    mockedDelete.mockResolvedValue(undefined)
    mount()
    await screen.findByText('Border looks off')

    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(mockedDelete).toHaveBeenCalledWith(1)
    await waitFor(() => expect(screen.queryByText('Border looks off')).toBeNull())
    expect(screen.getByText('No poster reminders')).toBeTruthy()
  })
})
