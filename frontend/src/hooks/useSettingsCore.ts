import { useState } from 'react'
import {
  getDebugStatus,
  getSettings,
  saveBulkSettings,
  toggleDebug,
  uploadServiceAccountJson,
  getApiErrorMessage,
} from '../api/client'
import { MediaSettingsState, ServerInstance } from './useSettingsMedia'

type ToastType = 'success' | 'error' | 'info'

interface RcloneSettings {
  google_client_id: string
  google_client_secret: string
  google_token: string
  google_service_account_file: string
}

export interface SettingsCoreSnapshot {
  rclone: RcloneSettings
  media: MediaSettingsState
}

interface UseSettingsCoreParams {
  showToast: (message: string, type?: ToastType) => void
  setSaving: (value: boolean) => void
  setMediaSettings: (value: MediaSettingsState) => void
}

const defaultRcloneSettings: RcloneSettings = {
  google_client_id: '',
  google_client_secret: '',
  google_token: '',
  google_service_account_file: '',
}

const parseInstances = (
  value: string | undefined,
  fallbackName: string,
  seedBlankCard = true
): ServerInstance[] => {
  const fallback: ServerInstance[] = seedBlankCard
    ? [{ name: fallbackName, url: '', api_key: '' }]
    : []
  if (!value) return fallback

  try {
    const parsed = JSON.parse(value)
    // A saved empty array means the user removed every instance — don't reseed a blank card
    if (Array.isArray(parsed)) {
      return parsed
    }
  } catch (error) {
    console.error(`Error parsing ${fallbackName.toLowerCase()}_instances:`, error)
  }

  return fallback
}

export const useSettingsCore = ({
  showToast,
  setSaving,
  setMediaSettings,
}: UseSettingsCoreParams) => {
  const [debugEnabled, setDebugEnabled] = useState(false)
  const [loading, setLoading] = useState(true)
  const [rcloneSettings, setRcloneSettings] = useState<RcloneSettings>(defaultRcloneSettings)
  const [showSaveConfirm, setShowSaveConfirm] = useState(false)
  const [uploadingServiceAccount, setUploadingServiceAccount] = useState(false)
  const [tmdbApiKey, setTmdbApiKey] = useState('')
  const [tvdbApiKey, setTvdbApiKey] = useState('')
  const [tvdbPin, setTvdbPin] = useState('')
  const [psdExportFolder, setPsdExportFolder] = useState('')
  const [psdTemplatePath, setPsdTemplatePath] = useState('')
  const [psdOpenPhotopea, setPsdOpenPhotopea] = useState(false)

  const fetchSettings = async (): Promise<SettingsCoreSnapshot | null> => {
    try {
      const settings = await getSettings()
      const nextRcloneSettings = {
        google_client_id: settings.google_client_id || '',
        google_client_secret: settings.google_client_secret || '',
        google_token: settings.google_token || '',
        google_service_account_file: settings.google_service_account_file || '',
      }
      setRcloneSettings(nextRcloneSettings)

      const tmdbKey = (settings.tmdb_api_key || '').trim()
      // tmdb_api_key is sensitive so it comes back masked if set; treat masked as configured
      setTmdbApiKey(tmdbKey === '***masked***' ? '***masked***' : tmdbKey)
      // Also sensitive, so the same masked-means-configured treatment applies.
      setTvdbApiKey((settings.tvdb_api_key || '').trim())
      setTvdbPin((settings.tvdb_pin || '').trim())
      setPsdExportFolder((settings.psd_export_folder || '').trim())
      setPsdTemplatePath((settings.psd_template_path || '').trim())
      setPsdOpenPhotopea((settings.psd_open_photopea || '').trim().toLowerCase() === 'true')

      const plexInstances = parseInstances(settings.plex_instances, 'Plex')
      // Arrs are optional (media-server sourcing) — no blank starter card
      const sonarrInstances = parseInstances(settings.sonarr_instances, 'Sonarr', false)
      const radarrInstances = parseInstances(settings.radarr_instances, 'Radarr', false)

      const nextMediaSettings = {
        plex_instances: plexInstances,
        sonarr_instances: sonarrInstances,
        radarr_instances: radarrInstances,
        media_server_media_source: (settings.media_server_media_source || '').trim(),
      }

      setMediaSettings(nextMediaSettings)

      return {
        rclone: nextRcloneSettings,
        media: nextMediaSettings,
      }
    } catch (error) {
      console.error('Error fetching settings:', error)
      return null
    }
  }

  const fetchDebugStatus = async () => {
    try {
      const status = await getDebugStatus()
      setDebugEnabled(status.debug_enabled)
    } catch (error) {
      console.error('Error fetching debug status:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleDebugToggle = async () => {
    try {
      const newState = !debugEnabled
      await toggleDebug(newState)
      setDebugEnabled(newState)
      showToast(`Debug mode ${newState ? 'enabled' : 'disabled'}`)
    } catch (error) {
      showToast('Failed to toggle debug mode', 'error')
    }
  }

  const confirmSaveRclone = async (): Promise<boolean> => {
    setShowSaveConfirm(false)
    try {
      setSaving(true)
      const settingsToSave: Record<string, string> = {
        google_client_id: rcloneSettings.google_client_id,
        google_client_secret: rcloneSettings.google_client_secret,
        google_token: rcloneSettings.google_token,
        google_service_account_file: rcloneSettings.google_service_account_file,
      }
      await saveBulkSettings(settingsToSave)
      showToast('Rclone settings saved successfully!')
      return true
    } catch (error) {
      console.error('Error saving rclone settings:', error)
      showToast('Failed to save rclone settings', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleSaveRclone = async (): Promise<boolean> => {
    const hasServiceAccount = !!rcloneSettings.google_service_account_file.trim()
    const hasOAuth =
      !!rcloneSettings.google_client_id.trim() &&
      !!rcloneSettings.google_client_secret.trim() &&
      !!rcloneSettings.google_token.trim()

    if (!hasServiceAccount && !hasOAuth) {
      showToast('Provide either OAuth credentials or a Service Account JSON path', 'error')
      return false
    }

    const settingsExist = await getSettings()
    const hasExistingSettings =
      settingsExist.google_client_id ||
      settingsExist.google_client_secret ||
      settingsExist.google_token ||
      settingsExist.google_service_account_file

    if (hasExistingSettings) {
      setShowSaveConfirm(true)
      return false
    } else {
      return await confirmSaveRclone()
    }
  }

  const handleSaveTmdbApiKey = async (): Promise<boolean> => {
    const valueToSave = tmdbApiKey.trim()
    if (!valueToSave || valueToSave === '***masked***') {
      return false
    }
    try {
      setSaving(true)
      await saveBulkSettings({ tmdb_api_key: valueToSave })
      showToast('TMDB API key saved!')
      return true
    } catch (error) {
      console.error('Error saving TMDB API key:', error)
      showToast('Failed to save TMDB API key', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleSaveTvdbApiKey = async (): Promise<boolean> => {
    const valueToSave = tvdbApiKey.trim()
    if (!valueToSave || valueToSave === '***masked***') {
      return false
    }
    try {
      setSaving(true)
      const payload: Record<string, string> = { tvdb_api_key: valueToSave }
      // The PIN is only used by subscriber keys. A masked value means "leave the saved one
      // alone"; anything else (including blank, to clear it) is written through.
      const pin = tvdbPin.trim()
      if (pin !== '***masked***') payload.tvdb_pin = pin
      await saveBulkSettings(payload)
      showToast('TheTVDB API key saved!')
      return true
    } catch (error) {
      console.error('Error saving TheTVDB API key:', error)
      showToast('Failed to save TheTVDB API key', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleSavePsdExportFolder = async (): Promise<boolean> => {
    try {
      setSaving(true)
      await saveBulkSettings({ psd_export_folder: psdExportFolder.trim() })
      showToast('PSD export folder saved!')
      return true
    } catch (error) {
      console.error('Error saving PSD export folder:', error)
      showToast('Failed to save PSD export folder', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleSavePsdTemplatePath = async (): Promise<boolean> => {
    try {
      setSaving(true)
      await saveBulkSettings({ psd_template_path: psdTemplatePath.trim() })
      showToast('PSD template path saved!')
      return true
    } catch (error) {
      console.error('Error saving PSD template path:', error)
      showToast('Failed to save PSD template path', 'error')
      return false
    } finally {
      setSaving(false)
    }
  }

  const handleUploadServiceAccount = async (file: File) => {
    try {
      setUploadingServiceAccount(true)
      const result = await uploadServiceAccountJson(file)
      setRcloneSettings((prev) => ({
        ...prev,
        google_service_account_file: result.path,
      }))
      showToast('Service account JSON uploaded successfully!')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to upload service account JSON'), 'error')
    } finally {
      setUploadingServiceAccount(false)
    }
  }

  const handleTogglePsdOpenPhotopea = async (value: boolean): Promise<void> => {
    try {
      setSaving(true)
      await saveBulkSettings({ psd_open_photopea: value ? 'true' : 'false' })
      setPsdOpenPhotopea(value)
      showToast(value ? 'Photopea auto-open enabled' : 'Photopea auto-open disabled')
    } catch (error) {
      console.error('Error saving Photopea setting:', error)
      showToast('Failed to save setting', 'error')
    } finally {
      setSaving(false)
    }
  }

  return {
    debugEnabled,
    loading,
    rcloneSettings,
    setRcloneSettings,
    showSaveConfirm,
    setShowSaveConfirm,
    fetchSettings,
    fetchDebugStatus,
    handleDebugToggle,
    handleSaveRclone,
    confirmSaveRclone,
    handleUploadServiceAccount,
    uploadingServiceAccount,
    tmdbApiKey,
    setTmdbApiKey,
    handleSaveTmdbApiKey,
    tvdbApiKey,
    setTvdbApiKey,
    tvdbPin,
    setTvdbPin,
    handleSaveTvdbApiKey,
    psdExportFolder,
    setPsdExportFolder,
    handleSavePsdExportFolder,
    psdTemplatePath,
    setPsdTemplatePath,
    handleSavePsdTemplatePath,
    psdOpenPhotopea,
    handleTogglePsdOpenPhotopea,
  }
}
