import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import PosterManager from '../../src/pages/PosterManager'

const mockHandleStartAssetRename = vi.fn()
const mockHandleDiscardChanges = vi.fn()
const mockResetPriorityToOriginal = vi.fn()
const mockResetBorderSettingsToOriginal = vi.fn()
const mockResetLibrarySettingsToOriginal = vi.fn()
const mockResetFlowConfigToOriginal = vi.fn()
const mockResetConfigToOriginal = vi.fn()

vi.mock('react-router-dom', () => ({
  useLocation: () => ({ state: null }),
}))

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('../../src/contexts/AppEventsContext', () => ({
  useAppEvents: () => ({
    unmatchedStats: { summary: { grand_total: { unmatched: 2 } } },
    refreshStats: vi.fn(),
    jobs: [],
  }),
}))

vi.mock('../../src/hooks/usePosterManagerUnmatchedReports', () => ({
  usePosterManagerUnmatchedReports: () => ({
    downloadUnmatchedList: vi.fn(),
    downloadCompleteReport: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerTabGuard', () => ({
  usePosterManagerTabGuard: (args: { setActiveTab: (tab: 'priority' | 'settings' | 'unmatched' | 'flow' | 'rename' | 'border') => void }) => ({
    showUnsavedModal: true,
    handleTabChange: (tab: 'priority' | 'settings' | 'unmatched' | 'flow' | 'rename' | 'border') => args.setActiveTab(tab),
    handleDiscardChanges: mockHandleDiscardChanges,
    handleCancelTabChange: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerPriority', () => ({
  usePosterManagerPriority: () => ({
    enabledStyles: new Set<string>(),
    priorityList: [],
    draggedDrive: null,
    dragOverIndex: null,
    loadPriority: vi.fn(),
    toggleStyle: vi.fn(),
    handleDragStart: vi.fn(),
    handleDragOver: vi.fn(),
    handleDragOverCard: vi.fn(),
    handleDragLeave: vi.fn(),
    handleDropInPriority: vi.fn(),
    handleRemoveFromPriority: vi.fn(),
    handleDropInAvailable: vi.fn(),
    handleDragOverEnd: vi.fn(),
    savePriority: vi.fn(),
    resetPriorityToOriginal: mockResetPriorityToOriginal,
    clearDragOverTimeout: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerFlow', () => ({
  usePosterManagerFlow: () => ({
    flowConfig: { sync_drives: { enabled: true, stop_on_error: false } },
    flowRunning: false,
    flowResult: null,
    hasUnsavedFlowChanges: false,
    setFlowRunning: vi.fn(),
    fetchFlowConfig: vi.fn(),
    checkActiveWorkflow: vi.fn(),
    handleFlowConfigChange: vi.fn(),
    handleSaveFlowConfig: vi.fn(),
    resetFlowConfigToOriginal: mockResetFlowConfigToOriginal,
    handleRunFlow: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerBorder', () => ({
  usePosterManagerBorder: () => ({
    borderColors: [],
    borderWidth: 10,
    borderMode: 'incremental',
    newColor: '',
    autoRunBorder: false,
    setBorderWidth: vi.fn(),
    setBorderMode: vi.fn(),
    setNewColor: vi.fn(),
    setAutoRunBorder: vi.fn(),
    setSkipRunOutsideHoliday: vi.fn(),
    setRemoveBorders: vi.fn(),
    holidaySchedules: [],
    skipRunOutsideHoliday: false,
    removeBorders: false,
    fetchBorderSettings: vi.fn(),
    saveBorderSettings: vi.fn(),
    resetBorderSettingsToOriginal: mockResetBorderSettingsToOriginal,
    addBorderColor: vi.fn(),
    removeBorderColor: vi.fn(),
    addHolidaySchedule: vi.fn(),
    removeHolidaySchedule: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerLibraries', () => ({
  usePosterManagerLibraries: () => ({
    libraryConfigs: [],
    selectedLibraries: new Set<string>(),
    unmatchedIgnoreRootFoldersText: '',
    unmatchedIgnoreUnmonitored: false,
    unmatchedTmdbApiKey: '',
    hasUnsavedUnmatchedSettings: false,
    fetchLibraryConfigs: vi.fn(),
    toggleLibrarySelection: vi.fn(),
    setUnmatchedIgnoreRootFoldersText: vi.fn(),
    setUnmatchedIgnoreUnmonitored: vi.fn(),
    setUnmatchedTmdbApiKey: vi.fn(),
    saveRenameSettings: vi.fn(),
    resetLibrarySettingsToOriginal: mockResetLibrarySettingsToOriginal,
  }),
}))

vi.mock('../../src/hooks/usePosterManagerJobMonitoring', () => ({
  usePosterManagerJobMonitoring: vi.fn(),
}))

vi.mock('../../src/hooks/usePosterManagerSettings', () => ({
  usePosterManagerSettings: () => ({
    fetchConfig: vi.fn(),
    fetchDrives: vi.fn(),
    handleConfigChange: vi.fn(),
    handleSaveConfig: vi.fn(),
    resetConfigToOriginal: mockResetConfigToOriginal,
  }),
}))

vi.mock('../../src/hooks/usePosterManagerActions', () => ({
  usePosterManagerActions: () => ({
    handleDetectUnmatched: vi.fn(),
    handleStartAssetRename: mockHandleStartAssetRename,
    handleRunBorderReplacer: vi.fn(),
  }),
}))

vi.mock('../../src/hooks/usePosterManagerLifecycle', () => ({
  usePosterManagerLifecycle: vi.fn(),
}))

vi.mock('../../src/components/poster-manager/PriorityTab', () => ({ default: () => <div>Priority Tab Mock</div> }))
vi.mock('../../src/components/poster-manager/FlowTab', () => ({ default: () => <div>Flow Tab Mock</div> }))
vi.mock('../../src/components/poster-manager/UnmatchedTab', () => ({ default: () => <div>Unmatched Tab Mock</div> }))
vi.mock('../../src/components/poster-manager/SettingsTab', () => ({ default: () => <div>Settings Tab Mock</div> }))
vi.mock('../../src/components/poster-manager/BorderTab', () => ({ default: () => <div>Border Tab Mock</div> }))
vi.mock('../../src/components/poster-manager/UnmatchedItemsModal', () => ({ default: () => null }))
vi.mock('../../src/components/poster-manager/UnsavedChangesModal', () => ({
  default: (props: { isOpen: boolean; onDiscard: () => void }) =>
    props.isOpen ? <button type="button" onClick={props.onDiscard}>Discard Changes</button> : null,
}))
vi.mock('../../src/components/poster-manager/RenamerTab', () => ({
  default: (props: { onRunRename: (dryRun?: boolean) => void }) => (
    <div>
      Rename Tab Mock
      <button type="button" onClick={() => props.onRunRename(false)}>Run Rename</button>
    </div>
  ),
}))

describe('PosterManager', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()

  })

  it('renders flow tab by default and switches to settings tab', async () => {
    const user = userEvent.setup()
    render(<PosterManager />)

    expect(screen.getByText('Flow Tab Mock')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: /Settings/i }))

    expect(screen.getByText('Settings Tab Mock')).toBeTruthy()
  })

  it('triggers rename action from rename tab', async () => {
    const user = userEvent.setup()
    render(<PosterManager />)

    await user.click(screen.getByRole('button', { name: /Asset Renamer/i }))
    expect(screen.getByText('Rename Tab Mock')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Run Rename' }))
    expect(mockHandleStartAssetRename).toHaveBeenCalledWith(false)
  })

  it('resets border state when discarding unsaved changes from border tab', async () => {
    const user = userEvent.setup()
    render(<PosterManager />)

    await user.click(screen.getByRole('button', { name: /Border Replacer/i }))
    await user.click(screen.getByRole('button', { name: 'Discard Changes' }))

    expect(mockResetBorderSettingsToOriginal).toHaveBeenCalledTimes(1)
    expect(mockHandleDiscardChanges).toHaveBeenCalledTimes(1)
    expect(mockResetPriorityToOriginal).not.toHaveBeenCalled()
    expect(mockResetConfigToOriginal).not.toHaveBeenCalled()
    expect(mockResetFlowConfigToOriginal).not.toHaveBeenCalled()
    expect(mockResetLibrarySettingsToOriginal).not.toHaveBeenCalled()
  })
})
