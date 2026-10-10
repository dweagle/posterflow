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
  getAppriseNotificationConfig,
  saveAppriseNotificationConfig,
  testAppriseNotification,
  AppriseNotificationConfig,
  AppriseNotificationFeatureConfig,
  getApiErrorMessage,
  revealSensitiveSetting,
} from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Eye, EyeOff, Settings as SettingsIcon } from 'lucide-react'
import SettingsTabs from '../components/settings/SettingsTabs'
import SourceAttribution from '../components/SourceAttribution'
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
import { API_KEY_SETTINGS, ApiKeyName, useSettingsCore } from '../hooks/useSettingsCore'
import './Settings.css'

type SettingsTab = 'basic' | 'notifications' | 'rclone' | 'media' | 'scheduling' | 'backup' | 'maintenance' | 'security' | 'scripts'
const SETTINGS_TAB_STORAGE_KEY = 'posterflow.settings.activeTab'
type NotificationChannel = 'discord' | 'apprise'
const NOTIFICATION_CHANNEL_STORAGE_KEY = 'posterflow.settings.notificationChannel'

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
  type?: 'plex' | 'jellyfin' // media server instances only; absent = plex
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
    type: instance.type === 'jellyfin' ? 'jellyfin' : 'plex',
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
    || (current.type === 'jellyfin') !== (baseline.type === 'jellyfin')
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

const NOTIFICATION_FEATURE_ORDER = [
  'workflow',
  'sync',
  'poster_renamer',
  'unmatched_assets',
  'plex_upload',
  'idarr',
  'maker_monitor',
  'system_errors',
]

const NOTIFICATION_FEATURE_EVENTS: Record<string, { type: 'success' | 'error' | 'info'; label: string }[]> = {
  workflow: [
    { type: 'success', label: 'Success - Per-step summary embed — one embed per step that ran (poster sync, artwork sync, renamer, border, plex, unmatched), each showing its own result' },
    { type: 'error', label: 'Error - Step failed — included in the summary as an error embed for that step' },
    { type: 'error', label: 'Error - Workflow crashed — sent if the entire workflow throws before finishing' },
  ],
  sync: [
    { type: 'success', label: 'Success - Sync completed — poster or artwork drive name, files added / replaced / deleted' },
    { type: 'error', label: 'Error - Sync failed — error message (poster and artwork drives both reported)' },
  ],
  poster_renamer: [
    { type: 'success', label: 'Success - Rename completed — posters matched (movies / series / collections list) and artwork files placed' },
    { type: 'error', label: 'Error - Rename failed — error message' },
  ],
  unmatched_assets: [
    { type: 'info', label: 'Info- Scan completed — missing posters (movies / shows / seasons / collections) and missing artwork per type (logo / background / squareart)' },
    { type: 'error', label: 'Error - Detection failed — error message' },
  ],
  plex_upload: [
    { type: 'success', label: 'Success - Full upload completed — movies / shows / seasons / collections uploaded, plus artwork uploaded by type' },
    { type: 'success', label: 'Success - Webhook upload — item uploaded (media type, title, count)' },
    { type: 'error', label: 'Error - Upload failed — error message' },
    { type: 'info', label: 'Info - Webhook: no local assets found for target item (no ping)' },
  ],
  idarr: [
    { type: 'success', label: 'Success - IDarr completed — files renamed, unresolved count' },
    { type: 'error', label: 'Error - IDarr failed — error message' },
  ],
  maker_monitor: [
    { type: 'info', label: 'Info - Posters needed report — upcoming seasons, premieres found, posters needed' },
    { type: 'error', label: 'Error - Maker Monitor failed — error message' },
  ],
  system_errors: [
    { type: 'error', label: 'Error - Major error from any feature — source and error message' },
  ],
}

const NOTIFICATION_FEATURE_LABELS: Record<string, string> = {
  workflow: 'Workflow Start/End',
  sync: 'Drive Sync Summary (Posters + Artwork)',
  poster_renamer: 'Asset Renamer Results',
  unmatched_assets: 'Unmatched Summary',
  plex_upload: 'Asset Upload Item',
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
  webhook_url: '',
  mention: '',
  mention_on_error: true,
  mention_on_success: false,
  mention_on_info: false,
})

const defaultDiscordConfig = (): DiscordNotificationConfig => ({
  enabled: false,
  webhook_url: '',
  mention: '',
  mention_on_error: true,
  mention_on_success: false,
  mention_on_info: false,
  features: NOTIFICATION_FEATURE_ORDER.reduce<Record<string, DiscordNotificationFeatureConfig>>((accumulator, key) => {
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
  normalized.mention = config.mention ?? ''
  normalized.mention_on_error = config.mention_on_error ?? true
  normalized.mention_on_success = config.mention_on_success ?? false
  normalized.mention_on_info = config.mention_on_info ?? false

  if (config.features) {
    NOTIFICATION_FEATURE_ORDER.forEach((key) => {
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
        webhook_url: candidate.webhook_url ?? '',
        mention: candidate.mention ?? '',
        mention_on_error: candidate.mention_on_error ?? true,
        mention_on_success: candidate.mention_on_success ?? false,
        mention_on_info: candidate.mention_on_info ?? false,
      }
    })
  }

  return normalized
}

const defaultAppriseFeatureConfig = (): AppriseNotificationFeatureConfig => ({
  enabled: false,
  on_success: true,
  on_error: true,
  include_summary: true,
  include_details: true,
  urls: '',
  tags: '',
})

const defaultAppriseConfig = (): AppriseNotificationConfig => ({
  enabled: false,
  urls: '',
  features: NOTIFICATION_FEATURE_ORDER.reduce<Record<string, AppriseNotificationFeatureConfig>>((accumulator, key) => {
    accumulator[key] = defaultAppriseFeatureConfig()
    return accumulator
  }, {}),
})

const normalizeAppriseConfig = (config?: Partial<AppriseNotificationConfig>): AppriseNotificationConfig => {
  const normalized = defaultAppriseConfig()
  if (!config) {
    return normalized
  }

  normalized.enabled = !!config.enabled
  normalized.urls = config.urls || ''

  if (config.features) {
    NOTIFICATION_FEATURE_ORDER.forEach((key) => {
      const candidate = config.features?.[key]
      if (!candidate) {
        return
      }
      const defaults = defaultAppriseFeatureConfig()
      normalized.features[key] = {
        enabled: !!candidate.enabled,
        on_success: candidate.on_success ?? defaults.on_success,
        on_error: candidate.on_error ?? defaults.on_error,
        include_summary: candidate.include_summary ?? defaults.include_summary,
        include_details: candidate.include_details ?? defaults.include_details,
        urls: candidate.urls ?? '',
        tags: candidate.tags ?? '',
      }
    })
  }

  return normalized
}

// URL lists grow one line per URL; at one line they size like the inputs beside them
const autoGrowTextarea = (element: HTMLTextAreaElement | null) => {
  if (!element) {
    return
  }
  element.style.height = 'auto'
  element.style.height = `${element.scrollHeight + element.offsetHeight - element.clientHeight}px`
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
  const [hasUnsavedDiscord, setHasUnsavedDiscord] = useState(false)
  const [hasUnsavedApprise, setHasUnsavedApprise] = useState(false)
  const hasUnsavedNotifications = hasUnsavedDiscord || hasUnsavedApprise
  const [testingDiscord, setTestingDiscord] = useState(false)
  const [showDiscordWebhook, setShowDiscordWebhook] = useState(false)
  const [showFeatureWebhooks, setShowFeatureWebhooks] = useState<Record<string, boolean>>({})
  const [appriseConfig, setAppriseConfig] = useState<AppriseNotificationConfig>(defaultAppriseConfig())
  const [testingApprise, setTestingApprise] = useState(false)
  const [showAppriseUrls, setShowAppriseUrls] = useState(false)
  const [showFeatureAppriseUrls, setShowFeatureAppriseUrls] = useState<Record<string, boolean>>({})
  const [notificationChannel, setNotificationChannel] = useState<NotificationChannel>(() => {
    const saved = localStorage.getItem(NOTIFICATION_CHANNEL_STORAGE_KEY)
    return saved === 'apprise' ? 'apprise' : 'discord'
  })
  const [showTmdbKey, setShowTmdbKey] = useState(false)
  const [showTvdbKey, setShowTvdbKey] = useState(false)
  const [showFanartKey, setShowFanartKey] = useState(false)
  const [removeApiKey, setRemoveApiKey] = useState<ApiKeyName | null>(null)

  const handleToggleTvdbKeyVisibility = async () => {
    const willShow = !showTvdbKey
    if (willShow && tvdbApiKey === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'tvdb_api_key' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved TheTVDB API key available to reveal', 'error')
          return
        }
        setTvdbApiKey(revealedValue)
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal TheTVDB API key'), 'error')
        return
      }
    }
    setShowTvdbKey((prev) => !prev)
  }

  const handleToggleFanartKeyVisibility = async () => {
    const willShow = !showFanartKey
    if (willShow && fanartApiKey === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'fanart_api_key' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved fanart.tv API key available to reveal', 'error')
          return
        }
        setFanartApiKey(revealedValue)
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal fanart.tv API key'), 'error')
        return
      }
    }
    setShowFanartKey((prev) => !prev)
  }

  const handleToggleTmdbKeyVisibility = async () => {
    const willShow = !showTmdbKey
    if (willShow && tmdbApiKey === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'tmdb_api_key' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved TMDB API key available to reveal', 'error')
          return
        }
        setTmdbApiKey(revealedValue)
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal TMDB API key'), 'error')
        return
      }
    }
    setShowTmdbKey((prev) => !prev)
  }
  
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
  const appriseBaselineRef = useRef<AppriseNotificationConfig>(defaultAppriseConfig())
  
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
    mediaServerMediaSourceEnabled,
    mediaServerMediaSourceAuto,
    toggleMediaServerMediaSource,
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
    onInstanceRemoved: (settingsKey, index) => {
      // The removal is already persisted; drop the entry from the dirty-tracking
      // baseline so the page doesn't warn about unsaved changes with no Save button
      const baseline = normalizeMediaSettings(mediaBaselineRef.current)
      const updatedInstances = [...baseline[settingsKey]]
      if (index < updatedInstances.length) {
        updatedInstances.splice(index, 1)
        mediaBaselineRef.current = {
          ...baseline,
          [settingsKey]: updatedInstances,
        }
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
    tmdbApiKey,
    setTmdbApiKey,
    handleSaveTmdbApiKey,
    tvdbApiKey,
    setTvdbApiKey,
    tvdbPin,
    setTvdbPin,
    handleSaveTvdbApiKey,
    fanartApiKey,
    setFanartApiKey,
    handleSaveFanartApiKey,
    savedApiKeys,
    handleRemoveApiKey,
    appleArtworkEnabled,
    handleToggleAppleArtwork,
    appTimezone,
    effectiveTimezone,
    handleSaveAppTimezone,
  } = useSettingsCore({ showToast, setSaving, setMediaSettings })

  const removeApiKeyLabel = removeApiKey ? API_KEY_SETTINGS[removeApiKey].label : ''
  const handleConfirmRemoveApiKey = async () => {
    const name = removeApiKey
    setRemoveApiKey(null)
    if (name) await handleRemoveApiKey(name)
  }

  useEffect(() => {
    let cancelled = false

    const loadInitialSettings = async () => {
      const coreSnapshotPromise = fetchSettings()
      const discordSnapshotPromise = fetchDiscordNotificationSettings()
      const appriseSnapshotPromise = fetchAppriseNotificationSettings()

      await Promise.all([
        fetchDebugStatus(),
        coreSnapshotPromise,
        discordSnapshotPromise,
        appriseSnapshotPromise,
        fetchSchedules(),
        fetchDrives(),
        fetchLibraryConfigs(),
      ])

      const coreSnapshot = await coreSnapshotPromise
      const discordSnapshot = await discordSnapshotPromise
      const appriseSnapshot = await appriseSnapshotPromise
      if (coreSnapshot) {
        rcloneBaselineRef.current = normalizeRcloneSettings(coreSnapshot.rclone)
        mediaBaselineRef.current = normalizeMediaSettings(coreSnapshot.media)
        setMediaBaselineVersion((previous) => previous + 1)
        setHasUnsavedRclone(false)
        setHasUnsavedMedia(false)
      }
      if (discordSnapshot) {
        discordBaselineRef.current = normalizeDiscordConfig(discordSnapshot)
        setHasUnsavedDiscord(false)
      }
      if (appriseSnapshot) {
        appriseBaselineRef.current = normalizeAppriseConfig(appriseSnapshot)
        setHasUnsavedApprise(false)
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

  const fetchAppriseNotificationSettings = async (): Promise<AppriseNotificationConfig | null> => {
    try {
      const config = await getAppriseNotificationConfig()
      const normalized = normalizeAppriseConfig(config)
      setAppriseConfig(normalized)
      return normalized
    } catch (error) {
      console.error('Error fetching Apprise notification settings:', error)
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
    // mediaBaselineVersion: the baseline ref can change without mediaSettings changing
    // (api-key reveal, persisted instance removal)
  }, [mediaSettings, mediaBaselineVersion])

  useEffect(() => {
    const baseline = normalizeDiscordConfig(discordBaselineRef.current)
    const current = normalizeDiscordConfig(discordConfig)
    setHasUnsavedDiscord(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [discordConfig])

  useEffect(() => {
    const baseline = normalizeAppriseConfig(appriseBaselineRef.current)
    const current = normalizeAppriseConfig(appriseConfig)
    setHasUnsavedApprise(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [appriseConfig])

  useEffect(() => {
    localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  useEffect(() => {
    localStorage.setItem(NOTIFICATION_CHANNEL_STORAGE_KEY, notificationChannel)
  }, [notificationChannel])

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
      setHasUnsavedDiscord(false)
      setAppriseConfig(normalizeAppriseConfig(appriseBaselineRef.current))
      setHasUnsavedApprise(false)
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
    if (!hasUnsavedDiscord) {
      return
    }

    try {
      setSaving(true)
      const payload = normalizeDiscordConfig(discordConfig)
      await saveDiscordNotificationConfig(payload)
      discordBaselineRef.current = normalizeDiscordConfig(payload)
      setHasUnsavedDiscord(false)
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

  const handleToggleFeatureWebhookVisibility = async (featureKey: string) => {
    const isCurrentlyShown = showFeatureWebhooks[featureKey]
    const willShow = !isCurrentlyShown
    const currentWebhook = discordConfig.features[featureKey]?.webhook_url ?? ''

    if (willShow && currentWebhook === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({
          setting_key: 'discord_notifications_features',
          field: 'webhook_url',
          instance_name: featureKey,
        })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast(`No saved webhook URL for ${featureKey}`, 'error')
          return
        }
        discordBaselineRef.current = normalizeDiscordConfig({
          ...discordBaselineRef.current,
          features: {
            ...discordBaselineRef.current.features,
            [featureKey]: {
              ...discordBaselineRef.current.features[featureKey],
              webhook_url: revealedValue,
            },
          },
        })
        setDiscordConfig((prev) => ({
          ...prev,
          features: {
            ...prev.features,
            [featureKey]: {
              ...prev.features[featureKey],
              webhook_url: revealedValue,
            },
          },
        }))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal feature webhook URL'), 'error')
        return
      }
    }

    setShowFeatureWebhooks((prev) => ({ ...prev, [featureKey]: !prev[featureKey] }))
  }

  const updateAppriseFeature = (featureKey: string, patch: Partial<AppriseNotificationFeatureConfig>) => {
    setAppriseConfig((prev) => ({
      ...prev,
      features: {
        ...prev.features,
        [featureKey]: { ...prev.features[featureKey], ...patch },
      },
    }))
  }

  const handleSaveAppriseNotifications = async () => {
    if (!hasUnsavedApprise) {
      return
    }

    try {
      setSaving(true)
      const payload = normalizeAppriseConfig(appriseConfig)
      await saveAppriseNotificationConfig(payload)
      appriseBaselineRef.current = normalizeAppriseConfig(payload)
      setHasUnsavedApprise(false)
      showToast('Apprise notification settings saved successfully!', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save Apprise notification settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleTestAppriseNotification = async () => {
    try {
      setTestingApprise(true)
      const result = await testAppriseNotification(normalizeAppriseConfig(appriseConfig))
      showToast(result.message || 'Apprise test notification sent', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to send Apprise test notification'), 'error')
    } finally {
      setTestingApprise(false)
    }
  }

  const handleToggleAppriseUrlsVisibility = async () => {
    const willShow = !showAppriseUrls
    if (willShow && appriseConfig.urls === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({ setting_key: 'apprise_notifications_urls' })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast('No saved Apprise URLs available to reveal', 'error')
          return
        }
        appriseBaselineRef.current = normalizeAppriseConfig({ ...appriseBaselineRef.current, urls: revealedValue })
        setAppriseConfig((prev) => ({ ...prev, urls: revealedValue }))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal Apprise URLs'), 'error')
        return
      }
    }

    setShowAppriseUrls((prev) => !prev)
  }

  const handleToggleFeatureAppriseUrlsVisibility = async (featureKey: string) => {
    const willShow = !showFeatureAppriseUrls[featureKey]
    const currentUrls = appriseConfig.features[featureKey]?.urls ?? ''

    if (willShow && currentUrls === MASKED_VALUE) {
      try {
        const response = await revealSensitiveSetting({
          setting_key: 'apprise_notifications_features',
          field: 'urls',
          instance_name: featureKey,
        })
        const revealedValue = String(response.value || '')
        if (!revealedValue) {
          showToast(`No saved Apprise URLs for ${featureKey}`, 'error')
          return
        }
        const withRevealed = (config: AppriseNotificationConfig): AppriseNotificationConfig => ({
          ...config,
          features: {
            ...config.features,
            [featureKey]: { ...config.features[featureKey], urls: revealedValue },
          },
        })
        appriseBaselineRef.current = normalizeAppriseConfig(withRevealed(appriseBaselineRef.current))
        setAppriseConfig((prev) => withRevealed(prev))
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reveal feature Apprise URLs'), 'error')
        return
      }
    }

    setShowFeatureAppriseUrls((prev) => ({ ...prev, [featureKey]: !prev[featureKey] }))
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
              <h2>API Keys</h2>
            </div>
            <div className="setting-item api-key-setting">
              <div className="setting-info">
                <label>TMDB API Key</label>
                <p className="setting-description">
                  Used by Unmatched Assets, IDarr, and the Maker Tools monitor to search The Movie Database. Get a free key at{' '}
                  <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>
                    themoviedb.org
                  </a>.
                </p>
              </div>
              <div className="settings-input-row api-key-control">
                <div className="input-with-toggle">
                  <input
                    type={showTmdbKey ? 'text' : 'password'}
                    value={tmdbApiKey === MASKED_VALUE ? '' : tmdbApiKey}
                    onChange={(e) => setTmdbApiKey(e.target.value)}
                    placeholder={tmdbApiKey === MASKED_VALUE ? '••••••••••••••••' : 'Enter your TMDB API key'}
                  />
                  <button
                    className="toggle-visibility"
                    type="button"
                    onClick={handleToggleTmdbKeyVisibility}
                    title={showTmdbKey ? 'Hide key' : 'Show key'}
                  >
                    {showTmdbKey ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                <button
                  className="btn-primary btn-inline-save"
                  onClick={handleSaveTmdbApiKey}
                  disabled={saving}
                >
                  Save
                </button>
                {savedApiKeys.tmdb && (
                  <button
                    className="btn-secondary btn-inline-save"
                    type="button"
                    onClick={() => setRemoveApiKey('tmdb')}
                    disabled={saving}
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
            <SourceAttribution source="tmdb" />
            <div className="setting-item api-key-setting">
              <div className="setting-info">
                <label>TheTVDB API Key</label>
                <p className="setting-description">
                  Optional. Adds TheTVDB as a second source in the Maker Tools image browser, and lets the
                  Maker Tools monitor take season numbers and premiere dates from TheTVDB — the same source
                  Sonarr uses — with TMDB as the fallback. Create a v4 key at{' '}
                  <a href="https://thetvdb.com/api-information" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>
                    thetvdb.com
                  </a>. Leave the PIN blank unless yours is a subscriber-supported key.
                </p>
              </div>
              <div className="settings-input-row api-key-control">
                <div className="input-with-toggle">
                  <input
                    type={showTvdbKey ? 'text' : 'password'}
                    value={tvdbApiKey === MASKED_VALUE ? '' : tvdbApiKey}
                    onChange={(e) => setTvdbApiKey(e.target.value)}
                    placeholder={tvdbApiKey === MASKED_VALUE ? '••••••••••••••••' : 'Enter your TheTVDB API key'}
                  />
                  <button
                    className="toggle-visibility"
                    type="button"
                    onClick={handleToggleTvdbKeyVisibility}
                    title={showTvdbKey ? 'Hide key' : 'Show key'}
                  >
                    {showTvdbKey ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                <input
                  type="password"
                  style={{ maxWidth: 130 }}
                  value={tvdbPin === MASKED_VALUE ? '' : tvdbPin}
                  onChange={(e) => setTvdbPin(e.target.value)}
                  placeholder={tvdbPin === MASKED_VALUE ? '••••• PIN' : 'PIN (optional)'}
                />
                <button
                  className="btn-primary btn-inline-save"
                  onClick={handleSaveTvdbApiKey}
                  disabled={saving}
                >
                  Save
                </button>
                {savedApiKeys.tvdb && (
                  <button
                    className="btn-secondary btn-inline-save"
                    type="button"
                    onClick={() => setRemoveApiKey('tvdb')}
                    disabled={saving}
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
            <SourceAttribution source="tvdb" />
            <div className="setting-item api-key-setting">
              <div className="setting-info">
                <label>fanart.tv API Key</label>
                <p className="setting-description">
                  Optional. Adds fanart.tv as a third source in the Maker Tools image browser and the
                  Artwork Finder, alongside TMDB and TheTVDB. Sign in at{' '}
                  <a href="https://fanart.tv/" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>
                    fanart.tv
                  </a>{' '}
                  and copy the personal API key from your profile.
                </p>
              </div>
              <div className="settings-input-row api-key-control">
                <div className="input-with-toggle">
                  <input
                    type={showFanartKey ? 'text' : 'password'}
                    value={fanartApiKey === MASKED_VALUE ? '' : fanartApiKey}
                    onChange={(e) => setFanartApiKey(e.target.value)}
                    placeholder={fanartApiKey === MASKED_VALUE ? '••••••••••••••••' : 'Enter your fanart.tv API key'}
                  />
                  <button
                    className="toggle-visibility"
                    type="button"
                    onClick={handleToggleFanartKeyVisibility}
                    title={showFanartKey ? 'Hide key' : 'Show key'}
                  >
                    {showFanartKey ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
                <button
                  className="btn-primary btn-inline-save"
                  onClick={handleSaveFanartApiKey}
                  disabled={saving}
                >
                  Save
                </button>
                {savedApiKeys.fanart && (
                  <button
                    className="btn-secondary btn-inline-save"
                    type="button"
                    onClick={() => setRemoveApiKey('fanart')}
                    disabled={saving}
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>
            <SourceAttribution source="fanart" />
            <div className="setting-item">
              <div className="setting-info">
                <label>Apple TV Artwork</label>
                <p className="setting-description">
                  Adds Apple TV as a source in the Maker Tools image browser and the Artwork Finder: movie posters,
                  square show art, logos and backgrounds, found by title in the storefronts that sell it. No key
                  needed. Apple publishes no artwork API, so this uses the search behind tv.apple.com and may stop
                  working without notice.
                </p>
              </div>
              <div className="setting-control">
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={appleArtworkEnabled}
                    onChange={(e) => void handleToggleAppleArtwork(e.target.checked)}
                    disabled={saving}
                  />
                  <span className="toggle-slider"></span>
                </label>
              </div>
            </div>
          </div>

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
        <>
        <div className="notification-channel-tabs pf-subtabs" role="tablist" aria-label="Notification service">
          {(['discord', 'apprise'] as NotificationChannel[]).map((channel) => {
            const unsaved = channel === 'discord' ? hasUnsavedDiscord : hasUnsavedApprise
            return (
              <button
                key={channel}
                type="button"
                role="tab"
                aria-selected={notificationChannel === channel}
                className={notificationChannel === channel ? 'active' : ''}
                onClick={() => setNotificationChannel(channel)}
                title={unsaved ? 'Unsaved changes' : undefined}
              >
                {channel === 'discord' ? 'Discord' : 'Apprise'}
                {unsaved && <span className="notification-channel-unsaved" aria-label="Unsaved changes" />}
              </button>
            )
          })}
        </div>

        {notificationChannel === 'discord' && (
        <div className="settings-section">
          <div className="settings-section-header">
            <div>
              <h2>Discord Notifications</h2>
              <p className="setting-description">
                Configure one Discord webhook and choose which PosterFlow areas send notifications.
              </p>
            </div>
            <button
              type="button"
              className={`btn-save ${hasUnsavedDiscord ? 'btn-unsaved' : ''}`}
              onClick={handleSaveDiscordNotifications}
              disabled={saving || !hasUnsavedDiscord}
              title={hasUnsavedDiscord ? 'Save changes' : 'No changes to save'}
            >
              {saving ? 'Saving...' : 'Save Discord Settings'}
            </button>
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
            <p className="notification-global-description">
              These settings apply globally to all features below. Individual features can override the webhook URL and mention settings independently.
            </p>
            <details className="feature-events-disclosure global-events-disclosure">
              <summary className="feature-events-summary">How does it work?</summary>
              <ul className="feature-events-list">
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Each enabled feature below sends notifications to this webhook URL.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Features with their own webhook URL override send to that channel instead.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Global Ping Target is used as a fallback — only if a feature has no ping target of its own.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>If a feature has its own ping target, its own "Ping on error / success / info" toggles are used instead of the global ones.</li>
              </ul>
              <div className="feature-events-divider" />
              <p className="feature-events-subheading">Finding your Webhook URL</p>
              <ul className="feature-events-list">
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>1.</span>In Discord, open <strong>Server Settings</strong> → <strong>Integrations</strong> → <strong>Webhooks</strong>.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>2.</span>Click <strong>New Webhook</strong>, pick a channel, then click <strong>Copy Webhook URL</strong>.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>3.</span>Paste the URL here. It starts with <code>https://discord.com/api/webhooks/…</code></li>
              </ul>
              <div className="feature-events-divider" />
              <p className="feature-events-subheading">Finding User IDs &amp; Role IDs</p>
              <ul className="feature-events-list">
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>1.</span>Enable <strong>Developer Mode</strong> in Discord: <em>User Settings → Advanced → Developer Mode</em>.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>2.</span><strong>User ID:</strong> Right-click a user → <em>Copy User ID</em>. Use as <code>{'<@USER_ID>'}</code>.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>3.</span><strong>Role ID:</strong> Right-click a role in <em>Server Settings → Roles</em> → <em>Copy Role ID</em>. Use as <code>{'<@&ROLE_ID>'}</code>.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#aaa' }}>4.</span>Use <code>@here</code> or <code>@everyone</code> to ping all online / all members in the channel.</li>
              </ul>
            </details>
            <div className="settings-input-row">
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

          <div className="notification-global-mention">
            <label>Global Ping Target</label>
            <input
              type="text"
              value={discordConfig.mention ?? ''}
              onChange={(event) => setDiscordConfig((prev) => ({ ...prev, mention: event.target.value }))}
              placeholder="@here, @everyone, <@USER_ID>, <@&ROLE_ID> — applies to all features without their own target"
            />
            {(discordConfig.mention ?? '').trim() && (
              <div className="notification-mention-triggers">
                <label className="notification-mention-trigger-label">
                  <input
                    type="checkbox"
                    checked={discordConfig.mention_on_error ?? true}
                    onChange={(event) => setDiscordConfig((prev) => ({ ...prev, mention_on_error: event.target.checked }))}
                  />
                  Ping on error
                </label>
                <label className="notification-mention-trigger-label">
                  <input
                    type="checkbox"
                    checked={discordConfig.mention_on_success ?? false}
                    onChange={(event) => setDiscordConfig((prev) => ({ ...prev, mention_on_success: event.target.checked }))}
                  />
                  Ping on success
                </label>
                <label className="notification-mention-trigger-label">
                  <input
                    type="checkbox"
                    checked={discordConfig.mention_on_info ?? false}
                    onChange={(event) => setDiscordConfig((prev) => ({ ...prev, mention_on_info: event.target.checked }))}
                  />
                  Ping on info
                </label>
              </div>
            )}
          </div>

          <div className="notification-section-divider" />

          <div className="notification-feature-list">
            {NOTIFICATION_FEATURE_ORDER.map((featureKey) => {
              const feature = discordConfig.features[featureKey]
              const featureWebhookVisible = showFeatureWebhooks[featureKey] ?? false
              const featureWebhookValue = feature?.webhook_url ?? ''
              return (
                <div className="notification-feature-item" key={featureKey}>
                  <div className="setting-item">
                    <div className="setting-info">
                      <label>{NOTIFICATION_FEATURE_LABELS[featureKey] || featureKey}</label>
                      {NOTIFICATION_FEATURE_EVENTS[featureKey] && (
                        <details className="feature-events-disclosure">
                          <summary className="feature-events-summary">What gets sent?</summary>
                          <ul className="feature-events-list">
                            {NOTIFICATION_FEATURE_EVENTS[featureKey].map((event, index) => (
                              <li key={index} className={`feature-event-item feature-event-${event.type}`}>
                                <span className="feature-event-icon">{event.type === 'success' ? '✓' : event.type === 'info' ? 'ℹ' : '✗'}</span>
                                {event.label}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
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
                  <div className="notification-feature-inputs-row">
                  <div className="notification-feature-webhook">
                    <div className="input-with-toggle">
                      <input
                        type={featureWebhookVisible ? 'text' : 'password'}
                        value={featureWebhookValue}
                        onChange={(event) => setDiscordConfig((prev) => ({
                          ...prev,
                          features: {
                            ...prev.features,
                            [featureKey]: {
                              ...prev.features[featureKey],
                              webhook_url: event.target.value,
                            },
                          },
                        }))}
                        placeholder="Override webhook URL (optional)"
                      />
                      <button
                        type="button"
                        className="toggle-visibility"
                        onClick={() => handleToggleFeatureWebhookVisibility(featureKey)}
                        title={featureWebhookVisible ? 'Hide' : 'Show'}
                        aria-label={featureWebhookVisible ? 'Hide feature webhook URL' : 'Show feature webhook URL'}
                      >
                        {featureWebhookVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </div>
                  <div className="notification-feature-mention">
                    <input
                      type="text"
                      value={feature?.mention ?? ''}
                      onChange={(event) => setDiscordConfig((prev) => ({
                        ...prev,
                        features: {
                          ...prev.features,
                          [featureKey]: {
                            ...prev.features[featureKey],
                            mention: event.target.value,
                          },
                        },
                      }))}
                      placeholder="Ping target: @here, @everyone, <@USER_ID>, <@&ROLE_ID>"
                    />
                    {(feature?.mention ?? '').trim() && (
                      <div className="notification-mention-triggers">
                        <label className="notification-mention-trigger-label">
                          <input
                            type="checkbox"
                            checked={feature?.mention_on_error ?? true}
                            onChange={(event) => setDiscordConfig((prev) => ({
                              ...prev,
                              features: {
                                ...prev.features,
                                [featureKey]: {
                                  ...prev.features[featureKey],
                                  mention_on_error: event.target.checked,
                                },
                              },
                            }))}
                          />
                          Ping on error
                        </label>
                        <label className="notification-mention-trigger-label">
                          <input
                            type="checkbox"
                            checked={feature?.mention_on_success ?? false}
                            onChange={(event) => setDiscordConfig((prev) => ({
                              ...prev,
                              features: {
                                ...prev.features,
                                [featureKey]: {
                                  ...prev.features[featureKey],
                                  mention_on_success: event.target.checked,
                                },
                              },
                            }))}
                          />
                          Ping on success
                        </label>
                        <label className="notification-mention-trigger-label">
                          <input
                            type="checkbox"
                            checked={feature?.mention_on_info ?? false}
                            onChange={(event) => setDiscordConfig((prev) => ({
                              ...prev,
                              features: {
                                ...prev.features,
                                [featureKey]: {
                                  ...prev.features[featureKey],
                                  mention_on_info: event.target.checked,
                                },
                              },
                            }))}
                          />
                          Ping on info
                        </label>
                      </div>
                    )}
                  </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        )}

        {notificationChannel === 'apprise' && (
        <div className="settings-section">
          <div className="settings-section-header">
            <div>
              <h2>Apprise Notifications</h2>
              <p className="setting-description">
                Send the same notifications through Apprise to Telegram, Pushover, ntfy, Gotify, Slack, email and many more services.
              </p>
            </div>
            <button
              type="button"
              className={`btn-save ${hasUnsavedApprise ? 'btn-unsaved' : ''}`}
              onClick={handleSaveAppriseNotifications}
              disabled={saving || !hasUnsavedApprise}
              title={hasUnsavedApprise ? 'Save changes' : 'No changes to save'}
            >
              {saving ? 'Saving...' : 'Save Apprise Settings'}
            </button>
          </div>

          <div className="setting-item">
            <div className="setting-info">
              <label>Enable Apprise Notifications</label>
              <p className="setting-description">
                Master switch for Apprise notifications across all selected features.
              </p>
            </div>
            <div className="setting-control">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={appriseConfig.enabled}
                  onChange={(event) => setAppriseConfig((prev) => ({
                    ...prev,
                    enabled: event.target.checked,
                  }))}
                />
                <span className="toggle-slider"></span>
              </label>
            </div>
          </div>

          <div className="notification-webhook-group">
            <label>Apprise URLs</label>
            <p className="notification-global-description">
              One URL per line. Every enabled feature below sends to these URLs unless it has its own list.
            </p>
            <details className="feature-events-disclosure global-events-disclosure">
              <summary className="feature-events-summary">How does it work?</summary>
              <ul className="feature-events-list">
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Apprise turns one URL into a delivery to one service. The URL names the service and carries its credentials.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span><span>Find the URL format for your service at <a href="https://appriseit.com/" target="_blank" rel="noreferrer">appriseit.com</a>.</span></li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Lines starting with <code>#</code> are ignored, so you can label your URLs.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span>Running the Apprise API server? Add <code>apprise://HOST:PORT/KEY</code> and manage targets there.</li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span><span>Tags route notifications. Prefix a URL with tags, for example <code>home, urgent = pover://USER_KEY@APP_TOKEN</code>.</span></li>
                <li className="feature-event-item"><span className="feature-event-icon" style={{ color: '#64b5f6' }}>•</span><span>A feature with tags only sends to URLs carrying one of them. A URL tagged <code>always</code> receives everything.</span></li>
              </ul>
            </details>
            <div className="settings-input-row">
              <div className="input-with-toggle">
                <textarea
                  ref={(element) => autoGrowTextarea(element)}
                  className={showAppriseUrls ? '' : 'password-textarea'}
                  value={appriseConfig.urls}
                  onChange={(event) => setAppriseConfig((prev) => ({
                    ...prev,
                    urls: event.target.value,
                  }))}
                  placeholder="tgram://BOT_TOKEN/CHAT_ID (one URL per line)"
                  rows={1}
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="toggle-visibility"
                  onClick={handleToggleAppriseUrlsVisibility}
                  title={showAppriseUrls ? 'Hide' : 'Show'}
                  aria-label={showAppriseUrls ? 'Hide Apprise URLs' : 'Show Apprise URLs'}
                >
                  {showAppriseUrls ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
              <button
                type="button"
                className="btn-secondary notification-test-inline"
                onClick={handleTestAppriseNotification}
                disabled={testingApprise || saving}
              >
                {testingApprise ? 'Testing...' : 'Test'}
              </button>
            </div>
          </div>

          <div className="notification-section-divider" />

          <div className="notification-feature-list">
            {NOTIFICATION_FEATURE_ORDER.map((featureKey) => {
              const feature = appriseConfig.features[featureKey]
              const featureUrlsVisible = showFeatureAppriseUrls[featureKey] ?? false
              return (
                <div className="notification-feature-item" key={featureKey}>
                  <div className="setting-item">
                    <div className="setting-info">
                      <label>{NOTIFICATION_FEATURE_LABELS[featureKey] || featureKey}</label>
                      {NOTIFICATION_FEATURE_EVENTS[featureKey] && (
                        <details className="feature-events-disclosure">
                          <summary className="feature-events-summary">What gets sent?</summary>
                          <ul className="feature-events-list">
                            {NOTIFICATION_FEATURE_EVENTS[featureKey].map((event, index) => (
                              <li key={index} className={`feature-event-item feature-event-${event.type}`}>
                                <span className="feature-event-icon">{event.type === 'success' ? '✓' : event.type === 'info' ? 'ℹ' : '✗'}</span>
                                {event.label}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </div>
                    <div className="setting-control">
                      <label className="toggle-switch">
                        <input
                          type="checkbox"
                          checked={feature.enabled}
                          onChange={(event) => updateAppriseFeature(featureKey, {
                            enabled: event.target.checked,
                            on_success: true,
                            on_error: true,
                            include_summary: true,
                            include_details: true,
                          })}
                        />
                        <span className="toggle-slider"></span>
                      </label>
                    </div>
                  </div>
                  <div className="notification-feature-inputs-row">
                    <div className="notification-feature-webhook">
                      <div className="input-with-toggle">
                        <textarea
                          ref={(element) => autoGrowTextarea(element)}
                          className={featureUrlsVisible ? '' : 'password-textarea'}
                          value={feature?.urls ?? ''}
                          onChange={(event) => updateAppriseFeature(featureKey, { urls: event.target.value })}
                          placeholder="Override Apprise URLs (optional, one per line)"
                          rows={1}
                          spellCheck={false}
                        />
                        <button
                          type="button"
                          className="toggle-visibility"
                          onClick={() => handleToggleFeatureAppriseUrlsVisibility(featureKey)}
                          title={featureUrlsVisible ? 'Hide' : 'Show'}
                          aria-label={featureUrlsVisible ? 'Hide feature Apprise URLs' : 'Show feature Apprise URLs'}
                        >
                          {featureUrlsVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                    </div>
                    <div className="notification-feature-mention">
                      <input
                        type="text"
                        value={feature?.tags ?? ''}
                        onChange={(event) => updateAppriseFeature(featureKey, { tags: event.target.value })}
                        placeholder="Tags: home, urgent (only URLs with one of these)"
                        spellCheck={false}
                      />
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        )}
        </>
      )}

      {activeTab === 'media' && (
        <SettingsMediaSection
          mediaSettings={mediaSettings}
          mediaServerMediaSourceEnabled={mediaServerMediaSourceEnabled}
          mediaServerMediaSourceAuto={mediaServerMediaSourceAuto}
          onToggleMediaServerMediaSource={toggleMediaServerMediaSource}
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
          appTimezone={appTimezone}
          effectiveTimezone={effectiveTimezone}
          onSaveAppTimezone={handleSaveAppTimezone}
          saving={saving}
        />
      )}

      <ScheduleEditModal
        editingSchedule={editingSchedule}
        drives={drives}
        scheduleSaving={scheduleSaving}
        effectiveTimezone={effectiveTimezone}
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
          showToast={showToast}
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

      <ConfirmDialog
        isOpen={removeApiKey !== null}
        title={`Remove ${removeApiKeyLabel} API Key`}
        message={`Remove the saved ${removeApiKeyLabel} API key${removeApiKey === 'tvdb' ? ' and PIN' : ''}? Features that use it stop working until you add a key again.`}
        confirmText="Remove"
        cancelText="Cancel"
        variant="danger"
        onConfirm={handleConfirmRemoveApiKey}
        onCancel={() => setRemoveApiKey(null)}
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