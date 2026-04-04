import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Settings from '../../src/pages/Settings'

const mockNavigate = vi.fn()
const mockShowToast = vi.fn()

const mockGetSchedules = vi.fn()
const mockGetDrives = vi.fn()
const mockGetDiscordNotificationConfig = vi.fn()
const mockSaveDiscordNotificationConfig = vi.fn()
const mockTestDiscordNotification = vi.fn()

const mockFetchDebugStatus = vi.fn()
const mockFetchSettings = vi.fn()
const mockConfirmSaveRclone = vi.fn()
const mockSetShowSaveConfirm = vi.fn()

const mockFetchLibraryConfigs = vi.fn()

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getSchedules: (...args: unknown[]) => mockGetSchedules(...args),
  getDrives: (...args: unknown[]) => mockGetDrives(...args),
  getDiscordNotificationConfig: (...args: unknown[]) => mockGetDiscordNotificationConfig(...args),
  saveDiscordNotificationConfig: (...args: unknown[]) => mockSaveDiscordNotificationConfig(...args),
  testDiscordNotification: (...args: unknown[]) => mockTestDiscordNotification(...args),
  getApiErrorMessage: vi.fn(() => 'api error'),
}))

vi.mock('../../src/hooks/useSettingsOperations', () => ({
  useSettingsOperations: () => ({
    databaseStats: null,
    loadingStats: false,
    cleanupLoading: false,
    showCleanupConfirm: false,
    setShowCleanupConfirm: vi.fn(),
    backupLoading: false,
    restoreLoading: false,
    showRestartModal: false,
    setShowRestartModal: vi.fn(),
    fetchDatabaseStats: vi.fn(),
    handleDatabaseCleanup: vi.fn(),
    handleDownloadBackup: vi.fn(),
    handleRestoreBackup: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/useSettingsMedia', () => ({
  useSettingsMedia: () => ({
    mediaSettings: {
      plex_instances: [{ name: 'Plex', url: '', api_key: '' }],
      sonarr_instances: [{ name: 'Sonarr', url: '', api_key: '' }],
      radarr_instances: [{ name: 'Radarr', url: '', api_key: '' }],
    },
    setMediaSettings: vi.fn(),
    deleteConfirm: { show: false, type: null, index: null, name: '' },
    setDeleteConfirm: vi.fn(),
    editingSonarr: new Set<number>(),
    editingRadarr: new Set<number>(),
    editingPlex: new Set<number>(),
    testingPlex: new Set<number>(),
    testingSonarr: new Set<number>(),
    testingRadarr: new Set<number>(),
    showPlexTokens: {},
    showSonarrKeys: {},
    showRadarrKeys: {},
    showLibraryModal: false,
    setShowLibraryModal: vi.fn(),
    libraryModalInstance: null,
    loadingLibraries: false,
    libraries: [],
    fetchLibraryConfigs: (...args: unknown[]) => mockFetchLibraryConfigs(...args),
    updateSonarrInstance: vi.fn(),
    updateRadarrInstance: vi.fn(),
    updatePlexInstance: vi.fn(),
    toggleEditPlex: vi.fn(),
    testPlexConnection: vi.fn(),
    openLibraryModal: vi.fn(),
    toggleLibrary: vi.fn(),
    togglePlexTokenVisibility: vi.fn(),
    toggleSonarrKeyVisibility: vi.fn(),
    toggleRadarrKeyVisibility: vi.fn(),
    saveLibraryConfig: vi.fn(),
    addPlexInstance: vi.fn(),
    confirmRemovePlexInstance: vi.fn(),
    testSonarrConnection: vi.fn(),
    testRadarrConnection: vi.fn(),
    addSonarrInstance: vi.fn(),
    toggleEditSonarr: vi.fn(),
    addRadarrInstance: vi.fn(),
    toggleEditRadarr: vi.fn(),
    confirmRemoveSonarrInstance: vi.fn(),
    confirmRemoveRadarrInstance: vi.fn(),
    handleConfirmDelete: vi.fn(),
    handleSaveMediaSettings: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/useSettingsCore', () => ({
  useSettingsCore: () => ({
    debugEnabled: false,
    loading: false,
    rcloneSettings: {
      google_client_id: '',
      google_client_secret: '',
      google_token: '',
      google_service_account_file: '',
    },
    setRcloneSettings: vi.fn(),
    showSaveConfirm: true,
    setShowSaveConfirm: (...args: unknown[]) => mockSetShowSaveConfirm(...args),
    fetchSettings: (...args: unknown[]) => mockFetchSettings(...args),
    fetchDebugStatus: (...args: unknown[]) => mockFetchDebugStatus(...args),
    handleDebugToggle: vi.fn(),
    handleSaveRclone: vi.fn(),
    confirmSaveRclone: (...args: unknown[]) => mockConfirmSaveRclone(...args),
    handleUploadServiceAccount: vi.fn(),
    uploadingServiceAccount: false,
  }),
}))

vi.mock('../../src/hooks/useSettingsSchedules', () => ({
  useSettingsSchedules: () => ({
    editingSchedule: null,
    scheduleSaving: false,
    addSchedule: vi.fn(),
    updateScheduleField: vi.fn(),
    toggleEditSchedule: vi.fn(),
    toggleScheduleEnabled: vi.fn(),
    saveSchedule: vi.fn(),
    removeSchedule: vi.fn(),
    getScheduleSummary: vi.fn(() => ''),
    setEditingSchedule: vi.fn(),
  }),
}))

vi.mock('../../src/components/settings/SettingsTabs', () => ({
  default: (props: { activeTab: string; onTabChange: (tab: 'basic' | 'rclone' | 'media' | 'scheduling' | 'backup' | 'maintenance') => void }) => (
    <div>
      <button type="button" onClick={() => props.onTabChange('basic')}>Basic</button>
      <button type="button" onClick={() => props.onTabChange('rclone')}>Rclone</button>
      <button type="button" onClick={() => props.onTabChange('media')}>Media</button>
    </div>
  ),
}))

vi.mock('../../src/components/settings/SettingsRcloneSection', () => ({
  default: (props: { onSave: () => void }) => (
    <div>
      <div>Rclone Section Mock</div>
      <button type="button" onClick={props.onSave}>Save Rclone</button>
    </div>
  ),
}))

vi.mock('../../src/components/settings/SettingsMediaSection', () => ({
  default: () => <div>Media Section Mock</div>,
}))

vi.mock('../../src/components/settings/SettingsSchedulingSection', () => ({
  default: () => <div>Scheduling Section Mock</div>,
}))

vi.mock('../../src/components/settings/BackupRestoreSection', () => ({
  default: () => <div>Backup Section Mock</div>,
}))

vi.mock('../../src/components/settings/MaintenanceSection', () => ({
  default: () => <div>Maintenance Section Mock</div>,
}))

vi.mock('../../src/components/settings/ScheduleEditModal', () => ({
  default: () => null,
}))

vi.mock('../../src/components/settings/PlexLibraryModal', () => ({
  default: () => null,
}))

vi.mock('../../src/components/settings/RestartRequiredModal', () => ({
  default: () => null,
}))

vi.mock('../../src/components/ConfirmDialog', () => ({
  default: (props: {
    isOpen: boolean
    title: string
    onConfirm: () => void
    onCancel: () => void
  }) => (
    props.isOpen ? (
      <div>
        <div>{props.title}</div>
        <button type="button" onClick={props.onConfirm}>Confirm</button>
        <button type="button" onClick={props.onCancel}>Cancel</button>
      </div>
    ) : null
  ),
}))

describe('Settings', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()

    mockGetSchedules.mockResolvedValue([])
    mockGetDrives.mockResolvedValue([])
    mockGetDiscordNotificationConfig.mockResolvedValue({
      enabled: false,
      webhook_url: '',
      features: {},
    })
    mockSaveDiscordNotificationConfig.mockResolvedValue({ message: 'ok' })
    mockTestDiscordNotification.mockResolvedValue({ success: true, message: 'ok' })
  })

  it('fetches initial settings data on mount', () => {
    render(<Settings />)

    expect(mockFetchDebugStatus).toHaveBeenCalledTimes(1)
    expect(mockFetchSettings).toHaveBeenCalledTimes(1)
    expect(mockGetSchedules).toHaveBeenCalledTimes(1)
    expect(mockGetDrives).toHaveBeenCalledTimes(1)
    expect(mockFetchLibraryConfigs).toHaveBeenCalledTimes(1)
  })

  it('switches tabs and renders matching section', async () => {
    const user = userEvent.setup()
    render(<Settings />)

    await user.click(screen.getByRole('button', { name: 'Rclone' }))
    expect(screen.getByText('Rclone Section Mock')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Media' }))
    expect(screen.getByText('Media Section Mock')).toBeTruthy()
  })

  it('calls confirm save when save dialog is confirmed', async () => {
    const user = userEvent.setup()
    render(<Settings />)

    expect(screen.getByText('Save Rclone Settings')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(mockConfirmSaveRclone).toHaveBeenCalledTimes(1)
  })
})
