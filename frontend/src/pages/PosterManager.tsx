import { useEffect, useState, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { 
  Drive,
  PosterConfig
} from '../api/client'
import { useToast } from '../components/Toast'
import { useUnmatched } from '../contexts/UnmatchedContext'
import PosterManagerTabs, { PosterManagerTab } from '../components/poster-manager/PosterManagerTabs'
import UnmatchedItemsModal, { UnmatchedModalType } from '../components/poster-manager/UnmatchedItemsModal'
import UnsavedChangesModal from '../components/poster-manager/UnsavedChangesModal'
import PriorityTab from '../components/poster-manager/PriorityTab'
import FlowTab from '../components/poster-manager/FlowTab'
import UnmatchedTab from '../components/poster-manager/UnmatchedTab'
import RenamerTab from '../components/poster-manager/RenamerTab'
import BorderTab from '../components/poster-manager/BorderTab'
import SettingsTab from '../components/poster-manager/SettingsTab'
import { usePosterManagerUnmatchedReports } from '../hooks/usePosterManagerUnmatchedReports'
import { usePosterManagerTabGuard } from '../hooks/usePosterManagerTabGuard'
import { usePosterManagerPriority } from '../hooks/usePosterManagerPriority'
import { usePosterManagerFlow } from '../hooks/usePosterManagerFlow'
import { BorderHolidaySchedule, usePosterManagerBorder } from '../hooks/usePosterManagerBorder'
import { usePosterManagerLibraries } from '../hooks/usePosterManagerLibraries'
import { usePosterManagerJobMonitoring } from '../hooks/usePosterManagerJobMonitoring'
import { usePosterManagerSettings } from '../hooks/usePosterManagerSettings'
import { usePosterManagerActions } from '../hooks/usePosterManagerActions'
import { usePosterManagerLifecycle } from '../hooks/usePosterManagerLifecycle'
import './PosterManager.css'

// Limits to prevent performance issues
const CARD_PREVIEW_LIMIT = 5 // Number of items to show in card preview
const MODAL_DISPLAY_LIMIT = 200 // Max items to render in modal
const POSTER_MANAGER_TAB_STORAGE_KEY = 'posterflow.posterManager.activeTab'

const isPosterManagerTab = (value: string): value is PosterManagerTab => {
  return ['flow', 'settings', 'priority', 'border', 'rename', 'unmatched'].includes(value)
}

function PosterManager() {
  const location = useLocation()
  const { unmatchedStats, refreshStats } = useUnmatched()
  const [activeTab, setActiveTab] = useState<PosterManagerTab>(() => {
    const savedTab = localStorage.getItem(POSTER_MANAGER_TAB_STORAGE_KEY)
    if (savedTab && isPosterManagerTab(savedTab)) {
      return savedTab
    }
    return 'flow'
  })
  const [config, setConfig] = useState<PosterConfig | null>(null)
  const [showUnmatchedModal, setShowUnmatchedModal] = useState<UnmatchedModalType>(null)
  const [drives, setDrives] = useState<Drive[]>([])
  const [renaming, setRenaming] = useState(false)
  const [detectingUnmatched, setDetectingUnmatched] = useState(false)
  const [runningBorderReplacer, setRunningBorderReplacer] = useState(false)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const [saving, setSaving] = useState(false)
  
  
  // Unsaved changes tracking for other tabs
  const [hasUnsavedPriorityChanges, setHasUnsavedPriorityChanges] = useState(false)
  const [hasUnsavedBorderChanges, setHasUnsavedBorderChanges] = useState(false)
  const [hasUnsavedLibraryChanges, setHasUnsavedLibraryChanges] = useState(false)
  const trackedWorkflowJobRef = useRef<number | null>(null)
  const trackedUnmatchedJobRef = useRef<number | null>(null)
  
  // Drive priority state
  
  // Original values for smart comparison (stored in refs to avoid re-renders)
  const originalConfigRef = useRef<PosterConfig | null>(null)
  const originalPriorityRef = useRef<{ drive_ids: number[], enabled_styles: string[] } | null>(null)
  const originalBorderSettingsRef = useRef<{
    colors: string[]
    width: number
    mode: 'incremental' | 'full'
    autoRunBorder: boolean
    holidaySchedules: BorderHolidaySchedule[]
    skipRunOutsideHoliday: boolean
    removeBorders: boolean
  } | null>(null)
  const originalLibrarySelectionRef = useRef<Set<string> | null>(null)
  
  const { showToast } = useToast()

  useEffect(() => {
    localStorage.setItem(POSTER_MANAGER_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  const {
    enabledStyles,
    priorityList,
    draggedDrive,
    dragOverIndex,
    loadPriority,
    toggleStyle,
    handleDragStart,
    handleDragOver,
    handleDragOverStart,
    handleDragEnd,
    handleDragOverCard,
    handleDragLeave,
    handleDropInPriority,
    handleRemoveFromPriority,
    handleDropInAvailable,
    handleDragOverEnd,
    savePriority,
    resetPriorityToOriginal,
    clearDragOverTimeout,
  } = usePosterManagerPriority({
    drives,
    originalPriorityRef,
    setHasUnsavedPriorityChanges,
    showToast,
  })

  const {
    borderColors,
    borderWidth,
    borderMode,
    newColor,
    autoRunBorder,
    holidaySchedules,
    skipRunOutsideHoliday,
    removeBorders,
    setBorderWidth,
    setBorderMode,
    setNewColor,
    setAutoRunBorder,
    setSkipRunOutsideHoliday,
    setRemoveBorders,
    fetchBorderSettings,
    saveBorderSettings,
    resetBorderSettingsToOriginal,
    addBorderColor,
    removeBorderColor,
    addHolidaySchedule,
    removeHolidaySchedule,
  } = usePosterManagerBorder({
    originalBorderSettingsRef,
    setHasUnsavedBorderChanges,
    setSaving,
    showToast,
  })

  const borderReplacerConfigured =
    removeBorders ||
    borderColors.length > 0 ||
    holidaySchedules.some(
      (holiday) => Array.isArray(holiday.colors) && holiday.colors.some((color) => color.trim().length > 0)
    )

  const {
    flowConfig,
    flowRunning,
    flowResult,
    hasUnsavedFlowChanges,
    setFlowRunning,
    fetchFlowConfig,
    checkActiveWorkflow,
    handleFlowConfigChange,
    handleSaveFlowConfig,
    resetFlowConfigToOriginal,
    handleRunFlow,
  } = usePosterManagerFlow({
    setSaving,
    showToast,
    trackedWorkflowJobRef,
  })

  const {
    libraryConfigs,
    selectedLibraries,
    unmatchedIgnoreRootFoldersText,
    unmatchedIgnoreCollectionsText,
    unmatchedIgnoreUnmonitored,
    unmatchedTmdbApiKey,
    hasUnsavedUnmatchedSettings,
    fetchLibraryConfigs,
    toggleLibrarySelection,
    setUnmatchedIgnoreRootFoldersText,
    setUnmatchedIgnoreCollectionsText,
    setUnmatchedIgnoreUnmonitored,
    setUnmatchedTmdbApiKey,
    saveRenameSettings,
    resetLibrarySettingsToOriginal,
  } = usePosterManagerLibraries({
    activeTab,
    autoRunBorder,
    originalLibrarySelectionRef,
    originalBorderSettingsRef,
    setHasUnsavedLibraryChanges,
    setHasUnsavedBorderChanges,
    showToast,
  })

  const {
    showUnsavedModal,
    handleTabChange,
    handleDiscardChanges,
    handleCancelTabChange,
  } = usePosterManagerTabGuard({
    activeTab,
    setActiveTab,
    hasUnsavedChanges,
    hasUnsavedFlowChanges,
    hasUnsavedPriorityChanges,
    hasUnsavedBorderChanges,
    hasUnsavedLibraryChanges,
  })

  const formatPercent = (percent: number): string => {
    if (percent === 100) {
      return '100.0'
    } else if (percent >= 99.95) {
      return percent.toFixed(2)
    }
    return percent.toFixed(1)
  }

  const { downloadUnmatchedList, downloadCompleteReport } = usePosterManagerUnmatchedReports({
    unmatchedStats,
    showToast,
  })

  const {
    fetchConfig,
    fetchDrives,
    handleConfigChange,
    handleSaveConfig,
    resetConfigToOriginal,
  } = usePosterManagerSettings({
    config,
    setConfig,
    setDrives,
    originalConfigRef,
    setHasUnsavedChanges,
    setSaving,
    showToast,
  })

  const {
    handleDetectUnmatched,
    handleStartRename,
    handleRunBorderReplacer,
  } = usePosterManagerActions({
    config,
    borderReplacerConfigured,
    setRenaming,
    setDetectingUnmatched,
    setRunningBorderReplacer,
    trackedUnmatchedJobRef,
    showToast,
  })

  const handleDiscardChangesWithReset = () => {
    if (activeTab === 'settings') {
      resetConfigToOriginal()
    } else if (activeTab === 'flow') {
      resetFlowConfigToOriginal()
    } else if (activeTab === 'priority') {
      resetPriorityToOriginal()
    } else if (activeTab === 'border') {
      resetBorderSettingsToOriginal()
    } else if (activeTab === 'rename') {
      resetLibrarySettingsToOriginal()
      resetBorderSettingsToOriginal()
    } else if (activeTab === 'unmatched') {
      resetLibrarySettingsToOriginal()
    }

    handleDiscardChanges()
  }

  usePosterManagerJobMonitoring({
    trackedWorkflowJobRef,
    trackedUnmatchedJobRef,
    setFlowRunning,
    setDetectingUnmatched,
    refreshStats,
    showToast,
  })

  usePosterManagerLifecycle({
    locationState: location.state,
    activeTab,
    setActiveTab,
    config,
    originalConfigRef,
    setHasUnsavedChanges,
    enabledStyles,
    priorityList,
    originalPriorityRef,
    setHasUnsavedPriorityChanges,
    borderColors,
    borderWidth,
    borderMode,
    autoRunBorder,
    holidaySchedules,
    skipRunOutsideHoliday,
    removeBorders,
    originalBorderSettingsRef,
    setHasUnsavedBorderChanges,
    selectedLibraries,
    originalLibrarySelectionRef,
    setHasUnsavedLibraryChanges,
    clearDragOverTimeout,
    refreshStats,
    fetchConfig,
    fetchDrives,
    fetchFlowConfig,
    fetchBorderSettings,
    checkActiveWorkflow,
    fetchLibraryConfigs,
    drives,
    loadPriority,
  })

  return (
    <div className="page-container poster-manager">
      <div className="poster-manager-header">
        <h1>Poster Manager</h1>
        <p>Organize and rename poster files from multiple sources</p>
      </div>

      <PosterManagerTabs
        activeTab={activeTab}
        onTabChange={handleTabChange}
        unmatchedCount={unmatchedStats?.summary?.grand_total?.unmatched || 0}
      />



      {activeTab === 'settings' && (
        <SettingsTab
          config={config}
          hasUnsavedChanges={hasUnsavedChanges}
          saving={saving}
          onSaveConfig={handleSaveConfig}
          onConfigChange={handleConfigChange}
        />
      )}

      {activeTab === 'priority' && (
        <PriorityTab
          hasUnsavedPriorityChanges={hasUnsavedPriorityChanges}
          saving={saving}
          onSavePriority={savePriority}
          enabledStyles={enabledStyles}
          onToggleStyle={(style) => toggleStyle(style)}
          drives={drives}
          priorityList={priorityList}
          draggedDrive={draggedDrive}
          dragOverIndex={dragOverIndex}
          onDragStart={handleDragStart}
          onDragOver={handleDragOver}
          onDragOverStart={handleDragOverStart}
          onDragEnd={handleDragEnd}
          onDropInAvailable={handleDropInAvailable}
          onDragOverCard={handleDragOverCard}
          onDropInPriority={handleDropInPriority}
          onDragOverEnd={handleDragOverEnd}
          onDragLeave={handleDragLeave}
          onRemoveFromPriority={handleRemoveFromPriority}
        />
      )}

      {activeTab === 'unmatched' && (
        <UnmatchedTab
          unmatchedStats={unmatchedStats}
          hasUnsavedLibraryChanges={hasUnsavedLibraryChanges}
          saving={saving}
          detectingUnmatched={detectingUnmatched}
          libraryConfigs={libraryConfigs}
          selectedLibraries={selectedLibraries}
          cardPreviewLimit={CARD_PREVIEW_LIMIT}
          formatPercent={formatPercent}
          onSaveSettings={saveRenameSettings}
          onDetectUnmatched={handleDetectUnmatched}
          onDownloadReport={downloadCompleteReport}
          onToggleLibrarySelection={toggleLibrarySelection}
          unmatchedIgnoreRootFoldersText={unmatchedIgnoreRootFoldersText}
          unmatchedIgnoreCollectionsText={unmatchedIgnoreCollectionsText}
          unmatchedIgnoreUnmonitored={unmatchedIgnoreUnmonitored}
          unmatchedTmdbApiKey={unmatchedTmdbApiKey}
          hasUnsavedUnmatchedSettings={hasUnsavedUnmatchedSettings}
          onSetUnmatchedIgnoreRootFoldersText={setUnmatchedIgnoreRootFoldersText}
          onSetUnmatchedIgnoreCollectionsText={setUnmatchedIgnoreCollectionsText}
          onSetUnmatchedIgnoreUnmonitored={setUnmatchedIgnoreUnmonitored}
          onSetUnmatchedTmdbApiKey={setUnmatchedTmdbApiKey}
          onOpenModal={setShowUnmatchedModal}
        />
      )}

      {showUnmatchedModal && (
        <UnmatchedItemsModal
          modalType={showUnmatchedModal}
          unmatchedStats={unmatchedStats}
          modalDisplayLimit={MODAL_DISPLAY_LIMIT}
          tmdbApiKeyConfigured={!!unmatchedTmdbApiKey.trim()}
          onClose={() => setShowUnmatchedModal(null)}
          onDownloadList={downloadUnmatchedList}
        />
      )}

      {/* Flow Tab */}
      {activeTab === 'rename' && (
        <RenamerTab
          hasUnsavedLibraryChanges={hasUnsavedLibraryChanges}
          hasUnsavedBorderChanges={hasUnsavedBorderChanges}
          saving={saving}
          renaming={renaming}
          autoRunBorder={autoRunBorder}
          libraryConfigs={libraryConfigs}
          selectedLibraries={selectedLibraries}
          onSaveSettings={saveRenameSettings}
          onRunRename={handleStartRename}
          onSetAutoRunBorder={setAutoRunBorder}
          onToggleLibrarySelection={toggleLibrarySelection}
        />
      )}

      {/* Border Replacer Tab */}
      {activeTab === 'border' && (
        <BorderTab
          hasUnsavedBorderChanges={hasUnsavedBorderChanges}
          saving={saving}
          runningBorderReplacer={runningBorderReplacer}
          borderWidth={borderWidth}
          borderMode={borderMode}
          newColor={newColor}
          borderColors={borderColors}
          holidaySchedules={holidaySchedules}
          skipRunOutsideHoliday={skipRunOutsideHoliday}
          removeBorders={removeBorders}
          onSaveSettings={saveBorderSettings}
          onRunBorderReplacer={handleRunBorderReplacer}
          onSetBorderWidth={setBorderWidth}
          onSetBorderMode={setBorderMode}
          onSetNewColor={setNewColor}
          onAddBorderColor={addBorderColor}
          onRemoveBorderColor={removeBorderColor}
          onSetRemoveBorders={setRemoveBorders}
          onSetSkipRunOutsideHoliday={setSkipRunOutsideHoliday}
          onAddHolidaySchedule={addHolidaySchedule}
          onRemoveHolidaySchedule={removeHolidaySchedule}
        />
      )}

      {activeTab === 'flow' && flowConfig && (
        <FlowTab
          flowConfig={flowConfig}
          flowResult={flowResult}
          destination={config?.destination}
          hasUnsavedFlowChanges={hasUnsavedFlowChanges}
          saving={saving}
          flowRunning={flowRunning}
          formatPercent={formatPercent}
          onSaveFlowConfig={handleSaveFlowConfig}
          onRunFlow={handleRunFlow}
          onChangeFlowConfig={handleFlowConfigChange}
        />
      )}
      
      <UnsavedChangesModal
        isOpen={showUnsavedModal}
        onCancel={handleCancelTabChange}
        onDiscard={handleDiscardChangesWithReset}
      />
    </div>
  )
}

export default PosterManager
