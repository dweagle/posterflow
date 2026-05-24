import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PlexUpload from '../../src/pages/PlexUpload'

const mockShowToast = vi.fn()

const mockGetJobs = vi.fn()
const mockGetJob = vi.fn()
const mockRunPlexUpload = vi.fn()
const mockGetPlexWebhookSettings = vi.fn()
const mockGetPlexWebhookStats = vi.fn()
const mockSavePlexWebhookSettings = vi.fn()
const mockGetPlexManualSettings = vi.fn()
const mockSavePlexManualSettings = vi.fn()
const mockGetPlexUploadLibraryOverrideSettings = vi.fn()
const mockSavePlexUploadLibraryOverrideSettings = vi.fn()
const mockGetPlexUploadCache = vi.fn()
const mockClearPlexUploadCache = vi.fn()
const mockDownloadPlexUploadCacheExport = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: () => 'api error',
  getJobs: (...args: unknown[]) => mockGetJobs(...args),
  getJob: (...args: unknown[]) => mockGetJob(...args),
  runPlexUpload: (...args: unknown[]) => mockRunPlexUpload(...args),
  getPlexWebhookSettings: (...args: unknown[]) => mockGetPlexWebhookSettings(...args),
  getPlexWebhookStats: (...args: unknown[]) => mockGetPlexWebhookStats(...args),
  savePlexWebhookSettings: (...args: unknown[]) => mockSavePlexWebhookSettings(...args),
  getPlexManualSettings: (...args: unknown[]) => mockGetPlexManualSettings(...args),
  savePlexManualSettings: (...args: unknown[]) => mockSavePlexManualSettings(...args),
  getPlexUploadLibraryOverrideSettings: (...args: unknown[]) => mockGetPlexUploadLibraryOverrideSettings(...args),
  savePlexUploadLibraryOverrideSettings: (...args: unknown[]) => mockSavePlexUploadLibraryOverrideSettings(...args),
  getPlexUploadCache: (...args: unknown[]) => mockGetPlexUploadCache(...args),
  clearPlexUploadCache: (...args: unknown[]) => mockClearPlexUploadCache(...args),
  downloadPlexUploadCacheExport: (...args: unknown[]) => mockDownloadPlexUploadCacheExport(...args),
}))

describe('PlexUpload', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    mockGetJobs.mockResolvedValue([])
    mockGetJob.mockResolvedValue({})
    mockRunPlexUpload.mockResolvedValue({ success: true, job_id: 1, message: 'queued' })
    mockGetPlexWebhookSettings.mockResolvedValue({
      enabled: true,
      remove_overlay_label: false,
      rename_then_upload: false,
      adopt_existing_processed: false,
      retry_attempts: 10,
      retry_delay_seconds: 30,
      upload_delay_ms: 50,
    })
    mockGetPlexWebhookStats.mockResolvedValue({
      received: 0,
      queued: 0,
      duplicates: 0,
      skipped_test: 0,
      skipped_cached: 0,
      rejected_disabled: 0,
      parse_errors: 0,
      internal_errors: 0,
      last_event_at: null,
      last_queued_at: null,
      last_error: null,
    })
    mockSavePlexWebhookSettings.mockResolvedValue({
      success: true,
      enabled: true,
      remove_overlay_label: false,
      rename_then_upload: false,
      adopt_existing_processed: true,
      retry_attempts: 10,
      retry_delay_seconds: 30,
      upload_delay_ms: 50,
    })
    mockGetPlexManualSettings.mockResolvedValue({
      remove_overlay_label: false,
      rename_then_upload: false,
      retry_attempts: 10,
      retry_delay_seconds: 30,
      upload_delay_ms: 50,
    })
    mockSavePlexManualSettings.mockResolvedValue({ success: true })
    mockGetPlexUploadLibraryOverrideSettings.mockResolvedValue({
      enabled: false,
      configs: [],
      global_configs: [],
    })
    mockSavePlexUploadLibraryOverrideSettings.mockResolvedValue({ success: true, enabled: false, configs: [], global_configs: [] })
    mockGetPlexUploadCache.mockResolvedValue({
      entries_count: 0,
      total_library_refs: 0,
      total_edition_refs: 0,
      entries: [],
    })
    mockClearPlexUploadCache.mockResolvedValue({
      success: true,
      removed: 0,
      cleared_file_path: null,
      entries_count: 0,
      total_library_refs: 0,
      total_edition_refs: 0,
      entries: [],
    })
    mockDownloadPlexUploadCacheExport.mockReturnValue('/api/poster-manager/plex-upload/upload-cache/export')
  })

  afterEach(() => {
    cleanup()
  })

  it('loads initial plex upload data on mount', async () => {
    render(<PlexUpload />)

    await waitFor(() => {
      expect(mockGetJobs).toHaveBeenCalledTimes(1)
      expect(mockGetPlexWebhookSettings).toHaveBeenCalledTimes(1)
      expect(mockGetPlexWebhookStats).toHaveBeenCalledTimes(1)
      expect(mockGetPlexUploadLibraryOverrideSettings).toHaveBeenCalledTimes(1)
      expect(mockGetPlexUploadCache).toHaveBeenCalledTimes(1)
    })
  })

  it('shows settings tab by default and switches to manual tab', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    expect(screen.getByText('Webhook Settings')).toBeTruthy()
    expect(screen.queryByText('Manual Run Options (Shared)')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Manual Uploads' }))

    expect(screen.getByText('Manual Run Options (Shared)')).toBeTruthy()
    expect(screen.getByText('Run & Monitor')).toBeTruthy()
  })

  it('keeps setup walkthrough collapsed by default and expands on Show', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await user.click(screen.getByRole('button', { name: 'Settings' }))

    expect(screen.queryByText(/Enable webhook processing below/i)).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Show' }))

    expect(screen.getByText(/Enable webhook processing below/i)).toBeTruthy()
  })

  it('saves adopt existing processed toggle in webhook settings payload', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await waitFor(() => {
      expect(mockGetPlexWebhookSettings).toHaveBeenCalledTimes(1)
    })

    const adoptCheckbox = screen.getByLabelText(/Adopt existing processed posters/i)
    await user.click(adoptCheckbox)
    await user.click(screen.getByRole('button', { name: 'Save Webhook Settings' }))

    await waitFor(() => {
      expect(mockSavePlexWebhookSettings).toHaveBeenCalledWith(expect.objectContaining({
        enabled: true,
        remove_overlay_label: false,
        rename_then_upload: false,
        adopt_existing_processed: true,
        retry_attempts: 10,
        retry_delay_seconds: 30,
      }))
    })
  })
})
