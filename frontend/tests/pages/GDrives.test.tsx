import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import GDrives from '../../src/pages/GDrives'

const renderWithRouter = (ui: ReactElement) =>
  render(
    <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      {ui}
    </MemoryRouter>,
  )

const mockShowToast = vi.fn()
const mockGetDrives = vi.fn()
const mockSubscribeDrive = vi.fn()
const mockUnsubscribeDrive = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/contexts/AppEventsContext', () => ({
  useAppEvents: () => ({ jobs: [] }),
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
