import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Dashboard from '../../src/pages/Dashboard'

const mockNavigate = vi.fn()
const mockShowToast = vi.fn()

const mockGetStats = vi.fn()
const mockGetSchedules = vi.fn()
const mockGetDrives = vi.fn()
const mockRunFlow = vi.fn()
const mockGetRecentSyncedPosters = vi.fn()
const mockGetRecentSyncedArtwork = vi.fn()
const mockGetMakerIdarrConfig = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/contexts/AppEventsContext', () => ({
  useAppEvents: () => ({
    jobs: [],
    refreshStats: vi.fn(),
    unmatchedStats: {
      summary: {
        movies: { total: 10, unmatched: 2, percent_complete: 80 },
        series: { total: 5, unmatched: 1, percent_complete: 80 },
        seasons: { total: 0, unmatched: 0, percent_complete: 0 },
        collections: { total: 0, unmatched: 0, percent_complete: 0 },
        grand_total: { total: 15, unmatched: 3, percent_complete: 80 },
      },
      last_run: '2026-02-15T00:00:00Z',
    },
  }),
}))

vi.mock('../../src/api/client', () => ({
  getStats: (...args: unknown[]) => mockGetStats(...args),
  getSchedules: (...args: unknown[]) => mockGetSchedules(...args),
  getDrives: (...args: unknown[]) => mockGetDrives(...args),
  getRecentSyncedPosters: (...args: unknown[]) => mockGetRecentSyncedPosters(...args),
  getRecentSyncedArtwork: (...args: unknown[]) => mockGetRecentSyncedArtwork(...args),
  getMakerIdarrConfig: (...args: unknown[]) => mockGetMakerIdarrConfig(...args),
  runFlow: (...args: unknown[]) => mockRunFlow(...args),
  runBorderReplacer: vi.fn(),
  startUnmatchedDetection: vi.fn(),
  startPosterRename: vi.fn(),
  getPosterConfig: vi.fn(),
  getPosterActivityStats: vi.fn().mockResolvedValue({ items: [] }),
  getArtworkUnmatchedStats: vi.fn().mockResolvedValue({ logo: null, background: null, squareart: null, last_run: null }),
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
    mockGetSchedules.mockResolvedValue([])
    mockGetDrives.mockResolvedValue([])
    mockGetRecentSyncedPosters.mockResolvedValue({ items: [] })
    mockGetRecentSyncedArtwork.mockResolvedValue({ items: [] })
    mockGetMakerIdarrConfig.mockResolvedValue({ sync_targets: [] })
    mockRunFlow.mockResolvedValue({ success: true, job_id: 10 })
  })

  it('fetches dashboard data on mount and renders stats', async () => {
    render(<Dashboard />)

    await screen.findByText('Dashboard')

    await waitFor(() => {
      expect(mockGetStats).toHaveBeenCalledTimes(1)
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

    // Dashboard shows poster coverage, so the link pins the poster sub-tab regardless of
    // which asset type the Unmatched view was last left on.
    expect(mockNavigate).toHaveBeenCalledWith('/poster-manager', { state: { activeTab: 'unmatched', unmatchedScope: 'posters' } })
  })

  it('switches the recently synced carousel to the clicked coverage scope', async () => {
    const user = userEvent.setup()
    mockGetRecentSyncedArtwork.mockResolvedValue({
      items: [{
        id: 7,
        file_name: 'Dune (2021) - logo.png',
        drive_id: 'logo-drive',
        drive_name: 'Logo Drive',
        downloaded_at: '2026-09-01T00:00:00Z',
        image_url: '/api/stats/artwork/7/image',
      }],
    })
    render(<Dashboard />)

    await screen.findByText('Recently Synced Posters')
    expect(mockGetRecentSyncedArtwork).not.toHaveBeenCalled()

    await user.click(screen.getByRole('tab', { name: 'Logos' }))

    await waitFor(() => {
      expect(mockGetRecentSyncedArtwork).toHaveBeenCalledWith('logo', 100)
    })
    expect(await screen.findByText('Recently Synced Logos')).toBeTruthy()
    const thumb = await screen.findByAltText('Dune (2021) - logo.png')
    expect(thumb.getAttribute('src')).toContain('/api/stats/artwork/7/image')

    await user.click(screen.getByRole('tab', { name: 'Posters' }))
    expect(await screen.findByText('No recently synced posters')).toBeTruthy()
    expect(mockGetRecentSyncedPosters).toHaveBeenCalledTimes(2)
  })

  it('keeps the carousel row mounted with placeholders while a scope loads', async () => {
    const user = userEvent.setup()
    let resolveLogos: (value: { items: [] }) => void = () => {}
    mockGetRecentSyncedArtwork.mockReturnValue(new Promise<{ items: [] }>(resolve => { resolveLogos = resolve }))
    render(<Dashboard />)
    await screen.findByText('No recently synced posters')

    await user.click(screen.getByRole('tab', { name: 'Logos' }))

    expect(await screen.findByText('Recently Synced Logos')).toBeTruthy()
    expect(document.querySelectorAll('.poster-carousel-item--placeholder').length).toBeGreaterThan(0)
    expect(screen.queryByText('No recently synced logos')).toBeNull()

    resolveLogos({ items: [] })
    expect(await screen.findByText('No recently synced logos')).toBeTruthy()
    expect(document.querySelectorAll('.poster-carousel-item--placeholder').length).toBe(0)
  })

  it('wraps carousel paging around at both ends', async () => {
    const user = userEvent.setup()
    mockGetRecentSyncedPosters.mockResolvedValue({
      items: Array.from({ length: 12 }, (_, i) => ({
        id: i + 1,
        file_name: `Movie ${i + 1} (2020).jpg`,
        drive_id: 'drive',
        drive_name: 'Drive',
        downloaded_at: null,
        image_url: `/api/stats/posters/${i + 1}/image`,
      })),
    })
    render(<Dashboard />)

    expect(await screen.findByText('1 / 2')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('2 / 2')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('1 / 2')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(await screen.findByText('2 / 2')).toBeTruthy()
  })
})
