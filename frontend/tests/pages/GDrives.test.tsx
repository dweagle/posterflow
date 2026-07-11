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

vi.mock('../../src/components/ConfirmDialog', () => ({
  default: () => null,
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
  })

  it('loads and renders drives', async () => {
    mockGetDrives.mockResolvedValue([buildDrive({ name: 'CL2K Movies' })])

    renderWithRouter(<GDrives />)

    await screen.findByText('CL2K Movies')
    expect(mockGetDrives).toHaveBeenCalled()
  })

  it('subscribes when subscribe button is clicked', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 42, subscribed: false })])
    mockSubscribeDrive.mockResolvedValue({})

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByRole('button', { name: 'Subscribe' }))

    await waitFor(() => {
      expect(mockSubscribeDrive).toHaveBeenCalledWith(42)
    })
  })

  it('unsubscribes when subscribed action button is clicked', async () => {
    const user = userEvent.setup()
    mockGetDrives.mockResolvedValue([buildDrive({ id: 7, subscribed: true })])
    mockUnsubscribeDrive.mockResolvedValue({})

    renderWithRouter(<GDrives />)

    await screen.findByText('Test Drive')
    await user.click(screen.getByTitle('Unsubscribe from drive'))

    await waitFor(() => {
      expect(mockUnsubscribeDrive).toHaveBeenCalledWith(7)
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
