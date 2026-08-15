import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import SetupWizard from '../../src/pages/SetupWizard'

const mockNavigate = vi.fn()
const mockShowToast = vi.fn()
const mockOnComplete = vi.fn()

const mockGetSettings = vi.fn()
const mockSaveSettings = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  DEFAULT_POSTER_DESTINATION: '/config/posters/assets',
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  getPosterConfig: vi.fn().mockResolvedValue({ default_destination: '/config/posters/assets', is_docker: true }),
  saveSettings: (...args: unknown[]) => mockSaveSettings(...args),
  saveGdriveStoragePath: vi.fn().mockResolvedValue({ path: '/config/posters/gdrive' }),
  saveArtworkGdriveStoragePath: vi.fn().mockResolvedValue({ path: '/config/artwork/gdrive' }),
  testPlex: vi.fn(),
  testSonarr: vi.fn(),
  testRadarr: vi.fn(),
  uploadBackup: vi.fn(),
  uploadServiceAccountJson: vi.fn(),
  getApiErrorMessage: vi.fn(() => 'error'),
}))

describe('SetupWizard', () => {
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>

  afterEach(() => {
    cleanup()
    consoleErrorSpy.mockRestore()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

    mockGetSettings.mockResolvedValue({})
    mockSaveSettings.mockResolvedValue({ success: true })
  })

  it('renders welcome screen after loading settings', async () => {
    render(<SetupWizard onComplete={mockOnComplete} />)

    expect(screen.getByText('Loading existing settings...')).toBeTruthy()

    await screen.findByText('Start Setup Wizard')
    expect(screen.getByText('Start Setup Wizard')).toBeTruthy()
  })

  it('shows toast when trying to advance step 1 without credentials', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByText('Start Setup Wizard')
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await screen.findByText('Google Drive Configuration')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith(
        expect.stringContaining('OAuth credentials'),
        'error'
      )
    })
  })

  it('moves from step 1 to media server step when Save & Continue is clicked', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Storage Configuration')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Media Server Configuration')
  })

  it('allows step 1 with service account path only', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(
      screen.getByPlaceholderText(/\/config\/service_accounts\/my-service-account\.json/i),
      '/config/service_accounts/my-service-account.json'
    )

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Storage Configuration')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Media Server Configuration')
  })

  it('skips setup and navigates to dashboard', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Skip to Dashboard' })
    await user.click(screen.getByRole('button', { name: 'Skip to Dashboard' }))

    await waitFor(() => {
      expect(mockSaveSettings).toHaveBeenCalledWith({ setup_complete: 'true' })
      expect(mockOnComplete).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
    })
  })

  it('submits setup successfully after valid step data', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Storage Configuration')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Media Server Configuration')

    await user.click(screen.getByRole('checkbox', { name: /I don't have Plex/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Sonarr/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Radarr/i }))

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByRole('heading', { name: 'API Keys' })

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Destination Folder Setup')

    await user.type(screen.getByPlaceholderText(/ex\. \/kometa\/config\/assets/i), '/kometa/config/assets')
    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))

    await screen.findByText('Setup Complete')
    await user.click(screen.getByRole('button', { name: 'Complete Setup' }))

    await waitFor(() => {
      expect(mockSaveSettings).toHaveBeenNthCalledWith(1, {
        google_client_id: 'client-id',
        google_client_secret: 'client-secret',
        google_refresh_token: 'refresh-token',
        google_token: 'refresh-token',
        google_service_account_file: '',
      })
      expect(mockSaveSettings).toHaveBeenNthCalledWith(2, {
        plex_instances: '[]',
        sonarr_instances: '[]',
        radarr_instances: '[]',
      })
      expect(mockSaveSettings).toHaveBeenNthCalledWith(3, {
        poster_destination: '/kometa/config/assets',
      })
      expect(mockSaveSettings).toHaveBeenNthCalledWith(4, {
        setup_complete: 'true',
      })
      expect(mockOnComplete).toHaveBeenCalled()
      expect(mockNavigate).toHaveBeenCalledWith('/', { replace: true })
    })
  })

  it('shows toast and does not navigate when skip setup fails', async () => {
    const user = userEvent.setup()
    mockSaveSettings.mockRejectedValueOnce(new Error('skip failed'))

    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Skip to Dashboard' })
    await user.click(screen.getByRole('button', { name: 'Skip to Dashboard' }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Error skipping setup. Please try again.', 'error')
    })

    expect(mockOnComplete).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('shows toast and does not complete when final setup submit fails', async () => {
    const user = userEvent.setup()
    // Step 1 (google), step 3 (media servers), step 4 (destination) succeed; Complete Setup fails
    mockSaveSettings
      .mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ success: true })
      .mockResolvedValueOnce({ success: true })
      .mockRejectedValueOnce(new Error('submit failed'))

    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Storage Configuration')

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Media Server Configuration')

    await user.click(screen.getByRole('checkbox', { name: /I don't have Plex/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Sonarr/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Radarr/i }))

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByRole('heading', { name: 'API Keys' })

    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))
    await screen.findByText('Destination Folder Setup')

    await user.type(screen.getByPlaceholderText(/ex\. \/kometa\/config\/assets/i), '/kometa/config/assets')
    await user.click(screen.getByRole('button', { name: 'Save & Continue' }))

    await screen.findByText('Setup Complete')
    await user.click(screen.getByRole('button', { name: 'Complete Setup' }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Error saving settings. Please try again.', 'error')
    })

    expect(mockOnComplete).not.toHaveBeenCalled()
  })
})
