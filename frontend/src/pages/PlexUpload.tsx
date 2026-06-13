import { type MouseEvent, useEffect, useMemo, useRef, useState } from 'react'
import { Settings2, UploadCloud, RefreshCw, Webhook, Search } from 'lucide-react'
import {
  getApiErrorMessage,
  getJob,
  getJobs,
  Job,
  runPlexUpload,
  getPlexManualSettings,
  savePlexManualSettings,
  getPlexWebhookSettings,
  getPlexWebhookStats,
  resetPlexWebhookStats,
  clearPlexWebhookDedupe,
  getPlexWebhookDedupeEntries,
  savePlexWebhookSettings,
  PlexWebhookStats,
  PlexWebhookDedupeEntry,
  PlexUploadLibraryConfig,
  PlexUploadCacheSummary,
  getPlexUploadCache,
  getPlexUploadCacheEntries,
  clearPlexUploadCache,
  downloadPlexUploadCacheExport,
  getPlexUploadLibraryOverrideSettings,
  savePlexUploadLibraryOverrideSettings,
  getPlexUploadInstanceMap,
  savePlexUploadInstanceMap,
  PlexUploadInstanceMap,
  getSettings,
  PlexSearchItem,
  runPlexSingleUpload,
  searchPlexItems,
  getPlexWebhookToken,
  regeneratePlexWebhookToken,
} from '../api/client'
import { useToast } from '../components/Toast'
import UnsavedChangesModal from '../components/poster-manager/UnsavedChangesModal'
import './PlexUpload.css'


type PlexUploadTab = 'manual' | 'settings'
const PLEX_UPLOAD_TAB_STORAGE_KEY = 'posterflow.plexUpload.activeTab'

const isPlexUploadTab = (value: string): value is PlexUploadTab => {
  return ['manual', 'settings'].includes(value)
}

type ManualSettingsSnapshot = {
  dry_run: boolean
  reapply: boolean
  remove_overlay_label: boolean
  sync_before_upload: boolean
  rename_before_upload: boolean
  border_before_upload: boolean
  upload_delay_ms: number
}

type AutomationSettingsSnapshot = {
  webhook_enabled: boolean
  webhook_remove_overlay_label: boolean
  webhook_rename_then_upload: boolean
  webhook_adopt_existing_processed: boolean
  webhook_retry_attempts: number
  webhook_retry_delay_seconds: number
  webhook_upload_delay_ms: number
  override_enabled: boolean
  override_configs: PlexUploadLibraryConfig[]
}

const normalizeOverrideConfigs = (configs: PlexUploadLibraryConfig[]): PlexUploadLibraryConfig[] => {
  return (configs || [])
    .map((config) => ({
      instance_name: String((config as { instance_name?: unknown }).instance_name ?? ''),
      libraries: [...(config?.libraries || [])]
        .map((library, index) => {
          const normalizedKey = String((library as { key?: unknown }).key ?? (library as { title?: unknown }).title ?? `library-${index}`)
          return {
            ...library,
            title: String((library as { title?: unknown }).title ?? normalizedKey),
            key: normalizedKey,
            type: String((library as { type?: unknown }).type ?? ''),
            enabled: Boolean(library.enabled),
          }
        })
        .sort((left, right) => String(left.key).localeCompare(String(right.key))),
    }))
    .sort((left, right) => String(left.instance_name).localeCompare(String(right.instance_name)))
}

const createManualSnapshot = (dryRun: boolean, reapply: boolean, removeOverlayLabel: boolean, syncBeforeUpload: boolean, renameBeforeUpload: boolean, borderBeforeUpload: boolean, uploadDelayMs: number): ManualSettingsSnapshot => ({
  dry_run: Boolean(dryRun),
  reapply: Boolean(reapply),
  remove_overlay_label: Boolean(removeOverlayLabel),
  sync_before_upload: Boolean(syncBeforeUpload),
  rename_before_upload: Boolean(renameBeforeUpload),
  border_before_upload: Boolean(borderBeforeUpload),
  upload_delay_ms: Number(uploadDelayMs) || 50,
})

const createAutomationSnapshot = (
  webhookEnabled: boolean,
  webhookRemoveOverlayLabel: boolean,
  webhookRenameThenUpload: boolean,
  webhookAdoptExistingProcessed: boolean,
  webhookRetryAttempts: number,
  webhookRetryDelaySeconds: number,
  webhookUploadDelayMs: number,
  overrideEnabled: boolean,
  overrideConfigs: PlexUploadLibraryConfig[],
): AutomationSettingsSnapshot => ({
  webhook_enabled: Boolean(webhookEnabled),
  webhook_remove_overlay_label: Boolean(webhookRemoveOverlayLabel),
  webhook_rename_then_upload: Boolean(webhookRenameThenUpload),
  webhook_adopt_existing_processed: Boolean(webhookAdoptExistingProcessed),
  webhook_retry_attempts: Number(webhookRetryAttempts) || 10,
  webhook_retry_delay_seconds: Number(webhookRetryDelaySeconds) || 30,
  webhook_upload_delay_ms: Number(webhookUploadDelayMs) || 50,
  override_enabled: Boolean(overrideEnabled),
  override_configs: normalizeOverrideConfigs(overrideConfigs),
})

function PlexUpload() {
  const SEARCH_DEBOUNCE_MS = 250
  const CACHE_BROWSER_PAGE_SIZE = 25
  const [activeTab, setActiveTab] = useState<PlexUploadTab>(() => {
    const savedTab = localStorage.getItem(PLEX_UPLOAD_TAB_STORAGE_KEY)
    if (savedTab && isPlexUploadTab(savedTab)) {
      return savedTab
    }
    return 'settings'
  })
  const [manualSettingsReady, setManualSettingsReady] = useState(false)
  const [toggleSettingsReady, setToggleSettingsReady] = useState(false)
  const [dryRun, setDryRun] = useState(true)
  const [reapply, setReapply] = useState(false)
  const [removeOverlayLabel, setRemoveOverlayLabel] = useState(false)
  const [syncBeforeUpload, setSyncBeforeUpload] = useState(false)
  const [renameBeforeUpload, setRenameBeforeUpload] = useState(true)
  const [borderBeforeUpload, setBorderBeforeUpload] = useState(false)
  const [manualUploadDelayMs, setManualUploadDelayMs] = useState(50)
  const [showSetupGuide, setShowSetupGuide] = useState(false)
  const [manualSettingsLoading, setManualSettingsLoading] = useState(false)
  const [manualSettingsSaving, setManualSettingsSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [lastJob, setLastJob] = useState<Job | null>(null)
  const [webhookLoading, setWebhookLoading] = useState(false)
  const [webhookSaving, setWebhookSaving] = useState(false)
  const [webhookEnabled, setWebhookEnabled] = useState(true)
  const [webhookRemoveOverlayLabel, setWebhookRemoveOverlayLabel] = useState(true)
  const [webhookRenameThenUpload, setWebhookRenameThenUpload] = useState(true)
  const [webhookAdoptExistingProcessed, setWebhookAdoptExistingProcessed] = useState(true)
  const [webhookRetryAttempts, setWebhookRetryAttempts] = useState(10)
  const [webhookRetryDelaySeconds, setWebhookRetryDelaySeconds] = useState(30)
  const [webhookUploadDelayMs, setWebhookUploadDelayMs] = useState(50)
  const [webhookStatsLoading, setWebhookStatsLoading] = useState(false)
  const [webhookStatsResetting, setWebhookStatsResetting] = useState(false)
  const [webhookDedupeClearing, setWebhookDedupeClearing] = useState(false)
  const [webhookDedupeMediaType, setWebhookDedupeMediaType] = useState<'movie' | 'series'>('movie')
  const [webhookDedupeTitle, setWebhookDedupeTitle] = useState('')
  const [webhookDedupeYear, setWebhookDedupeYear] = useState('')
  const [webhookDedupeSeason, setWebhookDedupeSeason] = useState('')
  const [webhookDedupeSearchLoading, setWebhookDedupeSearchLoading] = useState(false)
  const [webhookDedupeSearchResults, setWebhookDedupeSearchResults] = useState<PlexWebhookDedupeEntry[]>([])
  const [webhookStats, setWebhookStats] = useState<PlexWebhookStats | null>(null)
  const suppressNextWebhookDedupeSearchRef = useRef(false)
  const [overrideLoading, setOverrideLoading] = useState(false)
  const [overrideSaving, setOverrideSaving] = useState(false)
  const [overrideEnabled, setOverrideEnabled] = useState(false)
  const [globalLibraryConfigs, setGlobalLibraryConfigs] = useState<PlexUploadLibraryConfig[]>([])
  const [overrideConfigs, setOverrideConfigs] = useState<PlexUploadLibraryConfig[]>([])
  const [instanceMap, setInstanceMap] = useState<PlexUploadInstanceMap>({})
  const [arrInstanceNames, setArrInstanceNames] = useState<string[]>([])
  const [instanceMapLoading, setInstanceMapLoading] = useState(false)
  const [instanceMapSaving, setInstanceMapSaving] = useState(false)
  const [cacheLoading, setCacheLoading] = useState(false)
  const [cacheClearing, setCacheClearing] = useState(false)
  const [uploadCache, setUploadCache] = useState<PlexUploadCacheSummary | null>(null)
  const [showCacheBrowser, setShowCacheBrowser] = useState(false)
  const [showLibraryModal, setShowLibraryModal] = useState(false)
  const [cacheBrowserQuery, setCacheBrowserQuery] = useState('')
  const [cacheBrowserEntries, setCacheBrowserEntries] = useState<PlexUploadCacheSummary['entries']>([])
  const [cacheBrowserTotal, setCacheBrowserTotal] = useState(0)
  const [cacheBrowserOffset, setCacheBrowserOffset] = useState(0)
  const [cacheBrowserHasMore, setCacheBrowserHasMore] = useState(false)
  const [cacheBrowserLoading, setCacheBrowserLoading] = useState(false)
  const [sourceQuery, setSourceQuery] = useState('')
  const [sourceSearching, setSourceSearching] = useState(false)
  const [sourceResults, setSourceResults] = useState<PlexSearchItem[]>([])
  const [sourceHasSearched, setSourceHasSearched] = useState(false)
  const [singleUploadLoading, setSingleUploadLoading] = useState(false)
  const [selectedSourcePoster, setSelectedSourcePoster] = useState<PlexSearchItem | null>(null)
  const [hasUnsavedManualSettings, setHasUnsavedManualSettings] = useState(false)
  const [hasUnsavedAutomationSettings, setHasUnsavedAutomationSettings] = useState(false)
  const [showUnsavedModal, setShowUnsavedModal] = useState(false)
  const [pendingTabChange, setPendingTabChange] = useState<PlexUploadTab | null>(null)
  const singleSourceSearchInputRef = useRef<HTMLInputElement | null>(null)
  const manualBaselineRef = useRef<ManualSettingsSnapshot>(createManualSnapshot(dryRun, reapply, removeOverlayLabel, syncBeforeUpload, renameBeforeUpload, borderBeforeUpload, manualUploadDelayMs))
  const automationBaselineRef = useRef<AutomationSettingsSnapshot>(
    createAutomationSnapshot(
      webhookEnabled,
      webhookRemoveOverlayLabel,
      webhookRenameThenUpload,
      webhookAdoptExistingProcessed,
      webhookRetryAttempts,
      webhookRetryDelaySeconds,
      webhookUploadDelayMs,
      overrideEnabled,
      overrideConfigs,
    ),
  )
  const { showToast } = useToast()
  const [webhookToken, setWebhookToken] = useState<string>('')
  const [passwordSet, setPasswordSet] = useState(false)
  const [regeneratingToken, setRegeneratingToken] = useState(false)

  useEffect(() => {
    localStorage.setItem(PLEX_UPLOAD_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  useEffect(() => {
    let cancelled = false
    getPlexWebhookToken()
      .then((res) => {
        if (!cancelled) {
          setWebhookToken(res.token)
          setPasswordSet(res.password_set)
        }
      })
      .catch((error) => {
        console.error('Failed to load webhook token:', error)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const sourceMatchCount = useMemo(() => sourceResults.length, [sourceResults])

  const isJobActive = useMemo(() => {
    if (!lastJob) {
      return false
    }
    return lastJob.status === 'pending' || lastJob.status === 'running'
  }, [lastJob])

  const webhookPath = '/api/posterflow/plex-upload/webhook'

  const formatWebhookTimestamp = (value: string | null): string => {
    if (!value) {
      return '—'
    }

    const normalized = value.replace(/(\.\d{3})\d+(?=(Z|[+-]\d{2}:\d{2})$)/, '$1')
    const parsed = new Date(normalized)
    if (Number.isNaN(parsed.getTime())) {
      return value
    }

    const month = String(parsed.getMonth() + 1).padStart(2, '0')
    const day = String(parsed.getDate()).padStart(2, '0')
    const year = parsed.getFullYear()
    const hours24 = parsed.getHours()
    const minutes = String(parsed.getMinutes()).padStart(2, '0')
    const amPm = hours24 >= 12 ? 'PM' : 'AM'
    const hours12 = String((hours24 % 12) || 12).padStart(2, '0')

    return `${month}/${day}/${year} ${hours12}:${minutes} ${amPm}`
  }

  const webhookUrl = useMemo(() => {
    if (typeof window === 'undefined') {
      return webhookPath
    }
    return `${window.location.origin}${webhookPath}`
  }, [])

  // When an app password is set, inbound webhooks must carry the token in the
  // URL (the password's Bearer header can't be sent by Radarr/Sonarr).
  const tokenizedWebhookUrl = useMemo(() => {
    if (!webhookToken) {
      return webhookUrl
    }
    return `${webhookUrl}?token=${encodeURIComponent(webhookToken)}`
  }, [webhookUrl, webhookToken])

  // The URL the user should paste into Radarr/Sonarr: tokenized when a password
  // is set, plain otherwise.
  const effectiveWebhookUrl = passwordSet ? tokenizedWebhookUrl : webhookUrl

  const copyWebhookUrl = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url)
      showToast('Webhook URL copied to clipboard')
    } catch {
      // Fallback for non-secure (HTTP) contexts where Clipboard API is unavailable
      try {
        const textArea = document.createElement('textarea')
        textArea.value = url
        textArea.style.position = 'fixed'
        textArea.style.left = '-9999px'
        textArea.style.top = '-9999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        // Cast through unknown to avoid deprecated type annotation on execCommand
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(textArea)
        showToast('Webhook URL copied to clipboard')
      } catch (fallbackError) {
        console.error('Failed to copy webhook URL:', fallbackError)
        showToast('Could not copy — copy the URL manually', 'error')
      }
    }
  }

  const handleRegenerateToken = async () => {
    const confirmed = window.confirm(
      'Generate a new webhook token? Any Radarr/Sonarr webhook URLs using the current token will stop working until you update them with the new URL.',
    )
    if (!confirmed) {
      return
    }
    try {
      setRegeneratingToken(true)
      const res = await regeneratePlexWebhookToken()
      setWebhookToken(res.token)
      setPasswordSet(res.password_set)
      showToast('Webhook token regenerated — update your Radarr/Sonarr URLs')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to regenerate webhook token'), 'error')
    } finally {
      setRegeneratingToken(false)
    }
  }

  const fetchLatestPlexJob = async (showLoading: boolean = true) => {
    if (showLoading) {
      setRefreshing(true)
    }

    try {
      const jobs = await getJobs()
      const latest = jobs.find((job) => job.job_type === 'Plex Upload') || null
      setLastJob(latest)
    } catch (error) {
      console.error('Failed to fetch plex upload job status:', error)
      showToast(getApiErrorMessage(error, 'Failed to load Plex upload status'), 'error')
    } finally {
      if (showLoading) {
        setRefreshing(false)
      }
    }
  }

  const runUpload = async () => {
    setLoading(true)
    try {
      const response = await runPlexUpload({
        dry_run: dryRun,
        reapply,
        remove_overlay_label: removeOverlayLabel,
        sync_before_upload: syncBeforeUpload,
        rename_before_upload: renameBeforeUpload,
        border_before_upload: borderBeforeUpload,
      })
      showToast(response.message || 'Plex upload job queued', 'success')

      const job = await getJob(response.job_id)
      setLastJob(job)
    } catch (error) {
      console.error('Failed to start Plex upload:', error)
      showToast(getApiErrorMessage(error, 'Failed to start Plex upload'), 'error')
    } finally {
      setLoading(false)
    }
  }

  const runSourceSearch = async (queryOverride?: string) => {
    const shouldPreserveInputFocus = document.activeElement === singleSourceSearchInputRef.current
    const query = (queryOverride ?? sourceQuery).trim()
    if (!query) {
      setSourceHasSearched(true)
      setSourceResults([])
      setSelectedSourcePoster(null)
      if (shouldPreserveInputFocus) {
        requestAnimationFrame(() => {
          singleSourceSearchInputRef.current?.focus({ preventScroll: true })
        })
      }
      return
    }

    setSourceSearching(true)
    setSourceHasSearched(true)

    try {
      const response = await searchPlexItems(query)
      setSourceResults(response.items || [])
      setSelectedSourcePoster(null)
    } catch (error) {
      console.error('Failed to search Plex:', error)
      showToast(getApiErrorMessage(error, 'Failed to search Plex'), 'error')
    } finally {
      setSourceSearching(false)
      if (shouldPreserveInputFocus) {
        requestAnimationFrame(() => {
          singleSourceSearchInputRef.current?.focus({ preventScroll: true })
        })
      }
    }
  }

  const runSingleUpload = async () => {
    if (!selectedSourcePoster) {
      showToast('Select a Plex item first', 'error')
      return
    }

    setSingleUploadLoading(true)
    try {
      const response = await runPlexSingleUpload({
        media_type: selectedSourcePoster.media_type,
        title: selectedSourcePoster.title,
        year: selectedSourcePoster.year,
        season_number: null,
        tmdb_id: selectedSourcePoster.tmdb_id,
        tvdb_id: selectedSourcePoster.tvdb_id,
        imdb_id: selectedSourcePoster.imdb_id,
        plex_rating_key: selectedSourcePoster.plex_rating_key,
        dry_run: dryRun,
        reapply,
        remove_overlay_label: removeOverlayLabel,
        rename_before_upload: renameBeforeUpload,
      })
      showToast(response.message || 'Single Plex upload job queued', 'success')

      const job = await getJob(response.job_id)
      setLastJob(job)
    } catch (error) {
      console.error('Failed to start single Plex upload:', error)
      showToast(getApiErrorMessage(error, 'Failed to start single Plex upload'), 'error')
    } finally {
      setSingleUploadLoading(false)
    }
  }

  const fetchWebhookSettings = async () => {
    setWebhookLoading(true)
    try {
      const settings = await getPlexWebhookSettings()
      const nextWebhookEnabled = Boolean(settings.enabled)
      const nextWebhookRemoveOverlayLabel = Boolean(settings.remove_overlay_label)
      const nextWebhookRenameThenUpload = Boolean(settings.rename_then_upload)
      const nextWebhookAdoptExistingProcessed = Boolean(settings.adopt_existing_processed)
      const nextWebhookRetryAttempts = Number(settings.retry_attempts) || 10
      const nextWebhookRetryDelaySeconds = Number(settings.retry_delay_seconds) || 30
      const nextWebhookUploadDelayMs = Number(settings.upload_delay_ms) || 50

      setWebhookEnabled(nextWebhookEnabled)
      setWebhookRemoveOverlayLabel(nextWebhookRemoveOverlayLabel)
      setWebhookRenameThenUpload(nextWebhookRenameThenUpload)
      setWebhookAdoptExistingProcessed(nextWebhookAdoptExistingProcessed)
      setWebhookRetryAttempts(nextWebhookRetryAttempts)
      setWebhookRetryDelaySeconds(nextWebhookRetryDelaySeconds)
      setWebhookUploadDelayMs(nextWebhookUploadDelayMs)

      automationBaselineRef.current = createAutomationSnapshot(
        nextWebhookEnabled,
        nextWebhookRemoveOverlayLabel,
        nextWebhookRenameThenUpload,
        nextWebhookAdoptExistingProcessed,
        nextWebhookRetryAttempts,
        nextWebhookRetryDelaySeconds,
        nextWebhookUploadDelayMs,
        automationBaselineRef.current.override_enabled,
        automationBaselineRef.current.override_configs,
      )
    } catch (error) {
      console.error('Failed to load webhook settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to load webhook settings'), 'error')
    } finally {
      setWebhookLoading(false)
    }
  }

  const fetchManualSettings = async () => {
    setManualSettingsLoading(true)
    try {
      const settings = await getPlexManualSettings()
      const nextSnapshot = createManualSnapshot(
        Boolean(settings.dry_run),
        Boolean(settings.reapply),
        Boolean(settings.remove_overlay_label),
        Boolean(settings.sync_before_upload ?? false),
        Boolean(settings.rename_before_upload ?? true),
        Boolean(settings.border_before_upload ?? false),
        Number(settings.upload_delay_ms) || 50,
      )

      setDryRun(nextSnapshot.dry_run)
      setReapply(nextSnapshot.reapply)
      setRemoveOverlayLabel(nextSnapshot.remove_overlay_label)
      setSyncBeforeUpload(nextSnapshot.sync_before_upload)
      setRenameBeforeUpload(nextSnapshot.rename_before_upload)
      setBorderBeforeUpload(nextSnapshot.border_before_upload)
      setManualUploadDelayMs(nextSnapshot.upload_delay_ms)
      manualBaselineRef.current = nextSnapshot
    } catch (error) {
      console.error('Failed to load manual Plex Upload settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to load manual settings'), 'error')
    } finally {
      setManualSettingsLoading(false)
      setManualSettingsReady(true)
    }
  }

  const saveManualSettings = async () => {
    setManualSettingsSaving(true)
    try {
      const response = await savePlexManualSettings({
        dry_run: dryRun,
        reapply,
        remove_overlay_label: removeOverlayLabel,
        sync_before_upload: syncBeforeUpload,
        rename_before_upload: renameBeforeUpload,
        border_before_upload: borderBeforeUpload,
        upload_delay_ms: manualUploadDelayMs,
      })

      const nextSnapshot = createManualSnapshot(
        Boolean(response.dry_run),
        Boolean(response.reapply),
        Boolean(response.remove_overlay_label),
        Boolean(response.sync_before_upload ?? false),
        Boolean(response.rename_before_upload ?? true),
        Boolean(response.border_before_upload ?? false),
        Number(response.upload_delay_ms) || 50,
      )

      setDryRun(nextSnapshot.dry_run)
      setReapply(nextSnapshot.reapply)
      setRemoveOverlayLabel(nextSnapshot.remove_overlay_label)
      setSyncBeforeUpload(nextSnapshot.sync_before_upload)
      setRenameBeforeUpload(nextSnapshot.rename_before_upload)
      setBorderBeforeUpload(nextSnapshot.border_before_upload)
      setManualUploadDelayMs(nextSnapshot.upload_delay_ms)
      manualBaselineRef.current = nextSnapshot
      setHasUnsavedManualSettings(false)
      showToast('Manual run settings saved', 'success')
    } catch (error) {
      console.error('Failed to save manual Plex Upload settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to save manual settings'), 'error')
    } finally {
      setManualSettingsSaving(false)
    }
  }

  const fetchWebhookStats = async (showLoading: boolean = true) => {
    if (showLoading) {
      setWebhookStatsLoading(true)
    }
    try {
      const stats = await getPlexWebhookStats()
      setWebhookStats(stats)
    } catch (error) {
      console.error('Failed to load webhook stats:', error)
      showToast(getApiErrorMessage(error, 'Failed to load webhook stats'), 'error')
    } finally {
      if (showLoading) {
        setWebhookStatsLoading(false)
      }
    }
  }

  const resetWebhookStats = async () => {
    setWebhookStatsResetting(true)
    try {
      const response = await resetPlexWebhookStats()
      setWebhookStats(response)
      showToast('Webhook stats reset', 'success')
    } catch (error) {
      console.error('Failed to reset webhook stats:', error)
      showToast(getApiErrorMessage(error, 'Failed to reset webhook stats'), 'error')
    } finally {
      setWebhookStatsResetting(false)
    }
  }

  const clearWebhookDedupeForItem = async () => {
    const title = webhookDedupeTitle.trim()
    if (!title) {
      showToast('Title is required to clear a duplicate lock', 'error')
      return
    }

    const yearTrimmed = webhookDedupeYear.trim()
    const seasonTrimmed = webhookDedupeSeason.trim()

    let parsedYear: number | undefined
    let parsedSeason: number | undefined

    if (yearTrimmed) {
      const nextYear = Number(yearTrimmed)
      if (!Number.isInteger(nextYear) || nextYear < 0) {
        showToast('Year must be a whole number', 'error')
        return
      }
      parsedYear = nextYear
    }

    if (webhookDedupeMediaType === 'series' && seasonTrimmed) {
      const nextSeason = Number(seasonTrimmed)
      if (!Number.isInteger(nextSeason) || nextSeason < 0) {
        showToast('Season must be a whole number', 'error')
        return
      }
      parsedSeason = nextSeason
    }

    setWebhookDedupeClearing(true)
    try {
      const response = await clearPlexWebhookDedupe({
        media_type: webhookDedupeMediaType,
        title,
        year: parsedYear,
        season_number: webhookDedupeMediaType === 'series' ? parsedSeason : undefined,
      })
      showToast(`Cleared duplicate lock entries: ${response.removed}`, 'success')
      fetchWebhookStats(false)
    } catch (error) {
      console.error('Failed to clear webhook dedupe cache:', error)
      showToast(getApiErrorMessage(error, 'Failed to clear duplicate lock'), 'error')
    } finally {
      setWebhookDedupeClearing(false)
    }
  }

  const cloneLibraryConfigs = (configs: PlexUploadLibraryConfig[]): PlexUploadLibraryConfig[] => {
    return configs.map((config) => ({
      instance_name: config.instance_name,
      libraries: (config.libraries || []).map((library) => ({ ...library })),
    }))
  }

  const buildInitialOverrideConfigs = (
    globalConfigs: PlexUploadLibraryConfig[],
    existingOverrideConfigs: PlexUploadLibraryConfig[],
  ): PlexUploadLibraryConfig[] => {
    if (existingOverrideConfigs.length > 0) {
      return cloneLibraryConfigs(existingOverrideConfigs)
    }

    return globalConfigs.map((config) => ({
      instance_name: config.instance_name,
      libraries: (config.libraries || []).map((library) => ({
        ...library,
        enabled: false,
      })),
    }))
  }

  const fetchLibraryOverrideSettings = async () => {
    setOverrideLoading(true)
    try {
      const data = await getPlexUploadLibraryOverrideSettings()
      const globalConfigs = normalizeOverrideConfigs(data.global_configs || [])
      const existingOverrideConfigs = normalizeOverrideConfigs(data.configs || [])

      const nextOverrideEnabled = Boolean(data.enabled)
      const nextOverrideConfigs = buildInitialOverrideConfigs(globalConfigs, existingOverrideConfigs)

      setOverrideEnabled(nextOverrideEnabled)
      setGlobalLibraryConfigs(globalConfigs)
      setOverrideConfigs(nextOverrideConfigs)

      automationBaselineRef.current = createAutomationSnapshot(
        automationBaselineRef.current.webhook_enabled,
        automationBaselineRef.current.webhook_remove_overlay_label,
        automationBaselineRef.current.webhook_rename_then_upload,
        automationBaselineRef.current.webhook_adopt_existing_processed,
        automationBaselineRef.current.webhook_retry_attempts,
        automationBaselineRef.current.webhook_retry_delay_seconds,
        automationBaselineRef.current.webhook_upload_delay_ms,
        nextOverrideEnabled,
        nextOverrideConfigs,
      )
    } catch (error) {
      console.error('Failed to load Plex Upload library override settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to load Plex Upload library override settings'), 'error')
    } finally {
      setOverrideLoading(false)
    }
  }

  const toggleOverrideLibrary = (instanceName: string, libraryKey: string, enabled: boolean) => {
    setOverrideConfigs((current) => {
      const instanceFromGlobal = globalLibraryConfigs.find((config) => config.instance_name === instanceName)
      if (!instanceFromGlobal) {
        return current
      }

      const existing = current.find((config) => config.instance_name === instanceName)
      const existingByKey = new Map((existing?.libraries || []).map((library) => [library.key, library.enabled]))

      const nextInstance: PlexUploadLibraryConfig = {
        instance_name: instanceName,
        libraries: (instanceFromGlobal.libraries || []).map((library) => ({
          ...library,
          enabled: library.key === libraryKey ? enabled : Boolean(existingByKey.get(library.key)),
        })),
      }

      const withoutInstance = current.filter((config) => config.instance_name !== instanceName)
      return [...withoutInstance, nextInstance]
    })
  }

  const isOverrideLibraryChecked = (instanceName: string, libraryKey: string): boolean => {
    const instance = overrideConfigs.find((config) => config.instance_name === instanceName)
    if (!instance) {
      return false
    }
    return Boolean(instance.libraries.find((library) => library.key === libraryKey)?.enabled)
  }

  const getEnabledOverrideCount = (): number => {
    return overrideConfigs.reduce((count, config) => {
      const enabledLibraries = (config.libraries || []).filter((library) => library.enabled)
      return count + enabledLibraries.length
    }, 0)
  }

  const saveLibraryOverrideSettings = async () => {
    setOverrideSaving(true)
    try {
      const response = await savePlexUploadLibraryOverrideSettings({
        enabled: overrideEnabled,
        configs: overrideConfigs,
      })

      const nextOverrideEnabled = Boolean(response.enabled)
      const nextGlobalConfigs = normalizeOverrideConfigs(response.global_configs || [])
      const nextOverrideConfigs = buildInitialOverrideConfigs(nextGlobalConfigs, normalizeOverrideConfigs(response.configs || []))

      setOverrideEnabled(nextOverrideEnabled)
      setGlobalLibraryConfigs(nextGlobalConfigs)
      setOverrideConfigs(nextOverrideConfigs)

      automationBaselineRef.current = createAutomationSnapshot(
        automationBaselineRef.current.webhook_enabled,
        automationBaselineRef.current.webhook_remove_overlay_label,
        automationBaselineRef.current.webhook_rename_then_upload,
        automationBaselineRef.current.webhook_adopt_existing_processed,
        automationBaselineRef.current.webhook_retry_attempts,
        automationBaselineRef.current.webhook_retry_delay_seconds,
        automationBaselineRef.current.webhook_upload_delay_ms,
        nextOverrideEnabled,
        nextOverrideConfigs,
      )
      setHasUnsavedAutomationSettings(false)
      showToast('Plex Upload library override settings saved', 'success')
    } catch (error) {
      console.error('Failed to save Plex Upload library override settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to save Plex Upload library override settings'), 'error')
    } finally {
      setOverrideSaving(false)
    }
  }

  const parseArrInstanceNames = (settings: Record<string, string>): string[] => {
    const names: string[] = []
    for (const key of ['radarr_instances', 'sonarr_instances']) {
      const raw = settings[key]
      if (!raw) {
        continue
      }
      try {
        const parsed = JSON.parse(raw)
        if (Array.isArray(parsed)) {
          for (const entry of parsed) {
            const name = String((entry as { name?: unknown })?.name ?? '').trim()
            if (name) {
              names.push(name)
            }
          }
        }
      } catch {
        // Ignore malformed instance settings — the map editor simply shows fewer rows.
      }
    }
    return Array.from(new Set(names))
  }

  const fetchInstanceRoutingMap = async () => {
    setInstanceMapLoading(true)
    try {
      const [mapResponse, settings] = await Promise.all([getPlexUploadInstanceMap(), getSettings()])
      setInstanceMap(mapResponse.map || {})
      setArrInstanceNames(parseArrInstanceNames(settings))
    } catch (error) {
      console.error('Failed to load Plex Upload instance routing map:', error)
      showToast(getApiErrorMessage(error, 'Failed to load instance routing map'), 'error')
    } finally {
      setInstanceMapLoading(false)
    }
  }

  const isInstanceLibraryChecked = (instanceName: string, plexInstance: string, libraryKey: string): boolean => {
    return (instanceMap[instanceName] || []).some(
      (entry) => entry.plex_instance === plexInstance && entry.library_key === libraryKey,
    )
  }

  const toggleInstanceLibrary = (
    instanceName: string,
    plexInstance: string,
    libraryKey: string,
    enabled: boolean,
  ) => {
    setInstanceMap((current) => {
      const existing = current[instanceName] || []
      const filtered = existing.filter(
        (entry) => !(entry.plex_instance === plexInstance && entry.library_key === libraryKey),
      )
      const nextEntries = enabled ? [...filtered, { plex_instance: plexInstance, library_key: libraryKey }] : filtered
      const next = { ...current }
      if (nextEntries.length > 0) {
        next[instanceName] = nextEntries
      } else {
        delete next[instanceName]
      }
      return next
    })
  }

  const saveInstanceRoutingMap = async () => {
    setInstanceMapSaving(true)
    try {
      const response = await savePlexUploadInstanceMap(instanceMap)
      setInstanceMap(response.map || {})
      showToast('Instance → library routing saved', 'success')
    } catch (error) {
      console.error('Failed to save Plex Upload instance routing map:', error)
      showToast(getApiErrorMessage(error, 'Failed to save instance routing map'), 'error')
    } finally {
      setInstanceMapSaving(false)
    }
  }

  // The per-instance webhook URL adds an &instance= token so each Radarr/Sonarr
  // instance is attributed even when its payload instanceName doesn't match config.
  const instanceWebhookUrl = (instanceName: string): string => {
    const separator = effectiveWebhookUrl.includes('?') ? '&' : '?'
    return `${effectiveWebhookUrl}${separator}instance=${encodeURIComponent(instanceName)}`
  }

  // Resolve a (plex instance, library key) pair to its human-readable library title
  // for the compact routing summary shown on the page.
  const libraryTitleFor = (plexInstance: string, libraryKey: string): string => {
    const instanceConfig = globalLibraryConfigs.find((config) => config.instance_name === plexInstance)
    const library = (instanceConfig?.libraries || []).find((lib) => lib.key === libraryKey)
    return library?.title || libraryKey
  }

  // One-line summary of where an instance routes, for the collapsed page card.
  const instanceRoutingSummary = (instanceName: string): string => {
    const entries = instanceMap[instanceName] || []
    if (entries.length === 0) {
      return 'all selected libraries (default)'
    }
    return entries.map((entry) => libraryTitleFor(entry.plex_instance, entry.library_key)).join(', ')
  }

  const libraryModalSnapshotRef = useRef<{
    overrideEnabled: boolean
    overrideConfigs: PlexUploadLibraryConfig[]
    instanceMap: PlexUploadInstanceMap
  } | null>(null)

  const openLibraryModal = () => {
    libraryModalSnapshotRef.current = {
      overrideEnabled,
      overrideConfigs: JSON.parse(JSON.stringify(overrideConfigs)),
      instanceMap: JSON.parse(JSON.stringify(instanceMap)),
    }
    setShowLibraryModal(true)
  }

  const cancelLibraryModal = () => {
    const snapshot = libraryModalSnapshotRef.current
    if (snapshot) {
      setOverrideEnabled(snapshot.overrideEnabled)
      setOverrideConfigs(snapshot.overrideConfigs)
      setInstanceMap(snapshot.instanceMap)
    }
    setShowLibraryModal(false)
  }

  const saveLibraryTargeting = async () => {
    await saveLibraryOverrideSettings()
    await saveInstanceRoutingMap()
    setShowLibraryModal(false)
  }

  const fetchUploadCache = async (showLoading: boolean = true) => {
    if (showLoading) {
      setCacheLoading(true)
    }
    try {
      const data = await getPlexUploadCache()
      setUploadCache(data)
    } catch (error) {
      console.error('Failed to load Plex upload cache:', error)
      showToast(getApiErrorMessage(error, 'Failed to load Plex upload cache'), 'error')
    } finally {
      if (showLoading) {
        setCacheLoading(false)
      }
    }
  }

  const fetchUploadCacheEntries = async (
    reset: boolean,
    queryOverride?: string,
  ) => {
    if (cacheBrowserLoading) {
      return
    }

    setCacheBrowserLoading(true)
    try {
      const queryValue = typeof queryOverride === 'string' ? queryOverride : cacheBrowserQuery
      const offset = reset ? 0 : cacheBrowserOffset
      const data = await getPlexUploadCacheEntries({
        q: queryValue,
        limit: CACHE_BROWSER_PAGE_SIZE,
        offset,
      })

      setCacheBrowserTotal(data.total)
      setCacheBrowserHasMore(data.has_more)
      setCacheBrowserOffset(data.offset + data.entries.length)

      if (reset) {
        setCacheBrowserEntries(data.entries)
      } else {
        setCacheBrowserEntries((current) => [...current, ...data.entries])
      }
    } catch (error) {
      console.error('Failed to load paginated Plex upload cache entries:', error)
      showToast(getApiErrorMessage(error, 'Failed to load cache entries'), 'error')
    } finally {
      setCacheBrowserLoading(false)
    }
  }

  const clearUploadCacheEntries = async (filePath?: string) => {
    setCacheClearing(true)
    try {
      const response = await clearPlexUploadCache(filePath ? { file_path: filePath } : undefined)
      setUploadCache(response)
      if (showCacheBrowser) {
        fetchUploadCacheEntries(true)
      }
      const actionLabel = filePath ? 'cache entry' : 'cache'
      showToast(`Plex upload ${actionLabel} cleared (${response.removed} removed)`, 'success')
    } catch (error) {
      console.error('Failed to clear Plex upload cache:', error)
      showToast(getApiErrorMessage(error, 'Failed to clear Plex upload cache'), 'error')
    } finally {
      setCacheClearing(false)
    }
  }

  const exportUploadCache = () => {
    const url = downloadPlexUploadCacheExport()
    window.open(url, '_blank')
    showToast('Upload cache export started', 'success')
  }

  const openCacheBrowser = () => {
    setCacheBrowserQuery('')
    setCacheBrowserEntries([])
    setCacheBrowserTotal(0)
    setCacheBrowserOffset(0)
    setCacheBrowserHasMore(false)
    setShowCacheBrowser(true)
  }

  const closeCacheBrowser = () => {
    setShowCacheBrowser(false)
  }

  const saveWebhookSettings = async () => {
    setWebhookSaving(true)
    try {
      const response = await savePlexWebhookSettings({
        enabled: webhookEnabled,
        remove_overlay_label: webhookRemoveOverlayLabel,
        rename_then_upload: webhookRenameThenUpload,
        adopt_existing_processed: webhookAdoptExistingProcessed,
        retry_attempts: webhookRetryAttempts,
        retry_delay_seconds: webhookRetryDelaySeconds,
        upload_delay_ms: webhookUploadDelayMs,
      })
      const nextWebhookEnabled = Boolean(response.enabled)
      const nextWebhookRemoveOverlayLabel = Boolean(response.remove_overlay_label)
      const nextWebhookRenameThenUpload = Boolean(response.rename_then_upload)
      const nextWebhookAdoptExistingProcessed = Boolean(response.adopt_existing_processed)
      const nextWebhookRetryAttempts = Number(response.retry_attempts) || 10
      const nextWebhookRetryDelaySeconds = Number(response.retry_delay_seconds) || 30
      const nextWebhookUploadDelayMs = Number(response.upload_delay_ms) || 50

      setWebhookEnabled(nextWebhookEnabled)
      setWebhookRemoveOverlayLabel(nextWebhookRemoveOverlayLabel)
      setWebhookRenameThenUpload(nextWebhookRenameThenUpload)
      setWebhookAdoptExistingProcessed(nextWebhookAdoptExistingProcessed)
      setWebhookRetryAttempts(nextWebhookRetryAttempts)
      setWebhookRetryDelaySeconds(nextWebhookRetryDelaySeconds)
      setWebhookUploadDelayMs(nextWebhookUploadDelayMs)

      automationBaselineRef.current = createAutomationSnapshot(
        nextWebhookEnabled,
        nextWebhookRemoveOverlayLabel,
        nextWebhookRenameThenUpload,
        nextWebhookAdoptExistingProcessed,
        nextWebhookRetryAttempts,
        nextWebhookRetryDelaySeconds,
        nextWebhookUploadDelayMs,
        automationBaselineRef.current.override_enabled,
        automationBaselineRef.current.override_configs,
      )
      setHasUnsavedAutomationSettings(false)
      showToast('Webhook settings saved', 'success')
    } catch (error) {
      console.error('Failed to save webhook settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to save webhook settings'), 'error')
    } finally {
      setWebhookSaving(false)
    }
  }

  useEffect(() => {
    if (webhookDedupeMediaType === 'movie') {
      setWebhookDedupeSeason('')
    }
  }, [webhookDedupeMediaType])

  useEffect(() => {
    if (suppressNextWebhookDedupeSearchRef.current) {
      suppressNextWebhookDedupeSearchRef.current = false
      setWebhookDedupeSearchLoading(false)
      return
    }

    const query = webhookDedupeTitle.trim()
    if (!query) {
      setWebhookDedupeSearchLoading(false)
      setWebhookDedupeSearchResults([])
      return
    }

    const timeoutId = setTimeout(async () => {
      setWebhookDedupeSearchLoading(true)
      try {
        const response = await getPlexWebhookDedupeEntries(query, webhookDedupeMediaType, 10)
        setWebhookDedupeSearchResults(response.items || [])
      } catch (error) {
        console.error('Failed to search webhook dedupe titles:', error)
        setWebhookDedupeSearchResults([])
      } finally {
        setWebhookDedupeSearchLoading(false)
      }
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timeoutId)
  }, [webhookDedupeTitle, webhookDedupeMediaType])

  useEffect(() => {
    let cancelled = false

    fetchLatestPlexJob()
    fetchWebhookStats()

    const loadToggleSettings = async () => {
      await Promise.all([fetchManualSettings(), fetchWebhookSettings(), fetchLibraryOverrideSettings(), fetchInstanceRoutingMap()])

      if (cancelled) {
        return
      }

      setToggleSettingsReady(true)

      if (!cancelled) {
        fetchUploadCache()
      }
    }

    loadToggleSettings()

    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!manualSettingsReady) {
      return
    }

    const currentSnapshot = createManualSnapshot(dryRun, reapply, removeOverlayLabel, syncBeforeUpload, renameBeforeUpload, borderBeforeUpload, manualUploadDelayMs)
    setHasUnsavedManualSettings(JSON.stringify(currentSnapshot) !== JSON.stringify(manualBaselineRef.current))
  }, [manualSettingsReady, dryRun, reapply, removeOverlayLabel, syncBeforeUpload, renameBeforeUpload, borderBeforeUpload, manualUploadDelayMs])

  useEffect(() => {
    if (!toggleSettingsReady) {
      return
    }

    const currentSnapshot = createAutomationSnapshot(
      webhookEnabled,
      webhookRemoveOverlayLabel,
      webhookRenameThenUpload,
      webhookAdoptExistingProcessed,
      webhookRetryAttempts,
      webhookRetryDelaySeconds,
      webhookUploadDelayMs,
      overrideEnabled,
      overrideConfigs,
    )

    setHasUnsavedAutomationSettings(JSON.stringify(currentSnapshot) !== JSON.stringify(automationBaselineRef.current))
  }, [
    toggleSettingsReady,
    webhookEnabled,
    webhookRemoveOverlayLabel,
    webhookRenameThenUpload,
    webhookAdoptExistingProcessed,
    webhookRetryAttempts,
    webhookRetryDelaySeconds,
    webhookUploadDelayMs,
    overrideEnabled,
    overrideConfigs,
  ])

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      const hasUnsavedChanges = (activeTab === 'manual' && hasUnsavedManualSettings)
        || (activeTab === 'settings' && hasUnsavedAutomationSettings)

      if (hasUnsavedChanges) {
        event.preventDefault()
        event.returnValue = ''
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [activeTab, hasUnsavedManualSettings, hasUnsavedAutomationSettings])

  const handleTabChange = (nextTab: PlexUploadTab) => {
    if (nextTab === activeTab) {
      return
    }

    const hasUnsavedOnActiveTab = (activeTab === 'manual' && hasUnsavedManualSettings)
      || (activeTab === 'settings' && hasUnsavedAutomationSettings)

    if (hasUnsavedOnActiveTab) {
      setPendingTabChange(nextTab)
      setShowUnsavedModal(true)
      return
    }

    setActiveTab(nextTab)
  }

  const handleDiscardChanges = () => {
    if (activeTab === 'manual') {
      const baseline = manualBaselineRef.current
      setDryRun(baseline.dry_run)
      setReapply(baseline.reapply)
      setRemoveOverlayLabel(baseline.remove_overlay_label)
      setSyncBeforeUpload(baseline.sync_before_upload)
      setRenameBeforeUpload(baseline.rename_before_upload)
      setBorderBeforeUpload(baseline.border_before_upload)
      setManualUploadDelayMs(baseline.upload_delay_ms)
      setHasUnsavedManualSettings(false)
    } else if (activeTab === 'settings') {
      const baseline = automationBaselineRef.current
      setWebhookEnabled(baseline.webhook_enabled)
      setWebhookRemoveOverlayLabel(baseline.webhook_remove_overlay_label)
      setWebhookRenameThenUpload(baseline.webhook_rename_then_upload)
      setWebhookAdoptExistingProcessed(baseline.webhook_adopt_existing_processed)
      setWebhookRetryAttempts(baseline.webhook_retry_attempts)
      setWebhookRetryDelaySeconds(baseline.webhook_retry_delay_seconds)
      setWebhookUploadDelayMs(baseline.webhook_upload_delay_ms)
      setOverrideEnabled(baseline.override_enabled)
      setOverrideConfigs(normalizeOverrideConfigs(baseline.override_configs))
      setHasUnsavedAutomationSettings(false)
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

  useEffect(() => {
    if (!isJobActive || !lastJob) {
      return
    }

    const interval = setInterval(async () => {
      try {
        const updated = await getJob(lastJob.id)
        setLastJob(updated)
      } catch {
        // silently continue polling
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [isJobActive, lastJob])

  useEffect(() => {
    window.addEventListener('scroll', () => {}, true)
    return () => {
      window.removeEventListener('scroll', () => {}, true)
    }
  }, [])

  useEffect(() => {
    const trimmed = sourceQuery.trim()
    if (!trimmed) {
      setSourceHasSearched(false)
      setSourceResults([])
      setSelectedSourcePoster(null)
      return
    }

    const timeoutId = setTimeout(() => {
      runSourceSearch(trimmed)
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timeoutId)
  }, [sourceQuery])

  useEffect(() => {
    if (!showCacheBrowser) {
      return
    }

    const timeoutId = setTimeout(() => {
      fetchUploadCacheEntries(true, cacheBrowserQuery)
    }, SEARCH_DEBOUNCE_MS)

    return () => clearTimeout(timeoutId)
  }, [cacheBrowserQuery, showCacheBrowser])

  const handleCacheBrowserOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      closeCacheBrowser()
    }
  }

  const selectWebhookDedupeSearchResult = (item: PlexWebhookDedupeEntry) => {
    suppressNextWebhookDedupeSearchRef.current = true
    setWebhookDedupeTitle(item.title)
    setWebhookDedupeYear(item.year ? String(item.year) : '')
    if (item.media_type === 'series') {
      setWebhookDedupeSeason(item.season_number != null ? String(item.season_number) : '')
    }
    setWebhookDedupeSearchResults([])
  }

  return (
    <div className="page-container plex-upload-page">
      <div className="plex-upload-header">
        <h1>Plex Upload</h1>
        <p>Run manual poster uploads to Plex using your existing Media settings configuration.</p>
      </div>

      <div className="plex-upload-tabs">
        <button
          type="button"
          className={`tab-settings ${activeTab === 'settings' ? 'active' : ''}`}
          onClick={() => handleTabChange('settings')}
        >
          <Settings2 size={16} /> Settings
        </button>
        <button
          type="button"
          className={`tab-manual ${activeTab === 'manual' ? 'active' : ''}`}
          onClick={() => handleTabChange('manual')}
        >
          <UploadCloud size={16} /> Manual Uploads
        </button>
      </div>

      {activeTab === 'manual' && (
        <section className="plex-upload-section">
          <div className="plex-upload-section-header">
            <h2>Run & Monitor</h2>
            <p>Run uploads manually for all posters or a single item, then monitor the latest job status.</p>
          </div>

          <section className="plex-upload-card plex-manual-options-card">
            <div className="plex-card-header-row">
              <h2>Manual Run Options (Shared)</h2>
              <button
                type="button"
                className={`plex-refresh-btn ${hasUnsavedManualSettings ? 'btn-unsaved' : ''}`}
                onClick={saveManualSettings}
                disabled={!manualSettingsReady || manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
              >
                {manualSettingsSaving ? 'Saving…' : 'Save Manual Settings'}
              </button>
            </div>
            <p className="card-description">These options apply to both manual run actions below: <strong>All Posters</strong> and <strong>Single Poster</strong>.</p>
            <p className="card-description">Pre-upload preparation follows your configured <strong>Poster Renamer</strong> and <strong>Border Replacer</strong> settings.</p>

            {!manualSettingsReady ? (
              <p className="card-description">Loading saved manual options…</p>
            ) : (
              <>
                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={dryRun}
                    onChange={(event) => setDryRun(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Dry run (no posters are uploaded)</span>
                </label>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={removeOverlayLabel}
                    onChange={(event) => setRemoveOverlayLabel(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Remove Overlay label after upload (Kometa-compatible)</span>
                </label>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={syncBeforeUpload}
                    onChange={(event) => setSyncBeforeUpload(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Sync drives before upload (Full upload only)</span>
                </label>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={renameBeforeUpload}
                    onChange={(event) => setRenameBeforeUpload(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Rename posters before upload</span>
                </label>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={borderBeforeUpload}
                    onChange={(event) => setBorderBeforeUpload(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Replace borders before upload</span>
                </label>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={reapply}
                    onChange={(event) => setReapply(event.target.checked)}
                    disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                  />
                  <span>Reapply (clear upload cache before run; ignored during dry run)</span>
                </label>

                <div className="webhook-retry-row">
                  <label className="plex-input-row">
                    <span>Upload delay (ms) <span className="info-tooltip">ⓘ<span className="info-tooltip-text">Pause between each uploadPoster call. Lower values are faster; increase if Plex returns errors. 0 = no delay.</span></span></span>
                    <input
                      type="number"
                      className="no-spinner"
                      min={0}
                      max={2000}
                      value={manualUploadDelayMs}
                      onChange={(event) => setManualUploadDelayMs(Math.max(0, Number(event.target.value)))}
                      disabled={manualSettingsLoading || manualSettingsSaving || loading || singleUploadLoading || isJobActive}
                    />
                  </label>
                </div>
              </>
            )}
          </section>

          <div className="plex-upload-grid">
            <section className="plex-upload-card upload-cache-card">
              <h2>Manual Upload (All Posters)</h2>
              <p className="card-description">Queue a background Plex upload job for all posters from the current poster destination.</p>

              {!manualSettingsReady && <p className="card-description">Loading run options…</p>}

              <button type="button" className="plex-run-btn" onClick={runUpload} disabled={!manualSettingsReady || loading || isJobActive}>
                <UploadCloud size={18} />
                {loading ? 'Starting…' : isJobActive ? 'Job Running…' : 'Run All Posters Upload'}
              </button>
            </section>

            <section className="plex-upload-card">
              <div className="plex-card-header-row">
                <h2>Latest Job</h2>
                <button type="button" className="plex-refresh-btn" onClick={() => fetchLatestPlexJob()} disabled={refreshing}>
                  <RefreshCw size={16} className={refreshing ? 'spinning' : ''} />
                  Refresh
                </button>
              </div>

              {!lastJob ? (
                <p className="card-description">No Plex upload job has run yet.</p>
              ) : (
                <div className="plex-job-details">
                  <div><span>Job ID:</span> <strong>{lastJob.id}</strong></div>
                  <div><span>Status:</span> <strong className={`job-status ${lastJob.status}`}>{lastJob.status}</strong></div>
                  <div><span>Progress:</span> <strong>{lastJob.progress}%</strong></div>
                  <div><span>Message:</span> <strong>{lastJob.message || '—'}</strong></div>
                  {lastJob.error && <div><span>Error:</span> <strong className="job-error">{lastJob.error}</strong></div>}
                </div>
              )}
            </section>
          </div>

          <section className="plex-upload-card plex-single-upload-card">
            <h2>Single Poster Upload</h2>
            <p className="card-description">
              Search your Plex library, select a movie, show, or collection, and queue a single Plex upload. Uses the shared options above, and runs the same pre-upload rename and border replace workflow used by webhook uploads.
            </p>

            <div className="plex-single-search-row">
              <div className="plex-single-search-input-wrap">
                <Search size={16} />
                <input
                  ref={singleSourceSearchInputRef}
                  type="text"
                  value={sourceQuery}
                  onChange={(event) => setSourceQuery(event.target.value)}
                  placeholder="Search Plex library..."
                  disabled={singleUploadLoading || isJobActive}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      runSourceSearch(sourceQuery)
                    }
                  }}
                />
              </div>
              <button
                type="button"
                className="plex-refresh-btn"
                onClick={() => runSourceSearch(sourceQuery)}
                disabled={sourceSearching || singleUploadLoading || isJobActive}
              >
                {sourceSearching ? 'Searching…' : 'Search'}
              </button>
            </div>

            <div className="poster-search-results-list">
              {sourceResults.length > 0 ? (
                <ul className="drive-group-list">
                  {sourceResults.map((item, index) => {
                    const key = `${item.plex_rating_key ?? index}-${item.plex_instance}`
                    const selected = selectedSourcePoster === item
                    const displayTitle = `${item.title}${item.year ? ` (${item.year})` : ''}`
                    const typeLabel = item.media_type === 'movie' ? 'Movie' : item.media_type === 'collection' ? 'Collection' : 'Show'

                    return (
                      <li
                        key={key}
                        className={`poster-search-result-item ${selected ? 'selected' : ''}`}
                      >
                        <span className="result-title">
                          <span className={`plex-type-badge plex-type-${item.media_type}`}>{typeLabel}</span>
                          {displayTitle}
                          {item.library_name && (
                            <span className="result-library-name"> · {item.library_name}</span>
                          )}
                        </span>
                        <button
                          type="button"
                          className="result-copy-btn"
                          onClick={() => setSelectedSourcePoster(selected ? null : item)}
                          disabled={singleUploadLoading || isJobActive}
                          aria-label={`Select ${displayTitle}`}
                        >
                          {selected ? 'Selected' : 'Select'}
                        </button>
                      </li>
                    )
                  })}
                </ul>
              ) : (
                <div className="poster-search-empty">
                  {sourceHasSearched ? 'No Plex results found for this query.' : 'Search your Plex library to find poster targets.'}
                </div>
              )}
            </div>

            <div className="poster-search-count">
              {sourceHasSearched
                ? `${sourceMatchCount} result${sourceMatchCount === 1 ? '' : 's'}`
                : 'Search to load results.'}
            </div>

            <button
              type="button"
              className="plex-run-btn"
              onClick={runSingleUpload}
              disabled={!manualSettingsReady || !selectedSourcePoster || singleUploadLoading || isJobActive}
            >
              <UploadCloud size={18} />
              {singleUploadLoading ? 'Starting Single Upload…' : 'Upload Selected to Plex'}
            </button>
          </section>
        </section>
      )}

      {activeTab === 'settings' && (
        <>
          <section className="plex-upload-card walkthrough-card">
            <div className="plex-card-header-row walkthrough-header-row">
              <h2>Setup Walkthrough</h2>
              <button
                type="button"
                className="plex-refresh-btn walkthrough-toggle-btn"
                onClick={() => setShowSetupGuide((current) => !current)}
              >
                {showSetupGuide ? 'Hide' : 'Show'}
              </button>
            </div>

            {showSetupGuide && (
              <div className="walkthrough-content">
                <ol>
                  <li>
                    Open <strong>Settings → Media Servers</strong> and confirm Plex, Radarr, and Sonarr instances are configured.
                  </li>
                  <li>
                    Enable webhook processing below, then use this webhook URL in both Radarr and Sonarr:
                    <div className="walkthrough-endpoint webhook-url-block">
                      <code className="webhook-url-value">{effectiveWebhookUrl}</code>
                      <button
                        type="button"
                        className="plex-refresh-btn webhook-copy-btn"
                        onClick={() => copyWebhookUrl(effectiveWebhookUrl)}
                      >
                        Copy
                      </button>
                    </div>
                    {passwordSet ? (
                      <div className="webhook-token-warning">
                        <strong>App password is enabled.</strong> You must use the URL above — it
                        includes a <code>?token=</code> that authenticates the webhook. Without it,
                        Radarr/Sonarr requests are rejected with <code>401 Unauthorized</code> and no
                        posters upload. If you remove the app password later, the plain URL (without
                        the token) works again.
                        <div className="webhook-token-actions">
                          <button
                            type="button"
                            className="plex-refresh-btn"
                            onClick={handleRegenerateToken}
                            disabled={regeneratingToken}
                          >
                            {regeneratingToken ? 'Regenerating…' : 'Regenerate token'}
                          </button>
                          <span className="webhook-token-hint">
                            Rotating the token invalidates the old URL — update Radarr/Sonarr afterwards.
                          </span>
                        </div>
                      </div>
                    ) : (
                      <p className="card-description webhook-token-hint">
                        No app password is set, so this webhook needs no token. If you set a password
                        later (Settings → Security), come back here and copy the tokenized URL.
                      </p>
                    )}
                    <ul className="walkthrough-substeps">
                      <li>
                        <strong>Radarr:</strong> Settings → Connect → + → Webhook.
                        Set <strong>Method</strong> to <strong>POST</strong>, paste the URL above, and check <strong>On File Import</strong> and <strong>On File Upgrade</strong> only in import events.
                      </li>
                      <li>
                        <strong>Sonarr:</strong> Settings → Connect → + → Webhook.
                        Set <strong>Method</strong> to <strong>POST</strong>, paste the URL above, and check <strong>On Import Complete</strong> only in import events.
                      </li>
                      <li>
                        Save each connector, then use the built-in <strong>Test</strong> button in Radarr/Sonarr to confirm delivery.
                      </li>
                      <li className="walkthrough-multi-instance">
                        <strong>Running multiple Radarr/Sonarr instances?</strong> Open <strong>Library Targeting →
                        Configure libraries</strong> below. Each instance has its own webhook URL there ending in
                        <code>&instance=&lt;name&gt;</code> — paste <em>that</em> instance's URL into its own connector
                        instead of the generic URL above. Why it matters:
                        <ul className="walkthrough-substeps walkthrough-multi-instance-list">
                          <li>
                            <strong>Attribution:</strong> the <code>&instance=</code> token tells PosterFlow which
                            instance fired, so it can route the upload to that instance's libraries — reliably, even if
                            the instance's name in Radarr/Sonarr doesn't match what you configured here.
                          </li>
                          <li>
                            <strong>No cross-suppression:</strong> without it, two instances importing the same title
                            within ~10 minutes can be treated as duplicates and one upload gets skipped. Distinct
                            instance URLs keep them separate.
                          </li>
                          <li>
                            <strong>Optional:</strong> a single-instance setup can keep using the generic URL above.
                            Instances left unmapped simply upload to all selected libraries.
                          </li>
                        </ul>
                      </li>
                    </ul>
                  </li>
                  <li>
                    Verify activity with <strong>Webhook Stats</strong> on this page and <strong>logs</strong>:
                    <ul className="walkthrough-substeps">
                      <li><strong>Test on a small library or test library.</strong></li>
                      <li><strong>Real downloads/imports:</strong> Add new shows/movies through Sonarr/Radarr to test auto upload.</li>
                      <li><strong>Kometa 'Overlay':</strong> if the 'Remove Overlay label' setting is off, and you run Kometa overlays, new overlays will not be readded'.</li>
                    </ul>
                  </li>
                </ol>
                <p className="card-description walkthrough-note">
                  Possible Test: Run a manual upload from this page (start with <strong>Dry run</strong>) to backfill existing posters. A non-dry run can replace existing posters in Plex.
                </p>
              </div>
            )}
          </section>

          <div className="plex-upload-initial-warning">
            <strong>Initial Setup Recommendation:</strong> Run a full manual Plex upload on first use. This caches all current posters and prevents unnecessary re-uploads during future automated runs.
          </div>

          <section className="plex-upload-section">
            <div className="plex-upload-section-header">
              <h2>Automation & Controls</h2>
              <p>Configure webhook behavior, library targeting, then monitor stats and cache below.</p>
            </div>

            <div className="plex-upload-grid webhook-automation-grid">
              <section className="plex-upload-card webhook-card webhook-settings-card">
                <div className="webhook-title-row">
                  <div className="webhook-title-label">
                    <Webhook size={18} />
                    <h2>Webhook Settings</h2>
                  </div>
                  {toggleSettingsReady && (
                    <button
                      type="button"
                      className={`plex-refresh-btn webhook-save-btn ${hasUnsavedAutomationSettings ? 'btn-unsaved' : ''}`}
                      onClick={saveWebhookSettings}
                      disabled={webhookLoading || webhookSaving}
                    >
                      {webhookSaving ? 'Saving…' : 'Save Webhook Settings'}
                    </button>
                  )}
                </div>

                <p className="card-description webhook-compact-description">Endpoint: <strong>{webhookPath}</strong></p>
                {passwordSet && (
                  <p className="card-description webhook-compact-description webhook-token-inline-warning">
                    ⚠ App password is set — Radarr/Sonarr must call the <strong>tokenized</strong> URL
                    (see <strong>Setup Walkthrough</strong> above for the full URL and copy button), or
                    webhooks are rejected.
                  </p>
                )}
                <p className="card-description webhook-compact-description">Webhook pre-upload preparation follows your configured <strong>Poster Renamer</strong> and <strong>Border Replacer</strong> settings.</p>

                {!toggleSettingsReady ? (
                  <p className="card-description">Loading webhook settings…</p>
                ) : (
                  <>
                    <label className="plex-checkbox-row webhook-enable-row">
                      <input
                        type="checkbox"
                        checked={webhookEnabled}
                        onChange={(event) => setWebhookEnabled(event.target.checked)}
                        disabled={webhookLoading || webhookSaving}
                      />
                      <span>Enable webhook processing</span>
                    </label>

                    {webhookEnabled ? (
                      <div className="webhook-settings-grid">
                        <label className="plex-checkbox-row">
                          <input
                            type="checkbox"
                            checked={webhookRemoveOverlayLabel}
                            onChange={(event) => setWebhookRemoveOverlayLabel(event.target.checked)}
                            disabled={webhookLoading || webhookSaving}
                          />
                          <span>Remove Overlay label after webhook upload (Kometa-compatible)</span>
                        </label>

                        <label className="plex-checkbox-row">
                          <input
                            type="checkbox"
                            checked={webhookRenameThenUpload}
                            onChange={(event) => setWebhookRenameThenUpload(event.target.checked)}
                            disabled={webhookLoading || webhookSaving}
                          />
                          <span>Run rename pass before webhook upload (improves first-hit matching)</span>
                        </label>

                        <label className="plex-checkbox-row">
                          <input
                            type="checkbox"
                            checked={webhookAdoptExistingProcessed}
                            onChange={(event) => setWebhookAdoptExistingProcessed(event.target.checked)}
                            disabled={webhookLoading || webhookSaving}
                          />
                          <span>Adopt existing processed posters (skip pre-upload rename when destination already has target poster)</span>
                        </label>

                        <div className="webhook-retry-row">
                          <label className="plex-input-row">
                            <span>Retry attempts</span>
                            <input
                              type="number"
                              className="no-spinner"
                              min={1}
                              max={20}
                              value={webhookRetryAttempts}
                              onChange={(event) => setWebhookRetryAttempts(Number(event.target.value) || 1)}
                              disabled={webhookLoading || webhookSaving}
                            />
                          </label>

                          <label className="plex-input-row">
                            <span>Retry delay (seconds)</span>
                            <input
                              type="number"
                              className="no-spinner"
                              min={1}
                              max={120}
                              value={webhookRetryDelaySeconds}
                              onChange={(event) => setWebhookRetryDelaySeconds(Number(event.target.value) || 1)}
                              disabled={webhookLoading || webhookSaving}
                            />
                          </label>

                          <label className="plex-input-row">
                            <span>Upload delay (ms) <span className="info-tooltip">ⓘ<span className="info-tooltip-text">Pause between each uploadPoster call. Lower values are faster; increase if Plex returns errors. 0 = no delay.</span></span></span>
                            <input
                              type="number"
                              className="no-spinner"
                              min={0}
                              max={2000}
                              value={webhookUploadDelayMs}
                              onChange={(event) => setWebhookUploadDelayMs(Math.max(0, Number(event.target.value)))}
                              disabled={webhookLoading || webhookSaving}
                            />
                          </label>
                        </div>
                      </div>
                    ) : (
                      <p className="card-description webhook-disabled-note">
                        Webhook automation settings are hidden while webhook processing is disabled.
                      </p>
                    )}
                  </>
                )}
              </section>
              <section className="plex-upload-card library-targeting-card">
                <div className="plex-card-header-row">
                  <h2>Library Targeting</h2>
                  <button
                    type="button"
                    className={`plex-refresh-btn ${hasUnsavedAutomationSettings ? 'btn-unsaved' : ''}`}
                    onClick={openLibraryModal}
                    disabled={!toggleSettingsReady}
                  >
                    Configure libraries…
                  </button>
                </div>
                <p className="card-description">
                  Choose which Plex libraries Plex Upload targets — globally (override) and per Radarr/Sonarr
                  instance. Opens a focused editor so the selection boxes don't crowd this page.
                </p>

                {!toggleSettingsReady ? (
                  <p className="card-description">Loading library settings…</p>
                ) : (
                  <div className="library-targeting-summary">
                    <div className="library-targeting-summary-row">
                      <span className="library-targeting-summary-label">Override</span>
                      <span>
                        {overrideEnabled ? (
                          <><strong>On</strong> — {getEnabledOverrideCount()} {getEnabledOverrideCount() === 1 ? 'library' : 'libraries'}</>
                        ) : (
                          <>Off — using Media-tab library selection</>
                        )}
                      </span>
                    </div>
                    {arrInstanceNames.length === 0 ? (
                      <p className="card-description library-targeting-summary-empty">
                        No Radarr/Sonarr instances configured — add them in Settings → Media to route per instance.
                      </p>
                    ) : (
                      arrInstanceNames.map((instanceName) => (
                        <div key={instanceName} className="library-targeting-summary-row">
                          <span className="library-targeting-summary-label">{instanceName}</span>
                          <span>→ {instanceRoutingSummary(instanceName)}</span>
                        </div>
                      ))
                    )}
                  </div>
                )}
              </section>
            </div>

            <section className="plex-upload-card webhook-card webhook-stats-card">
              <div className="webhook-title-row webhook-stats-title-row">
                <div className="webhook-title-label">
                  <h2>Webhook Stats</h2>
                </div>
                <div className="webhook-stats-actions">
                  <button
                    type="button"
                    className="plex-refresh-btn"
                    onClick={resetWebhookStats}
                    disabled={webhookStatsResetting}
                  >
                    {webhookStatsResetting ? 'Resetting…' : 'Reset Stats'}
                  </button>
                  <button
                    type="button"
                    className="plex-refresh-btn"
                    onClick={() => fetchWebhookStats()}
                    disabled={webhookStatsLoading}
                  >
                    <RefreshCw size={16} className={webhookStatsLoading ? 'spinning' : ''} />
                    Refresh
                  </button>
                </div>
              </div>

              {webhookStats ? (
                <>
                  <div className="webhook-stat-chips">
                    <div className="webhook-stat-chip chip-received"><span>Received</span><strong>{webhookStats.received}</strong></div>
                    <div className="webhook-stat-chip chip-queued"><span>Queued</span><strong>{webhookStats.queued}</strong></div>
                    <div className="webhook-stat-chip chip-duplicates"><span>Duplicates</span><strong>{webhookStats.duplicates}</strong></div>
                    <div className="webhook-stat-chip chip-test"><span>Test events</span><strong>{webhookStats.skipped_test}</strong></div>
                    <div className="webhook-stat-chip chip-duplicates"><span>Skipped (cached)</span><strong>{webhookStats.skipped_cached}</strong></div>
                    <div className="webhook-stat-chip chip-parse"><span>Skipped (no asset)</span><strong>{webhookStats.skipped_no_asset ?? 0}</strong></div>
                    <div className="webhook-stat-chip chip-disabled"><span>Disabled rejects</span><strong>{webhookStats.rejected_disabled}</strong></div>
                    <div className="webhook-stat-chip chip-parse"><span>Parse errors</span><strong>{webhookStats.parse_errors}</strong></div>
                    <div className="webhook-stat-chip chip-internal"><span>Internal errors</span><strong>{webhookStats.internal_errors}</strong></div>
                  </div>

                  <div className="webhook-stats-grid-compact">
                    <div><span>Last event:</span> <strong>{formatWebhookTimestamp(webhookStats.last_event_at)}</strong></div>
                    <div><span>Last queued:</span> <strong>{formatWebhookTimestamp(webhookStats.last_queued_at)}</strong></div>
                    <div><span>Last error:</span> <strong>{webhookStats.last_error || '—'}</strong></div>
                  </div>
                </>
              ) : (
                <p className="card-description">No webhook stats available yet.</p>
              )}

              <p className="card-description webhook-compact-footnote">
                Duplicate events are automatically suppressed within a short time window.
              </p>

              <h3 className="webhook-dedupe-heading">Clear Single Duplicate Lock</h3>
              <p className="card-description webhook-dedupe-description">
                This search only looks at active duplicate locks stored in the database (not source filesystem assets).
              </p>

              <div className="webhook-dedupe-clear-row">
                <select
                  value={webhookDedupeMediaType}
                  onChange={(event) => setWebhookDedupeMediaType(event.target.value as 'movie' | 'series')}
                  className="webhook-dedupe-input"
                  disabled={webhookDedupeClearing}
                >
                  <option value="movie">Movie</option>
                  <option value="series">Series</option>
                </select>
                <div className="webhook-dedupe-search-wrap">
                  <input
                    type="text"
                    className="webhook-dedupe-input"
                    placeholder="Title"
                    value={webhookDedupeTitle}
                    onChange={(event) => setWebhookDedupeTitle(event.target.value)}
                    disabled={webhookDedupeClearing}
                  />
                  {(webhookDedupeSearchLoading || webhookDedupeTitle.trim()) && (
                    <div className="webhook-dedupe-search-results">
                      {webhookDedupeSearchLoading ? (
                        <div className="webhook-dedupe-search-empty">Searching…</div>
                      ) : webhookDedupeSearchResults.length === 0 ? (
                        <div className="webhook-dedupe-search-empty">No results</div>
                      ) : (
                        webhookDedupeSearchResults.map((item, index) => {
                          const key = `${item.dedupe_key}-${index}`
                          const label = `${item.title}${item.year ? ` (${item.year})` : ''}${item.media_type === 'series' && item.season_number != null ? ` • Season ${item.season_number}` : ''}`
                          return (
                            <button
                              key={key}
                              type="button"
                              className="webhook-dedupe-result"
                              onClick={() => selectWebhookDedupeSearchResult(item)}
                            >
                              {label}
                            </button>
                          )
                        })
                      )}
                    </div>
                  )}
                </div>
                <input
                  type="number"
                  className="webhook-dedupe-input webhook-dedupe-number no-spinner"
                  placeholder="Year"
                  value={webhookDedupeYear}
                  onChange={(event) => setWebhookDedupeYear(event.target.value)}
                  disabled={webhookDedupeClearing}
                />
                {webhookDedupeMediaType === 'series' && (
                  <input
                    type="number"
                    className="webhook-dedupe-input webhook-dedupe-number no-spinner"
                    placeholder="Season"
                    value={webhookDedupeSeason}
                    onChange={(event) => setWebhookDedupeSeason(event.target.value)}
                    disabled={webhookDedupeClearing}
                  />
                )}
                <button
                  type="button"
                  className="plex-refresh-btn"
                  onClick={clearWebhookDedupeForItem}
                  disabled={webhookDedupeClearing}
                >
                  {webhookDedupeClearing ? 'Clearing…' : 'Clear Duplicate Lock'}
                </button>
              </div>
            </section>

            <section className="plex-upload-card">
              <div className="plex-card-header-row">
                <h2>Upload Cache</h2>
                <button
                  type="button"
                  className="plex-refresh-btn"
                  onClick={() => fetchUploadCache()}
                  disabled={cacheLoading}
                >
                  <RefreshCw size={16} className={cacheLoading ? 'spinning' : ''} />
                  Refresh
                </button>
              </div>
              <p className="card-description">
                Tracks previously uploaded file/library and movie edition combinations to skip duplicates across runs.
              </p>

              {!uploadCache ? (
                <p className="card-description">No upload cache data loaded.</p>
              ) : (
                <>
                  <div className="webhook-stats-grid upload-cache-summary">
                    <div><span>Files cached:</span> <strong>{uploadCache.entries_count}</strong></div>
                    <div><span>Library refs:</span> <strong>{uploadCache.total_library_refs}</strong></div>
                    <div><span>Edition refs:</span> <strong>{uploadCache.total_edition_refs}</strong></div>
                  </div>

                  <div className="upload-cache-actions">
                    <button
                      type="button"
                      className="plex-refresh-btn"
                      onClick={exportUploadCache}
                      disabled={uploadCache.entries_count === 0}
                    >
                      Export JSON
                    </button>
                    <button
                      type="button"
                      className="plex-refresh-btn"
                      onClick={openCacheBrowser}
                      disabled={uploadCache.entries_count === 0}
                    >
                      Browse Cache
                    </button>
                    <button
                      type="button"
                      className="plex-refresh-btn"
                      onClick={() => clearUploadCacheEntries()}
                      disabled={cacheClearing || uploadCache.entries_count === 0}
                    >
                      {cacheClearing ? 'Clearing…' : 'Clear All Cache'}
                    </button>
                  </div>

                  {uploadCache.entries.length === 0 ? (
                    <p className="card-description">Upload cache is empty.</p>
                  ) : (
                    <>
                      <p className="card-description upload-cache-note">
                        Showing latest 10 cache entries. Use <strong>Clear All Cache</strong> to remove all entries, including older items not shown here.
                      </p>
                      <div className="upload-cache-list">
                      {uploadCache.entries.slice(-10).reverse().map((entry) => (
                        <div key={entry.file_path} className="upload-cache-item">
                          <div className="upload-cache-item-main">
                            <strong>{entry.file_path}</strong>
                            <span className="upload-cache-meta-row">
                              <span className="upload-cache-meta-label">Libraries ({entry.uploaded_to_libraries.length}):</span>
                              <span className="upload-cache-meta-value">{entry.uploaded_to_libraries.length > 0 ? entry.uploaded_to_libraries.join(', ') : <em>none</em>}</span>
                              <span className="upload-cache-meta-sep">•</span>
                              <span className="upload-cache-meta-label">Editions:</span>
                              <span className="upload-cache-meta-value">{entry.uploaded_editions.length > 0 ? entry.uploaded_editions.join(', ') : <em>none</em>}</span>
                            </span>
                          </div>
                          <button
                            type="button"
                            className="plex-refresh-btn"
                            onClick={() => clearUploadCacheEntries(entry.file_path)}
                            disabled={cacheClearing}
                          >
                            Clear Entry
                          </button>
                        </div>
                      ))}
                      </div>
                    </>
                  )}
                </>
              )}
            </section>
          </section>
        </>
      )}

      <UnsavedChangesModal
        isOpen={showUnsavedModal}
        onCancel={handleCancelDiscard}
        onDiscard={handleDiscardChanges}
      />

      {showLibraryModal && (
        <div
          className="modal-overlay"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              cancelLibraryModal()
            }
          }}
        >
          <div className="modal-content schedule-modal library-targeting-modal">
            <div className="modal-header">
              <h2>Library Targeting</h2>
              <button className="modal-close" onClick={cancelLibraryModal}>×</button>
            </div>

            <div className="modal-body library-targeting-modal-body">
              {/* Global override */}
              <div className="library-targeting-block">
                <div className="plex-card-header-row">
                  <h3>Library Override</h3>
                </div>
                <p className="card-description">
                  Override the Media-tab Plex library selection for Plex Upload only — handy for testing on a
                  smaller or separate library. Applies to all uploads unless an instance below routes elsewhere.
                </p>

                <label className="plex-checkbox-row">
                  <input
                    type="checkbox"
                    checked={overrideEnabled}
                    onChange={(event) => setOverrideEnabled(event.target.checked)}
                    disabled={overrideLoading || overrideSaving}
                  />
                  <span>Enable Plex Upload-only library override</span>
                </label>

                <p className="card-description override-count">
                  Selected override libraries: <strong>{getEnabledOverrideCount()}</strong>
                </p>

                {overrideLoading ? (
                  <p className="card-description">Loading library selections…</p>
                ) : globalLibraryConfigs.length === 0 ? (
                  <p className="card-description">No global Plex library configuration found. Configure libraries in Settings → Media first.</p>
                ) : (
                  <div className="override-library-groups">
                    {globalLibraryConfigs.map((instanceConfig) => (
                      <div key={instanceConfig.instance_name} className="override-library-instance">
                        <h4>{instanceConfig.instance_name}</h4>
                        <div className="override-library-list">
                          {(instanceConfig.libraries || []).map((library) => (
                            <label key={`${instanceConfig.instance_name}-${library.key}`} className="plex-checkbox-row">
                              <input
                                type="checkbox"
                                checked={isOverrideLibraryChecked(instanceConfig.instance_name, library.key)}
                                onChange={(event) => toggleOverrideLibrary(instanceConfig.instance_name, library.key, event.target.checked)}
                                disabled={overrideLoading || overrideSaving}
                              />
                              <span>{library.title} <em>({library.type})</em></span>
                            </label>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Per-instance routing */}
              <div className="library-targeting-block">
                <div className="plex-card-header-row">
                  <h3>Instance → Library Routing</h3>
                </div>
                <p className="card-description">
                  Send each Radarr/Sonarr instance's imports to specific Plex libraries. Instances left unmapped
                  upload to <strong>all</strong> selected libraries. Paste each instance's dedicated webhook URL
                  (below) into its connector so events are attributed even when the payload's instance name
                  doesn't match.
                </p>

                {instanceMapLoading ? (
                  <p className="card-description">Loading instance routing…</p>
                ) : arrInstanceNames.length === 0 ? (
                  <p className="card-description">No Radarr/Sonarr instances configured. Add them in Settings → Media first.</p>
                ) : globalLibraryConfigs.length === 0 ? (
                  <p className="card-description">No global Plex library configuration found. Configure libraries in Settings → Media first.</p>
                ) : (
                  <div className="instance-routing-groups">
                    {arrInstanceNames.map((instanceName) => (
                      <div key={instanceName} className="instance-routing-instance" data-testid={`instance-routing-${instanceName}`}>
                        <h4>{instanceName}</h4>
                        <div className="walkthrough-endpoint webhook-url-block instance-routing-url">
                          <code className="webhook-url-value">{instanceWebhookUrl(instanceName)}</code>
                          <button
                            type="button"
                            className="plex-refresh-btn webhook-copy-btn"
                            onClick={() => copyWebhookUrl(instanceWebhookUrl(instanceName))}
                          >
                            Copy
                          </button>
                        </div>
                        <div className="override-library-groups">
                          {globalLibraryConfigs.map((instanceConfig) => (
                            <div key={`${instanceName}-${instanceConfig.instance_name}`} className="override-library-instance">
                              <h5>{instanceConfig.instance_name}</h5>
                              <div className="override-library-list">
                                {(instanceConfig.libraries || []).map((library) => (
                                  <label key={`${instanceName}-${instanceConfig.instance_name}-${library.key}`} className="plex-checkbox-row">
                                    <input
                                      type="checkbox"
                                      checked={isInstanceLibraryChecked(instanceName, instanceConfig.instance_name, library.key)}
                                      onChange={(event) =>
                                        toggleInstanceLibrary(instanceName, instanceConfig.instance_name, library.key, event.target.checked)
                                      }
                                      disabled={instanceMapLoading || instanceMapSaving}
                                    />
                                    <span>{library.title} <em>({library.type})</em></span>
                                  </label>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            <div className="modal-footer">
              <button className="btn-secondary" onClick={cancelLibraryModal} disabled={overrideSaving || instanceMapSaving}>
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={saveLibraryTargeting}
                disabled={!toggleSettingsReady || overrideSaving || instanceMapSaving}
              >
                {overrideSaving || instanceMapSaving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCacheBrowser && (
        <div className="modal-overlay" onClick={handleCacheBrowserOverlayClick}>
          <div className="modal-content schedule-modal cache-browser-modal">
            <div className="modal-header">
              <h2>Upload Cache Browser</h2>
              <button className="modal-close" onClick={closeCacheBrowser}>×</button>
            </div>

            <div className="modal-body cache-browser-modal-body">
              <p className="card-description cache-browser-meta">
                Showing <strong>{cacheBrowserEntries.length}</strong> of <strong>{cacheBrowserTotal}</strong> matching cache entries.
              </p>
              <div className="upload-cache-browser-controls">
                <div className="plex-single-search-input-wrap">
                  <Search size={14} />
                  <input
                    type="text"
                    placeholder="Search file path, library, edition, or type"
                    value={cacheBrowserQuery}
                    onChange={(event) => setCacheBrowserQuery(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        fetchUploadCacheEntries(true, cacheBrowserQuery)
                      }
                    }}
                  />
                </div>
                <button
                  type="button"
                  className="plex-refresh-btn"
                  onClick={() => fetchUploadCacheEntries(true, cacheBrowserQuery)}
                  disabled={cacheBrowserLoading}
                >
                  Search
                </button>
              </div>

              {cacheBrowserEntries.length === 0 ? (
                <p className="card-description upload-cache-note">No cache entries match this filter.</p>
              ) : (
                <div className="upload-cache-list cache-browser-list">
                  {cacheBrowserEntries.map((entry) => (
                    <div key={`browser-${entry.file_path}`} className="upload-cache-item">
                      <div className="upload-cache-item-main">
                        <strong>{entry.file_path}</strong>
                        <span className="upload-cache-meta-row">
                          <span className="upload-cache-meta-label">Libraries ({entry.uploaded_to_libraries.length}):</span>
                          <span className="upload-cache-meta-value">{entry.uploaded_to_libraries.length > 0 ? entry.uploaded_to_libraries.join(', ') : <em>none</em>}</span>
                          <span className="upload-cache-meta-sep">•</span>
                          <span className="upload-cache-meta-label">Editions:</span>
                          <span className="upload-cache-meta-value">{entry.uploaded_editions.length > 0 ? entry.uploaded_editions.join(', ') : <em>none</em>}</span>
                        </span>
                      </div>
                      <button
                        type="button"
                        className="plex-refresh-btn"
                        onClick={() => clearUploadCacheEntries(entry.file_path)}
                        disabled={cacheClearing}
                      >
                        Clear Entry
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {cacheBrowserHasMore && (
                <button
                  type="button"
                  className="plex-refresh-btn cache-browser-load-more"
                  onClick={() => fetchUploadCacheEntries(false)}
                  disabled={cacheBrowserLoading}
                >
                  {cacheBrowserLoading ? 'Loading…' : 'Load More'}
                </button>
              )}
            </div>

            <div className="modal-footer">
              <button className="btn-secondary" onClick={closeCacheBrowser}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default PlexUpload