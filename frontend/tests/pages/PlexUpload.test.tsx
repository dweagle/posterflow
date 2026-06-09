import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
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
const mockGetPlexWebhookToken = vi.fn()
const mockRegeneratePlexWebhookToken = vi.fn()
const mockGetPlexManualSettings = vi.fn()
const mockSavePlexManualSettings = vi.fn()
const mockGetPlexUploadLibraryOverrideSettings = vi.fn()
const mockSavePlexUploadLibraryOverrideSettings = vi.fn()
const mockGetPlexUploadCache = vi.fn()
const mockClearPlexUploadCache = vi.fn()
const mockDownloadPlexUploadCacheExport = vi.fn()
const mockGetPlexUploadInstanceMap = vi.fn()
const mockSavePlexUploadInstanceMap = vi.fn()
const mockGetSettings = vi.fn()

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
  getPlexWebhookToken: (...args: unknown[]) => mockGetPlexWebhookToken(...args),
  regeneratePlexWebhookToken: (...args: unknown[]) => mockRegeneratePlexWebhookToken(...args),
  getPlexManualSettings: (...args: unknown[]) => mockGetPlexManualSettings(...args),
  savePlexManualSettings: (...args: unknown[]) => mockSavePlexManualSettings(...args),
  getPlexUploadLibraryOverrideSettings: (...args: unknown[]) => mockGetPlexUploadLibraryOverrideSettings(...args),
  savePlexUploadLibraryOverrideSettings: (...args: unknown[]) => mockSavePlexUploadLibraryOverrideSettings(...args),
  getPlexUploadCache: (...args: unknown[]) => mockGetPlexUploadCache(...args),
  clearPlexUploadCache: (...args: unknown[]) => mockClearPlexUploadCache(...args),
  downloadPlexUploadCacheExport: (...args: unknown[]) => mockDownloadPlexUploadCacheExport(...args),
  getPlexUploadInstanceMap: (...args: unknown[]) => mockGetPlexUploadInstanceMap(...args),
  savePlexUploadInstanceMap: (...args: unknown[]) => mockSavePlexUploadInstanceMap(...args),
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
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
    mockGetPlexWebhookToken.mockResolvedValue({ token: 'test-token', password_set: false })
    mockRegeneratePlexWebhookToken.mockResolvedValue({ success: true, token: 'new-token', password_set: false })
    mockGetPlexWebhookStats.mockResolvedValue({
      received: 0,
      queued: 0,
      duplicates: 0,
      skipped_test: 0,
      skipped_cached: 0,
      skipped_no_asset: 0,
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
    mockGetPlexUploadInstanceMap.mockResolvedValue({ map: {} })
    mockSavePlexUploadInstanceMap.mockResolvedValue({ message: 'saved', map: {} })
    mockGetSettings.mockResolvedValue({})
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

  it('loads the instance routing map and arr instances on mount', async () => {
    render(<PlexUpload />)

    await waitFor(() => {
      expect(mockGetPlexUploadInstanceMap).toHaveBeenCalledTimes(1)
      expect(mockGetSettings).toHaveBeenCalledTimes(1)
    })
  })

  it('renders a per-instance webhook URL and saves a library mapping', async () => {
    const user = userEvent.setup()
    mockGetSettings.mockResolvedValue({
      radarr_instances: JSON.stringify([{ name: 'Radarr-4K', url: 'http://radarr4k:7878', api_key: 'x' }]),
    })
    mockGetPlexUploadLibraryOverrideSettings.mockResolvedValue({
      enabled: false,
      configs: [],
      global_configs: [
        {
          instance_name: 'Main',
          libraries: [{ key: 'k_movies4k', title: 'Movies 4K', type: 'movie', enabled: true }],
        },
      ],
    })

    render(<PlexUpload />)

    // The per-instance selection now lives in the Library Targeting modal.
    const configureButton = await screen.findByRole('button', { name: /Configure libraries/i })
    await user.click(configureButton)

    const routingBlock = await screen.findByTestId('instance-routing-Radarr-4K')
    expect(routingBlock).toBeTruthy()
    // Per-instance webhook URL carries the &instance= attribution token.
    expect(screen.getByText(/instance=Radarr-4K/)).toBeTruthy()

    const checkbox = within(routingBlock).getByLabelText(/Movies 4K/i)
    await user.click(checkbox)
    // Single footer Save persists override + routing together.
    await user.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => {
      expect(mockSavePlexUploadInstanceMap).toHaveBeenCalledWith({
        'Radarr-4K': [{ plex_instance: 'Main', library_key: 'k_movies4k' }],
      })
    })
  })

  it('reverts unsaved modal toggles when cancelled', async () => {
    const user = userEvent.setup()
    mockGetSettings.mockResolvedValue({
      radarr_instances: JSON.stringify([{ name: 'Radarr-4K', url: 'http://radarr4k:7878', api_key: 'x' }]),
    })
    mockGetPlexUploadLibraryOverrideSettings.mockResolvedValue({
      enabled: false,
      configs: [],
      global_configs: [
        { instance_name: 'Main', libraries: [{ key: 'k_movies4k', title: 'Movies 4K', type: 'movie', enabled: true }] },
      ],
    })

    render(<PlexUpload />)
    await user.click(await screen.findByRole('button', { name: /Configure libraries/i }))

    const routingBlock = await screen.findByTestId('instance-routing-Radarr-4K')
    await user.click(within(routingBlock).getByLabelText(/Movies 4K/i))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    // Nothing persisted, and the page summary still shows the default (reverted) routing.
    expect(mockSavePlexUploadInstanceMap).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getByText(/→ all selected libraries/i)).toBeTruthy()
    })
  })

  it('shows a compact routing summary on the page and keeps the selection in a modal', async () => {
    mockGetSettings.mockResolvedValue({
      radarr_instances: JSON.stringify([{ name: 'Radarr-4K', url: 'http://radarr4k:7878', api_key: 'x' }]),
    })
    mockGetPlexUploadInstanceMap.mockResolvedValue({
      map: { 'Radarr-4K': [{ plex_instance: 'Main', library_key: 'k_movies4k' }] },
    })
    mockGetPlexUploadLibraryOverrideSettings.mockResolvedValue({
      enabled: false,
      configs: [],
      global_configs: [
        {
          instance_name: 'Main',
          libraries: [{ key: 'k_movies4k', title: 'Movies 4K', type: 'movie', enabled: true }],
        },
      ],
    })

    render(<PlexUpload />)

    // Summary resolves the mapped library to its title on the page itself…
    await waitFor(() => {
      expect(screen.getByText(/→ Movies 4K/)).toBeTruthy()
    })
    // …while the checkbox grid is not rendered until the modal is opened.
    expect(screen.queryByTestId('instance-routing-Radarr-4K')).toBeNull()
    expect(screen.getByRole('button', { name: /Configure libraries/i })).toBeTruthy()
  })
})
