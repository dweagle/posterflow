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
  fallbackName: string
): ServerInstance[] => {
  const fallback: ServerInstance[] = [{ name: fallbackName, url: '', api_key: '' }]
  if (!value) return fallback

  try {
    const parsed = JSON.parse(value)
    if (Array.isArray(parsed) && parsed.length > 0) {
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

      const plexInstances = parseInstances(settings.plex_instances, 'Plex')
      const sonarrInstances = parseInstances(settings.sonarr_instances, 'Sonarr')
      const radarrInstances = parseInstances(settings.radarr_instances, 'Radarr')

      const nextMediaSettings = {
        plex_instances: plexInstances,
        sonarr_instances: sonarrInstances,
        radarr_instances: radarrInstances,
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
  }
}
