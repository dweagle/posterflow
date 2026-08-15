import { useCallback } from 'react'
import { DEFAULT_POSTER_DESTINATION, Drive, PosterConfig, getDrives, getPosterConfig, savePosterConfig } from '../api/client'

interface UsePosterManagerSettingsOptions {
  config: PosterConfig | null
  setConfig: React.Dispatch<React.SetStateAction<PosterConfig | null>>
  setDrives: React.Dispatch<React.SetStateAction<Drive[]>>
  originalConfigRef: React.MutableRefObject<PosterConfig | null>
  setHasUnsavedChanges: React.Dispatch<React.SetStateAction<boolean>>
  setSaving: React.Dispatch<React.SetStateAction<boolean>>
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export function usePosterManagerSettings({
  config,
  setConfig,
  setDrives,
  originalConfigRef,
  setHasUnsavedChanges,
  setSaving,
  showToast,
}: UsePosterManagerSettingsOptions) {
  const fetchConfig = useCallback(async () => {
    try {
      const data = await getPosterConfig()
      setConfig(data)
      originalConfigRef.current = JSON.parse(JSON.stringify(data))
    } catch (error) {
      console.error('Error fetching poster config:', error)
      showToast('Failed to load poster configuration', 'error')
    }
  }, [originalConfigRef, setConfig, showToast])

  const fetchDrives = useCallback(async () => {
    try {
      const data = await getDrives()
      setDrives(data.filter((d: Drive) => d.subscribed))
    } catch (error) {
      console.error('Error fetching drives:', error)
    }
  }, [setDrives])

  const handleConfigChange = useCallback((updatedConfig: PosterConfig) => {
    setConfig(updatedConfig)
  }, [setConfig])

  const handleSaveConfig = useCallback(async () => {
    if (!config) return

    // Resolve a blank destination to the default, as the setup wizard does. The API
    // ignores an empty destination rather than storing it, so without this a cleared
    // field silently keeps the old path.
    const resolved: PosterConfig = {
      ...config,
      destination: config.destination.trim() || config.default_destination || DEFAULT_POSTER_DESTINATION,
    }

    try {
      setSaving(true)
      await savePosterConfig(resolved)
      setConfig(resolved)
      originalConfigRef.current = JSON.parse(JSON.stringify(resolved))
      setHasUnsavedChanges(false)
      showToast('Settings saved')
    } catch (error) {
      console.error('Error saving config:', error)
      showToast('Failed to save settings', 'error')
    } finally {
      setSaving(false)
    }
  }, [config, originalConfigRef, setConfig, setHasUnsavedChanges, setSaving, showToast])

  const resetConfigToOriginal = useCallback(() => {
    if (!originalConfigRef.current) return

    setConfig(JSON.parse(JSON.stringify(originalConfigRef.current)))
    setHasUnsavedChanges(false)
  }, [originalConfigRef, setConfig, setHasUnsavedChanges])

  return {
    fetchConfig,
    fetchDrives,
    handleConfigChange,
    handleSaveConfig,
    resetConfigToOriginal,
  }
}
