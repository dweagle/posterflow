import { MutableRefObject, useEffect, useState } from 'react'
import { getPlexLibraryConfigs, getSettings, PlexLibraryConfig, saveBulkSettings } from '../api/client'
import { PosterManagerTab } from '../components/poster-manager/PosterManagerTabs'

type ToastType = 'success' | 'error' | 'info'

interface OriginalBorderSettings {
  colors: string[]
  width: number
  mode: 'incremental' | 'full'
  autoRunBorder: boolean
  holidaySchedules: Array<{ name: string; schedule: string; colors: string[] }>
  skipRunOutsideHoliday: boolean
  removeBorders: boolean
}

interface UsePosterManagerLibrariesParams {
  activeTab: PosterManagerTab
  autoRunBorder: boolean
  originalLibrarySelectionRef: MutableRefObject<Set<string> | null>
  originalBorderSettingsRef: MutableRefObject<OriginalBorderSettings | null>
  setHasUnsavedLibraryChanges: (value: boolean) => void
  setHasUnsavedBorderChanges: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
}

export const usePosterManagerLibraries = ({
  activeTab,
  autoRunBorder,
  originalLibrarySelectionRef,
  originalBorderSettingsRef,
  setHasUnsavedLibraryChanges,
  setHasUnsavedBorderChanges,
  showToast,
}: UsePosterManagerLibrariesParams) => {
  const [libraryConfigs, setLibraryConfigs] = useState<PlexLibraryConfig[]>([])
  const [selectedLibraries, setSelectedLibraries] = useState<Set<string>>(new Set())
  const [unmatchedIgnoreRootFoldersText, setUnmatchedIgnoreRootFoldersText] = useState('')
  const [unmatchedIgnoreCollectionsText, setUnmatchedIgnoreCollectionsText] = useState('')
  const [unmatchedIgnoreUnmonitored, setUnmatchedIgnoreUnmonitored] = useState(false)
  const [originalUnmatchedIgnoreRootFolders, setOriginalUnmatchedIgnoreRootFolders] = useState<string[]>([])
  const [originalUnmatchedIgnoreCollections, setOriginalUnmatchedIgnoreCollections] = useState<string[]>([])
  const [originalUnmatchedIgnoreUnmonitored, setOriginalUnmatchedIgnoreUnmonitored] = useState(false)
  const [hasUnsavedUnmatchedSettings, setHasUnsavedUnmatchedSettings] = useState(false)

  const parseStringArraySetting = (raw: string | undefined): string[] => {
    if (!raw) return []
    try {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        return parsed.map(item => String(item).trim()).filter(Boolean)
      }
    } catch {
      // fallback to split values
    }
    return raw
      .split(/[\n,]+/)
      .map(item => item.trim())
      .filter(Boolean)
  }

  const parseBooleanSetting = (raw: string | undefined): boolean => {
    if (!raw) return false
    return ['true', '1', 'yes', 'on'].includes(raw.trim().toLowerCase())
  }

  const parseListInput = (raw: string): string[] => {
    return raw
      .split(/[\n,]+/)
      .map(item => item.trim())
      .filter(Boolean)
  }

  useEffect(() => {
    const currentRootFolders = parseListInput(unmatchedIgnoreRootFoldersText)
    const currentCollections = parseListInput(unmatchedIgnoreCollectionsText)

    const hasRootChanges =
      JSON.stringify(currentRootFolders) !== JSON.stringify(originalUnmatchedIgnoreRootFolders)
    const hasCollectionChanges =
      JSON.stringify(currentCollections) !== JSON.stringify(originalUnmatchedIgnoreCollections)
    const hasUnmonitoredChange =
      unmatchedIgnoreUnmonitored !== originalUnmatchedIgnoreUnmonitored

    setHasUnsavedUnmatchedSettings(
      hasRootChanges || hasCollectionChanges || hasUnmonitoredChange
    )
  }, [
    unmatchedIgnoreRootFoldersText,
    unmatchedIgnoreCollectionsText,
    unmatchedIgnoreUnmonitored,
    originalUnmatchedIgnoreRootFolders,
    originalUnmatchedIgnoreCollections,
    originalUnmatchedIgnoreUnmonitored,
  ])

  const fetchLibraryConfigs = async () => {
    try {
      const data = await getPlexLibraryConfigs()
      setLibraryConfigs(data.configs)

      const settingKey = activeTab === 'unmatched' ? 'unmatched_assets_libraries' : 'poster_renamer_libraries'

      const settings = await getSettings()
      let finalSelection: Set<string> = new Set()

      const rawSelection = settings[settingKey]

      if (rawSelection) {
        try {
          const saved = JSON.parse(rawSelection)
          finalSelection = new Set(saved)
          setSelectedLibraries(finalSelection)
        } catch (error) {
          console.error(`Error parsing ${settingKey}:`, error)
        }
      } else if (data.configs.length > 0) {
        const allEnabledLibraries: string[] = []
        data.configs.forEach(config => {
          config.libraries.filter(lib => lib.enabled).forEach(lib => {
            allEnabledLibraries.push(`${config.instance_name}:${lib.key}`)
          })
        })

        if (allEnabledLibraries.length > 0) {
          finalSelection = new Set(allEnabledLibraries)
          setSelectedLibraries(finalSelection)
          try {
            await saveBulkSettings({
              [settingKey]: JSON.stringify(allEnabledLibraries),
            })
          } catch (error) {
            console.error(`Error auto-saving ${settingKey}:`, error)
          }
        }
      }

      const rootFolders = parseStringArraySetting(settings.unmatched_ignore_root_folders)
      const collections = parseStringArraySetting(settings.unmatched_ignore_collections)
      const ignoreUnmonitored = parseBooleanSetting(settings.unmatched_ignore_unmonitored)

      setUnmatchedIgnoreRootFoldersText(rootFolders.join(', '))
      setUnmatchedIgnoreCollectionsText(collections.join(', '))
      setUnmatchedIgnoreUnmonitored(ignoreUnmonitored)

      setOriginalUnmatchedIgnoreRootFolders(rootFolders)
      setOriginalUnmatchedIgnoreCollections(collections)
      setOriginalUnmatchedIgnoreUnmonitored(ignoreUnmonitored)

      originalLibrarySelectionRef.current = new Set(finalSelection)
    } catch (error) {
      console.error('Error fetching library configs:', error)
    }
  }

  const toggleLibrarySelection = (instanceName: string, libraryKey: string) => {
    const fullKey = `${instanceName}:${libraryKey}`
    setSelectedLibraries(prev => {
      const next = new Set(prev)
      if (next.has(fullKey)) {
        next.delete(fullKey)
      } else {
        next.add(fullKey)
      }
      return next
    })
  }

  const saveRenameSettings = async () => {
    try {
      const settingKey = activeTab === 'unmatched' ? 'unmatched_assets_libraries' : 'poster_renamer_libraries'
      const libraryArray = Array.from(selectedLibraries)

      const settingsToSave: Record<string, string> = {
        [settingKey]: JSON.stringify(libraryArray),
      }

      if (activeTab === 'unmatched') {
        const parsedRootFolders = parseListInput(unmatchedIgnoreRootFoldersText)
        const parsedCollections = parseListInput(unmatchedIgnoreCollectionsText)

        settingsToSave['unmatched_ignore_root_folders'] = JSON.stringify(parsedRootFolders)
        settingsToSave['unmatched_ignore_collections'] = JSON.stringify(parsedCollections)
        settingsToSave['unmatched_ignore_unmonitored'] = unmatchedIgnoreUnmonitored ? 'true' : 'false'
      }

      if (activeTab === 'rename') {
        settingsToSave['auto_run_border'] = autoRunBorder ? 'true' : 'false'
      }

      await saveBulkSettings(settingsToSave)

      originalLibrarySelectionRef.current = new Set(selectedLibraries)
      setHasUnsavedLibraryChanges(false)

      if (activeTab === 'unmatched') {
        setOriginalUnmatchedIgnoreRootFolders(parseListInput(unmatchedIgnoreRootFoldersText))
        setOriginalUnmatchedIgnoreCollections(parseListInput(unmatchedIgnoreCollectionsText))
        setOriginalUnmatchedIgnoreUnmonitored(unmatchedIgnoreUnmonitored)
      }

      if (activeTab === 'rename') {
        if (originalBorderSettingsRef.current) {
          originalBorderSettingsRef.current.autoRunBorder = autoRunBorder
        }
        setHasUnsavedBorderChanges(false)
      }

      const featureName = activeTab === 'unmatched' ? 'Unmatched assets' : 'Poster Renamer'
      showToast(`${featureName} settings saved successfully`)
    } catch (error) {
      console.error('Error saving library settings:', error)
      showToast('Failed to save settings', 'error')
    }
  }

  const resetLibrarySettingsToOriginal = () => {
    if (originalLibrarySelectionRef.current) {
      setSelectedLibraries(new Set(originalLibrarySelectionRef.current))
    }

    setUnmatchedIgnoreRootFoldersText(originalUnmatchedIgnoreRootFolders.join(', '))
    setUnmatchedIgnoreCollectionsText(originalUnmatchedIgnoreCollections.join(', '))
    setUnmatchedIgnoreUnmonitored(originalUnmatchedIgnoreUnmonitored)
    setHasUnsavedLibraryChanges(false)
  }

  return {
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
  }
}
