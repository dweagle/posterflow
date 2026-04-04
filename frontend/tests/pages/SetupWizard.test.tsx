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
  getSettings: (...args: unknown[]) => mockGetSettings(...args),
  saveSettings: (...args: unknown[]) => mockSaveSettings(...args),
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

    await screen.findByText('Welcome to PosterFlow Setup')
    expect(screen.getByText('Start Setup Wizard')).toBeTruthy()
  })

  it('requires google fields before enabling Next on step 1', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByText('Start Setup Wizard')
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await screen.findByText('Google Drive Configuration')

    const nextButton = screen.getByRole('button', { name: 'Next' })
    expect(nextButton.hasAttribute('disabled')).toBe(true)

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await waitFor(() => {
      expect(nextButton.hasAttribute('disabled')).toBe(false)
    })
  })

  it('moves from step 1 to media server step when Next is clicked', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await user.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('Media Server Configuration')
    expect(screen.getByRole('button', { name: 'Next' })).toBeTruthy()
  })

  it('allows step 1 with service account path only', async () => {
    const user = userEvent.setup()
    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    const nextButton = screen.getByRole('button', { name: 'Next' })
    expect(nextButton.hasAttribute('disabled')).toBe(true)

    await user.type(
      screen.getByPlaceholderText(/\/config\/service_accounts\/my-service-account\.json/i),
      '/config/service_accounts/my-service-account.json'
    )

    await waitFor(() => {
      expect(nextButton.hasAttribute('disabled')).toBe(false)
    })
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

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('Media Server Configuration')

    await user.click(screen.getByRole('checkbox', { name: /I don't have Plex/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Sonarr/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Radarr/i }))

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('Destination Folder Setup')

    await user.type(screen.getByPlaceholderText(/ex\. \/kometa\/config\/assets/i), '/kometa/config/assets')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('Setup Complete')
    await user.click(screen.getByRole('button', { name: 'Complete Setup' }))

    await waitFor(() => {
      expect(mockSaveSettings).toHaveBeenCalledWith({
        google_client_id: 'client-id',
        google_client_secret: 'client-secret',
        google_refresh_token: 'refresh-token',
        google_token: 'refresh-token',
        google_service_account_file: '',
        poster_destination: '/kometa/config/assets',
        plex_instances: '[]',
        sonarr_instances: '[]',
        radarr_instances: '[]',
        setup_complete: 'true',
      })
      expect(mockOnComplete).toHaveBeenCalled()
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
    mockSaveSettings.mockRejectedValueOnce(new Error('submit failed'))

    render(<SetupWizard onComplete={mockOnComplete} />)

    await screen.findByRole('button', { name: 'Start Setup Wizard' })
    await user.click(screen.getByRole('button', { name: 'Start Setup Wizard' }))

    await user.type(screen.getByPlaceholderText(/apps\.googleusercontent\.com/i), 'client-id')
    await user.type(screen.getByPlaceholderText(/GOCSPX-/i), 'client-secret')
    await user.type(screen.getByPlaceholderText(/1\/\//i), 'refresh-token')

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('Media Server Configuration')

    await user.click(screen.getByRole('checkbox', { name: /I don't have Plex/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Sonarr/i }))
    await user.click(screen.getByRole('checkbox', { name: /I don't have Radarr/i }))

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByText('Destination Folder Setup')

    await user.type(screen.getByPlaceholderText(/ex\. \/kometa\/config\/assets/i), '/kometa/config/assets')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    await screen.findByText('Setup Complete')
    await user.click(screen.getByRole('button', { name: 'Complete Setup' }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Error saving settings. Please try again.', 'error')
    })

    expect(mockOnComplete).not.toHaveBeenCalled()
  })
})
