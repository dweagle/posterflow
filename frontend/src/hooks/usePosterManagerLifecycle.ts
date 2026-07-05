import { useEffect } from 'react'
import { Drive, PosterConfig } from '../api/client'
import { PosterManagerTab } from '../components/poster-manager/PosterManagerTabs'
import {
  BorderHolidaySchedule,
  BorderStyle,
  GradientDirection,
  InnerEffect,
  OriginalBorderSettings,
  PlexBorderRule,
  RuleRunType,
  SeasonStyle,
} from './usePosterManagerBorder'

interface UsePosterManagerLifecycleOptions {
  locationState: unknown
  activeTab: PosterManagerTab
  setActiveTab: React.Dispatch<React.SetStateAction<PosterManagerTab>>
  config: PosterConfig | null
  originalConfigRef: React.MutableRefObject<PosterConfig | null>
  setHasUnsavedChanges: React.Dispatch<React.SetStateAction<boolean>>
  enabledStyles: Set<string>
  priorityList: Drive[]
  originalPriorityRef: React.MutableRefObject<{ drive_ids: number[]; enabled_styles: string[] } | null>
  setHasUnsavedPriorityChanges: React.Dispatch<React.SetStateAction<boolean>>
  borderColors: string[]
  borderWidth: number
  borderMode: 'incremental' | 'full'
  autoRunBorder: boolean
  autoRunCleanup: boolean
  cleanupDeleteUnknown: boolean
  holidaySchedules: BorderHolidaySchedule[]
  skipRunOutsideHoliday: boolean
  removeBorders: boolean
  seasonMode: 'inherit' | 'remove' | 'custom'
  seasonColors: string[]
  seasonWidth: number
  seasonStyle: SeasonStyle
  borderStyle: BorderStyle
  overlayImage: string
  overlayRemoveExisting: boolean
  gradientColors: string[]
  gradientDirection: GradientDirection
  innerEffect: InnerEffect
  innerColor: string
  innerOpacity: number
  innerWidth: number
  fadeWidth: number
  plexRules: PlexBorderRule[]
  ruleRunTypes: RuleRunType[]
  ruleLibraries: Set<string>
  originalBorderSettingsRef: React.MutableRefObject<OriginalBorderSettings | null>
  setHasUnsavedBorderChanges: React.Dispatch<React.SetStateAction<boolean>>
  selectedLibraries: Set<string>
  originalLibrarySelectionRef: React.MutableRefObject<Set<string> | null>
  setHasUnsavedLibraryChanges: React.Dispatch<React.SetStateAction<boolean>>
  clearDragOverTimeout: () => void
  refreshStats: () => Promise<void>
  fetchConfig: () => Promise<void>
  fetchDrives: () => Promise<void>
  fetchFlowConfig: () => Promise<void>
  fetchIdarrSyncTargets: () => Promise<void>
  fetchBorderSettings: () => Promise<void>
  checkActiveWorkflow: () => Promise<void>
  fetchLibraryConfigs: () => Promise<void>
  drives: Drive[]
  loadPriority: () => Promise<void>
}

type PosterManagerLocationState = {
  activeTab?: PosterManagerTab
}

export function usePosterManagerLifecycle({
  locationState,
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
}: UsePosterManagerLifecycleOptions) {
  useEffect(() => {
    return () => {
      clearDragOverTimeout()
    }
  }, [clearDragOverTimeout])

  useEffect(() => {
    if (!config || !originalConfigRef.current) return
    const hasChanged = JSON.stringify(config) !== JSON.stringify(originalConfigRef.current)
    setHasUnsavedChanges(hasChanged)
  }, [config, originalConfigRef, setHasUnsavedChanges])

  useEffect(() => {
    if (!originalPriorityRef.current) return

    const currentDriveIds = priorityList.map((drive) => drive.id)
    const currentStyles = Array.from(enabledStyles).sort()
    const originalStyles = [...originalPriorityRef.current.enabled_styles].sort()

    const driveIdsChanged = JSON.stringify(currentDriveIds) !== JSON.stringify(originalPriorityRef.current.drive_ids)
    const stylesChanged = JSON.stringify(currentStyles) !== JSON.stringify(originalStyles)

    setHasUnsavedPriorityChanges(driveIdsChanged || stylesChanged)
  }, [enabledStyles, originalPriorityRef, priorityList, setHasUnsavedPriorityChanges])

  useEffect(() => {
    if (!originalBorderSettingsRef.current) return

    const colorsChanged = JSON.stringify(borderColors) !== JSON.stringify(originalBorderSettingsRef.current.colors)
    const widthChanged = borderWidth !== originalBorderSettingsRef.current.width
    const modeChanged = borderMode !== originalBorderSettingsRef.current.mode
    const autoRunChanged = autoRunBorder !== originalBorderSettingsRef.current.autoRunBorder
    const autoRunCleanupChanged = autoRunCleanup !== originalBorderSettingsRef.current.autoRunCleanup
    const cleanupDeleteUnknownChanged = cleanupDeleteUnknown !== originalBorderSettingsRef.current.cleanupDeleteUnknown
    const holidaysChanged = JSON.stringify(holidaySchedules) !== JSON.stringify(originalBorderSettingsRef.current.holidaySchedules)
    const skipRunChanged = skipRunOutsideHoliday !== originalBorderSettingsRef.current.skipRunOutsideHoliday
    const removeBordersChanged = removeBorders !== originalBorderSettingsRef.current.removeBorders
    const seasonModeChanged = seasonMode !== originalBorderSettingsRef.current.seasonMode
    const seasonColorsChanged = JSON.stringify(seasonColors) !== JSON.stringify(originalBorderSettingsRef.current.seasonColors)
    const seasonWidthChanged = seasonWidth !== originalBorderSettingsRef.current.seasonWidth
    const seasonStyleChanged = JSON.stringify(seasonStyle) !== JSON.stringify(originalBorderSettingsRef.current.seasonStyle)
    const borderStyleChanged = borderStyle !== originalBorderSettingsRef.current.borderStyle
    const overlayImageChanged = overlayImage !== originalBorderSettingsRef.current.overlayImage
    const overlayRemoveExistingChanged = overlayRemoveExisting !== originalBorderSettingsRef.current.overlayRemoveExisting
    const gradientColorsChanged = JSON.stringify(gradientColors) !== JSON.stringify(originalBorderSettingsRef.current.gradientColors)
    const gradientDirectionChanged = gradientDirection !== originalBorderSettingsRef.current.gradientDirection
    const innerEffectChanged = innerEffect !== originalBorderSettingsRef.current.innerEffect
    const innerColorChanged = innerColor !== originalBorderSettingsRef.current.innerColor
    const innerOpacityChanged = innerOpacity !== originalBorderSettingsRef.current.innerOpacity
    const innerWidthChanged = innerWidth !== originalBorderSettingsRef.current.innerWidth
    const fadeWidthChanged = fadeWidth !== originalBorderSettingsRef.current.fadeWidth
    const plexRulesChanged = JSON.stringify(plexRules) !== JSON.stringify(originalBorderSettingsRef.current.plexRules)
    const ruleRunTypesChanged =
      JSON.stringify([...ruleRunTypes].sort()) !== JSON.stringify([...originalBorderSettingsRef.current.ruleRunTypes].sort())
    const ruleLibrariesChanged =
      JSON.stringify(Array.from(ruleLibraries).sort()) !== JSON.stringify([...originalBorderSettingsRef.current.ruleLibraries].sort())

    setHasUnsavedBorderChanges(colorsChanged || widthChanged || modeChanged || autoRunChanged || autoRunCleanupChanged || cleanupDeleteUnknownChanged || holidaysChanged || skipRunChanged || removeBordersChanged || seasonModeChanged || seasonColorsChanged || seasonWidthChanged || seasonStyleChanged || borderStyleChanged || overlayImageChanged || overlayRemoveExistingChanged || gradientColorsChanged || gradientDirectionChanged || innerEffectChanged || innerColorChanged || innerOpacityChanged || innerWidthChanged || fadeWidthChanged || plexRulesChanged || ruleRunTypesChanged || ruleLibrariesChanged)
  }, [
    autoRunBorder,
    autoRunCleanup,
    cleanupDeleteUnknown,
    borderColors,
    borderMode,
    borderWidth,
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
  ])

  useEffect(() => {
    if (!originalLibrarySelectionRef.current) return

    const currentSelection = Array.from(selectedLibraries).sort()
    const originalSelection = Array.from(originalLibrarySelectionRef.current).sort()
    const hasChanged = JSON.stringify(currentSelection) !== JSON.stringify(originalSelection)

    setHasUnsavedLibraryChanges(hasChanged)
  }, [originalLibrarySelectionRef, selectedLibraries, setHasUnsavedLibraryChanges])

  useEffect(() => {
    const navigationState = locationState as PosterManagerLocationState | null
    if (navigationState?.activeTab) {
      setActiveTab(navigationState.activeTab)
    }
  }, [locationState, setActiveTab])

  useEffect(() => {
    refreshStats()
    fetchConfig()
    fetchDrives()
    fetchFlowConfig()
    fetchIdarrSyncTargets()
    fetchBorderSettings()
    checkActiveWorkflow()
  }, [])

  useEffect(() => {
    if (activeTab === 'rename' || activeTab === 'unmatched') {
      fetchLibraryConfigs()
    }
  }, [activeTab])

  useEffect(() => {
    if (activeTab === 'border') {
      fetchBorderSettings()
    }
  }, [activeTab])

  useEffect(() => {
    if (drives.length > 0) {
      loadPriority()
    }
  }, [drives])
}
