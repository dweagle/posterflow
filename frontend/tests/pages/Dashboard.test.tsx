import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from '../../src/pages/Dashboard'

const mockNavigate = vi.fn()
const mockShowToast = vi.fn()

const mockGetStats = vi.fn()
const mockGetUnmatchedStats = vi.fn()
const mockGetSchedules = vi.fn()
const mockGetDrives = vi.fn()
const mockRunFlow = vi.fn()
const mockGetRecentSyncedPosters = vi.fn()
const mockGetMakerIdarrConfig = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/contexts/UnmatchedContext', () => ({
  useUnmatched: () => ({
    jobs: [],
    refreshStats: vi.fn(),
    unmatchedStats: null,
  }),
}))

vi.mock('../../src/api/client', () => ({
  getStats: (...args: unknown[]) => mockGetStats(...args),
  getUnmatchedStats: (...args: unknown[]) => mockGetUnmatchedStats(...args),
  getSchedules: (...args: unknown[]) => mockGetSchedules(...args),
  getDrives: (...args: unknown[]) => mockGetDrives(...args),
  getRecentSyncedPosters: (...args: unknown[]) => mockGetRecentSyncedPosters(...args),
  getMakerIdarrConfig: (...args: unknown[]) => mockGetMakerIdarrConfig(...args),
  runFlow: (...args: unknown[]) => mockRunFlow(...args),
  runBorderReplacer: vi.fn(),
  startUnmatchedDetection: vi.fn(),
  startPosterRename: vi.fn(),
  getPosterConfig: vi.fn(),
  getPosterActivityStats: vi.fn().mockResolvedValue({ items: [] }),
  getApiErrorMessage: vi.fn(() => 'error'),
}))

describe('Dashboard', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()

    mockGetStats.mockResolvedValue({
      total_posters: 120,
      subscribed_posters: 80,
      posters_by_type: { cl2k: 30, mm2k: 40, custom: 10 },
      drives: {
        total: 4,
        subscribed: 3,
        synced: 2,
        by_type: { cl2k: 2, mm2k: 1, custom: 1 },
      },
      subscribed_drives_by_type: { cl2k: 1, mm2k: 1, custom: 1 },
    })
    mockGetUnmatchedStats.mockResolvedValue({
      summary: {
        movies: { total: 10, unmatched: 2, percent_complete: 80 },
        series: { total: 5, unmatched: 1, percent_complete: 80 },
        seasons: { total: 0, unmatched: 0, percent_complete: 0 },
        collections: { total: 0, unmatched: 0, percent_complete: 0 },
        grand_total: { total: 15, unmatched: 3, percent_complete: 80 },
      },
      last_run: '2026-02-15T00:00:00Z',
    })
    mockGetSchedules.mockResolvedValue([])
    mockGetDrives.mockResolvedValue([])
    mockGetRecentSyncedPosters.mockResolvedValue({ items: [] })
    mockGetMakerIdarrConfig.mockResolvedValue({ sync_targets: [] })
    mockRunFlow.mockResolvedValue({ success: true, job_id: 10 })
  })

  it('fetches dashboard data on mount and renders stats', async () => {
    render(<Dashboard />)

    await screen.findByText('Dashboard')

    await waitFor(() => {
      expect(mockGetStats).toHaveBeenCalledTimes(1)
      expect(mockGetUnmatchedStats).toHaveBeenCalledTimes(1)
      expect(mockGetSchedules).toHaveBeenCalledTimes(1)
      expect(mockGetDrives).toHaveBeenCalledTimes(1)
      expect(mockGetRecentSyncedPosters).toHaveBeenCalledWith(100)
    })

    expect(screen.getByText('3 / 4')).toBeTruthy()
    expect(screen.getByText('No recently synced posters')).toBeTruthy()
    expect(screen.getByText('No active jobs')).toBeTruthy()
  })

  it('starts workflow and shows progress/success toasts', async () => {
    const user = userEvent.setup()
    render(<Dashboard />)

    await screen.findByText('Run Workflow')
    await user.click(screen.getByRole('button', { name: /Run Workflow/i }))

    await waitFor(() => {
      expect(mockRunFlow).toHaveBeenCalledTimes(1)
    })

    expect(mockShowToast).toHaveBeenCalledWith('Starting workflow...', 'info')
    expect(mockShowToast).toHaveBeenCalledWith('Workflow started! Check Job Logs for progress.', 'success')
  })

  it('navigates to unmatched tab when view details is clicked', async () => {
    const user = userEvent.setup()
    render(<Dashboard />)

    await screen.findByRole('button', { name: 'View Details →' })
    await user.click(screen.getByRole('button', { name: 'View Details →' }))

    expect(mockNavigate).toHaveBeenCalledWith('/poster-manager', { state: { activeTab: 'unmatched' } })
  })
})
