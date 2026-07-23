import { useState } from 'react'
import {
  getApiErrorMessage,
  getPlexLibraries,
  getPlexLibraryConfigs,
  PlexLibrary,
  PlexLibraryConfig,
  revealSensitiveSetting,
  saveBulkSettings,
  savePlexLibraryConfig,
  testPlex,
  testRadarr,
  testSonarr,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

type DeleteType = 'sonarr' | 'radarr' | 'plex' | null

export interface ServerInstance {
  name: string
  url: string
  api_key: string
}

export interface MediaSettingsState {
  plex_instances: ServerInstance[]
  sonarr_instances: ServerInstance[]
  radarr_instances: ServerInstance[]
}

export interface DeleteConfirmState {
  show: boolean
  type: DeleteType
  index: number | null
  name: string
}

interface UseSettingsMediaParams {
  showToast: (message: string, type?: ToastType) => void
  setSaving: (value: boolean) => void
  onRevealApiKey?: (settingsKey: 'plex_instances' | 'sonarr_instances' | 'radarr_instances', index: number, value: string) => void
}

const adjustIndexSet = (source: Set<number>, removedIndex: number): Set<number> => {
  const next = new Set(source)
  next.delete(removedIndex)
  const adjusted = new Set<number>()
  next.forEach(index => {
    if (index > removedIndex) adjusted.add(index - 1)
    else adjusted.add(index)
  })
  return adjusted
}

export const useSettingsMedia = ({ showToast, setSaving, onRevealApiKey }: UseSettingsMediaParams) => {
  const MASKED_VALUE = '***masked***'

  const [mediaSettings, setMediaSettings] = useState<MediaSettingsState>({
    plex_instances: [{ name: 'Plex', url: '', api_key: '' }],
    sonarr_instances: [{ name: 'Sonarr', url: '', api_key: '' }],
    radarr_instances: [{ name: 'Radarr', url: '', api_key: '' }],
  })

  const [deleteConfirm, setDeleteConfirm] = useState<DeleteConfirmState>({
    show: false,
    type: null,
    index: null,
    name: '',
  })

  const [editingSonarr, setEditingSonarr] = useState<Set<number>>(new Set())
  const [editingRadarr, setEditingRadarr] = useState<Set<number>>(new Set())
  const [editingPlex, setEditingPlex] = useState<Set<number>>(new Set())

  const [testingPlex, setTestingPlex] = useState<Set<number>>(new Set())
  const [testingSonarr, setTestingSonarr] = useState<Set<number>>(new Set())
  const [testingRadarr, setTestingRadarr] = useState<Set<number>>(new Set())

  const [showPlexTokens, setShowPlexTokens] = useState<Record<number, boolean>>({})
  const [showSonarrKeys, setShowSonarrKeys] = useState<Record<number, boolean>>({})
  const [showRadarrKeys, setShowRadarrKeys] = useState<Record<number, boolean>>({})

  const [showLibraryModal, setShowLibraryModal] = useState(false)
  const [libraryModalInstance, setLibraryModalInstance] = useState<{ name: string; url: string; token: string } | null>(null)
  const [loadingLibraries, setLoadingLibraries] = useState(false)
  const [libraries, setLibraries] = useState<PlexLibrary[]>([])
  const [libraryConfigs, setLibraryConfigs] = useState<PlexLibraryConfig[]>([])

  const fetchLibraryConfigs = async () => {
    try {
      const data = await getPlexLibraryConfigs()
      setLibraryConfigs(data.configs)
    } catch (error) {
      console.error('Error fetching library configs:', error)
    }
  }

  const updateSonarrInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setMediaSettings(prev => {
      const updated = [...prev.sonarr_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, sonarr_instances: updated }
    })
  }

  const updateRadarrInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setMediaSettings(prev => {
      const updated = [...prev.radarr_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, radarr_instances: updated }
    })
  }

  const updatePlexInstance = (index: number, field: keyof ServerInstance, value: string) => {
    setMediaSettings(prev => {
      const updated = [...prev.plex_instances]
      updated[index] = { ...updated[index], [field]: value }
      return { ...prev, plex_instances: updated }
    })
  }

  const toggleEditPlex = (index: number) => {
    setEditingPlex(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const testPlexConnection = async (index: number) => {
    const instance = mediaSettings.plex_instances[index]
    if (!instance.url || !instance.api_key) {
      showToast('Please enter both URL and Token', 'error')
      return
    }

    try {
      setTestingPlex(prev => new Set(prev).add(index))
      const result = await testPlex(instance.url, instance.api_key)
      if (result.success) {
        showToast(result.message || `${instance.name} connection successful!`)
      } else {
        showToast(result.message || `${instance.name} connection failed`, 'error')
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, `${instance.name} connection failed: Unable to reach server`), 'error')
    } finally {
      setTestingPlex(prev => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    }
  }

  const openLibraryModal = async (index: number) => {
    const instance = mediaSettings.plex_instances[index]
    if (!instance.url || !instance.api_key) {
      showToast('Please configure and test the Plex connection first', 'error')
      return
    }

    setLibraryModalInstance({
      name: instance.name,
      url: instance.url,
      token: instance.api_key,
    })
    setLoadingLibraries(true)
    setShowLibraryModal(true)

    try {
      const data = await getPlexLibraries(instance.url, instance.api_key)
      const existingConfig = libraryConfigs.find(c => c.instance_name === instance.name)

      // Safety guard
      if (data.libraries.length === 0 && existingConfig && existingConfig.libraries.length > 0) {
        showToast('Plex returned no libraries — check the connection before updating. Your saved libraries were left unchanged.', 'error')
        setShowLibraryModal(false)
        return
      }

      if (existingConfig) {
        const mergedLibraries = data.libraries.map(lib => {
          const existingLib = existingConfig.libraries.find(el => el.key === lib.key)
          // Newly-detected libraries (not in saved config) default to unchecked so the user
          // can see they aren't saved as available yet until they enable them and Save.
          return existingLib ? existingLib : { ...lib, enabled: false }
        })
        setLibraries(mergedLibraries)
      } else {
        const allEnabled = data.libraries.map(lib => ({ ...lib, enabled: true }))
        setLibraries(allEnabled)
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to fetch libraries'), 'error')
      setShowLibraryModal(false)
    } finally {
      setLoadingLibraries(false)
    }
  }

  const toggleLibrary = (libraryKey: string) => {
    setLibraries(prev => prev.map(lib => (lib.key === libraryKey ? { ...lib, enabled: !lib.enabled } : lib)))
  }

  const saveLibraryConfig = async () => {
    if (!libraryModalInstance) return

    try {
      const config: PlexLibraryConfig = {
        instance_name: libraryModalInstance.name,
        libraries,
      }

      await savePlexLibraryConfig(config)
      await fetchLibraryConfigs()
      setShowLibraryModal(false)
      showToast('Library configuration saved successfully')
    } catch (error) {
      console.error('Error saving library config:', error)
      showToast('Failed to save library configuration', 'error')
    }
  }

  const revealMaskedApiKey = async (
    settingsKey: 'plex_instances' | 'sonarr_instances' | 'radarr_instances',
    index: number,
  ): Promise<string | null> => {
    const instances = mediaSettings[settingsKey]
    const instance = instances[index]
    if (!instance) {
      return null
    }

    if (instance.api_key !== MASKED_VALUE) {
      return instance.api_key
    }

    try {
      const response = await revealSensitiveSetting({
        setting_key: settingsKey,
        field: 'api_key',
        instance_name: instance.name,
        index,
      })

      const revealedValue = String(response.value || '')
      if (!revealedValue) {
        showToast('No saved key available to reveal for this instance', 'error')
        return null
      }

      setMediaSettings(prev => {
        const updated = [...prev[settingsKey]]
        updated[index] = { ...updated[index], api_key: revealedValue }
        return { ...prev, [settingsKey]: updated }
      })
      onRevealApiKey?.(settingsKey, index, revealedValue)
      return revealedValue
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to reveal saved credential'), 'error')
      return null
    }
  }

  const togglePlexTokenVisibility = async (index: number) => {
    const willShow = !showPlexTokens[index]
    if (willShow) {
      const revealedValue = await revealMaskedApiKey('plex_instances', index)
      if (!revealedValue) {
        return
      }
    }
    setShowPlexTokens(prev => ({ ...prev, [index]: !prev[index] }))
  }

  const toggleSonarrKeyVisibility = async (index: number) => {
    const willShow = !showSonarrKeys[index]
    if (willShow) {
      const revealedValue = await revealMaskedApiKey('sonarr_instances', index)
      if (!revealedValue) {
        return
      }
    }
    setShowSonarrKeys(prev => ({ ...prev, [index]: !prev[index] }))
  }

  const toggleRadarrKeyVisibility = async (index: number) => {
    const willShow = !showRadarrKeys[index]
    if (willShow) {
      const revealedValue = await revealMaskedApiKey('radarr_instances', index)
      if (!revealedValue) {
        return
      }
    }
    setShowRadarrKeys(prev => ({ ...prev, [index]: !prev[index] }))
  }

  const addPlexInstance = () => {
    const newIndex = mediaSettings.plex_instances.length
    setMediaSettings(prev => ({
      ...prev,
      plex_instances: [...prev.plex_instances, { name: `Plex ${prev.plex_instances.length + 1}`, url: '', api_key: '' }],
    }))
    setEditingPlex(prev => new Set(prev).add(newIndex))
  }

  const removePlexInstance = (index: number) => {
    if (mediaSettings.plex_instances.length > 1) {
      setMediaSettings(prev => ({
        ...prev,
        plex_instances: prev.plex_instances.filter((_, i) => i !== index),
      }))
      setEditingPlex(prev => adjustIndexSet(prev, index))
    }
    setDeleteConfirm({ show: false, type: null, index: null, name: '' })
  }

  const confirmRemovePlexInstance = (index: number) => {
    const instance = mediaSettings.plex_instances[index]
    if (instance.url || instance.api_key) {
      setDeleteConfirm({
        show: true,
        type: 'plex',
        index,
        name: instance.name || `Plex ${index + 1}`,
      })
    } else {
      removePlexInstance(index)
    }
  }

  const testSonarrConnection = async (index: number) => {
    const instance = mediaSettings.sonarr_instances[index]
    if (!instance.url) {
      showToast('Please enter both URL and API Key', 'error')
      return
    }

    try {
      const apiKey = await revealMaskedApiKey('sonarr_instances', index)
      if (!apiKey) {
        return
      }
      setTestingSonarr(prev => new Set(prev).add(index))
      const result = await testSonarr(instance.url, apiKey)
      if (result.success) {
        showToast(result.message || `${instance.name} connection successful!`)
      } else {
        showToast(result.message || `${instance.name} connection failed`, 'error')
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, `${instance.name} connection failed: Unable to reach server`), 'error')
    } finally {
      setTestingSonarr(prev => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    }
  }

  const addSonarrInstance = () => {
    const newIndex = mediaSettings.sonarr_instances.length
    setMediaSettings(prev => ({
      ...prev,
      sonarr_instances: [...prev.sonarr_instances, { name: `Sonarr ${prev.sonarr_instances.length + 1}`, url: '', api_key: '' }],
    }))
    setEditingSonarr(prev => new Set(prev).add(newIndex))
  }

  const toggleEditSonarr = (index: number) => {
    setEditingSonarr(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const removeSonarrInstance = (index: number) => {
    if (mediaSettings.sonarr_instances.length > 1) {
      setMediaSettings(prev => ({
        ...prev,
        sonarr_instances: prev.sonarr_instances.filter((_, i) => i !== index),
      }))
      setEditingSonarr(prev => adjustIndexSet(prev, index))
    }
    setDeleteConfirm({ show: false, type: null, index: null, name: '' })
  }

  const confirmRemoveSonarrInstance = (index: number) => {
    const instance = mediaSettings.sonarr_instances[index]
    if (instance.url || instance.api_key) {
      setDeleteConfirm({
        show: true,
        type: 'sonarr',
        index,
        name: instance.name || `Sonarr ${index + 1}`,
      })
    } else {
      removeSonarrInstance(index)
    }
  }

  const testRadarrConnection = async (index: number) => {
    const instance = mediaSettings.radarr_instances[index]
    if (!instance.url) {
      showToast('Please enter both URL and API Key', 'error')
      return
    }

    try {
      const apiKey = await revealMaskedApiKey('radarr_instances', index)
      if (!apiKey) {
        return
      }
      setTestingRadarr(prev => new Set(prev).add(index))
      const result = await testRadarr(instance.url, apiKey)
      if (result.success) {
        showToast(result.message || `${instance.name} connection successful!`)
      } else {
        showToast(result.message || `${instance.name} connection failed`, 'error')
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, `${instance.name} connection failed: Unable to reach server`), 'error')
    } finally {
      setTestingRadarr(prev => {
        const next = new Set(prev)
        next.delete(index)
        return next
      })
    }
  }

  const addRadarrInstance = () => {
    const newIndex = mediaSettings.radarr_instances.length
    setMediaSettings(prev => ({
      ...prev,
      radarr_instances: [...prev.radarr_instances, { name: `Radarr ${prev.radarr_instances.length + 1}`, url: '', api_key: '' }],
    }))
    setEditingRadarr(prev => new Set(prev).add(newIndex))
  }

  const toggleEditRadarr = (index: number) => {
    setEditingRadarr(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  const removeRadarrInstance = (index: number) => {
    if (mediaSettings.radarr_instances.length > 1) {
      setMediaSettings(prev => ({
        ...prev,
        radarr_instances: prev.radarr_instances.filter((_, i) => i !== index),
      }))
      setEditingRadarr(prev => adjustIndexSet(prev, index))
    }
    setDeleteConfirm({ show: false, type: null, index: null, name: '' })
  }

  const confirmRemoveRadarrInstance = (index: number) => {
    const instance = mediaSettings.radarr_instances[index]
    if (instance.url || instance.api_key) {
      setDeleteConfirm({
        show: true,
        type: 'radarr',
        index,
        name: instance.name || `Radarr ${index + 1}`,
      })
    } else {
      removeRadarrInstance(index)
    }
  }

  const handleConfirmDelete = () => {
    if (deleteConfirm.type === 'plex' && deleteConfirm.index !== null) {
      removePlexInstance(deleteConfirm.index)
    } else if (deleteConfirm.type === 'sonarr' && deleteConfirm.index !== null) {
      removeSonarrInstance(deleteConfirm.index)
    } else if (deleteConfirm.type === 'radarr' && deleteConfirm.index !== null) {
      removeRadarrInstance(deleteConfirm.index)
    }
  }

  const handleSaveMediaSettings = async (): Promise<boolean> => {
    try {
      setSaving(true)
      const settingsToSave = {
        plex_instances: JSON.stringify(mediaSettings.plex_instances.filter(p => p.url)),
        sonarr_instances: JSON.stringify(mediaSettings.sonarr_instances.filter(s => s.url)),
        radarr_instances: JSON.stringify(mediaSettings.radarr_instances.filter(r => r.url)),
      }
      await saveBulkSettings(settingsToSave)

      // The bulk save migrates/prunes library configs on a server rename, so the
      // page-load cache is stale here; re-fetch before deciding what to auto-configure
      // or a renamed instance would get its migrated selection reset to all-enabled.
      let freshConfigs: PlexLibraryConfig[] | null = null
      try {
        freshConfigs = (await getPlexLibraryConfigs()).configs
      } catch (err) {
        console.error('[AUTO-CONFIG] Skipping library auto-configuration; could not load current configs:', err)
      }

      const autoConfigInstances = freshConfigs === null
        ? []
        : mediaSettings.plex_instances.filter(p => p.url && p.api_key)
      for (const instance of autoConfigInstances) {
        const existingConfig = freshConfigs?.find(c => c.instance_name === instance.name)

        if (!existingConfig) {
          try {
            const data = await getPlexLibraries(instance.url, instance.api_key)
            const allEnabled = data.libraries.map(lib => ({ ...lib, enabled: true }))
            await savePlexLibraryConfig({
              instance_name: instance.name,
              libraries: allEnabled,
            })
          } catch (err) {
            console.error(`[AUTO-CONFIG] Failed to auto-configure libraries for ${instance.name}:`, err)
          }
        }
      }

      await fetchLibraryConfigs()

      setEditingPlex(new Set())
      setEditingSonarr(new Set())
      setEditingRadarr(new Set())
      showToast('Media server settings saved successfully!')
      return true
    } catch (error) {
      console.error('Error saving media settings:', error)
      showToast('Failed to save media settings', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  return {
    mediaSettings,
    setMediaSettings,
    deleteConfirm,
    setDeleteConfirm,
    editingSonarr,
    editingRadarr,
    editingPlex,
    setEditingSonarr,
    setEditingRadarr,
    setEditingPlex,
    testingPlex,
    testingSonarr,
    testingRadarr,
    showPlexTokens,
    showSonarrKeys,
    showRadarrKeys,
    showLibraryModal,
    setShowLibraryModal,
    libraryModalInstance,
    loadingLibraries,
    libraries,
    libraryConfigs,
    fetchLibraryConfigs,
    updateSonarrInstance,
    updateRadarrInstance,
    updatePlexInstance,
    toggleEditPlex,
    testPlexConnection,
    openLibraryModal,
    toggleLibrary,
    togglePlexTokenVisibility,
    toggleSonarrKeyVisibility,
    toggleRadarrKeyVisibility,
    saveLibraryConfig,
    addPlexInstance,
    confirmRemovePlexInstance,
    testSonarrConnection,
    testRadarrConnection,
    addSonarrInstance,
    toggleEditSonarr,
    addRadarrInstance,
    toggleEditRadarr,
    confirmRemoveSonarrInstance,
    confirmRemoveRadarrInstance,
    handleConfirmDelete,
    handleSaveMediaSettings,
  }
}
