import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { 
  getSchedules, 
  Schedule, 
  getDrives, 
  Drive, 
  getDiscordNotificationConfig,
  saveDiscordNotificationConfig,
  testDiscordNotification,
  DiscordNotificationConfig,
  DiscordNotificationFeatureConfig,
  getApiErrorMessage,
  revealSensitiveSetting,
} from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Eye, EyeOff, Settings as SettingsIcon } from 'lucide-react'
import SettingsTabs from '../components/settings/SettingsTabs'
import BackupRestoreSection from '../components/settings/BackupRestoreSection'
import MaintenanceSection from '../components/settings/MaintenanceSection'
import RestartRequiredModal from '../components/settings/RestartRequiredModal'
import SettingsRcloneSection from '../components/settings/SettingsRcloneSection'
import SettingsSchedulingSection from '../components/settings/SettingsSchedulingSection'
import ScheduleEditModal from '../components/settings/ScheduleEditModal'
import SettingsMediaSection from '../components/settings/SettingsMediaSection'
import PlexLibraryModal from '../components/settings/PlexLibraryModal'
import UnsavedChangesModal from '../components/poster-manager/UnsavedChangesModal'
import SettingsSecuritySection from '../components/settings/SettingsSecuritySection'
import SettingsScriptsSection from '../components/settings/SettingsScriptsSection'
import { useSettingsSchedules } from '../hooks/useSettingsSchedules'
import { useSettingsMedia } from '../hooks/useSettingsMedia'
import { useSettingsOperations } from '../hooks/useSettingsOperations'
import { useSettingsCore } from '../hooks/useSettingsCore'
import './Settings.css'

type SettingsTab = 'basic' | 'notifications' | 'rclone' | 'media' | 'scheduling' | 'backup' | 'maintenance' | 'security' | 'scripts'
const SETTINGS_TAB_STORAGE_KEY = 'posterflow.settings.activeTab'

const isSettingsTab = (value: string): value is SettingsTab => {
  return ['basic', 'notifications', 'rclone', 'media', 'scheduling', 'backup', 'maintenance', 'security', 'scripts'].includes(value)
}

type RcloneSettingsSnapshot = {
  google_client_id: string
  google_client_secret: string
  google_token: string
  google_service_account_file: string
}

type MediaInstanceSnapshot = {
  name: string
  url: string
  api_key: string
}

type MediaSettingsSnapshot = {
  plex_instances: MediaInstanceSnapshot[]
  sonarr_instances: MediaInstanceSnapshot[]
  radarr_instances: MediaInstanceSnapshot[]
}

type MediaGroupKey = keyof MediaSettingsSnapshot

const normalizeRcloneSettings = (settings: RcloneSettingsSnapshot): RcloneSettingsSnapshot => ({
  google_client_id: settings.google_client_id || '',
  google_client_secret: settings.google_client_secret || '',
  google_token: settings.google_token || '',
  google_service_account_file: settings.google_service_account_file || '',
})

const normalizeMediaInstances = (instances: MediaInstanceSnapshot[]): MediaInstanceSnapshot[] => {
  return (instances || []).map((instance) => ({
    name: instance.name || '',
    url: instance.url || '',
    api_key: instance.api_key || '',
  }))
}

const normalizeMediaSettings = (settings: MediaSettingsSnapshot): MediaSettingsSnapshot => ({
  plex_instances: normalizeMediaInstances(settings.plex_instances),
  sonarr_instances: normalizeMediaInstances(settings.sonarr_instances),
  radarr_instances: normalizeMediaInstances(settings.radarr_instances),
})

const mediaInstanceChanged = (current: MediaInstanceSnapshot | undefined, baseline: MediaInstanceSnapshot | undefined): boolean => {
  if (!baseline) {
    return !!current
  }

  if (!current) {
    return true
  }

  return current.name !== baseline.name
    || current.url !== baseline.url
    || current.api_key !== baseline.api_key
}

const calculateDirtyMediaIndices = (
  currentSettings: MediaSettingsSnapshot,
  baselineSettings: MediaSettingsSnapshot,
) => {
  const buildDirtySet = (key: MediaGroupKey): Set<number> => {
    const current = currentSettings[key] || []
    const baseline = baselineSettings[key] || []
    const dirty = new Set<number>()

    current.forEach((instance, index) => {
      if (mediaInstanceChanged(instance, baseline[index])) {
        dirty.add(index)
      }
    })

    return dirty
  }

  return {
    plex_instances: buildDirtySet('plex_instances'),
    sonarr_instances: buildDirtySet('sonarr_instances'),
    radarr_instances: buildDirtySet('radarr_instances'),
  }
}

const DISCORD_NOTIFICATION_FEATURE_ORDER = [
  'workflow',
  'sync',
  'poster_renamer',
  'unmatched_assets',
  'plex_upload',
  'idarr',
  'maker_monitor',
  'system_errors',
]

const DISCORD_NOTIFICATION_FEATURE_LABELS: Record<string, string> = {
  workflow: 'Workflow Start/End',
  sync: 'Google Drive Sync Summary',
  poster_renamer: 'Poster Renamer Results',
  unmatched_assets: 'Unmatched Summary',
  plex_upload: 'Plex Upload Item',
  idarr: 'IDarr Summary',
  maker_monitor: 'Maker Monitor Summary',
  system_errors: 'Major Errors',
}

const defaultDiscordFeatureConfig = (): DiscordNotificationFeatureConfig => ({
  enabled: false,
  on_success: true,
  on_error: true,
  include_summary: true,
  include_details: true,
})

const defaultDiscordConfig = (): DiscordNotificationConfig => ({
  enabled: false,
  webhook_url: '',
  features: DISCORD_NOTIFICATION_FEATURE_ORDER.reduce<Record<string, DiscordNotificationFeatureConfig>>((accumulator, key) => {
    accumulator[key] = defaultDiscordFeatureConfig()
    return accumulator
  }, {}),
})

const normalizeDiscordConfig = (config?: Partial<DiscordNotificationConfig>): DiscordNotificationConfig => {
  const normalized = defaultDiscordConfig()
  if (!config) {
    return normalized
  }

  normalized.enabled = !!config.enabled
  normalized.webhook_url = config.webhook_url || ''

  if (config.features) {
    DISCORD_NOTIFICATION_FEATURE_ORDER.forEach((key) => {
      const candidate = config.features?.[key]
      if (!candidate) {
        return
      }
      const defaults = defaultDiscordFeatureConfig()
      normalized.features[key] = {
        enabled: !!candidate.enabled,
        on_success: candidate.on_success ?? defaults.on_success,
        on_error: candidate.on_error ?? defaults.on_error,
        include_summary: candidate.include_summary ?? defaults.include_summary,
        include_details: candidate.include_details ?? defaults.include_details,
      }
    })
  }

  return normalized
}

function Settings() {
  const MASKED_VALUE = '***masked***'

  const navigate = useNavigate()
  const [toggleAnimationsEnabled, setToggleAnimationsEnabled] = useState(false)
  const [activeTab, setActiveTab] = useState<SettingsTab>(() => {
    const savedTab = localStorage.getItem(SETTINGS_TAB_STORAGE_KEY)
    if (savedTab && isSettingsTab(savedTab)) {
      return savedTab
    }
    return 'basic'
  })
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [drives, setDrives] = useState<Drive[]>([])
  const [saving, setSaving] = useState(false)
  const [showInstructions, setShowInstructions] = useState(false)
  const [hasUnsavedRclone, setHasUnsavedRclone] = useState(false)
  const [hasUnsavedMedia, setHasUnsavedMedia] = useState(false)
  const [showUnsavedModal, setShowUnsavedModal] = useState(false)
  const [pendingTabChange, setPendingTabChange] = useState<SettingsTab | null>(null)
  const [discordConfig, setDiscordConfig] = useState<DiscordNotificationConfig>(defaultDiscordConfig())
  const [hasUnsavedNotifications, setHasUnsavedNotifications] = useState(false)
  const [testingDiscord, setTestingDiscord] = useState(false)
  const [showDiscordWebhook, setShowDiscordWebhook] = useState(false)
  
  // Field visibility state for sensitive data
  const [showClientId, setShowClientId] = useState(false)
  const [showClientSecret, setShowClientSecret] = useState(false)
  const [showToken, setShowToken] = useState(false)

  const handleToggleClientSecretVisibility = async () => {
    const willShow = !showClientSecret
    if (willShow && rcloneSettings.google_client_secret === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'google_client_secret' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved Client Secret available to reveal', 'error')
          return
        }
        rcloneBaselineRef.current = normalizeRcloneSettings({
          ...rcloneBaselineRef.current,
          google_client_secret: revealedValue,
        })
        setRcloneSettings((prev) => ({ ...prev, google_client_secret: revealedValue }))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal Client Secret'), 'error')
        return
      }
    }
    setShowClientSecret((prev) => !prev)
  }

  const handleToggleTokenVisibility = async () => {
    const willShow = !showToken
    if (willShow && rcloneSettings.google_token === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'google_token' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved Google Token available to reveal', 'error')
          return
        }
        rcloneBaselineRef.current = normalizeRcloneSettings({
          ...rcloneBaselineRef.current,
          google_token: revealedValue,
        })
        setRcloneSettings((prev) => ({ ...prev, google_token: revealedValue }))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal Google Token'), 'error')
        return
      }
    }
    setShowToken((prev) => !prev)
  }
  const [mediaBaselineVersion, setMediaBaselineVersion] = useState(0)
  const rcloneBaselineRef = useRef<RcloneSettingsSnapshot>(normalizeRcloneSettings({
    google_client_id: '',
    google_client_secret: '',
    google_token: '',
    google_service_account_file: '',
  }))
  const mediaBaselineRef = useRef<MediaSettingsSnapshot>(normalizeMediaSettings({
    plex_instances: [{ name: 'Plex', url: '', api_key: '' }],
    sonarr_instances: [{ name: 'Sonarr', url: '', api_key: '' }],
    radarr_instances: [{ name: 'Radarr', url: '', api_key: '' }],
  }))
  const discordBaselineRef = useRef<DiscordNotificationConfig>(defaultDiscordConfig())
  
  const { showToast } = useToast()

  const {
    databaseStats,
    loadingStats,
    cleanupLoading,
    showCleanupConfirm,
    setShowCleanupConfirm,
    backupLoading,
    restoreLoading,
    showRestartModal,
    setShowRestartModal,
    fetchDatabaseStats,
    handleDatabaseCleanup,
    handleDownloadBackup,
    handleRestoreBackup,
  } = useSettingsOperations({ showToast })

  const {
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
  } = useSettingsMedia({
    showToast,
    setSaving,
    onRevealApiKey: (settingsKey, index, value) => {
      const baseline = normalizeMediaSettings(mediaBaselineRef.current)
      const updatedInstances = [...baseline[settingsKey]]
      if (!updatedInstances[index]) {
        return
      }
      updatedInstances[index] = {
        ...updatedInstances[index],
        api_key: value,
      }
      mediaBaselineRef.current = {
        ...baseline,
        [settingsKey]: updatedInstances,
      }
      setMediaBaselineVersion((previous) => previous + 1)
    },
  })

  const dirtyMediaIndices = useMemo(() => {
    const baseline = normalizeMediaSettings(mediaBaselineRef.current)
    const current = normalizeMediaSettings(mediaSettings)
    return calculateDirtyMediaIndices(current, baseline)
  }, [mediaSettings, mediaBaselineVersion])

  const {
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
  } = useSettingsCore({ showToast, setSaving, setMediaSettings })

  useEffect(() => {
    let cancelled = false

    const loadInitialSettings = async () => {
      const coreSnapshotPromise = fetchSettings()
      const discordSnapshotPromise = fetchDiscordNotificationSettings()

      await Promise.all([
        fetchDebugStatus(),
        coreSnapshotPromise,
        discordSnapshotPromise,
        fetchSchedules(),
        fetchDrives(),
        fetchLibraryConfigs(),
      ])

      const coreSnapshot = await coreSnapshotPromise
      const discordSnapshot = await discordSnapshotPromise
      if (coreSnapshot) {
        rcloneBaselineRef.current = normalizeRcloneSettings(coreSnapshot.rclone)
        mediaBaselineRef.current = normalizeMediaSettings(coreSnapshot.media)
        setMediaBaselineVersion((previous) => previous + 1)
        setHasUnsavedRclone(false)
        setHasUnsavedMedia(false)
      }
      if (discordSnapshot) {
        discordBaselineRef.current = normalizeDiscordConfig(discordSnapshot)
        setHasUnsavedNotifications(false)
      }

      if (cancelled) {
        return
      }

      requestAnimationFrame(() => {
        if (!cancelled) {
          setToggleAnimationsEnabled(true)
        }
      })
    }

    loadInitialSettings()

    return () => {
      cancelled = true
    }
  }, [])

  const fetchSchedules = async () => {
    try {
      const fetchedSchedules = await getSchedules()
      setSchedules(fetchedSchedules)
    } catch (error) {
      console.error('Error fetching schedules:', error)
    }
  }

  const fetchDrives = async () => {
    try {
      const fetchedDrives = await getDrives()
      setDrives(fetchedDrives)
    } catch (error) {
      console.error('Error fetching drives:', error)
    }
  }

  const fetchDiscordNotificationSettings = async (): Promise<DiscordNotificationConfig | null> => {
    try {
      const config = await getDiscordNotificationConfig()
      const normalized = normalizeDiscordConfig(config)
      setDiscordConfig(normalized)
      return normalized
    } catch (error) {
      console.error('Error fetching Discord notification settings:', error)
      return null
    }
  }

  const {
    editingSchedule,
    scheduleSaving,
    addSchedule,
    updateScheduleField,
    toggleEditSchedule,
    toggleScheduleEnabled,
    saveSchedule,
    removeSchedule,
    cancelScheduleEdit,
    getScheduleSummary,
  } = useSettingsSchedules({
    schedules,
    setSchedules,
    drives,
    fetchSchedules,
    showToast,
  })

  useEffect(() => {
    const baseline = normalizeRcloneSettings(rcloneBaselineRef.current)
    const current = normalizeRcloneSettings(rcloneSettings)
    setHasUnsavedRclone(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [rcloneSettings])

  useEffect(() => {
    const baseline = normalizeMediaSettings(mediaBaselineRef.current)
    const current = normalizeMediaSettings(mediaSettings)
    setHasUnsavedMedia(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [mediaSettings])

  useEffect(() => {
    const baseline = normalizeDiscordConfig(discordBaselineRef.current)
    const current = normalizeDiscordConfig(discordConfig)
    setHasUnsavedNotifications(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [discordConfig])

  useEffect(() => {
    localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      const hasUnsavedChanges = (activeTab === 'rclone' && hasUnsavedRclone)
        || (activeTab === 'media' && hasUnsavedMedia)
        || (activeTab === 'notifications' && hasUnsavedNotifications)

      if (hasUnsavedChanges) {
        event.preventDefault()
        event.returnValue = ''
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [activeTab, hasUnsavedRclone, hasUnsavedMedia, hasUnsavedNotifications])

  const handleTabChange = (nextTab: SettingsTab) => {
    if (nextTab === activeTab) {
      return
    }

    const hasUnsavedChanges = (activeTab === 'rclone' && hasUnsavedRclone)
      || (activeTab === 'media' && hasUnsavedMedia)
      || (activeTab === 'notifications' && hasUnsavedNotifications)

    if (hasUnsavedChanges) {
      setPendingTabChange(nextTab)
      setShowUnsavedModal(true)
      return
    }

    setActiveTab(nextTab)
  }

  const handleDiscardChanges = () => {
    if (activeTab === 'rclone') {
      setRcloneSettings(normalizeRcloneSettings(rcloneBaselineRef.current))
      setHasUnsavedRclone(false)
    }

    if (activeTab === 'media') {
      setMediaSettings(normalizeMediaSettings(mediaBaselineRef.current))
      setEditingPlex(new Set())
      setEditingSonarr(new Set())
      setEditingRadarr(new Set())
      setHasUnsavedMedia(false)
    }

    if (activeTab === 'notifications') {
      setDiscordConfig(normalizeDiscordConfig(discordBaselineRef.current))
      setHasUnsavedNotifications(false)
    }

    if (pendingTabChange) {
      setActiveTab(pendingTabChange)
    }

    setPendingTabChange(null)
    setShowUnsavedModal(false)
  }

  const handleCancelDiscard = () => {
    setPendingTabChange(null)
    setShowUnsavedModal(false)
  }

  const handleSaveRcloneWithSnapshot = async () => {
    const saved = await handleSaveRclone()
    if (!saved) {
      return
    }

    rcloneBaselineRef.current = normalizeRcloneSettings(rcloneSettings)
    setHasUnsavedRclone(false)
  }

  const handleConfirmSaveRclone = async () => {
    const saved = await confirmSaveRclone()
    if (!saved) {
      return
    }

    rcloneBaselineRef.current = normalizeRcloneSettings(rcloneSettings)
    setHasUnsavedRclone(false)
  }

  const handleSaveMediaWithSnapshot = async () => {
    const saved = await handleSaveMediaSettings()
    if (!saved) {
      return
    }

    mediaBaselineRef.current = normalizeMediaSettings(mediaSettings)
    setMediaBaselineVersion((previous) => previous + 1)
    setHasUnsavedMedia(false)
  }

  const handleSaveDiscordNotifications = async () => {
    if (!hasUnsavedNotifications) {
      return
    }

    try {
      setSaving(true)
      const payload = normalizeDiscordConfig(discordConfig)
      await saveDiscordNotificationConfig(payload)
      discordBaselineRef.current = normalizeDiscordConfig(payload)
      setHasUnsavedNotifications(false)
      showToast('Discord notification settings saved successfully!', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save Discord notification settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleTestDiscordNotification = async () => {
    try {
      setTestingDiscord(true)
      const payload = normalizeDiscordConfig(discordConfig)
      const result = await testDiscordNotification(payload)
      showToast(result.message || 'Discord test notification sent successfully', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to send Discord test notification'), 'error')
    } finally {
      setTestingDiscord(false)
    }
  }

  const handleToggleDiscordWebhookVisibility = async () => {
    const willShow = !showDiscordWebhook
    if (willShow && discordConfig.webhook_url === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({
          setting_key: 'discord_notifications_webhook_url',
        })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved Discord webhook URL available to reveal', 'error')
          return
        }
        discordBaselineRef.current = normalizeDiscordConfig({
          ...discordBaselineRef.current,
          webhook_url: revealedValue,
        })
        setDiscordConfig((prev) => ({
          ...prev,
          webhook_url: revealedValue,
        }))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal Discord webhook URL'), 'error')
        return
      }
    }

    setShowDiscordWebhook((prev) => !prev)
  }

  return (
    <div className={`page-container settings ${toggleAnimationsEnabled ? 'toggle-animations-enabled' : ''}`}>
      <div className="settings-title-row">
        <div>
          <h1>Settings</h1>
          <p className="settings-description">Configure application settings</p>
        </div>
        <button 
          className="btn-rerun-setup" 
          onClick={() => navigate('/setup')}
          title="Re-run Setup Wizard"
        >
          <SettingsIcon size={18} />
          Re-run Setup Wizard
        </button>
      </div>
      
      <SettingsTabs activeTab={activeTab} onTabChange={handleTabChange} />

      {activeTab === 'basic' && (
        <>
          <div className="settings-section">
            <div className="settings-section-header">
              <h2>Logging</h2>
            </div>
            <div className="setting-item">
              <div className="setting-info">
                <label>Debug Mode</label>
                <p className="setting-description">
                  Enable detailed debug logging. This will show more verbose logs in the console and logs page.
                </p>
              </div>
              <div className="setting-control">
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={debugEnabled}
                    onChange={handleDebugToggle}
                    disabled={loading}
                  />
                  <span className="toggle-slider"></span>
                </label>
              </div>
            </div>
          </div>
        </>
      )}

      {activeTab === 'notifications' && (
        <div className="settings-section">
          <div className="settings-section-header">
            <h2>Discord Notifications</h2>
            <p className="setting-description">
              Configure one Discord webhook and choose which PosterFlow areas send notifications.
            </p>
          </div>

          <div className="setting-item">
            <div className="setting-info">
              <label>Enable Discord Notifications</label>
              <p className="setting-description">
                Master switch for Discord notifications across all selected features.
              </p>
            </div>
            <div className="setting-control">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={discordConfig.enabled}
                  onChange={(event) => setDiscordConfig((prev) => ({
                    ...prev,
                    enabled: event.target.checked,
                  }))}
                />
                <span className="toggle-slider"></span>
              </label>
            </div>
          </div>

          <div className="notification-webhook-group">
            <label>Discord Webhook URL</label>
            <div className="notification-webhook-row">
              <div className="input-with-toggle">
                <input
                  type={showDiscordWebhook ? 'text' : 'password'}
                  value={discordConfig.webhook_url}
                  onChange={(event) => setDiscordConfig((prev) => ({
                    ...prev,
                    webhook_url: event.target.value,
                  }))}
                  placeholder="https://discord.com/api/webhooks/..."
                />
                <button
                  type="button"
                  className="toggle-visibility"
                  onClick={handleToggleDiscordWebhookVisibility}
                  title={showDiscordWebhook ? 'Hide' : 'Show'}
                  aria-label={showDiscordWebhook ? 'Hide Discord webhook URL' : 'Show Discord webhook URL'}
                >
                  {showDiscordWebhook ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              <button
                type="button"
                className="btn-secondary notification-test-inline"
                onClick={handleTestDiscordNotification}
                disabled={testingDiscord || saving}
              >
                {testingDiscord ? 'Testing...' : 'Test'}
              </button>
            </div>
          </div>

          <div className="notification-feature-list">
            {DISCORD_NOTIFICATION_FEATURE_ORDER.map((featureKey) => {
              const feature = discordConfig.features[featureKey]
              return (
                <div className="setting-item" key={featureKey}>
                  <div className="setting-info">
                    <label>{DISCORD_NOTIFICATION_FEATURE_LABELS[featureKey] || featureKey}</label>
                  </div>
                  <div className="setting-control">
                    <label className="toggle-switch">
                      <input
                        type="checkbox"
                        checked={feature.enabled}
                        onChange={(event) => setDiscordConfig((prev) => ({
                          ...prev,
                          features: {
                            ...prev.features,
                            [featureKey]: {
                              ...prev.features[featureKey],
                              enabled: event.target.checked,
                              on_success: true,
                              on_error: true,
                              include_summary: true,
                              include_details: true,
                            },
                          },
                        }))}
                      />
                      <span className="toggle-slider"></span>
                    </label>
                  </div>
                </div>
              )
            })}
          </div>

          <div className="notification-actions">
            <button
              type="button"
              className={`btn-save ${hasUnsavedNotifications ? 'btn-unsaved' : ''}`}
              onClick={handleSaveDiscordNotifications}
              disabled={saving || !hasUnsavedNotifications}
              title={hasUnsavedNotifications ? 'Save changes' : 'No changes to save'}
            >
              {saving ? 'Saving...' : 'Save Notification Settings'}
            </button>
          </div>
        </div>
      )}

      {activeTab === 'media' && (
        <SettingsMediaSection
          mediaSettings={mediaSettings}
          editingPlex={editingPlex}
          editingSonarr={editingSonarr}
          editingRadarr={editingRadarr}
          testingPlex={testingPlex}
          testingSonarr={testingSonarr}
          testingRadarr={testingRadarr}
          showPlexTokens={showPlexTokens}
          showSonarrKeys={showSonarrKeys}
          showRadarrKeys={showRadarrKeys}
          saving={saving}
          dirtyPlexInstances={dirtyMediaIndices.plex_instances}
          dirtySonarrInstances={dirtyMediaIndices.sonarr_instances}
          dirtyRadarrInstances={dirtyMediaIndices.radarr_instances}
          onAddPlexInstance={addPlexInstance}
          onAddSonarrInstance={addSonarrInstance}
          onAddRadarrInstance={addRadarrInstance}
          onToggleEditPlex={toggleEditPlex}
          onToggleEditSonarr={toggleEditSonarr}
          onToggleEditRadarr={toggleEditRadarr}
          onTestPlexConnection={testPlexConnection}
          onTestSonarrConnection={testSonarrConnection}
          onTestRadarrConnection={testRadarrConnection}
          onOpenLibraryModal={openLibraryModal}
          onSaveMediaSettings={handleSaveMediaWithSnapshot}
          onConfirmRemovePlexInstance={confirmRemovePlexInstance}
          onConfirmRemoveSonarrInstance={confirmRemoveSonarrInstance}
          onConfirmRemoveRadarrInstance={confirmRemoveRadarrInstance}
          onUpdatePlexInstance={updatePlexInstance}
          onUpdateSonarrInstance={updateSonarrInstance}
          onUpdateRadarrInstance={updateRadarrInstance}
          onTogglePlexTokenVisibility={togglePlexTokenVisibility}
          onToggleSonarrKeyVisibility={toggleSonarrKeyVisibility}
          onToggleRadarrKeyVisibility={toggleRadarrKeyVisibility}
        />
      )}

      {activeTab === 'rclone' && (
        <>
          <SettingsRcloneSection
            showInstructions={showInstructions}
            onToggleInstructions={() => setShowInstructions(!showInstructions)}
            rcloneSettings={rcloneSettings}
            setRcloneSettings={setRcloneSettings}
            showClientId={showClientId}
            setShowClientId={setShowClientId}
            showClientSecret={showClientSecret}
            onToggleClientSecretVisibility={handleToggleClientSecretVisibility}
            showToken={showToken}
            onToggleTokenVisibility={handleToggleTokenVisibility}
            onSave={handleSaveRcloneWithSnapshot}
            onUploadServiceAccount={handleUploadServiceAccount}
            uploadingServiceAccount={uploadingServiceAccount}
            saving={saving}
            hasUnsaved={hasUnsavedRclone}
          />
        </>
      )}

      {activeTab === 'scheduling' && (
        <SettingsSchedulingSection
          schedules={schedules}
          onAddSchedule={addSchedule}
          onToggleScheduleEnabled={toggleScheduleEnabled}
          onEditSchedule={toggleEditSchedule}
          onRemoveSchedule={removeSchedule}
          getScheduleSummary={getScheduleSummary}
        />
      )}

      <ScheduleEditModal
        editingSchedule={editingSchedule}
        drives={drives}
        scheduleSaving={scheduleSaving}
        updateScheduleField={updateScheduleField}
        onClose={cancelScheduleEdit}
        onSave={saveSchedule}
      />

      {activeTab === 'backup' && (
        <BackupRestoreSection
          backupLoading={backupLoading}
          restoreLoading={restoreLoading}
          onDownloadBackup={handleDownloadBackup}
          onRestoreBackup={handleRestoreBackup}
        />
      )}

      {activeTab === 'maintenance' && (
        <MaintenanceSection
          databaseStats={databaseStats}
          loadingStats={loadingStats}
          cleanupLoading={cleanupLoading}
          onFetchStats={fetchDatabaseStats}
          onStartCleanup={() => setShowCleanupConfirm(true)}
        />
      )}

      {activeTab === 'security' && (
        <SettingsSecuritySection showToast={showToast} />
      )}

      {activeTab === 'scripts' && (
        <SettingsScriptsSection showToast={showToast} />
      )}

      <ConfirmDialog
        isOpen={showSaveConfirm}
        title="Save Rclone Settings"
        message="Are you sure you want to save these Rclone settings? This will update your Google Drive credentials and may affect all sync operations."
        confirmText="Save Settings"
        cancelText="Cancel"
        variant="warning"
        onConfirm={handleConfirmSaveRclone}
        onCancel={() => setShowSaveConfirm(false)}
      />

      <ConfirmDialog
        isOpen={deleteConfirm.show}
        title={`Delete ${deleteConfirm.type === 'sonarr' ? 'Sonarr' : 'Radarr'} Instance`}
        message={`Are you sure you want to delete "${deleteConfirm.name}"? This instance has saved settings.`}
        confirmText="Delete"
        cancelText="Cancel"
        variant="danger"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteConfirm({ show: false, type: null, index: null, name: '' })}
      />

      <ConfirmDialog
        isOpen={showCleanupConfirm}
        title="Clean Database"
        message={`Are you sure you want to clean up ${databaseStats?.orphaned_count.toLocaleString()} orphaned database records? This action cannot be undone.`}
        confirmText={cleanupLoading ? 'Cleaning...' : 'Clean Database'}
        cancelText="Cancel"
        variant="danger"
        onConfirm={handleDatabaseCleanup}
        onCancel={() => setShowCleanupConfirm(false)}
      />

      <RestartRequiredModal isOpen={showRestartModal} onClose={() => setShowRestartModal(false)} />

      <PlexLibraryModal
        isOpen={showLibraryModal}
        instanceName={libraryModalInstance?.name}
        loadingLibraries={loadingLibraries}
        libraries={libraries}
        onClose={() => setShowLibraryModal(false)}
        onToggleLibrary={toggleLibrary}
        onSave={saveLibraryConfig}
      />

      <UnsavedChangesModal
        isOpen={showUnsavedModal}
        onCancel={handleCancelDiscard}
        onDiscard={handleDiscardChanges}
      />
    </div>
  )
}

export default Settings