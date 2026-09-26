import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GDrives from '../../src/pages/GDrives'

const renderWithRouter = (ui: ReactElement) =>
  render(
    <MemoryRouter>
      {ui}
    </MemoryRouter>,
  )

const mockShowToast = vi.fn()
const mockGetDrives = vi.fn()
const mockSubscribeDrive = vi.fn()
const mockUnsubscribeDrive = vi.fn()
const mockMarkNewDrivesSeen = vi.fn()
const mockDismissNewDrives = vi.fn()
const mockRefreshNewDrives = vi.fn()

type NewDriveEntry = {
  id: number
  drive_id: string
  name: string
  display_name: string | null
  style_type: string | null
  added_at: string
  seen: boolean
}
const EMPTY_NEW_DRIVES = { poster: [] as NewDriveEntry[], artwork: [] as NewDriveEntry[], unseen_count: 0 }
let mockNewDrives = EMPTY_NEW_DRIVES
const buildNewDrive = (overrides?: Partial<NewDriveEntry>): NewDriveEntry => ({
  id: 7,
  drive_id: 'new-drive',
  name: 'Fresh Drive',
  display_name: null,
  style_type: 'MM2K',
  added_at: '2026-09-25T00:00:00+00:00',
  seen: false,
  ...overrides,
})

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/contexts/AppEventsContext', () => ({
  useAppEvents: () => ({ jobs: [], newDrives: mockNewDrives, refreshNewDrives: mockRefreshNewDrives }),
}))

vi.mock('../../src/api/client', () => ({
  getDrives: (...args: unknown[]) => mockGetDrives(...args),
  subscribeDrive: (...args: unknown[]) => mockSubscribeDrive(...args),
  unsubscribeDrive: (...args: unknown[]) => mockUnsubscribeDrive(...args),
  startSync: vi.fn(),
  startSyncAll: vi.fn(),
  updateDrive: vi.fn(),
  createCustomDrive: vi.fn(),
  deleteDrive: vi.fn(),
  reloadDrives: vi.fn(),
  markNewDrivesSeen: (...args: unknown[]) => mockMarkNewDrivesSeen(...args),
  dismissNewDrives: (...args: unknown[]) => mockDismissNewDrives(...args),
  getApiErrorMessage: vi.fn(() => 'error'),
}))

vi.mock('../../src/components/DriveEditModal', () => ({
  default: () => null,
}))

vi.mock('../../src/components/AddCustomDriveModal', () => ({
  default: () => null,
}))

// Minimal functional stand-in: renders the dialog's buttons/children when open, so tests
// can drive confirm flows without the real modal markup.
vi.mock('../../src/components/ConfirmDialog', () => ({
  default: ({ isOpen, title, confirmText, cancelText, onConfirm, onCancel, children }: {
    isOpen: boolean
    title: string
    confirmText?: string
    cancelText?: string
    onConfirm: () => void
    onCancel: () => void
    children?: React.ReactNode
  }) =>
    isOpen ? (
      <div role="dialog" aria-label={title}>
        {children}
        <button onClick={onCancel}>{cancelText ?? 'Cancel'}</button>
        <button onClick={onConfirm}>{confirmText ?? 'Confirm'}</button>
      </div>
    ) : null,
}))

const buildDrive = (overrides?: Partial<{
  id: number
  name: string
  drive_id: string
  style_type: string
  subscribed: boolean
  is_custom: boolean
  is_deprecated: boolean
  poster_count: number
  sync_file_count: number
}> ) => ({
  id: 1,
  name: 'Test Drive',
  drive_id: 'drive-1234567890abcdef',
  style_type: 'CL2K',
  subscribed: false,
  priority: 1,
  custom_path: null,
  is_custom: false,
  is_deprecated: false,
  last_synced: null,
  poster_count: 0,
  sync_file_count: 0,
  ...overrides,
})

describe('GDrives', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockNewDrives = EMPTY_NEW_DRIVES
  })

  it('shows no new-drive notice when nothing is new', async () => {
    mockGetDrives.mockResolvedValue([buildDrive({ name: 'Old Drive', drive_id: 'old-drive' })])

    renderWithRouter(<GDrives />)

    await screen.findByText('Old Drive')
    expect(screen.queryByRole('status')).toBeNull()
    expect(document.getElementById('drive-card-poster-old-drive')?.classList.contains('is-new')).toBe(false)
    expect(mockMarkNewDrivesSeen).not.toHaveBeenCalled()
  })

  it('tags new community drives, lists them in the notice, and marks them seen on open', async () => {
    mockNewDrives = { poster: [buildNewDrive()], artwork: [], unseen_count: 1 }
    mockGetDrives.mockResolvedValue([
      buildDrive({ id: 7, name: 'Fresh Drive', drive_id: 'new-drive', style_type: 'MM2K' }),
      buildDrive({ id: 8, name: 'Old Drive', drive_id: 'old-drive' }),
    ])
    mockMarkNewDrivesSeen.mockResolvedValue(undefined)
    mockRefreshNewDrives.mockResolvedValue(undefined)

    renderWithRouter(<GDrives />)

    await screen.findByText('Old Drive')
    expect(screen.getByText('New community drive:')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Fresh Drive/ })).not.toBeNull()
    expect(document.getElementById('drive-card-poster-new-drive')?.classList.contains('is-new')).toBe(true)
    expect(document.getElementById('drive-card-poster-old-drive')?.classList.contains('is-new')).toBe(false)
    // Opening the page acknowledges the sidebar badge; the tags themselves stay.
    await waitFor(() => expect(mockMarkNewDrivesSeen).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(mockRefreshNewDrives).toHaveBeenCalled())
  })

  it('Dismiss clears the new-drive flags without re-marking seen ones', async () => {
    const user = userEvent.setup()
    mockNewDrives = { poster: [buildNewDrive({ seen: true })], artwork: [buildNewDrive({ id: 9, drive_id: 'art-drive', name: 'Art Drive', style_type: null, seen: true })], unseen_count: 0 }
    mockGetDrives.mockResolvedValue([buildDrive({ id: 8, name: 'Old Drive', drive_id: 'old-drive' })])
    mockDismissNewDrives.mockResolvedValue(undefined)
    mockRefreshNewDrives.mockResolvedValue(undefined)

    renderWithRouter(<GDrives />)

    await screen.findByText('Old Drive')
    expect(screen.getByText('2 new community drives:')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Art Drive/ }).textContent).toContain('Artwork')
    await user.click(screen.getByRole('button', { name: /Dismiss/ }))

    await waitFor(() => expect(mockDismissNewDrives).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(mockRefreshNewDrives).toHaveBeenCalled())
    expect(mockMarkNewDrivesSeen).not.toHaveBeenCalled()
  })

  it('loads and renders drives', async () => {
    mockGetDrives.mockResolvedValue([buildDrive({ name: 'CL2K Movies' })])

    renderWithRouter(<GDrives />)

    await screen.findByText('CL2K Movies')
    expect(mockGetDrives).toHaveBeenCalled()
  })

  // Drive the two outcomes via the remembered preference (which skips the popup) — this
  // verifies the add-to-priority flag is threaded through subscribe correctly.
  it('subscribes without adding to priority when preference is "never"', async () => {
    localStorage.setItem('posterflow.subscribeAddToPriority.poster', 'never')
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 42, subscribed: false })])
    mockSubscribeDrive.mockResolvedValue({})

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByRole('button', { name: 'Subscribe' }))

    await waitFor(() => {
      expect(mockSubscribeDrive).toHaveBeenCalledWith(42, false)
    })
  })

  it('subscribes and adds to priority when preference is "always"', async () => {
    localStorage.setItem('posterflow.subscribeAddToPriority.poster', 'always')
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 42, subscribed: false })])
    mockSubscribeDrive.mockResolvedValue({ added_to_priority: true })

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByRole('button', { name: 'Subscribe' }))

    await waitFor(() => {
      expect(mockSubscribeDrive).toHaveBeenCalledWith(42, true)
    })
  })

  it('unsubscribes after confirming the dialog, keeping files by default', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 7, subscribed: true })])
    mockUnsubscribeDrive.mockResolvedValue({})

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByTitle('Unsubscribe from drive'))

    // Confirm dialog opens; the API is not called until confirmed.
    expect(mockUnsubscribeDrive).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Unsubscribe' }))

    await waitFor(() => {
      expect(mockUnsubscribeDrive).toHaveBeenCalledWith(7, false)
    })
  })

  it('unsubscribes with file deletion when the checkbox is ticked', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 7, subscribed: true })])
    mockUnsubscribeDrive.mockResolvedValue({ files_deleted: true })

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByTitle('Unsubscribe from drive'))

    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Unsubscribe' }))

    await waitFor(() => {
      expect(mockUnsubscribeDrive).toHaveBeenCalledWith(7, true)
    })
  })

  // Overlapping subscribes race the shared drive-priority setting server-side
  it('bulk subscribe issues one request at a time', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([
      buildDrive({ id: 1, name: 'Drive A', style_type: 'CL2K', subscribed: false }),
      buildDrive({ id: 2, name: 'Drive B', style_type: 'CL2K', subscribed: false }),
      buildDrive({ id: 3, name: 'Drive C', style_type: 'CL2K', subscribed: false }),
    ])
    let inFlight = 0
    let maxInFlight = 0
    mockSubscribeDrive.mockImplementation(async () => {
      inFlight += 1
      maxInFlight = Math.max(maxInFlight, inFlight)
      await new Promise(resolve => setTimeout(resolve, 0))
      inFlight -= 1
      return { added_to_priority: true }
    })

    renderWithRouter(<GDrives />)
    await screen.findByText('Drive A')
    await user.click(screen.getAllByRole('button', { name: 'Subscribe All' })[0])
    await user.click(screen.getByRole('button', { name: 'Add to Priority' }))

    await waitFor(() => expect(mockSubscribeDrive).toHaveBeenCalledTimes(3))
    expect(maxInFlight).toBe(1)
  })

  it('does not nag about adding drives the user just chose to add', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 1, style_type: 'CL2K', subscribed: false })])
    // added_to_priority false (already listed) used to leak a "Remember to add" toast.
    mockSubscribeDrive.mockResolvedValue({ added_to_priority: false })

    renderWithRouter(<GDrives />)
    await screen.findByText('Test Drive')
    await user.click(screen.getAllByRole('button', { name: 'Subscribe All' })[0])
    await user.click(screen.getByRole('button', { name: 'Add to Priority' }))

    await waitFor(() => expect(mockSubscribeDrive).toHaveBeenCalledWith(1, true))
    expect(mockShowToast).not.toHaveBeenCalledWith(
      expect.stringContaining('Remember to add'), 'info',
    )
  })

  it('still nags when the user declines to add them', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 1, style_type: 'CL2K', subscribed: false })])
    mockSubscribeDrive.mockResolvedValue({})

    renderWithRouter(<GDrives />)
    await screen.findByText('Test Drive')
    await user.click(screen.getAllByRole('button', { name: 'Subscribe All' })[0])
    await user.click(screen.getByRole('button', { name: 'Just Subscribe' }))

    await waitFor(() => expect(mockSubscribeDrive).toHaveBeenCalledWith(1, false))
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith(
      expect.stringContaining('Remember to add'), 'info',
    ))
  })

  it('shows info toast when bulk unsubscribe has no subscribed drives', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ style_type: 'CL2K', subscribed: false })])

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    const unsubscribeAllButtons = screen.getAllByRole('button', { name: 'Unsubscribe All' })
    await user.click(unsubscribeAllButtons[0])

    expect(mockShowToast).toHaveBeenCalledWith('No CL2K drives are subscribed', 'info')
  })
})
