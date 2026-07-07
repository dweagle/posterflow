import { useEffect, useState, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { 
  Drive,
  PosterConfig,
  getSettings,
} from '../api/client'
import { getPosterStats, PosterStyleStats } from '../api/posterManager'
import { useToast } from '../components/Toast'
import { useUnmatched } from '../contexts/UnmatchedContext'
import PosterManagerTabs, { PosterManagerTab } from '../components/poster-manager/PosterManagerTabs'
import UnmatchedItemsModal, { UnmatchedModalType } from '../components/poster-manager/UnmatchedItemsModal'
import UnsavedChangesModal from '../components/poster-manager/UnsavedChangesModal'
import PriorityTab from '../components/poster-manager/PriorityTab'
import { CommunityClaimStatusProvider } from '../hooks/useCommunityClaimStatus'
import FlowTab from '../components/poster-manager/FlowTab'
import UnmatchedTab from '../components/poster-manager/UnmatchedTab'
import RenamerTab from '../components/poster-manager/RenamerTab'
import BorderTab from '../components/poster-manager/BorderTab'
import SettingsTab from '../components/poster-manager/SettingsTab'
import { usePosterManagerUnmatchedReports } from '../hooks/usePosterManagerUnmatchedReports'
import { usePosterManagerTabGuard } from '../hooks/usePosterManagerTabGuard'
import { usePosterManagerPriority } from '../hooks/usePosterManagerPriority'
import { usePosterManagerFlow } from '../hooks/usePosterManagerFlow'
import { OriginalBorderSettings, usePosterManagerBorder } from '../hooks/usePosterManagerBorder'
import { usePosterManagerLibraries } from '../hooks/usePosterManagerLibraries'
import { usePosterManagerJobMonitoring } from '../hooks/usePosterManagerJobMonitoring'
import { usePosterManagerSettings } from '../hooks/usePosterManagerSettings'
import { usePosterManagerActions } from '../hooks/usePosterManagerActions'
import { usePosterManagerLifecycle } from '../hooks/usePosterManagerLifecycle'
import { usePosterManagerManualMedia } from '../hooks/usePosterManagerManualMedia'
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
  const [tmdbApiKeyConfigured, setTmdbApiKeyConfigured] = useState(false)
  const [styleStats, setStyleStats] = useState<PosterStyleStats | null>(null)
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
  const originalBorderSettingsRef = useRef<OriginalBorderSettings | null>(null)
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
    handleAddAllStyle,
    handleRemoveAllStyle,
    handleDropInAvailable,
    handleDragOverEnd,
    handleDriveTouchStart,
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
    bandWidth,
    borderMode,
    newColor,
    autoRunBorder,
    autoRunCleanup,
    cleanupDeleteUnknown,
    holidaySchedules,
    skipRunOutsideHoliday,
    removeBorders,
    setBorderWidth,
    setBandWidth,
    setBorderMode,
    setNewColor,
    setAutoRunBorder,
    setAutoRunCleanup,
    setCleanupDeleteUnknown,
    setSkipRunOutsideHoliday,
    setRemoveBorders,
    fetchBorderSettings,
    saveBorderSettings,
    resetBorderSettingsToOriginal,
    resetBorderSettingsToDefaults,
    addBorderColor,
    removeBorderColor,
    addHolidaySchedule,
    removeHolidaySchedule,
    seasonMode,
    seasonColors,
    seasonWidth,
    seasonStyle,
    setSeasonMode,
    setSeasonWidth,
    setSeasonColors,
    setSeasonStyle,
    borderStyle,
    overlayImage,
    overlayRemoveExisting,
    gradientColors,
    gradientDirection,
    newGradientColor,
    innerEffect,
    innerColor,
    innerOpacity,
    innerWidth,
    fadeWidth,
    setBorderStyle,
    setOverlayImage,
    setOverlayRemoveExisting,
    setGradientDirection,
    setNewGradientColor,
    setInnerEffect,
    setInnerColor,
    setInnerOpacity,
    setInnerWidth,
    setFadeWidth,
    addGradientColor,
    removeGradientColor,
    plexRules,
    ruleRunTypes,
    ruleLibraries,
    setPlexRules,
    toggleRuleRunType,
    toggleRuleLibrary,
  } = usePosterManagerBorder({
    originalBorderSettingsRef,
    setHasUnsavedBorderChanges,
    setSaving,
    showToast,
  })

  const borderReplacerConfigured =
    removeBorders ||
    borderColors.length > 0 ||
    (borderStyle === 'image' && overlayImage.trim().length > 0) ||
    (borderStyle === 'gradient' && gradientColors.length > 0) ||
    holidaySchedules.some((holiday) => {
      const hasColors = Array.isArray(holiday.colors) && holiday.colors.some((color) => color.trim().length > 0)
      const style = holiday.style
      const hasImage = style?.style === 'image' && style.overlayImage.trim().length > 0
      const hasGradient = style?.style === 'gradient' && style.gradientColors.length > 0
      return hasColors || hasImage || hasGradient
    })

  const {
    flowConfig,
    flowRunning,
    flowResult,
    hasUnsavedFlowChanges,
    idarrSyncTargets,
    idarrShowInWorkflow,
    idarrScopeError,
    setFlowRunning,
    fetchFlowConfig,
    fetchIdarrSyncTargets,
    checkActiveWorkflow,
    handleFlowConfigChange,
    handleIdarrFlowConfigChange,
    handleIdarrScopeToggle,
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
    hasUnsavedUnmatchedSettings,
    fetchLibraryConfigs,
    toggleLibrarySelection,
    setUnmatchedIgnoreRootFoldersText,
    setUnmatchedIgnoreCollectionsText,
    setUnmatchedIgnoreUnmonitored,
    saveRenameSettings,
    resetLibrarySettingsToOriginal,
  } = usePosterManagerLibraries({
    activeTab,
    autoRunBorder,
    autoRunCleanup,
    cleanupDeleteUnknown,
    originalLibrarySelectionRef,
    originalBorderSettingsRef,
    setHasUnsavedLibraryChanges,
    setHasUnsavedBorderChanges,
    showToast,
  })

  useEffect(() => {
    getSettings()
      .then((s) => {
        const key = (s.tmdb_api_key || '').trim()
        setTmdbApiKeyConfigured(key.length > 0)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (activeTab === 'priority') {
      getPosterStats().then(setStyleStats).catch(() => setStyleStats(null))
    }
  }, [activeTab])

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

  const { downloadCompleteReport } = usePosterManagerUnmatchedReports({
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

  const {
    manualEntries,
    formTitle,
    formYear,
    formType,
    formSeasonCount,
    formSpecials,
    formTmdbId,
    formTvdbId,
    formImdbId,
    adding,
    setFormTitle,
    setFormYear,
    setFormType,
    setFormSeasonCount,
    setFormSpecials,
    setFormTmdbId,
    setFormTvdbId,
    setFormImdbId,
    handleAddManualEntry,
    handleDeleteEntry,
  } = usePosterManagerManualMedia({ showToast })

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
    bandWidth,
    borderMode,
    autoRunBorder,
    autoRunCleanup,
    cleanupDeleteUnknown,
    holidaySchedules,
    skipRunOutsideHoliday,
    removeBorders,
    seasonMode,
    seasonColors,
    seasonWidth,
    seasonStyle,
    borderStyle,
    overlayImage,
    overlayRemoveExisting,
    gradientColors,
    gradientDirection,
    innerEffect,
    innerColor,
    innerOpacity,
    innerWidth,
    fadeWidth,
    plexRules,
    ruleRunTypes,
    ruleLibraries,
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
    fetchIdarrSyncTargets,
    fetchBorderSettings,
    checkActiveWorkflow,
    fetchLibraryConfigs,
    drives,
    loadPriority,
  })

  return (
    <CommunityClaimStatusProvider>
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
          onDriveTouchStart={handleDriveTouchStart}
          onRemoveFromPriority={handleRemoveFromPriority}
          onAddAllStyle={handleAddAllStyle}
          onRemoveAllStyle={handleRemoveAllStyle}
          styleStats={styleStats}
          tmdbApiKeyConfigured={tmdbApiKeyConfigured}
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
          hasUnsavedUnmatchedSettings={hasUnsavedUnmatchedSettings}
          onSetUnmatchedIgnoreRootFoldersText={setUnmatchedIgnoreRootFoldersText}
          onSetUnmatchedIgnoreCollectionsText={setUnmatchedIgnoreCollectionsText}
          onSetUnmatchedIgnoreUnmonitored={setUnmatchedIgnoreUnmonitored}
          onOpenModal={setShowUnmatchedModal}
        />
      )}

      {showUnmatchedModal && (
        <UnmatchedItemsModal
          modalType={showUnmatchedModal}
          unmatchedStats={unmatchedStats}
          modalDisplayLimit={MODAL_DISPLAY_LIMIT}
          tmdbApiKeyConfigured={tmdbApiKeyConfigured}
          onClose={() => setShowUnmatchedModal(null)}
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
          autoRunCleanup={autoRunCleanup}
          cleanupDeleteUnknown={cleanupDeleteUnknown}
          libraryConfigs={libraryConfigs}
          selectedLibraries={selectedLibraries}
          manualEntries={manualEntries}
          formTitle={formTitle}
          formYear={formYear}
          formType={formType}
          formSeasonCount={formSeasonCount}
          formSpecials={formSpecials}
          formTmdbId={formTmdbId}
          formTvdbId={formTvdbId}
          formImdbId={formImdbId}
          adding={adding}
          onSaveSettings={saveRenameSettings}
          onRunRename={handleStartRename}
          onSetAutoRunBorder={setAutoRunBorder}
          onSetAutoRunCleanup={setAutoRunCleanup}
          onSetCleanupDeleteUnknown={setCleanupDeleteUnknown}
          onToggleLibrarySelection={toggleLibrarySelection}
          onFormTitleChange={setFormTitle}
          onFormYearChange={setFormYear}
          onFormTypeChange={setFormType}
          onFormSeasonCountChange={setFormSeasonCount}
          onFormSpecialsChange={setFormSpecials}
          onFormTmdbIdChange={setFormTmdbId}
          onFormTvdbIdChange={setFormTvdbId}
          onFormImdbIdChange={setFormImdbId}
          onAddManualEntry={handleAddManualEntry}
          onDeleteEntry={handleDeleteEntry}
        />
      )}

      {/* Border Replacer Tab */}
      {activeTab === 'border' && (
        <BorderTab
          hasUnsavedBorderChanges={hasUnsavedBorderChanges}
          saving={saving}
          runningBorderReplacer={runningBorderReplacer}
          borderWidth={borderWidth}
          bandWidth={bandWidth}
          borderMode={borderMode}
          newColor={newColor}
          borderColors={borderColors}
          holidaySchedules={holidaySchedules}
          skipRunOutsideHoliday={skipRunOutsideHoliday}
          removeBorders={removeBorders}
          seasonMode={seasonMode}
          seasonColors={seasonColors}
          seasonWidth={seasonWidth}
          seasonStyle={seasonStyle}
          onSaveSettings={saveBorderSettings}
          onResetBorderSettings={resetBorderSettingsToDefaults}
          onRunBorderReplacer={handleRunBorderReplacer}
          onSetBorderWidth={setBorderWidth}
          onSetBandWidth={setBandWidth}
          onSetBorderMode={setBorderMode}
          onSetNewColor={setNewColor}
          onAddBorderColor={addBorderColor}
          onRemoveBorderColor={removeBorderColor}
          onSetRemoveBorders={setRemoveBorders}
          onSetSkipRunOutsideHoliday={setSkipRunOutsideHoliday}
          onAddHolidaySchedule={addHolidaySchedule}
          onRemoveHolidaySchedule={removeHolidaySchedule}
          onSetSeasonMode={setSeasonMode}
          onSetSeasonWidth={setSeasonWidth}
          onSetSeasonColors={setSeasonColors}
          onSetSeasonStyle={setSeasonStyle}
          borderStyle={borderStyle}
          overlayImage={overlayImage}
          overlayRemoveExisting={overlayRemoveExisting}
          gradientColors={gradientColors}
          gradientDirection={gradientDirection}
          newGradientColor={newGradientColor}
          innerEffect={innerEffect}
          innerColor={innerColor}
          innerOpacity={innerOpacity}
          innerWidth={innerWidth}
          fadeWidth={fadeWidth}
          onSetBorderStyle={setBorderStyle}
          onSetOverlayImage={setOverlayImage}
          onSetOverlayRemoveExisting={setOverlayRemoveExisting}
          onSetGradientDirection={setGradientDirection}
          onSetNewGradientColor={setNewGradientColor}
          onAddGradientColor={addGradientColor}
          onRemoveGradientColor={removeGradientColor}
          onSetInnerEffect={setInnerEffect}
          onSetInnerColor={setInnerColor}
          onSetInnerOpacity={setInnerOpacity}
          onSetInnerWidth={setInnerWidth}
          onSetFadeWidth={setFadeWidth}
          plexRules={plexRules}
          ruleRunTypes={ruleRunTypes}
          ruleLibraries={ruleLibraries}
          onSetPlexRules={setPlexRules}
          onToggleRuleRunType={toggleRuleRunType}
          onToggleRuleLibrary={toggleRuleLibrary}
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
          idarrSyncTargets={idarrSyncTargets}
          idarrShowInWorkflow={idarrShowInWorkflow}
          idarrScopeError={idarrScopeError}
          formatPercent={formatPercent}
          onSaveFlowConfig={handleSaveFlowConfig}
          onRunFlow={handleRunFlow}
          onChangeFlowConfig={handleFlowConfigChange}
          onChangeIdarrFlowConfig={handleIdarrFlowConfigChange}
          onToggleIdarrScope={handleIdarrScopeToggle}
        />
      )}
      
      <UnsavedChangesModal
        isOpen={showUnsavedModal}
        onCancel={handleCancelTabChange}
        onDiscard={handleDiscardChangesWithReset}
      />
    </div>
    </CommunityClaimStatusProvider>
  )
}

export default PosterManager
