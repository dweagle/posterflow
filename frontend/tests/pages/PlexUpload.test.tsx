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
    mockDownloadPlexUploadCacheExport.mockResolvedValue(undefined)
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

  it('shows the settings tab by default and switches to the manual uploads tab', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    expect(screen.getByText('Webhook Settings')).toBeTruthy()
    expect(screen.getByText('Upload Settings')).toBeTruthy()
    expect(screen.getByText('Library Targeting')).toBeTruthy()
    expect(screen.queryByText('Manual Run Options')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Manual Uploads' }))

    expect(screen.getByText('Manual Run Options')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 2, name: 'Manual Uploads' })).toBeTruthy()
    expect(screen.queryByText('Upload Settings')).toBeNull()
    expect(screen.queryByText('Webhook Settings')).toBeNull()
  })

  // The switch row and toolbar match the Asset Manager's Unmatched / Drive Priority tabs.
  it('renders the toolbar and marks the active switch', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    expect(screen.getByRole('heading', { level: 2, name: 'Asset Upload' })).toBeTruthy()

    const webhook = screen.getByRole('button', { name: 'Webhook / Upload Settings' })
    const manual = screen.getByRole('button', { name: 'Manual Uploads' })
    expect(screen.getByRole('button', { name: 'Activity / Cache' })).toBeTruthy()
    expect(webhook.className).toContain('active')
    expect(manual.className).not.toContain('active')

    await user.click(manual)
    expect(screen.getByRole('button', { name: 'Manual Uploads' }).className).toContain('active')
    expect(screen.getByRole('button', { name: 'Webhook / Upload Settings' }).className).not.toContain('active')
  })

  // Standalone page again (own sidebar entry + route), so it provides its own page container
  // and page header.
  it('renders as a standalone page', () => {
    render(<PlexUpload />)

    expect(document.querySelector('.plex-upload-page.page-container')).toBeTruthy()
    expect(screen.getByRole('heading', { level: 1, name: 'Asset Upload' })).toBeTruthy()
    expect(document.querySelector('.plex-upload-scope-toggle')).toBeTruthy()
    // The old page-tab bar styling is gone; this is a switch row now.
    expect(document.querySelector('.plex-upload-tabs')).toBeNull()
  })

  // The toggle lives next to the toolbar title; the panel opens under the toolbar.
  it('keeps setup walkthrough collapsed by default and toggles it from the toolbar', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await user.click(screen.getByRole('button', { name: 'Webhook / Upload Settings' }))

    const toggle = screen.getByRole('button', { name: /Setup Walkthrough/i })
    expect(toggle.closest('.toolbar-title')).toBeTruthy()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByText(/Enable webhook processing below/i)).toBeNull()

    await user.click(toggle)

    expect(screen.getByText(/Enable webhook processing below/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Setup Walkthrough/i }).getAttribute('aria-expanded')).toBe('true')

    await user.click(screen.getByRole('button', { name: /Setup Walkthrough/i }))
    expect(screen.queryByText(/Enable webhook processing below/i)).toBeNull()
  })

  // The grouping is a claim about the backend: flow.py reads plex_upload_manual_remove_overlay_label
  // and the upload job reads the manual delay + plex_upload_artwork, while the workflow hardcodes
  // dry_run/reapply off and runs its own sync/rename/border steps.
  it('keeps the settings every run honours on the settings tab and the manual-run-only ones beside the run buttons', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    const everyRun = await waitFor(() => {
      const card = document.querySelector('.upload-settings-card') as HTMLElement
      expect(card.textContent).toMatch(/Remove Overlay label/i)
      return card
    })
    expect(everyRun.textContent).toMatch(/scheduled runs.*Workflow/i)
    expect(everyRun.textContent).toMatch(/Upload artwork/i)
    expect(everyRun.textContent).toMatch(/Upload delay/i)
    expect(everyRun.textContent).not.toMatch(/Dry run|Reapply|Sync drives|Replace borders/i)

    await user.click(screen.getByRole('button', { name: 'Manual Uploads' }))

    const manualOnly = document.querySelector('.plex-manual-options-card') as HTMLElement
    for (const label of [/Dry run/i, /Reapply/i, /Sync drives before upload/i, /Rename posters before upload/i, /Replace borders before upload/i]) {
      expect(manualOnly.textContent).toMatch(label)
    }
    expect(manualOnly.textContent).not.toMatch(/Remove Overlay label|Upload artwork|Upload delay/i)
    // The options sit in the same row as the run they shape.
    expect(manualOnly.parentElement?.querySelector('.plex-upload-stack')?.textContent).toMatch(/Run All Posters Upload/)
  })

  it('saves the Upload Settings card with the settings tab Save', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await waitFor(() => expect(document.querySelector('.upload-settings-card input')).toBeTruthy())
    const card = document.querySelector('.upload-settings-card') as HTMLElement
    await user.click(within(card).getByLabelText(/Upload artwork/i))
    await user.click(screen.getByRole('button', { name: /^Saved?$/ }))

    await waitFor(() => {
      expect(mockSavePlexManualSettings).toHaveBeenCalledWith(expect.objectContaining({ upload_artwork: true }))
    })
    expect(mockSavePlexWebhookSettings).not.toHaveBeenCalled()
  })

  it('asks before leaving the settings tab with an unsaved upload setting', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await waitFor(() => expect(document.querySelector('.upload-settings-card input')).toBeTruthy())
    const card = document.querySelector('.upload-settings-card') as HTMLElement
    await user.click(within(card).getByLabelText(/Upload artwork/i))
    await user.click(screen.getByRole('button', { name: 'Manual Uploads' }))

    expect(screen.getByRole('button', { name: 'Webhook / Upload Settings' }).className).toContain('active')
  })

  // Save sits in the toolbar like every other tab, and targets the active sub-tab.
  it('offers one toolbar Save per sub-tab, disabled until dirty', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await waitFor(() => expect(mockGetPlexWebhookSettings).toHaveBeenCalled())

    // Nothing edited yet.
    const save = screen.getByRole('button', { name: /^Saved?$/ }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    expect(document.querySelector('.toolbar .btn-toolbar')).toBeTruthy()

    await user.click(screen.getByLabelText(/Adopt existing processed posters/i))
    expect((screen.getByRole('button', { name: /^Saved?$/ }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByRole('button', { name: /^Saved?$/ }).className).toContain('btn-unsaved')
  })

  // Activity is monitoring, so there is nothing to save there.
  it('offers no Save on the Activity tab', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await user.click(screen.getByRole('button', { name: 'Activity / Cache' }))
    expect(screen.queryByRole('button', { name: /^Saved?$/ })).toBeNull()
  })

  // Monitoring isn't configuration: Stats and Cache belong under Activity, not the
  // webhook config surface where they used to live.
  it('keeps monitoring on Activity and out of the Webhook tab', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    // Webhook tab: config only.
    expect(screen.getByText('Webhook Settings')).toBeTruthy()
    expect(screen.queryByText('Webhook Stats')).toBeNull()
    expect(screen.queryByText('Upload Cache')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Activity / Cache' }))

    expect(screen.getByText('Webhook Stats')).toBeTruthy()
    expect(screen.getByText('Upload Cache')).toBeTruthy()
    expect(screen.queryByText('Webhook Settings')).toBeNull()
  })

  // The old permanent red banner read as an error; it's first-run advice.
  it('does not show a standing setup warning', () => {
    render(<PlexUpload />)

    expect(document.querySelector('.plex-upload-initial-warning')).toBeNull()
    expect(screen.queryByText(/Initial Setup Recommendation/i)).toBeNull()
  })

  // The walkthrough only documents webhook setup, so it has no place on the manual tab.
  it('offers the walkthrough toggle only on the settings tab', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    expect(screen.getByRole('button', { name: /Setup Walkthrough/i })).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Manual Uploads' }))
    expect(screen.queryByRole('button', { name: /Setup Walkthrough/i })).toBeNull()
  })

  it('saves adopt existing processed toggle in webhook settings payload', async () => {
    const user = userEvent.setup()
    render(<PlexUpload />)

    await waitFor(() => {
      expect(mockGetPlexWebhookSettings).toHaveBeenCalledTimes(1)
    })

    const adoptCheckbox = screen.getByLabelText(/Adopt existing processed posters/i)
    await user.click(adoptCheckbox)
    await user.click(screen.getByRole('button', { name: /^Saved?$/ }))

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
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }))

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

  it('leaves routing to a media server that is not configured out of the summary', async () => {
    mockGetSettings.mockResolvedValue({
      radarr_instances: JSON.stringify([
        { name: 'Radarr', url: 'http://radarr:7878', api_key: 'x' },
        { name: 'Radarr-4K', url: 'http://radarr4k:7878', api_key: 'x' },
      ]),
    })
    mockGetPlexUploadInstanceMap.mockResolvedValue({
      map: {
        Radarr: [{ plex_instance: 'Gone', library_key: 'k_old' }],
        'Radarr-4K': [
          { plex_instance: 'Main', library_key: 'k_movies4k' },
          { plex_instance: 'Gone', library_key: 'k_old4k' },
        ],
      },
    })
    mockGetPlexUploadLibraryOverrideSettings.mockResolvedValue({
      enabled: false,
      configs: [],
      global_configs: [
        { instance_name: 'Main', libraries: [{ key: 'k_movies4k', title: 'Movies 4K', type: 'movie', enabled: true }] },
      ],
    })

    render(<PlexUpload />)

    await waitFor(() => {
      expect(screen.getByText('→ Movies 4K')).toBeTruthy()
    })
    expect(screen.getByText(/→ all selected libraries/i)).toBeTruthy()
    expect(screen.queryByText(/k_old/)).toBeNull()
  })
})
