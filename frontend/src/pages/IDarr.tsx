import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import { Eye, Play, Save, UploadCloud, Plus, Trash2, FolderOpen, Search, RotateCw, Info, ChevronDown, ChevronRight, AlertTriangle, FileImage } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import IDarrTabs, { IDarrTab } from '../components/IDarr/IDarrTabs'
import ConfirmDialog from '../components/ConfirmDialog'
import UnsavedChangesModal from '../components/poster-manager/UnsavedChangesModal'
import { useIDarrResolverActions } from '../hooks/useIDarrResolverActions'
import { useIDarrPendingActions } from '../hooks/useIDarrPendingActions'
import { useIDarrOperationalActions } from '../hooks/useIDarrOperationalActions'
import { useIDarrResolverWorkflow } from '../hooks/useIDarrResolverWorkflow'
import {
  MakerIdarrCacheStats,
  MakerIdarrPendingCandidate,
  MakerIdarrConfig,
  MakerIdarrIgnoredItem,
  MakerIdarrLastRun,
  MakerIdarrPendingItem,
  MakerIdarrResolutionEvent,
  getApiErrorMessage,
  getMakerIdarrCacheStats,
  getMakerIdarrConfig,
  getMakerIdarrIgnoredTitles,
  getMakerIdarrLastRun,
  getMakerIdarrPendingCandidates,
  archiveIdarrSourceFile,
  getMakerIdarrPendingMatches,
  importMakerIdarrIgnoredTitles,
  replaceMakerIdarrIgnoredTitles,
  resolveMakerIdarrPendingMatch,
  runMakerIdarrCacheMaintenance,
  saveMakerIdarrConfig,
  startIdarr,
  uploadMakerIdarrFiles,
} from '../api/client'
import { API_URL } from '../api/http'
import { useToast } from '../components/Toast'
import { useAppEvents } from '../contexts/AppEventsContext'
import './IDarr.css'

const IDARR_TAB_STORAGE_KEY = 'posterflow.idarr.activeTab'
const IDARR_SYNC_TARGET_STORAGE_KEY = 'posterflow.idarr.selectedSyncTarget'
// Pending matches are paginated
const PENDING_PAGE_SIZE = 25

const isIDarrTab = (value: string): value is IDarrTab => {
  return ['IDarr', 'settings'].includes(value)
}

const getSyncTargetStorageValue = (target: { personal_drive_id?: string; source_dir?: string; label?: string; scope_token?: string }): string => {
  const scopeToken = String(target.scope_token || '').trim()
  if (scopeToken) {
    return `scope:${scopeToken}`
  }
  const driveId = String(target.personal_drive_id || '').trim()
  const sourceDir = String(target.source_dir || '').trim()
  const label = String(target.label || '').trim()
  return `${driveId}::${sourceDir}::${label}`
}

const DEFAULT_IDARR_CONFIG: MakerIdarrConfig = {
  sync_targets: [],
  tmdb_api_key: '',
  auto_rename_quick_add: true,
  auto_upload_quick_add: false,
  remove_non_image_files: false,
  show_unmatched: false,
  pending_matches: false,
  skip_collections: false,
  limit: null,
  frequency_days: 30,
  tvdb_frequency: 7,
  force_sync_after_run: false,
  show_in_workflow: false,
}

const cloneIdarrConfig = (value: MakerIdarrConfig): MakerIdarrConfig => ({
  ...value,
  sync_targets: Array.isArray(value.sync_targets)
    ? value.sync_targets.map((target) => ({
      personal_drive_id: String(target.personal_drive_id || ''),
      source_dir: String(target.source_dir || ''),
      ...(target.label ? { label: String(target.label) } : {}),
      ...(target.scope_token ? { scope_token: String(target.scope_token) } : {}),
      ...(target.is_asset_drive ? { is_asset_drive: true } : {}),
      ...(target.is_psd_drive ? { is_psd_drive: true } : {}),
    }))
    : [],
})

const normalizeIdarrConfigForCompare = (value: MakerIdarrConfig) => ({
  tmdb_api_key: String(value.tmdb_api_key || ''),
  auto_rename_quick_add: Boolean(value.auto_rename_quick_add),
  auto_upload_quick_add: Boolean(value.auto_upload_quick_add),
  remove_non_image_files: Boolean(value.remove_non_image_files),
  show_unmatched: Boolean(value.show_unmatched),
  pending_matches: Boolean(value.pending_matches),
  skip_collections: Boolean(value.skip_collections),
  limit: value.limit === null ? null : Number(value.limit),
  frequency_days: Number(value.frequency_days || 30),
  tvdb_frequency: Number(value.tvdb_frequency || 7),
  force_sync_after_run: Boolean(value.force_sync_after_run),
  show_in_workflow: Boolean(value.show_in_workflow),
  sync_targets: Array.isArray(value.sync_targets)
    ? value.sync_targets.map((target) => ({
      scope_token: String(target.scope_token || ''),
      label: String(target.label || ''),
      personal_drive_id: String(target.personal_drive_id || ''),
      source_dir: String(target.source_dir || ''),
      is_asset_drive: Boolean(target.is_asset_drive),
      is_psd_drive: Boolean(target.is_psd_drive),
    }))
    : [],
})

const stripJsonComments = (input: string): string => {
  let output = ''
  let inString = false
  let escaped = false
  let inLineComment = false
  let inBlockComment = false

  for (let index = 0; index < input.length; index += 1) {
    const char = input[index]
    const nextChar = index + 1 < input.length ? input[index + 1] : ''

    if (inLineComment) {
      if (char === '\n') {
        inLineComment = false
        output += char
      }
      continue
    }

    if (inBlockComment) {
      if (char === '*' && nextChar === '/') {
        inBlockComment = false
        index += 1
      }
      continue
    }

    if (!inString && char === '/' && nextChar === '/') {
      inLineComment = true
      index += 1
      continue
    }

    if (!inString && char === '/' && nextChar === '*') {
      inBlockComment = true
      index += 1
      continue
    }

    output += char

    if (inString) {
      if (escaped) {
        escaped = false
      } else if (char === '\\') {
        escaped = true
      } else if (char === '"') {
        inString = false
      }
    } else if (char === '"') {
      inString = true
    }
  }

  return output
}

const parseIgnoredTitlesJsonc = (content: string): string[] => {
  const cleaned = stripJsonComments(content)
  const parsed = JSON.parse(cleaned)
  if (!Array.isArray(parsed)) {
    throw new Error('File must contain a JSON array of title strings')
  }

  const normalized: string[] = []
  const seen = new Set<string>()

  parsed.forEach((entry) => {
    const value = String(entry || '').trim()
    if (!value) {
      return
    }
    const dedupeKey = value.toLowerCase()
    if (seen.has(dedupeKey)) {
      return
    }
    seen.add(dedupeKey)
    normalized.push(value)
  })

  return normalized
}

const parseIgnoredTitlesEditorText = (content: string): string[] => {
  const normalized: string[] = []
  const seen = new Set<string>()

  content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .forEach((line) => {
      if (!line) {
        return
      }
      const dedupeKey = line.toLowerCase()
      if (seen.has(dedupeKey)) {
        return
      }
      seen.add(dedupeKey)
      normalized.push(line)
    })

  return normalized
}

function IDarr() {
  type PendingActionConfirm = { type: 'run'; dryRun: boolean } | { type: 'sync' } | { type: 'run_and_sync' } | null
  type MaintenanceActionType = 'revert_last_run' | 'purge_stale' | 'prune_unmatched' | 'clear_pending' | 'clear_all_cache'
  type MaintenanceActionConfirm = {
    action: MaintenanceActionType
    title: string
    message: string
    confirmText: string
    variant: 'danger' | 'warning' | 'info'
  } | null

  const navigate = useNavigate()
  const { jobs: wsJobs, refreshIdarrPendingCount } = useAppEvents()
  const [activeTab, setActiveTab] = useState<IDarrTab>(() => {
    const savedTab = localStorage.getItem(IDARR_TAB_STORAGE_KEY)
    if (savedTab && isIDarrTab(savedTab)) {
      return savedTab
    }
    return 'IDarr'
  })
  const [config, setConfig] = useState<MakerIdarrConfig>(DEFAULT_IDARR_CONFIG)
  const [configLoaded, setConfigLoaded] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [hasUnsavedSettings, setHasUnsavedSettings] = useState(false)
  const [showUnsavedModal, setShowUnsavedModal] = useState(false)
  const [pendingTabChange, setPendingTabChange] = useState<IDarrTab | null>(null)
  const [running, setRunning] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [selectedSyncTargetIndex, setSelectedSyncTargetIndex] = useState(0)
  const [frequencyDaysInput, setFrequencyDaysInput] = useState(String(DEFAULT_IDARR_CONFIG.frequency_days))
  const [tvdbFrequencyInput, setTvdbFrequencyInput] = useState(String(DEFAULT_IDARR_CONFIG.tvdb_frequency))
  const [limitInput, setLimitInput] = useState('')
  const [lastRun, setLastRun] = useState<MakerIdarrLastRun | null>(null)
  const [pendingItems, setPendingItems] = useState<MakerIdarrPendingItem[]>([])
  const [pendingPage, setPendingPage] = useState(0)
  const [pendingTotal, setPendingTotal] = useState(0)
  const [pendingPaging, setPendingPaging] = useState(false)
  const pendingPageRef = useRef(0)
  const [ignoredItems, setIgnoredItems] = useState<MakerIdarrIgnoredItem[]>([])
  const [pendingLoading, setPendingLoading] = useState(false)
  const [resolving, setResolving] = useState(false)
  const [cacheMaintaining, setCacheMaintaining] = useState(false)
  const [, setCacheStats] = useState<MakerIdarrCacheStats | null>(null)
  const [reverting, setReverting] = useState(false)
  const [showTargetedPruneModal, setShowTargetedPruneModal] = useState(false)
  const [showIgnoredEditorModal, setShowIgnoredEditorModal] = useState(false)
  const [ignoredEditorValue, setIgnoredEditorValue] = useState('')
  const [importingIgnoredTitles, setImportingIgnoredTitles] = useState(false)
  const [savingIgnoredTitles, setSavingIgnoredTitles] = useState(false)
  const [pruneTitleValue, setPruneTitleValue] = useState('')
  const [pruneAssetKeyValue, setPruneAssetKeyValue] = useState('')
  const [pruneTmdbValue, setPruneTmdbValue] = useState('')
  const [pruneTvdbValue, setPruneTvdbValue] = useState('')
  const [pruneImdbValue, setPruneImdbValue] = useState('')
  const [uploadingFiles, setUploadingFiles] = useState(false)
  const [isDragOverUpload, setIsDragOverUpload] = useState(false)
  const [resolverItem, setResolverItem] = useState<MakerIdarrPendingItem | null>(null)
  const [resolverIndex, setResolverIndex] = useState(0)
  const [resolverTmdbId, setResolverTmdbId] = useState('')
  const [resolverTmdbType, setResolverTmdbType] = useState<'movie' | 'tv_series' | 'collection' | ''>('')
  const [resolverManualSearch, setResolverManualSearch] = useState('')
  const [resolverTvdbId, setResolverTvdbId] = useState('')
  const [resolverImdbId, setResolverImdbId] = useState('')
  const [resolverCandidates, setResolverCandidates] = useState<MakerIdarrPendingCandidate[]>([])
  const [resolverCandidatesLoading, setResolverCandidatesLoading] = useState(false)
  const [resolverHistory, setResolverHistory] = useState<MakerIdarrResolutionEvent[]>([])
  const [resolverPreviewUrl, setResolverPreviewUrl] = useState<string | null>(null)
  const [cardPreviewUrl, setCardPreviewUrl] = useState<string | null>(null)
  const [selectedCandidate, setSelectedCandidate] = useState<MakerIdarrPendingCandidate | null>(null)
  const [pendingActionConfirm, setPendingActionConfirm] = useState<PendingActionConfirm>(null)
  const [maintenanceActionConfirm, setMaintenanceActionConfirm] = useState<MaintenanceActionConfirm>(null)
  const [pendingAssetDriveToggleIndex, setPendingAssetDriveToggleIndex] = useState<number | null>(null)
  const [pendingPsdDriveToggleIndex, setPendingPsdDriveToggleIndex] = useState<number | null>(null)
  const [resolverAutoAdvance] = useState(false)
  const [manualSectionOpen, setManualSectionOpen] = useState(false)
  const uploadInputRef = useRef<HTMLInputElement | null>(null)
  const ignoredTitlesImportInputRef = useRef<HTMLInputElement | null>(null)
  const resolverModalBodyRef = useRef<HTMLDivElement | null>(null)
  const originalConfigRef = useRef<MakerIdarrConfig>(cloneIdarrConfig(DEFAULT_IDARR_CONFIG))
  const hasCompletedInitialLoadRef = useRef(false)
  const { showToast } = useToast()

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  useEffect(() => {
    localStorage.setItem(IDARR_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])


  // Refresh state when a sidebar drop upload completes (upload handled by Sidebar directly)
  useEffect(() => {
    const handleSidebarUploadComplete = () => {
      void loadPendingMatches()
      void loadCacheStats()
      void loadIgnoredTitles()
    }
    window.addEventListener('idarr-sidebar-upload-complete', handleSidebarUploadComplete)
    return () => window.removeEventListener('idarr-sidebar-upload-complete', handleSidebarUploadComplete)
  }, [])

  useEffect(() => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    if (syncTargets.length === 0) {
      return
    }

    const selectedTarget = syncTargets[selectedSyncTargetIndex]

    if (!selectedTarget) {
      return
    }

    localStorage.setItem(IDARR_SYNC_TARGET_STORAGE_KEY, getSyncTargetStorageValue(selectedTarget))
  }, [config.sync_targets, selectedSyncTargetIndex])

  const getCandidateTmdbUrl = (tmdbId: number, mediaType: 'movie' | 'show' | 'collection'): string => {
    const tmdbPath = mediaType === 'show' ? 'tv' : mediaType
    return `https://www.themoviedb.org/${tmdbPath}/${tmdbId}`
  }

  const getTypeChipMeta = (rawType: string | null | undefined): { label: string; className: string } => {
    const normalized = String(rawType || '').trim().toLowerCase()
    if (normalized === 'movie') {
      return { label: 'Movie', className: 'chip-movie' }
    }
    if (normalized === 'collection') {
      return { label: 'Collection', className: 'chip-collection' }
    }
    if (normalized === 'pending') {
      return { label: 'Pending', className: 'chip-pending' }
    }
    return { label: 'Show', className: 'chip-show' }
  }

  const getPendingReasonChipMeta = (rawReason: string | null | undefined): { label: string; className: string } | null => {
    const normalized = String(rawReason || '').trim().toLowerCase()
    if (normalized === 'rename_conflict' || normalized === 'in_place_conflict_kept_existing') {
      return { label: 'Conflict', className: 'chip-conflict' }
    }
    if (normalized === 'low_confidence_alternate' || normalized === 'review_required_low_confidence_alternate') {
      return { label: 'Review', className: 'chip-review' }
    }
    return null
  }

  const getResolverSearchUrls = (): { tmdbMovie: string; tmdbShow: string; tmdbCollection: string; tvdb: string; google: string } => {
    const title = String(resolverItem?.title || '').trim()
    const tmdbYearPart = typeof resolverItem?.year === 'number' ? ` y:${resolverItem.year}` : ''
    const tmdbQuery = `${title}${tmdbYearPart}`.trim()
    const plainQuery = `${title}${typeof resolverItem?.year === 'number' ? ` ${resolverItem.year}` : ''}`.trim()
    const googleQuery = `${plainQuery} (tvdb OR tmdb OR imdb)`.trim()
    const encodedTmdbQuery = encodeURIComponent(tmdbQuery)
    const encodedTitleQuery = encodeURIComponent(title)
    const encodedGoogleQuery = encodeURIComponent(googleQuery)
    return {
      tmdbMovie: `https://www.themoviedb.org/search/movie?query=${encodedTmdbQuery}`,
      tmdbShow: `https://www.themoviedb.org/search/tv?query=${encodedTmdbQuery}`,
      tmdbCollection: `https://www.themoviedb.org/search/collection?query=${encodedTmdbQuery}`,
      tvdb: `https://thetvdb.com/search?query=${encodedTitleQuery}`,
      google: `https://www.google.com/search?q=${encodedGoogleQuery}`,
    }
  }

  // Show the transparency checkerboard behind formats that can carry an alpha channel — logos
  // are typically PNG or WebP. Matches the extension whether it's a bare filename or embedded in
  // a preview URL (e.g. "…/source-image?path=Foo.webp&cb=123").
  const isTransparentImage = (url: string | null | undefined) => /\.(png|webp|gif|avif)\b/i.test(String(url || ''))
  // Browsers can't render .psd, so any preview pointing at one would just break — detect it
  // and show a PSD placeholder instead. Matches a .psd extension whether it's a bare filename or
  // sits inside a preview URL (e.g. "…/source-image?path=Foo.psd&cb=123").
  const isPsd = (url: string | null | undefined) => /\.psd\b/i.test(String(url || ''))

  const getPreviewImageUrl = (rawUrl: string | null | undefined): string | null => {
    const value = String(rawUrl || '').trim()
    if (!value) {
      return null
    }
    if (value.startsWith('http://') || value.startsWith('https://')) {
      return value
    }
    return `${API_URL}${value}`
  }

  const resetResolver = () => {
    setResolverItem(null)
    setResolverTmdbId('')
    setResolverTmdbType('')
    setResolverManualSearch('')
    setResolverTvdbId('')
    setResolverImdbId('')
    setResolverCandidates([])
    setResolverCandidatesLoading(false)
    setResolverHistory([])
    setResolverPreviewUrl(null)
    setSelectedCandidate(null)
  }

  const {
    loadResolverCandidates,
    openResolver,
  } = useIDarrResolverWorkflow({
    selectedSyncTargetIndex,
    showToast,
    setResolverItem,
    setResolverTmdbId,
    setResolverTmdbType,
    setResolverTvdbId,
    setResolverImdbId,
    setResolverCandidates,
    setResolverCandidatesLoading,
    setResolverHistory,
    setResolverManualSearch,
    setManualSectionOpen,
  })

  const loadPendingMatches = async ({
    silent = false,
    syncTargetIndex,
    page,
  }: {
    silent?: boolean
    syncTargetIndex?: number
    page?: number
  } = {}): Promise<MakerIdarrPendingItem[]> => {
    const requestedIndex = typeof syncTargetIndex === 'number' ? syncTargetIndex : selectedSyncTargetIndex
    // Default to the page the user is currently on (via ref, so resolver-action callers with a
    // stale closure still reload the right page).
    let targetPage = Math.max(0, typeof page === 'number' ? page : pendingPageRef.current)
    try {
      if (!silent) {
        setPendingLoading(true)
      }
      let response = await getMakerIdarrPendingMatches(requestedIndex, PENDING_PAGE_SIZE, targetPage * PENDING_PAGE_SIZE)
      let total = typeof response.total === 'number' ? response.total : (response.items?.length ?? 0)
      // If we paged past the end (items resolved/dismissed away), clamp to the last page and refetch.
      const lastPage = Math.max(0, Math.ceil(total / PENDING_PAGE_SIZE) - 1)
      if (targetPage > lastPage) {
        targetPage = lastPage
        response = await getMakerIdarrPendingMatches(requestedIndex, PENDING_PAGE_SIZE, targetPage * PENDING_PAGE_SIZE)
        total = typeof response.total === 'number' ? response.total : (response.items?.length ?? 0)
      }
      const items = response.items || []
      pendingPageRef.current = targetPage
      setPendingPage(targetPage)
      setPendingTotal(total)
      setPendingItems(items)
      void refreshIdarrPendingCount()
      return items
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load pending matches'), 'error')
      return []
    } finally {
      if (!silent) {
        setPendingLoading(false)
      }
    }
  }

  const goToPendingPage = async (page: number) => {
    if (pendingPaging) {
      return
    }
    setPendingPaging(true)
    try {
      await loadPendingMatches({ page, silent: true })
    } finally {
      setPendingPaging(false)
    }
  }

  const openResolverAtIndex = async (index: number): Promise<boolean> => {
    const requested = Math.max(0, index)
    try {
      const response = await getMakerIdarrPendingMatches(selectedSyncTargetIndex, 1, requested)
      let total = typeof response.total === 'number' ? response.total : (response.items?.length ?? 0)
      setPendingTotal(total)
      if (total <= 0) {
        resetResolver()
        return false
      }
      let item = (response.items || [])[0]
      const clamped = Math.min(requested, total - 1)
      if (!item && clamped !== requested) {
        const retry = await getMakerIdarrPendingMatches(selectedSyncTargetIndex, 1, clamped)
        total = typeof retry.total === 'number' ? retry.total : total
        setPendingTotal(total)
        item = (retry.items || [])[0]
      }
      if (!item) {
        resetResolver()
        return false
      }
      setResolverIndex(clamped)
      await openResolver(item)
      return true
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load pending match'), 'error')
      return false
    }
  }

  const refreshPendingAndHandleResolverAdvance = async (
    resolvedAssetKey: string,
    options?: { forceAdvance?: boolean },
  ) => {
    void loadPendingMatches({ silent: true })

    if (!resolverItem || resolverItem.asset_key !== resolvedAssetKey) {
      return
    }

    const shouldAutoAdvance = options?.forceAdvance ?? resolverAutoAdvance
    if (!shouldAutoAdvance) {
      resetResolver()
      return
    }

    const advanced = await openResolverAtIndex(resolverIndex)
    if (!advanced) {
      resetResolver()
    }
  }

  const loadIgnoredTitles = async (syncTargetIndex?: number) => {
    const requestedIndex = typeof syncTargetIndex === 'number' ? syncTargetIndex : selectedSyncTargetIndex
    try {
      const response = await getMakerIdarrIgnoredTitles(requestedIndex)
      setIgnoredItems(response.items || [])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load ignored titles'), 'error')
    }
  }

  const loadCacheStats = async (syncTargetIndex?: number) => {
    const requestedIndex = typeof syncTargetIndex === 'number' ? syncTargetIndex : selectedSyncTargetIndex
    try {
      const stats = await getMakerIdarrCacheStats(requestedIndex)
      setCacheStats(stats)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load cache stats'), 'error')
    }
  }

  const loadRuntimeData = async ({
    silentPending = false,
    syncTargetIndex,
  }: {
    silentPending?: boolean
    syncTargetIndex?: number
  } = {}) => {
    const requestedIndex = typeof syncTargetIndex === 'number' ? syncTargetIndex : selectedSyncTargetIndex
    try {
      const [lastRunData] = await Promise.all([
        getMakerIdarrLastRun(requestedIndex),
        loadPendingMatches({ silent: silentPending, syncTargetIndex: requestedIndex, page: 0 }),
        loadCacheStats(requestedIndex),
        loadIgnoredTitles(requestedIndex),
      ])
      setLastRun(lastRunData && Object.keys(lastRunData).length > 0 ? lastRunData : null)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to refresh IDarr data'), 'error')
    }
  }

  const resetRuntimeData = () => {
    setLastRun(null)
    setPendingItems([])
    setIgnoredItems([])
    setCacheStats({
      total: 0,
      matched: 0,
      unmatched: 0,
      never_checked: 0,
    })
  }

  const loadIDarrTabData = async () => {
    try {
      setLoading(true)
      const data = await getMakerIdarrConfig()
      const mergedConfig = cloneIdarrConfig({ ...DEFAULT_IDARR_CONFIG, ...data })
      setConfig(mergedConfig)
      originalConfigRef.current = cloneIdarrConfig(mergedConfig)
      setHasUnsavedSettings(false)
      requestAnimationFrame(() => { setConfigLoaded(true) })
      const resolvedTargets = Array.isArray(mergedConfig.sync_targets) ? mergedConfig.sync_targets : []
      let resolvedIndex = 0
      if (resolvedTargets.length > 0) {
        const storedTargetValue = localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)
        if (storedTargetValue) {
          const storedIndex = resolvedTargets.findIndex((target) => getSyncTargetStorageValue(target) === storedTargetValue)
          if (storedIndex >= 0) {
            resolvedIndex = storedIndex
          } else {
            resolvedIndex = Math.min(selectedSyncTargetIndex, resolvedTargets.length - 1)
          }
        } else {
          resolvedIndex = Math.min(selectedSyncTargetIndex, resolvedTargets.length - 1)
        }
      }

      setSelectedSyncTargetIndex(resolvedIndex)
      if (resolvedTargets.length === 0) {
        resetRuntimeData()
      } else {
        await loadRuntimeData({ syncTargetIndex: resolvedIndex })
      }
      hasCompletedInitialLoadRef.current = true
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load IDarr configuration'), 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadIDarrTabData()
  }, [])

  // Auto-refresh pending matches when an IDarr job completes
  const lastIdarrJobStatusRef = useRef<{ [key: number]: string }>({})
  useEffect(() => {
    wsJobs.forEach((job) => {
      if (job.job_type !== 'idarr') return
      const prev = lastIdarrJobStatusRef.current[job.id]
      const isObservedTransition = prev !== undefined && prev !== job.status
      if ((job.status === 'completed' || job.status === 'failed') && isObservedTransition) {
        void (async () => {
          const [items, lastRunData] = await Promise.all([
            loadPendingMatches({ silent: true }),
            getMakerIdarrLastRun(selectedSyncTargetIndex),
          ])
          void refreshIdarrPendingCount()
          if (lastRunData && Object.keys(lastRunData).length > 0) {
            setLastRun(lastRunData)
          }
          const unresolvedItems = items.filter((item) => !item.pending_status)
          if (job.status === 'completed' && unresolvedItems.length > 0) {
            showToast(`IDarr run complete — ${unresolvedItems.length} pending match${unresolvedItems.length === 1 ? '' : 'es'} need attention`, 'info')
          }
        })()
      }
      lastIdarrJobStatusRef.current[job.id] = job.status
    })
  }, [wsJobs])

  useEffect(() => {
    if (activeTab !== 'IDarr' || !hasCompletedInitialLoadRef.current) {
      return
    }

    if (!Array.isArray(config.sync_targets) || config.sync_targets.length === 0) {
      return
    }

    const persistedTargets = Array.isArray(originalConfigRef.current.sync_targets)
      ? originalConfigRef.current.sync_targets
      : []
    if (persistedTargets.length === 0) {
      return
    }

    const safeRuntimeIndex = Math.min(selectedSyncTargetIndex, persistedTargets.length - 1)
    void loadRuntimeData({ silentPending: true, syncTargetIndex: safeRuntimeIndex })
  }, [activeTab, config.sync_targets, selectedSyncTargetIndex])

  useEffect(() => {
    const baseline = normalizeIdarrConfigForCompare(originalConfigRef.current)
    const current = normalizeIdarrConfigForCompare(config)
    setHasUnsavedSettings(JSON.stringify(current) !== JSON.stringify(baseline))
  }, [config])

  useEffect(() => {
    setFrequencyDaysInput(String(config.frequency_days || DEFAULT_IDARR_CONFIG.frequency_days))
  }, [config.frequency_days])

  useEffect(() => {
    setTvdbFrequencyInput(String(config.tvdb_frequency || DEFAULT_IDARR_CONFIG.tvdb_frequency))
  }, [config.tvdb_frequency])

  useEffect(() => {
    setLimitInput(config.limit === null ? '' : String(config.limit))
  }, [config.limit])

  useEffect(() => {
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (activeTab === 'settings' && hasUnsavedSettings) {
        event.preventDefault()
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [activeTab, hasUnsavedSettings])

  const canRun = useMemo(() => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const selectedTarget = syncTargets[selectedSyncTargetIndex]
    return Boolean(selectedTarget && String(selectedTarget.source_dir || '').trim())
  }, [config, selectedSyncTargetIndex])

  const canSync = useMemo(() => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const selectedTarget = syncTargets[selectedSyncTargetIndex]
    return Boolean(selectedTarget && String(selectedTarget.personal_drive_id || '').trim() && String(selectedTarget.source_dir || '').trim())
  }, [config, selectedSyncTargetIndex])

  const selectedSyncTarget = useMemo(() => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    return syncTargets[selectedSyncTargetIndex] ?? null
  }, [config.sync_targets, selectedSyncTargetIndex])

  const updateConfig = <K extends keyof MakerIdarrConfig>(key: K, value: MakerIdarrConfig[K]) => {
    setConfig((previous) => ({ ...previous, [key]: value }))
  }

  const handleUploadFiles = async (incomingFiles: FileList | File[] | null) => {
    if (!incomingFiles || incomingFiles.length === 0) {
      return
    }

    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const selectedTarget = syncTargets[selectedSyncTargetIndex]

    if (!selectedTarget) {
      showToast('Select a sync target first', 'error')
      return
    }

    if (!String(selectedTarget.source_dir || '').trim()) {
      showToast('Selected sync target needs a Sync Folder', 'error')
      return
    }

    const files = Array.from(incomingFiles)
    if (files.length === 0) {
      return
    }

    try {
      setUploadingFiles(true)
      const response = await uploadMakerIdarrFiles(selectedSyncTargetIndex, files)
      const skippedMessage = response.skipped_count > 0 ? `, ${response.skipped_count} skipped` : ''
      showToast(`Uploaded ${response.uploaded_count} file(s)${skippedMessage}`, 'success')

      if (config.auto_rename_quick_add && response.uploaded_count > 0) {
        try {
          setRunning(true)
          const job = await startIdarr(false, selectedSyncTargetIndex, response.uploaded, config.auto_upload_quick_add)
          showToast(`IDarr auto-rename started for uploaded file(s) (Job ID: ${job.id})`, 'success')
          const refreshed = await getMakerIdarrLastRun(selectedSyncTargetIndex)
          setLastRun(refreshed && Object.keys(refreshed).length > 0 ? refreshed : null)
          await loadPendingMatches()
          await loadCacheStats()
          await loadIgnoredTitles()
        } catch (error) {
          showToast(getApiErrorMessage(error, 'Files uploaded, but failed to start IDarr auto-rename'), 'error')
        } finally {
          setRunning(false)
        }
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to upload files to IDarr source folder'), 'error')
    } finally {
      setUploadingFiles(false)
      setIsDragOverUpload(false)
      if (uploadInputRef.current) {
        uploadInputRef.current.value = ''
      }
    }
  }

  const openUploadPicker = () => {
    if (!uploadingFiles) {
      uploadInputRef.current?.click()
    }
  }

  const onUploadInputChange = async (event: ChangeEvent<HTMLInputElement>) => {
    await handleUploadFiles(event.target.files)
  }

  const onUploadDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragOverUpload(false)
    await handleUploadFiles(event.dataTransfer?.files || null)
  }

  const onUploadDragOver = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    if (!isDragOverUpload) {
      setIsDragOverUpload(true)
    }
  }

  const onUploadDragLeave = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    setIsDragOverUpload(false)
  }

  const updateSyncTarget = (index: number, key: 'label' | 'personal_drive_id' | 'source_dir' | 'is_asset_drive' | 'is_psd_drive', value: string | boolean) => {
    const currentTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    if (index < 0 || index >= currentTargets.length) {
      return
    }

    const updatedTargets = currentTargets.map((target, targetIndex) => (
      targetIndex === index ? { ...target, [key]: value } : target
    ))

    updateConfig('sync_targets', updatedTargets)
  }

  // Assets and PSD drive types are mutually exclusive — set both flags in a single update so a
  // drive is exactly one of: normal ('none'), assets, or psd.
  const setSyncTargetDriveType = (index: number, type: 'asset' | 'psd' | 'none') => {
    const currentTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    if (index < 0 || index >= currentTargets.length) {
      return
    }

    const updatedTargets = currentTargets.map((target, targetIndex) => (
      targetIndex === index
        ? { ...target, is_asset_drive: type === 'asset', is_psd_drive: type === 'psd' }
        : target
    ))

    updateConfig('sync_targets', updatedTargets)
  }

  const addSyncTarget = () => {
    const currentTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const nextTargets = [
      ...currentTargets,
      {
        label: `Drive ${currentTargets.length + 1}`,
        personal_drive_id: '',
        source_dir: '',
        is_asset_drive: false,
        is_psd_drive: false,
      },
    ]
    updateConfig('sync_targets', nextTargets)
    setSelectedSyncTargetIndex(nextTargets.length - 1)
  }

  const removeSyncTarget = (index: number) => {
    const currentTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    if (index < 0 || index >= currentTargets.length) {
      return
    }

    const nextTargets = currentTargets.filter((_, targetIndex) => targetIndex !== index)
    updateConfig('sync_targets', nextTargets)
    setSelectedSyncTargetIndex((previous) => {
      if (nextTargets.length === 0) {
        return 0
      }
      if (previous >= nextTargets.length) {
        return nextTargets.length - 1
      }
      return previous
    })
  }

  const handleIdarrTabChange = (nextTab: IDarrTab) => {
    if (nextTab === activeTab) {
      return
    }
    if (activeTab === 'settings' && hasUnsavedSettings) {
      setPendingTabChange(nextTab)
      setShowUnsavedModal(true)
      return
    }
    setActiveTab(nextTab)
  }

  const handleDiscardChanges = () => {
    setConfig(cloneIdarrConfig(originalConfigRef.current))
    setHasUnsavedSettings(false)
    if (pendingTabChange) {
      setActiveTab(pendingTabChange)
      setPendingTabChange(null)
    }
    setShowUnsavedModal(false)
  }

  const handleCancelDiscard = () => {
    setPendingTabChange(null)
    setShowUnsavedModal(false)
  }

  const handleConfigPersisted = () => {
    originalConfigRef.current = cloneIdarrConfig(config)
    setHasUnsavedSettings(false)
  }

  const displayedResolverCandidates = useMemo(() => (
    resolverCandidates
      .slice()
      .sort((left, right) => {
        // Closest match first: match_reason.score encodes title/year closeness
        // (exact title +70, partial +40, year match +30). Popularity/vote only break ties.
        const leftReasonScore = Number(left.match_reason?.score || 0)
        const rightReasonScore = Number(right.match_reason?.score || 0)
        if (rightReasonScore !== leftReasonScore) {
          return rightReasonScore - leftReasonScore
        }

        const leftPopularity = Number(left.popularity || 0)
        const rightPopularity = Number(right.popularity || 0)
        if (rightPopularity !== leftPopularity) {
          return rightPopularity - leftPopularity
        }

        const leftVote = Number(left.vote_average || 0)
        const rightVote = Number(right.vote_average || 0)
        if (rightVote !== leftVote) {
          return rightVote - leftVote
        }

        return 0
      })
  ), [resolverCandidates])

  const {
    handleSave,
    handleRun,
    handleRunAndSync,
    runCacheMaintenance,
    handleRevertLatestRun,
    handleSync,
  } = useIDarrOperationalActions({
    config,
    selectedSyncTargetIndex,
    onConfigPersisted: handleConfigPersisted,
    setSaving,
    setRunning,
    setSyncing,
    setCacheMaintaining,
    setReverting,
    setLastRun,
    showToast,
    loadPendingMatches,
    loadCacheStats,
    loadIgnoredTitles,
  })

  const openRunConfirmation = (dryRun: boolean) => {
    if (dryRun) {
      void handleRun(true)
      return
    }

    setPendingActionConfirm({ type: 'run', dryRun })
  }

  const openSyncConfirmation = () => {
    setPendingActionConfirm({ type: 'sync' })
  }

  const openRunAndSyncConfirmation = () => {
    setPendingActionConfirm({ type: 'run_and_sync' })
  }

  const closeActionConfirmation = () => {
    setPendingActionConfirm(null)
  }

  const openMaintenanceConfirmation = (action: MaintenanceActionType) => {
    if (action === 'revert_last_run') {
      setMaintenanceActionConfirm({
        action,
        title: 'Revert Last Run',
        message: 'Revert the latest IDarr run file operations? This will attempt to undo move/copy/rename actions where possible.',
        confirmText: 'Revert',
        variant: 'warning',
      })
      return
    }

    if (action === 'purge_stale') {
      setMaintenanceActionConfirm({
        action,
        title: 'Purge Stale Cache',
        message: `Purge stale cache rows older than ${config.frequency_days} day(s)?`,
        confirmText: 'Purge Stale',
        variant: 'warning',
      })
      return
    }

    if (action === 'prune_unmatched') {
      setMaintenanceActionConfirm({
        action,
        title: 'Prune Unmatched Cache',
        message: 'Delete unmatched cache rows?',
        confirmText: 'Prune Unmatched',
        variant: 'warning',
      })
      return
    }

    if (action === 'clear_pending') {
      setMaintenanceActionConfirm({
        action,
        title: 'Clear Pending Queue',
        message: 'Clear all pending unmatched items? This only clears the pending queue, not cache data.',
        confirmText: 'Clear Pending',
        variant: 'warning',
      })
      return
    }

    setMaintenanceActionConfirm({
      action,
      title: 'Clear All Cache',
      message: 'Clear all IDarr cache rows?',
      confirmText: 'Clear Cache',
      variant: 'danger',
    })
  }

  const closeMaintenanceConfirmation = () => {
    setMaintenanceActionConfirm(null)
  }

  const confirmMaintenanceAction = () => {
    if (!maintenanceActionConfirm) {
      return
    }

    const { action } = maintenanceActionConfirm
    setMaintenanceActionConfirm(null)

    if (action === 'revert_last_run') {
      void handleRevertLatestRun()
      return
    }

    if (action === 'purge_stale') {
      void runCacheMaintenance('purge_stale')
      return
    }

    if (action === 'prune_unmatched') {
      void runCacheMaintenance('prune_unmatched')
      return
    }

    if (action === 'clear_pending') {
      void handleClearAllPending()
      return
    }

    void runCacheMaintenance('clear_all')
  }

  const confirmPendingAction = () => {
    if (!pendingActionConfirm) {
      return
    }

    if (pendingActionConfirm.type === 'run') {
      void handleRun(pendingActionConfirm.dryRun)
    } else if (pendingActionConfirm.type === 'sync') {
      void handleSync()
    } else {
      void handleRunAndSync()
    }

    setPendingActionConfirm(null)
  }

  const sortedIgnoredItems = useMemo(() => (
    [...ignoredItems].sort((left, right) => {
      const titleCompare = String(left.title || '').localeCompare(String(right.title || ''), undefined, { sensitivity: 'base' })
      if (titleCompare !== 0) {
        return titleCompare
      }

      const leftYear = typeof left.year === 'number' ? left.year : Number.MAX_SAFE_INTEGER
      const rightYear = typeof right.year === 'number' ? right.year : Number.MAX_SAFE_INTEGER
      if (leftYear !== rightYear) {
        return leftYear - rightYear
      }

      return String(left.asset_key || '').localeCompare(String(right.asset_key || ''), undefined, { sensitivity: 'base' })
    })
  ), [ignoredItems])

  const formatIgnoredItemForEditor = (item: MakerIdarrIgnoredItem): string => {
    const title = String(item.title || '').trim()
    if (!title) {
      return ''
    }
    return typeof item.year === 'number' ? `${title} (${item.year})` : title
  }

  const openIgnoredTitlesImportPicker = () => {
    if (!importingIgnoredTitles) {
      ignoredTitlesImportInputRef.current?.click()
    }
  }

  const handleIgnoredTitlesImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }

    try {
      setImportingIgnoredTitles(true)
      const content = await file.text()
      const titles = parseIgnoredTitlesJsonc(content)
      if (titles.length === 0) {
        showToast('No ignored titles found in import file', 'error')
        return
      }

      const response = await importMakerIdarrIgnoredTitles({
        titles,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Imported ${response.added ?? 0} ignored item(s)`, 'success')
      await Promise.all([loadIgnoredTitles(), loadPendingMatches({ silent: true }), loadCacheStats()])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to import ignored titles'), 'error')
    } finally {
      setImportingIgnoredTitles(false)
      if (ignoredTitlesImportInputRef.current) {
        ignoredTitlesImportInputRef.current.value = ''
      }
    }
  }

  const openIgnoredEditor = () => {
    const lines = sortedIgnoredItems
      .map((item) => formatIgnoredItemForEditor(item))
      .filter((line) => Boolean(line))
    setIgnoredEditorValue(lines.join('\n'))
    setShowIgnoredEditorModal(true)
  }

  const closeIgnoredEditor = () => {
    setShowIgnoredEditorModal(false)
    setIgnoredEditorValue('')
  }

  const saveIgnoredEditor = async () => {
    const titles = parseIgnoredTitlesEditorText(ignoredEditorValue)

    try {
      setSavingIgnoredTitles(true)
      const response = await replaceMakerIdarrIgnoredTitles({
        titles,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Saved ignored list (${response.total} item(s))`, 'success')
      await Promise.all([loadIgnoredTitles(), loadPendingMatches({ silent: true }), loadCacheStats()])
      closeIgnoredEditor()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save ignored titles list'), 'error')
    } finally {
      setSavingIgnoredTitles(false)
    }
  }

  const wsJobsRef = useRef(wsJobs)
  wsJobsRef.current = wsJobs
  const isIdarrJobActive = () => wsJobsRef.current.some(
    (job) => job.job_type === 'idarr' && (job.status === 'running' || job.status === 'pending' || job.status === 'queued'),
  )

  const {
    handleResolvePending,
    handleResolveAndRename,
    handleResolveWithCandidate,
  } = useIDarrResolverActions({
    selectedSyncTargetIndex,
    resolverItem,
    resolverTmdbId,
    resolverTmdbType,
    resolverTvdbId,
    resolverImdbId,
    setResolving,
    showToast,
    refreshPendingAndHandleResolverAdvance,
    loadCacheStats,
    loadIgnoredTitles,
    isIdarrJobActive,
  })

  const handleManualCandidateSearch = async () => {
    if (!resolverManualSearch.trim() || !resolverItem) return
    setResolverCandidatesLoading(true)
    try {
      const response = await getMakerIdarrPendingCandidates({
        title: resolverManualSearch.trim(),
        year: null,
        type: resolverTmdbType || 'pending',
        sync_target_index: selectedSyncTargetIndex,
      })
      setResolverCandidates(response.candidates || [])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Search failed'), 'error')
    } finally {
      setResolverCandidatesLoading(false)
    }
  }

const {
    handleDismissPending,
    handleIgnorePending,
    handleRemoveIgnored,
    handleClearAllPending,
  } = useIDarrPendingActions({
    selectedSyncTargetIndex,
    resolverItem,
    setResolving,
    showToast,
    refreshPendingAndHandleResolverAdvance,
    loadPendingMatches,
    loadIgnoredTitles,
    loadCacheStats,
  })

  const handleResolveConflictFile = async (item: MakerIdarrPendingItem, selectedFile: string) => {
    try {
      setResolving(true)
      const otherFiles = (item.conflict_files ?? []).filter((f) => f !== selectedFile)
      for (const file of otherFiles) {
        await archiveIdarrSourceFile({ filename: file, sync_target_index: selectedSyncTargetIndex })
      }
      const job = await startIdarr(false, selectedSyncTargetIndex, [selectedFile])
      await resolveMakerIdarrPendingMatch({
        asset_key: item.asset_key,
        action: 'dismiss',
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Rename started for ${selectedFile} (Job ID: ${job.id})`, 'info')
      await loadPendingMatches({ silent: true })
      await loadCacheStats()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve conflict file'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const closeTargetedPruneModal = () => {
    setShowTargetedPruneModal(false)
    setPruneTitleValue('')
    setPruneAssetKeyValue('')
    setPruneTmdbValue('')
    setPruneTvdbValue('')
    setPruneImdbValue('')
  }

  const handlePruneTargetedCacheEntries = async () => {
    const title = pruneTitleValue.trim()
    const assetKey = pruneAssetKeyValue.trim()
    const tmdbRaw = pruneTmdbValue.trim()
    const tvdbRaw = pruneTvdbValue.trim()
    const imdb = pruneImdbValue.trim()

    if (!title && !assetKey && !tmdbRaw && !tvdbRaw && !imdb) {
      showToast('Enter at least one filter to purge cache entries', 'error')
      return
    }

    const payload: {
      action: 'prune_targeted'
      title?: string
      asset_key?: string
      tmdb_id?: number
      tvdb_id?: number
      imdb_id?: string
      sync_target_index?: number
    } = { action: 'prune_targeted' }

    if (title) {
      payload.title = title
    }

    if (assetKey) {
      payload.asset_key = assetKey
    }

    if (tmdbRaw) {
      const parsedTmdb = Number(tmdbRaw)
      if (!Number.isInteger(parsedTmdb) || parsedTmdb < 1) {
        showToast('TMDB ID must be a positive integer', 'error')
        return
      }
      payload.tmdb_id = parsedTmdb
    }

    if (tvdbRaw) {
      const parsedTvdb = Number(tvdbRaw)
      if (!Number.isInteger(parsedTvdb) || parsedTvdb < 1) {
        showToast('TVDB ID must be a positive integer', 'error')
        return
      }
      payload.tvdb_id = parsedTvdb
    }

    if (imdb) {
      payload.imdb_id = imdb
    }
    payload.sync_target_index = selectedSyncTargetIndex

    try {
      setCacheMaintaining(true)
      const response = await runMakerIdarrCacheMaintenance(payload)
      const firstPurged = Array.isArray(response.purged_items) && response.purged_items.length > 0
        ? response.purged_items[0]
        : null
      const firstLabel = firstPurged
        ? `${firstPurged.title}${typeof firstPurged.year === 'number' ? ` (${firstPurged.year})` : ''}`
        : ''

      if (response.deleted === 1 && firstLabel) {
        showToast(`Purged cache entry: ${firstLabel}`, 'success')
      } else if (response.deleted > 1 && firstLabel) {
        showToast(`Purged ${response.deleted} cache entries (including ${firstLabel})`, 'success')
      } else {
        showToast(`Targeted cache purge complete: ${response.deleted} deleted`, 'success')
      }
      await Promise.all([loadCacheStats(), loadPendingMatches()])
      closeTargetedPruneModal()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Targeted cache purge failed'), 'error')
    } finally {
      setCacheMaintaining(false)
    }
  }

  const isEditableTarget = (target: EventTarget | null): boolean => {
    const element = target as HTMLElement | null
    const tagName = element?.tagName?.toLowerCase()
    return tagName === 'input' || tagName === 'textarea' || tagName === 'select' || Boolean(element?.isContentEditable)
  }

  const handleResolverKeydown = (event: KeyboardEvent) => {
    if (resolving || pendingLoading || resolverCandidatesLoading) {
      return
    }

    if (isEditableTarget(event.target)) {
      return
    }

    const key = event.key.toLowerCase()
    if (key === 'escape') {
      event.preventDefault()
      if (resolverPreviewUrl) {
        setResolverPreviewUrl(null)
        return
      }
      resetResolver()
      return
    }

    if (key === 'n') {
      event.preventDefault()
      void openResolverAtIndex(resolverIndex + 1)
      return
    }

    if (key === 'p') {
      event.preventDefault()
      void openResolverAtIndex(resolverIndex - 1)
    }
  }

  useEffect(() => {
    if (!resolverItem) {
      return
    }

    window.addEventListener('keydown', handleResolverKeydown)
    return () => {
      window.removeEventListener('keydown', handleResolverKeydown)
    }
  }, [
    handleResolverKeydown,
    pendingLoading,
    resolverCandidatesLoading,
    resolverItem,
    resolverPreviewUrl,
    resolving,
  ])

  useEffect(() => {
    setSelectedCandidate(null)

    if (!resolverItem) {
      return
    }

    const modalBody = resolverModalBodyRef.current
    if (modalBody) {
      modalBody.scrollTo({ top: 0, behavior: 'auto' })
    }
  }, [resolverItem?.asset_key])

  const renderIDarrConfigurationSection = () => (
    <div className="settings-section idarr-settings-card">
      <h2>IDarr Configuration</h2>
      <p className="section-description">Split settings for connection paths, workflow behavior, and cache controls.</p>

      <div className="idarr-settings-groups">
        <div className="idarr-settings-group idarr-settings-group-combined">
          <h3>Paths, Credentials & Processing</h3>
          <div className="settings-grid idarr-settings-grid">
            <div className="field-group field-group-toggle">
              <label>
                <span>Show in Poster Workflow</span>
                <span className="idarr-toggle-control" style={{ flexShrink: 0 }}>
                  <input
                    type="checkbox"
                    checked={Boolean(config.show_in_workflow)}
                    onChange={(e) => updateConfig('show_in_workflow', e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </label>
              <small>When enabled, an IDarr Rename step will appear as step 1 in the Poster Workflow page so you can run it as part of your standard workflow.</small>
            </div>
            <div className="field-group">
              <label>Cache Frequency Days</label>
              <input
                type="text"
                inputMode="numeric"
                value={frequencyDaysInput}
                onChange={(e) => {
                  const raw = e.target.value
                  if (!/^\d*$/.test(raw)) {
                    return
                  }

                  setFrequencyDaysInput(raw)

                  if (!raw) {
                    return
                  }

                  const parsed = Number(raw)
                  if (Number.isFinite(parsed) && parsed > 0) {
                    updateConfig('frequency_days', parsed)
                  }
                }}
                onBlur={() => {
                  const parsed = Number(frequencyDaysInput)
                  const nextValue = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_IDARR_CONFIG.frequency_days
                  setFrequencyDaysInput(String(nextValue))
                  updateConfig('frequency_days', nextValue)
                }}
              />
              <small>How long TMDB cache entries are kept before refresh.</small>
            </div>

            <div className="field-group">
              <label>TVDB Rehydrate Days</label>
              <input
                type="text"
                inputMode="numeric"
                value={tvdbFrequencyInput}
                onChange={(e) => {
                  const raw = e.target.value
                  if (!/^\d*$/.test(raw)) {
                    return
                  }

                  setTvdbFrequencyInput(raw)

                  if (!raw) {
                    return
                  }

                  const parsed = Number(raw)
                  if (Number.isFinite(parsed) && parsed > 0) {
                    updateConfig('tvdb_frequency', parsed)
                  }
                }}
                onBlur={() => {
                  const parsed = Number(tvdbFrequencyInput)
                  const nextValue = Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_IDARR_CONFIG.tvdb_frequency
                  setTvdbFrequencyInput(String(nextValue))
                  updateConfig('tvdb_frequency', nextValue)
                }}
              />
              <small>How often TVDB fallback metadata is refreshed for cached items.</small>
            </div>

            <div className="field-group">
              <label>Run Limit</label>
              <input
                type="text"
                inputMode="numeric"
                value={limitInput}
                onChange={(e) => {
                  const raw = e.target.value
                  if (!/^\d*$/.test(raw)) {
                    return
                  }

                  setLimitInput(raw)

                  if (!raw) {
                    updateConfig('limit', null)
                    return
                  }

                  const parsed = Number(raw)
                  updateConfig('limit', Number.isFinite(parsed) && parsed > 0 ? parsed : null)
                }}
                onBlur={() => {
                  const raw = limitInput.trim()
                  if (!raw) {
                    setLimitInput('')
                    updateConfig('limit', null)
                    return
                  }

                  const parsed = Number(raw)
                  if (Number.isFinite(parsed) && parsed > 0) {
                    setLimitInput(String(parsed))
                    updateConfig('limit', parsed)
                    return
                  }

                  setLimitInput('')
                  updateConfig('limit', null)
                }}
                placeholder="No limit"
              />
              <small>Optional cap on number of assets processed per run. Leave blank for no limit.</small>
            </div>
          </div>
          <p className="setting-description" style={{ marginTop: '0.75rem', fontSize: '0.8rem', color: '#888' }}>
            <strong style={{ color: '#ccc' }}>TMDB API Key:</strong> Configured globally in{' '}
            <a
              href="/settings"
              style={{ color: '#64b5f6' }}
              onClick={(e) => { e.preventDefault(); localStorage.setItem('posterflow.settings.activeTab', 'basic'); navigate('/settings') }}
            >Settings → General → API Keys</a>.
          </p>
        </div>

        <div className="idarr-settings-group idarr-settings-group-behavior">
          <h3>Behavior Toggles</h3>
          <div className="idarr-toggle-grid">
            <label className="idarr-toggle-card">
              <div className="idarr-toggle-card-header">
                <div>
                  <strong>Remove Non-image Files</strong>
                  <small>Deletes non-image files from incoming folders during processing to keep poster inputs clean.</small>
                </div>
                <span className="idarr-toggle-control">
                  <input
                    type="checkbox"
                    checked={config.remove_non_image_files}
                    onChange={(e) => updateConfig('remove_non_image_files', e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </div>
            </label>

            <label className="idarr-toggle-card">
              <div className="idarr-toggle-card-header">
                <div>
                  <strong>Show Unmatched</strong>
                  <small>Includes unmatched items in run output so you can review and resolve them after processing.</small>
                </div>
                <span className="idarr-toggle-control">
                  <input
                    type="checkbox"
                    checked={config.show_unmatched}
                    onChange={(e) => updateConfig('show_unmatched', e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </div>
            </label>

            <label className="idarr-toggle-card">
              <div className="idarr-toggle-card-header">
                <div>
                  <strong>Pending Matches Only</strong>
                  <small>Only queues items needing manual review and skips normal resolved processing in that run.</small>
                </div>
                <span className="idarr-toggle-control">
                  <input
                    type="checkbox"
                    checked={config.pending_matches}
                    onChange={(e) => updateConfig('pending_matches', e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </div>
            </label>

            <label className="idarr-toggle-card">
              <div className="idarr-toggle-card-header">
                <div>
                  <strong>Skip Collections</strong>
                  <small>Ignores collection/box-set assets so only movie, series, and season posters are handled.</small>
                </div>
                <span className="idarr-toggle-control">
                  <input
                    type="checkbox"
                    checked={config.skip_collections}
                    onChange={(e) => updateConfig('skip_collections', e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </div>
            </label>

          </div>
        </div>

        <div className="idarr-settings-group idarr-settings-group-sync-targets">
          <div className="idarr-sync-targets-header">
            <h3>Personal Drive Sync Targets</h3>
            <button type="button" className="btn-toolbar" onClick={addSyncTarget} disabled={saving || loading || syncing}>
              <Plus size={16} />
              Add Personal Drive
            </button>
          </div>
          <p className="section-description">Each target includes a personal Drive folder ID and its own local sync folder.</p>

          <div className="idarr-sync-target-list">
            {(config.sync_targets || []).map((target, index) => (
              <div key={`sync-target-${index}`} className="idarr-sync-target-row">
                <div className="idarr-sync-target-card-header">
                  <strong>{target.label?.trim() || `Drive ${index + 1}`}</strong>
                  <button
                    type="button"
                    className="btn-toolbar"
                    onClick={() => removeSyncTarget(index)}
                    disabled={saving || loading || syncing}
                  >
                    <Trash2 size={16} />
                    Remove
                  </button>
                </div>
                <div className="idarr-sync-target-row-fields">
                  <div className="field-group">
                    <label>Target Name</label>
                    <input
                      type="text"
                      value={target.label || ''}
                      onChange={(e) => updateSyncTarget(index, 'label', e.target.value)}
                      placeholder={`Drive ${index + 1}`}
                    />
                  </div>
                  <div className="field-group">
                    <label>Personal Drive Folder ID</label>
                    <input
                      type="text"
                      value={target.personal_drive_id || ''}
                      onChange={(e) => updateSyncTarget(index, 'personal_drive_id', e.target.value)}
                      placeholder="Google Drive folder ID"
                    />
                  </div>
                  <div className="field-group">
                    <label>Sync Folder
                      <span className="idarr-info-icon-wrap">
                        <Info size={13} className="idarr-info-icon" />
                        <span className="idarr-info-tooltip">
                          Must be an absolute path (starting with /) — use the container-side path of any volume you've mounted, e.g. /config/idarr/sync/cl2k. Relative paths will fail, e.g. sync/cl2k.
                        </span>
                      </span>
                    </label>
                    <input
                      type="text"
                      value={target.source_dir || ''}
                      onChange={(e) => updateSyncTarget(index, 'source_dir', e.target.value)}
                      placeholder="/path/to/local/sync/folder"
                    />
                  </div>
                  <div className="idarr-drive-type-toggles">
                    <div className="field-group field-group-toggle">
                      <label>
                        <span>
                          Assets Drive
                          <span className="idarr-info-icon-wrap">
                            <Info size={13} className="idarr-info-icon" />
                            <span className="idarr-info-tooltip">
                              When enabled, this drive is treated as an assets drive. IDarr will scan <strong>logos/</strong>, <strong>backgrounds/</strong> and <strong>squareart/</strong> subfolders inside the sync folder instead of the root. Season-suffix hints are disabled — type detection relies on ID tags and TMDB lookup only.
                            </span>
                          </span>
                        </span>
                        <span className="idarr-toggle-control" style={{ flexShrink: 0 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(target.is_asset_drive)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setPendingAssetDriveToggleIndex(index)
                              } else {
                                updateSyncTarget(index, 'is_asset_drive', false)
                              }
                            }}
                          />
                          <span className="idarr-toggle-slider" />
                        </span>
                      </label>
                    </div>
                    <div className="field-group field-group-toggle">
                      <label>
                        <span>
                          PSD Drive
                          <span className="idarr-info-icon-wrap">
                            <Info size={13} className="idarr-info-icon" />
                            <span className="idarr-info-tooltip">
                              When enabled, this drive is treated as a PSD drive. IDarr scans the sync folder directly (no <strong>logos/</strong>/<strong>backgrounds/</strong>/<strong>squareart/</strong> subfolders) using asset-style matching — season-suffix hints are disabled, so type detection relies on ID tags and TMDB lookup only. Mutually exclusive with Assets Drive.
                            </span>
                          </span>
                        </span>
                        <span className="idarr-toggle-control" style={{ flexShrink: 0 }}>
                          <input
                            type="checkbox"
                            checked={Boolean(target.is_psd_drive)}
                            onChange={(e) => {
                              if (e.target.checked) {
                                setPendingPsdDriveToggleIndex(index)
                              } else {
                                updateSyncTarget(index, 'is_psd_drive', false)
                              }
                            }}
                          />
                          <span className="idarr-toggle-slider" />
                        </span>
                      </label>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
          {(config.sync_targets || []).length > 0 && (
            <div className="idarr-sync-targets-footer">
              <button
                className={`btn-toolbar ${hasUnsavedSettings ? 'btn-unsaved' : ''}`}
                onClick={handleSave}
                disabled={!hasUnsavedSettings || saving || loading}
                title={hasUnsavedSettings ? 'Save changes' : 'No changes to save'}
              >
                <Save size={16} />
                {saving ? 'Saving...' : 'Save Settings'}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )

  const renderPendingMatchesSection = () => {
    const pendingPageCount = Math.max(1, Math.ceil(pendingTotal / PENDING_PAGE_SIZE))
    const pendingSafePage = Math.min(pendingPage, pendingPageCount - 1)
    const pendingPageStart = pendingSafePage * PENDING_PAGE_SIZE
    const pendingPager = pendingPageCount > 1 ? (
      <div className="pending-pagination">
        <button
          type="button"
          className="btn-toolbar"
          disabled={pendingSafePage === 0 || resolving || pendingLoading || pendingPaging}
          onClick={() => { void goToPendingPage(pendingSafePage - 1) }}
        >
          Prev
        </button>
        <span className="pending-pagination-info">
          {pendingTotal === 0 ? 0 : pendingPageStart + 1}–{Math.min(pendingPageStart + pendingItems.length, pendingTotal)} of {pendingTotal} · Page {pendingSafePage + 1} of {pendingPageCount}
        </span>
        <button
          type="button"
          className="btn-toolbar"
          disabled={pendingSafePage >= pendingPageCount - 1 || resolving || pendingLoading || pendingPaging}
          onClick={() => { void goToPendingPage(pendingSafePage + 1) }}
        >
          Next
        </button>
      </div>
    ) : null
    return (
    <div className="settings-section idarr-pending-card">
      <div className="pending-section-header">
        <h2>Pending Matches</h2>
        <div className="action-buttons">
          <button
            type="button"
            className="btn-toolbar"
            onClick={() => {
              void loadPendingMatches()
            }}
            disabled={pendingLoading || resolving}
          >
            <RotateCw size={16} className={pendingLoading ? 'btn-icon-spin' : undefined} />
            {pendingLoading ? 'Refreshing...' : 'Refresh'}
          </button>
        </div>
      </div>
      <p className="section-description">
        Total: {pendingTotal}
      </p>
      <div className="pending-actions">
        <div className="pending-actions-row">
          <span className="pending-actions-label">Work Queue</span>
          <div className="action-buttons">
            <button
              className="btn-toolbar btn-primary"
              onClick={() => {
                void openResolverAtIndex(0)
              }}
              disabled={pendingLoading || pendingTotal === 0 || resolving}
            >
              Open First
            </button>
          </div>
        </div>

      </div>
      {pendingLoading ? (
        <p className="section-description">Loading pending matches...</p>
      ) : pendingItems.length === 0 ? (
        <p className="section-description">No pending matches.</p>
      ) : (
        <>
          {pendingPager}
          <div className="pending-list">
          {pendingItems.map((item, itemIndex) => {
            const globalIndex = pendingPage * PENDING_PAGE_SIZE + itemIndex
            const hasConflictFiles = Array.isArray(item.conflict_files) && item.conflict_files.length > 0
            return (
              <div key={item.asset_key} className={`pending-list-card${hasConflictFiles ? ' has-conflict-files' : ''}`}>
                <div className="pending-list-main">
                  <span className="pending-list-title">{item.title}{item.year ? ` (${item.year})` : ''}</span>
                  <div className="pending-list-meta">
                    {(() => {
                      const reasonChip = getPendingReasonChipMeta(item.pending_reason)
                      if (!reasonChip) {
                        return null
                      }
                      return <span className={`pending-type-chip pending-status-chip ${reasonChip.className}`}>{reasonChip.label}</span>
                    })()}
                    <span
                      className={`pending-type-chip ${item.type === 'movie' ? 'chip-movie' : item.type === 'collection' ? 'chip-collection' : item.type === 'pending' ? 'chip-pending' : 'chip-show'}`}
                    >
                      {item.type === 'movie' ? 'Movie' : item.type === 'collection' ? 'Collection' : item.type === 'pending' ? 'Pending' : 'Show'}
                    </span>
                  </div>
                </div>
                {hasConflictFiles && (
                  <div className="conflict-files-section">
                    <small className="conflict-files-label">Same target — select which to keep:</small>
                    <div className="conflict-files-grid">
                      {item.conflict_files!.map((file, fileIndex) => {
                        const fileIsPsd = isPsd(file)
                        const previewUrl = fileIsPsd ? null : getPreviewImageUrl(item.conflict_file_previews?.[fileIndex])
                        // "New" = discovered this scan; "Old" = already tracked in the cache.
                        const trackedFlags = item.conflict_file_tracked
                        const tracked = Array.isArray(trackedFlags) && trackedFlags.length === item.conflict_files!.length
                          ? trackedFlags[fileIndex]
                          : null
                        return (
                          <div key={file} className="conflict-file-cell">
                            <button
                              type="button"
                              className={`conflict-cell-thumb-btn${isTransparentImage(file) ? ' idarr-checkered' : ''}`}
                              title={previewUrl ? 'Click to preview' : undefined}
                              onClick={previewUrl ? () => setCardPreviewUrl(previewUrl) : undefined}
                              style={!previewUrl ? { cursor: 'default' } : undefined}
                            >
                              {tracked !== null && (
                                <span className={`conflict-cell-badge ${tracked ? 'conflict-cell-badge--old' : 'conflict-cell-badge--new'}`}>
                                  {tracked ? 'Old' : 'New'}
                                </span>
                              )}
                              {previewUrl ? (
                                <img src={previewUrl} alt={file} className="conflict-cell-thumb" loading="lazy" />
                              ) : fileIsPsd ? (
                                <div className="conflict-cell-thumb conflict-cell-thumb-placeholder idarr-psd-placeholder">
                                  <FileImage size={20} />
                                  <span>PSD</span>
                                </div>
                              ) : (
                                <div className="conflict-cell-thumb conflict-cell-thumb-placeholder" />
                              )}
                            </button>
                            <span className="conflict-cell-name" title={file}>{file}</span>
                            <button
                              className="btn-toolbar btn-primary conflict-cell-use-btn"
                              onClick={() => void handleResolveConflictFile(item, file)}
                              disabled={resolving}
                            >
                              Keep
                            </button>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}
                <div className="pending-list-actions">
                  {!hasConflictFiles && (
                    <button className="btn-toolbar btn-primary" onClick={() => { setResolverIndex(globalIndex); void openResolver(item) }} disabled={resolving}>
                      Resolve
                    </button>
                  )}
                  <button className="btn-toolbar" onClick={() => handleDismissPending(item)} disabled={resolving}>
                    Dismiss
                  </button>
                  {!hasConflictFiles && (
                    <button className="btn-toolbar" onClick={() => handleIgnorePending(item)} disabled={resolving}>
                      Ignore
                    </button>
                  )}
                </div>
              </div>
            )
          })}
          </div>
        </>
      )}
      {lastRun?.warnings && lastRun.warnings.length > 0 && (
        <div className="pending-list pending-warnings-list">
          {lastRun.warnings.map((w, i) => (
            <div key={i} className="pending-list-card pending-warning-card">
              <AlertTriangle size={14} className="pending-warning-icon" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}
    </div>
    )
  }

  const renderIgnoredTitlesSection = () => (
    <div className="settings-section idarr-ignored-card">
      <div className="pending-section-header ignored-section-header">
        <h2>Ignored Titles</h2>
        <div className="action-buttons">
          <button
            className="btn-toolbar"
            onClick={openIgnoredTitlesImportPicker}
            disabled={importingIgnoredTitles || savingIgnoredTitles || resolving}
          >
            {importingIgnoredTitles ? 'Importing...' : 'Import JSONC'}
          </button>
          <button
            className="btn-toolbar"
            onClick={openIgnoredEditor}
            disabled={importingIgnoredTitles || savingIgnoredTitles || resolving}
          >
            Edit List
          </button>
          <input
            ref={ignoredTitlesImportInputRef}
            type="file"
            accept=".json,.jsonc,application/json,text/plain"
            className="idarr-upload-input"
            onChange={(event) => { void handleIgnoredTitlesImport(event) }}
          />
        </div>
      </div>
      {ignoredItems.length === 0 ? (
        <p className="section-description">No ignored titles.</p>
      ) : (
        <>
          <p className="section-description">Total ignored: {sortedIgnoredItems.length}</p>
          <div className="ignored-titles-list">
            <div className="ignored-list-items">
              {sortedIgnoredItems.map((item) => {
                const titleText = `${item.title}${item.year ? ` (${item.year})` : ''}`
                return (
                  <div key={item.asset_key} className="ignored-list-card">
                    <div className="ignored-list-main">
                      <span className="ignored-list-title" title={titleText}>{titleText}</span>
                      <div className="ignored-list-meta">
                        {(() => {
                          const normalizedType = String(item.type || '').trim().toLowerCase()
                          const isCollection = normalizedType === 'collection' || normalizedType === 'collections'
                          const chip = isCollection
                            ? { label: 'Collection', className: 'chip-collection' }
                            : { label: 'Movie/Show', className: 'chip-show' }
                          return <span className={`pending-type-chip ${chip.className}`}>{chip.label}</span>
                        })()}
                      </div>
                    </div>
                    <div className="ignored-list-actions">
                      <button className="btn-toolbar" onClick={() => handleRemoveIgnored(item)} disabled={resolving}>
                        Remove Ignore
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
            </div>
        </>
      )}
    </div>
  )

  const renderSyncOptionsCard = () => {
    if (config.sync_targets.length === 0) return null
    return (
      <div className="settings-section idarr-sync-options-card">
        <h2>Run &amp; Sync Options</h2>
        <p className="section-description">Controls sync behaviour for all IDarr run types.</p>
        <label className="idarr-toggle-row">
          <span className="idarr-toggle-label-text">
            <strong>Force Sync After Run</strong>
            <small>When off, the sync is skipped if no files were renamed. When on, the sync always runs after any IDarr run that includes a sync step (Run &amp; Sync, Quick Add, and scheduled runs).</small>
          </span>
          <span className="idarr-toggle-control">
            <input
              type="checkbox"
              checked={Boolean(config.force_sync_after_run)}
              onChange={async (e) => {
                const next = e.target.checked
                const updatedConfig = { ...config, force_sync_after_run: next }
                updateConfig('force_sync_after_run', next)
                try {
                  await saveMakerIdarrConfig(updatedConfig)
                  originalConfigRef.current = cloneIdarrConfig(updatedConfig)
                  setHasUnsavedSettings(false)
                } catch (error) {
                  showToast(getApiErrorMessage(error, 'Failed to save settings'), 'error')
                }
              }}
            />
            <span className="idarr-toggle-slider" />
          </span>
        </label>
      </div>
    )
  }

  const renderRunAndCacheActionsSection = () => (
    <div className="settings-section run-cache-actions-section">
      <h2>Run & Cache Actions</h2>
      <p className="section-description">Operational tools for run recovery and cache cleanup.</p>
      <div className="pending-actions run-cache-actions-grid">
        <div className="run-cache-actions-group">
          <span className="pending-actions-label run-cache-actions-label">Maintenance Actions</span>
          <div className="action-buttons run-cache-buttons run-cache-buttons-combined">
            <button
              className="btn-toolbar"
              onClick={() => setShowTargetedPruneModal(true)}
              disabled={cacheMaintaining}
              title="Open targeted purge to delete cache entries by exact title, exact asset key, or TMDB/TVDB/IMDB IDs"
            >
              Purge by Criteria
            </button>
            <button
              className="btn-toolbar"
              onClick={() => openMaintenanceConfirmation('revert_last_run')}
              disabled={reverting || loading || !lastRun}
              title="Attempts to undo the latest IDarr run's successful move/rename/copy file operations"
            >
              {reverting ? 'Reverting...' : 'Revert Last Run'}
            </button>
            <button
              className="btn-toolbar"
              onClick={() => openMaintenanceConfirmation('purge_stale')}
              disabled={cacheMaintaining}
              title="Deletes cache entries older than the configured cache frequency or never checked"
            >
              {cacheMaintaining ? 'Running...' : 'Purge Stale'}
            </button>
            <button
              className="btn-toolbar"
              onClick={() => openMaintenanceConfirmation('prune_unmatched')}
              disabled={cacheMaintaining}
              title="Deletes unmatched entries from the IDarr cache table"
            >
              {cacheMaintaining ? 'Running...' : 'Prune Unmatched'}
            </button>
            <button
              className="btn-toolbar"
              onClick={() => {
                openMaintenanceConfirmation('clear_pending')
              }}
              disabled={pendingLoading || resolving || pendingTotal === 0}
              title="Clears all pending unmatched items"
            >
              Clear Pending
            </button>
            <button
              className="btn-toolbar"
              onClick={() => openMaintenanceConfirmation('clear_all_cache')}
              disabled={cacheMaintaining}
              title="Deletes all entries from the IDarr cache table"
            >
              {cacheMaintaining ? 'Running...' : 'Clear All Cache'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )

  const renderTargetedPruneModal = () => {
    if (!showTargetedPruneModal) {
      return null
    }

    return (
      <div className="modal-overlay">
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>Purge Cache by Criteria</h2>
            <button className="modal-close" onClick={closeTargetedPruneModal}>×</button>
          </div>
          <div className="modal-body">
            <p className="section-description">
              Enter one or more filters. Matching cache rows will be purged.
            </p>
            <ul className="section-description">
              <li>Permanently deletes matching rows from the IDarr cache table.</li>
              <li>Does not remove any poster files from disk.</li>
              <li>Does not change ignored titles.</li>
              <li>Title uses exact match (case-insensitive).</li>
              <li>Asset key uses exact match (case-insensitive).</li>
              <li>TMDB/TVDB/IMDB filters match those exact cache IDs.</li>
              <li>If multiple fields are entered, all must match the same row.</li>
            </ul>
            <div className="form-group">
              <label>Title (Exact)</label>
              <input
                type="text"
                value={pruneTitleValue}
                onChange={(event) => setPruneTitleValue(event.target.value)}
                placeholder="e.g. iZombie"
              />
            </div>
            <div className="form-group">
              <label>Asset Key (Exact)</label>
              <input
                type="text"
                value={pruneAssetKeyValue}
                onChange={(event) => setPruneAssetKeyValue(event.target.value)}
                placeholder="e.g. tv_series::izombie::2015"
              />
            </div>
            <div className="resolver-id-row">
              <div className="form-group">
                <label>TMDB ID</label>
                <input
                  type="text"
                  value={pruneTmdbValue}
                  onChange={(event) => setPruneTmdbValue(event.target.value)}
                  placeholder="e.g. 60866"
                />
              </div>
              <div className="form-group">
                <label>TVDB ID</label>
                <input
                  type="text"
                  value={pruneTvdbValue}
                  onChange={(event) => setPruneTvdbValue(event.target.value)}
                  placeholder="e.g. 281470"
                />
              </div>
              <div className="form-group">
                <label>IMDB ID</label>
                <input
                  type="text"
                  value={pruneImdbValue}
                  onChange={(event) => setPruneImdbValue(event.target.value)}
                  placeholder="e.g. tt3501584"
                />
              </div>
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={closeTargetedPruneModal} disabled={cacheMaintaining}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={() => {
                void handlePruneTargetedCacheEntries()
              }}
              disabled={cacheMaintaining}
            >
              {cacheMaintaining ? 'Purging...' : 'Purge Entries'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  const renderIgnoredEditorModal = () => {
    if (!showIgnoredEditorModal) {
      return null
    }

    return (
      <div className="modal-overlay">
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>Edit Ignored Titles</h2>
            <button className="modal-close" onClick={closeIgnoredEditor}>×</button>
          </div>
          <div className="modal-body">
            <p className="section-description">
              Enter one title per line. Use Title (Year) for movies/shows and plain title for collections.
            </p>
            <textarea
              className="ignored-editor-textarea"
              value={ignoredEditorValue}
              onChange={(event) => setIgnoredEditorValue(event.target.value)}
              placeholder="Example:\nMini-Series Collection\nBlood & Orchids (1986)"
              rows={18}
            />
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={closeIgnoredEditor} disabled={savingIgnoredTitles}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => { void saveIgnoredEditor() }} disabled={savingIgnoredTitles}>
              {savingIgnoredTitles ? 'Saving...' : 'Save List'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  const renderResolverModal = () => {
    if (!resolverItem) {
      return null
    }

    const assetSubtype = resolverItem.asset_subtype ?? null
    const sourcePosterClass = assetSubtype === 'logo'
      ? 'resolver-source-asset resolver-source-logo'
      : assetSubtype === 'background'
        ? 'resolver-source-asset resolver-source-background'
        : assetSubtype === 'squareart'
          ? 'resolver-source-asset resolver-source-squareart'
          : 'resolver-candidate-poster'
    const sourcePlaceholderLabel = assetSubtype === 'logo'
      ? 'No Logo'
      : assetSubtype === 'background'
        ? 'No Background'
        : assetSubtype === 'squareart'
          ? 'No Square Art'
          : 'No Poster'
    // PSD sources can't be rendered as an <img>; show a PSD placeholder instead of a broken image.
    const sourceIsPsd = isPsd(resolverItem.preview_url)
      || (Array.isArray(resolverItem.source_filenames) && resolverItem.source_filenames.some((f) => isPsd(f)))

    return (
      <div className="modal-overlay">
        <div className="modal-content schedule-modal resolver-modal">
          <div className="modal-header">
            <h2>Resolve Pending Match</h2>
            <button className="modal-close" onClick={resetResolver}>×</button>
          </div>
          <div className="modal-body" ref={resolverModalBodyRef}>
            <div className="form-group">
              <label>Asset</label>
              <div className="resolver-asset-header">
                <div className="resolver-candidate-poster-wrap">
                  {sourceIsPsd ? (
                    <div className={`${sourcePosterClass} resolver-candidate-poster-placeholder idarr-psd-placeholder`}>
                      <FileImage size={22} />
                      <span>PSD</span>
                    </div>
                  ) : resolverItem.preview_url ? (
                    <button
                      type="button"
                      className="resolver-candidate-poster-link resolver-source-preview-trigger"
                      title="Preview source image"
                      onClick={() => setResolverPreviewUrl(getPreviewImageUrl(resolverItem.preview_url))}
                    >
                      <img
                        src={getPreviewImageUrl(resolverItem.preview_url) || ''}
                        alt={`${resolverItem.title} source image`}
                        className={sourcePosterClass}
                        loading="lazy"
                      />
                    </button>
                  ) : (
                    <div className={`${sourcePosterClass} resolver-candidate-poster-placeholder`}>{sourcePlaceholderLabel}</div>
                  )}
                </div>
                <div className="resolver-asset-header-text">
                  <div>{resolverItem.title}{resolverItem.year ? ` (${resolverItem.year})` : ''}</div>
                  <div>
                    {(() => {
                      const chip = getTypeChipMeta(resolverItem.type)
                      return <span className={`pending-type-chip ${chip.className}`}>{chip.label}</span>
                    })()}
                  </div>
                </div>
              </div>
              <small className="resolver-item-count">
                Item {pendingTotal === 0 ? 0 : resolverIndex + 1} of {pendingTotal}
              </small>
              <div className="action-buttons">
                <button
                  className="btn-toolbar"
                  onClick={() => {
                    void openResolverAtIndex(resolverIndex - 1)
                  }}
                  disabled={resolving || pendingLoading || resolverIndex <= 0}
                >
                  Previous
                </button>
                <button
                  className="btn-toolbar"
                  onClick={() => {
                    void openResolverAtIndex(resolverIndex + 1)
                  }}
                  disabled={resolving || pendingLoading || resolverIndex >= pendingTotal - 1}
                >
                  Next
                </button>
                <button
                  className="btn-toolbar resolver-ignore-next-btn"
                  onClick={() => {
                    if (resolverItem) {
                      void handleIgnorePending(resolverItem, { forceAdvance: true })
                    }
                  }}
                  disabled={resolving || pendingLoading || !resolverItem}
                >
                  Ignore & Next
                </button>
              </div>
            </div>
            <div className="form-group">
              <small>Shortcuts: N = next pending, P = previous pending, Esc = close</small>
            </div>
            <div className="resolver-manual-section">
              <button
                type="button"
                className="resolver-manual-section-toggle"
                onClick={() => setManualSectionOpen((o) => !o)}
              >
                {manualSectionOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="resolver-manual-section-title">Manual Resolution</span>
                {!manualSectionOpen && (
                  <span className="resolver-manual-section-hint">Use if the candidates below don't match your item</span>
                )}
              </button>
              {manualSectionOpen && (
              <div className="resolver-manual-content">
              <div className="form-group">
                <label>Media Type</label>
                <small className="resolver-type-hint">Required when entering a TMDB ID manually — movie and TV IDs share the same number space.</small>
                <div className="resolver-type-buttons">
                  {(['movie', 'tv_series', 'collection'] as const).map((t) => (
                    <button
                      key={t}
                      type="button"
                      className={`resolver-type-btn${resolverTmdbType === t ? ' active' : ''}`}
                      onClick={() => setResolverTmdbType(resolverTmdbType === t ? '' : t)}
                    >
                      {t === 'movie' ? 'Movie' : t === 'tv_series' ? 'TV Show' : 'Collection'}
                    </button>
                  ))}
                </div>
              </div>
              <div className="resolver-id-row">
                <div className="form-group">
                  <label>TMDB ID</label>
                  <input type="text" value={resolverTmdbId} onChange={(e) => setResolverTmdbId(e.target.value)} placeholder="12345" />
                </div>
                <div className="form-group">
                  <label>TVDB ID</label>
                  <input type="text" value={resolverTvdbId} onChange={(e) => setResolverTvdbId(e.target.value)} placeholder="98765" />
                </div>
                <div className="form-group">
                  <label>IMDB ID</label>
                  <input type="text" value={resolverImdbId} onChange={(e) => setResolverImdbId(e.target.value)} placeholder="tt1234567" />
                </div>
              </div>
              <div className="resolver-manual-search-row">
                <input
                  type="text"
                  className="resolver-manual-search-input"
                  value={resolverManualSearch}
                  onChange={(e) => setResolverManualSearch(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void handleManualCandidateSearch() }}
                  placeholder="Search TMDB by title..."
                />
                <button
                  className="btn-toolbar"
                  onClick={() => void handleManualCandidateSearch()}
                  disabled={!resolverManualSearch.trim() || resolverCandidatesLoading}
                >
                  Search
                </button>
                {resolverManualSearch.trim() && (
                  <button
                    className="btn-toolbar"
                    onClick={() => {
                      setResolverManualSearch('')
                      if (resolverItem) void loadResolverCandidates(resolverItem)
                    }}
                    disabled={resolverCandidatesLoading}
                  >
                    Reset
                  </button>
                )}
              </div>
              </div>)}
            </div>
            <div className="form-group">
              <label>TMDB Candidates</label>
              <div className="resolver-candidates-list">
                {resolverCandidatesLoading ? (
                  <div>Searching TMDB candidates...</div>
                ) : displayedResolverCandidates.length === 0 ? (
                  <div className="resolver-no-candidates">
                    <div>No candidates found.</div>
                    {(() => {
                      const searchUrls = getResolverSearchUrls()
                      return (
                        <div className="resolver-no-candidates-links">
                          <a href={searchUrls.tmdbMovie} target="_blank" rel="noopener noreferrer" className="resolver-search-link" title="Search TMDB Movies">
                            <Search size={14} />
                            TMDB Movie
                          </a>
                          <a href={searchUrls.tmdbShow} target="_blank" rel="noopener noreferrer" className="resolver-search-link" title="Search TMDB Shows">
                            <Search size={14} />
                            TMDB Show
                          </a>
                          <a href={searchUrls.tmdbCollection} target="_blank" rel="noopener noreferrer" className="resolver-search-link" title="Search TMDB Collections">
                            <Search size={14} />
                            TMDB Collection
                          </a>
                          <a href={searchUrls.tvdb} target="_blank" rel="noopener noreferrer" className="resolver-search-link" title="Search TVDB">
                            <Search size={14} />
                            TVDB
                          </a>
                          <a href={searchUrls.google} target="_blank" rel="noopener noreferrer" className="resolver-search-link" title="Search Google">
                            <Search size={14} />
                            Google
                          </a>
                        </div>
                      )
                    })()}
                  </div>
                ) : (
                  <div className="settings-grid">
                    {displayedResolverCandidates.slice(0, 6).map((candidate) => {
                      const isSelected = selectedCandidate?.tmdb_id === candidate.tmdb_id && selectedCandidate?.media_type === candidate.media_type
                      const tmdbUrl = getCandidateTmdbUrl(candidate.tmdb_id, candidate.media_type)
                      return (
                        <div
                          key={`${candidate.tmdb_id}-${candidate.media_type}`}
                          className={`field-group resolver-candidate-card${isSelected ? ' selected' : ''}`}
                          onClick={() => setSelectedCandidate(isSelected ? null : candidate)}
                          role="button"
                          tabIndex={0}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelectedCandidate(isSelected ? null : candidate) }}
                        >
                          <div className="resolver-candidate-header">
                            <div className="resolver-candidate-poster-wrap">
                              {candidate.poster_url ? (
                                <button
                                  type="button"
                                  className="resolver-candidate-poster-link resolver-source-preview-trigger"
                                  title="Preview poster"
                                  onClick={(e) => { e.stopPropagation(); setResolverPreviewUrl(candidate.poster_url ?? null) }}
                                >
                                  <img
                                    src={candidate.poster_url}
                                    alt={`${candidate.title} poster`}
                                    className="resolver-candidate-poster"
                                    loading="lazy"
                                  />
                                </button>
                              ) : (
                                <div className="resolver-candidate-poster resolver-candidate-poster-placeholder">No Poster</div>
                              )}
                            </div>
                            <div className="resolver-candidate-header-text">
                              <label>{candidate.title}{candidate.year ? ` (${candidate.year})` : ''}</label>
                              <div className="resolver-candidate-meta-row">
                                <span>TMDB {candidate.tmdb_id}</span>
                                {(() => {
                                  const chip = getTypeChipMeta(candidate.media_type)
                                  return <span className={`pending-type-chip ${chip.className}`}>{chip.label}</span>
                                })()}
                              </div>
                              <a href={tmdbUrl} target="_blank" rel="noopener noreferrer" className="resolver-candidate-tmdb-link" onClick={(e) => e.stopPropagation()}>
                                TMDB Link ↗
                              </a>
                            </div>
                          </div>
                          <small>
                            Score: {Number(candidate.vote_average || 0).toFixed(1)} • Popularity: {Number(candidate.popularity || 0).toFixed(1)}
                          </small>
                          <small>
                            Match: {candidate.match_reason?.summary || 'tmdb_rank'}
                            {typeof candidate.match_reason?.score === 'number' ? ` • Reason Score ${candidate.match_reason.score}` : ''}
                          </small>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
              <div className="action-buttons resolver-refresh-row">
                <button
                  className="btn-toolbar"
                  onClick={() => {
                    if (resolverItem) {
                      void loadResolverCandidates(resolverItem)
                    }
                  }}
                  disabled={resolverCandidatesLoading || resolving || !resolverItem}
                >
                  {resolverCandidatesLoading ? 'Refreshing...' : 'Refresh Candidates'}
                </button>
              </div>
              {resolverHistory.length > 0 && (
                <div className="settings-grid">
                  {resolverHistory.slice(0, 3).map((entry, index) => (
                    <div key={`${entry.resolved_at || 'unknown'}-${index}`} className="field-group">
                      <label>{entry.source === 'candidate' ? 'Candidate Resolution' : 'Manual Resolution'}</label>
                      <div>
                        {entry.resolved_at || 'unknown time'}
                      </div>
                      <small>
                        IDs: {entry.tmdb_id ? `TMDB ${entry.tmdb_id}` : 'TMDB n/a'}
                        {entry.tvdb_id ? ` • TVDB ${entry.tvdb_id}` : ''}
                        {entry.imdb_id ? ` • IMDB ${entry.imdb_id}` : ''}
                      </small>
                      {entry.candidate_reason?.summary && (
                        <small>Reason: {entry.candidate_reason.summary}</small>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={resetResolver} disabled={resolving}>
              Cancel
            </button>
            {pendingTotal > 1 ? (
              <>
                <button
                  className="btn-toolbar resolver-footer-btn"
                  data-tooltip={selectedCandidate ? 'Use selected candidate and close — file not renamed, run Idarr to apply' : 'Save IDs and close — file not renamed, run Idarr to apply'}
                  onClick={() => {
                    selectedCandidate
                      ? void handleResolveWithCandidate(selectedCandidate, { forceAdvance: false })
                      : void handleResolvePending({ forceAdvance: false })
                  }}
                  disabled={resolving}
                >
                  {resolving ? 'Resolving...' : <>Resolve &amp; Close<Info size={11} className="resolver-btn-info-icon" /></>}
                </button>
                <button
                  className="btn-toolbar resolver-footer-btn"
                  data-tooltip={selectedCandidate ? 'Use selected candidate and advance to next — file not renamed, run Idarr to apply' : 'Save IDs and advance to next — file not renamed, run Idarr to apply'}
                  onClick={() => {
                    selectedCandidate
                      ? void handleResolveWithCandidate(selectedCandidate, { forceAdvance: true })
                      : void handleResolvePending({ forceAdvance: true })
                  }}
                  disabled={resolving}
                >
                  {resolving ? 'Resolving...' : <>Resolve &amp; Next<Info size={11} className="resolver-btn-info-icon" /></>}
                </button>
                <button
                  className="btn-toolbar btn-primary resolver-footer-btn"
                  data-tooltip={selectedCandidate ? 'Use selected candidate, start renaming the file, and advance to next' : 'Save IDs, start renaming the file, and advance to next'}
                  onClick={() => {
                    selectedCandidate
                      ? void handleResolveWithCandidate(selectedCandidate, { forceAdvance: true, andRename: true })
                      : void handleResolveAndRename()
                  }}
                  disabled={resolving}
                >
                  {resolving ? 'Resolving...' : <>Resolve &amp; Rename<Info size={11} className="resolver-btn-info-icon" /></>}
                </button>
              </>
            ) : (
              <>
                <button
                  className="btn-toolbar resolver-footer-btn"
                  data-tooltip={selectedCandidate ? 'Use selected candidate — file not renamed, run Idarr to apply' : 'Save IDs — file not renamed, run Idarr to apply'}
                  onClick={() => {
                    selectedCandidate
                      ? void handleResolveWithCandidate(selectedCandidate, { forceAdvance: false })
                      : void handleResolvePending({ forceAdvance: false })
                  }}
                  disabled={resolving}
                >
                  {resolving ? 'Resolving...' : <>Resolve<Info size={11} className="resolver-btn-info-icon" /></>}
                </button>
                <button
                  className="btn-toolbar btn-primary resolver-footer-btn"
                  data-tooltip={selectedCandidate ? 'Use selected candidate and immediately start renaming the file' : 'Save IDs and immediately start renaming the file'}
                  onClick={() => {
                    selectedCandidate
                      ? void handleResolveWithCandidate(selectedCandidate, { forceAdvance: false, andRename: true })
                      : void handleResolveAndRename()
                  }}
                  disabled={resolving}
                >
                  {resolving ? 'Resolving...' : <>Resolve &amp; Rename<Info size={11} className="resolver-btn-info-icon" /></>}
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  const renderResolverPreviewModal = () => {
    if (!resolverPreviewUrl || !resolverItem) {
      return null
    }

    return (
      <div
        className="modal-overlay"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setResolverPreviewUrl(null)
          }
        }}
      >
        <div className="modal-content resolver-preview-modal">
          <img
            src={resolverPreviewUrl}
            alt={`${resolverItem.title} source poster preview`}
            className={`resolver-preview-image${isTransparentImage(resolverPreviewUrl) ? ' idarr-checkered' : ''}`}
          />
        </div>
      </div>
    )
  }

  const renderActionConfirmModal = () => {
    if (!pendingActionConfirm) {
      return null
    }

    const targetLabel = String(selectedSyncTarget?.label || '').trim() || `Drive ${selectedSyncTargetIndex + 1}`
    const driveId = String(selectedSyncTarget?.personal_drive_id || '').trim() || 'Not set'
    const sourceDir = String(selectedSyncTarget?.source_dir || '').trim() || 'Not set'
    const stateLabel = pendingActionConfirm.type === 'run'
      ? (pendingActionConfirm.dryRun ? 'Dry Run' : 'Live Run')
      : pendingActionConfirm.type === 'run_and_sync'
        ? 'Run & Sync'
        : 'Personal Drive Sync'

    return (
      <div className="modal-overlay">
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>
              {pendingActionConfirm.type === 'run'
                ? 'Confirm IDarr Run'
                : pendingActionConfirm.type === 'run_and_sync'
                  ? 'Confirm IDarr Run & Sync'
                  : 'Confirm IDarr Sync'}
            </h2>
            <button className="modal-close" onClick={closeActionConfirmation}>×</button>
          </div>
          <div className="modal-body">
            <p className="section-description">
              Please confirm the selected target and action before continuing.
              {pendingActionConfirm.type === 'run_and_sync' && (
                <> IDarr will run first, then automatically sync to your personal drive on completion.</>
              )}
            </p>
            <div className="idarr-action-confirm-details">
              <p className="idarr-action-confirm-line"><strong>Action:</strong> <span>{stateLabel}</span></p>
              <p className="idarr-action-confirm-line"><strong>Selected Target:</strong> <span>{targetLabel}</span></p>
              <p className="idarr-action-confirm-line"><strong>Personal Drive ID:</strong> <span>{driveId}</span></p>
              <p className="idarr-action-confirm-line"><strong>Sync Folder:</strong> <span>{sourceDir}</span></p>
            </div>
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={closeActionConfirmation} disabled={running || syncing}>
              Cancel
            </button>
            <button className="btn-primary" onClick={confirmPendingAction} disabled={running || syncing}>
              {pendingActionConfirm.type === 'run'
                ? 'Start IDarr'
                : pendingActionConfirm.type === 'run_and_sync'
                  ? 'Start Run & Sync'
                  : 'Start Sync'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  const renderIDarrTabContent = () => (
    <>
      <div className="toolbar">
        <div className="toolbar-title">
          <h2>IDarr</h2>
          <div className="toolbar-info">
            <Info size={16} />
            <div className="toolbar-tooltip">Run native in-app IDarr processing from the UI. Configure sync targets and TMDB settings in the Settings tab.</div>
          </div>
        </div>
        <div className="action-buttons">
          <div className="btn-pair">
            <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings} disabled={saving || loading || running || syncing}>
              Scheduling
            </button>
            <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings} disabled={saving || loading || running || syncing}>
              Discord
            </button>
          </div>
          {config.sync_targets.length > 0 && (
            <select
              value={selectedSyncTargetIndex}
              onChange={(event) => setSelectedSyncTargetIndex(Number(event.target.value) || 0)}
              disabled={syncing || loading}
            >
              {config.sync_targets.map((target, index) => (
                <option key={`sync-target-option-${index}`} value={index}>
                  {target.label?.trim() || `Drive ${index + 1}`}
                </option>
              ))}
            </select>
          )}
          <button className="btn-toolbar" onClick={() => openRunConfirmation(true)} disabled={running || !canRun || loading}>
            <Eye size={16} />
            Dry Run
          </button>
          <button className="btn-toolbar btn-primary" onClick={() => openRunConfirmation(false)} disabled={running || !canRun || loading}>
            <Play size={16} />
            {running ? 'Starting...' : 'Run IDarr'}
          </button>
          <button className="btn-toolbar" onClick={openSyncConfirmation} disabled={syncing || loading || !canSync}>
            <UploadCloud size={16} />
            {syncing ? 'Starting Sync...' : 'Sync to Drive'}
          </button>
          <button className="btn-toolbar btn-primary" onClick={openRunAndSyncConfirmation} disabled={running || syncing || loading || !canRun || !canSync}>
            <Play size={16} />
            {running ? 'Starting...' : 'Run & Sync'}
          </button>
        </div>
      </div>

      <div className="idarr-layout">
        <div className="settings-section idarr-upload-card">
            <div className="idarr-upload-card-header">
              <h2>Quick Add Files</h2>
              <div className="idarr-upload-toggles">
                <label className="idarr-upload-inline-toggle">
                  <span>Auto-Rename Single Item on Upload</span>
                  <span className="idarr-toggle-control">
                    <input
                      type="checkbox"
                      checked={config.auto_rename_quick_add}
                      onChange={(e) => {
                        const next = e.target.checked
                        updateConfig('auto_rename_quick_add', next)
                        void saveMakerIdarrConfig({ ...config, auto_rename_quick_add: next })
                      }}
                    />
                    <span className="idarr-toggle-slider" />
                  </span>
                </label>
                <label className={`idarr-upload-inline-toggle ${!config.auto_rename_quick_add ? 'idarr-toggle-disabled' : ''}`}>
                  <span>Auto-Upload to GDrive after Rename</span>
                  <span className="idarr-toggle-control">
                    <input
                      type="checkbox"
                      checked={config.auto_upload_quick_add}
                      disabled={!config.auto_rename_quick_add}
                      onChange={(e) => {
                        const next = e.target.checked
                        updateConfig('auto_upload_quick_add', next)
                        void saveMakerIdarrConfig({ ...config, auto_upload_quick_add: next })
                      }}
                    />
                    <span className="idarr-toggle-slider" />
                  </span>
                </label>
              </div>
            </div>
            <p className="section-description">Drop poster-maker images here, or browse files to add them to the selected sync target folder.</p>
            <div
              className={`idarr-upload-dropzone ${isDragOverUpload ? 'is-drag-over' : ''} ${uploadingFiles ? 'is-uploading' : ''}`}
              onDrop={(event) => { void onUploadDrop(event) }}
              onDragOver={onUploadDragOver}
              onDragLeave={onUploadDragLeave}
              onClick={openUploadPicker}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  openUploadPicker()
                }
              }}
              aria-disabled={uploadingFiles}
            >
              <UploadCloud size={22} />
              <div>
                <strong>{uploadingFiles ? 'Uploading files…' : 'Drag & drop files here'}</strong>
                <p>Accepted: .jpg, .jpeg, .png, .webp, .psd</p>
              </div>
              <button
                type="button"
                className="btn-toolbar"
                onClick={(event) => {
                  event.stopPropagation()
                  openUploadPicker()
                }}
                disabled={uploadingFiles}
              >
                <FolderOpen size={16} />
                Open Files
              </button>
              <input
                ref={uploadInputRef}
                type="file"
                accept=".jpg,.jpeg,.png,.webp,.psd"
                multiple
                className="idarr-upload-input"
                onChange={(event) => { void onUploadInputChange(event) }}
              />
            </div>
          </div>
        {renderPendingMatchesSection()}
        {renderSyncOptionsCard()}
        {renderRunAndCacheActionsSection()}
        {renderIgnoredTitlesSection()}
      </div>
    </>
  )

  const renderSettingsTabContent = () => (
    <>
      <div className="toolbar">
        <div className="toolbar-title">
          <h2>Settings</h2>
          <div className="toolbar-info">
            <Info size={16} />
            <div className="toolbar-tooltip">Configure your IDarr workflow options, including TMDB API key, sync targets, and processing behaviour.</div>
          </div>
        </div>
        <div className="action-buttons">
          <div className="btn-pair">
            <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings} disabled={saving || loading}>
              Scheduling
            </button>
            <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings} disabled={saving || loading}>
              Discord
            </button>
          </div>
          <button
            className={`btn-toolbar ${hasUnsavedSettings ? 'btn-unsaved' : ''}`}
            onClick={handleSave}
            disabled={!hasUnsavedSettings || saving || loading}
            title={hasUnsavedSettings ? 'Save changes' : 'No changes to save'}
          >
            <Save size={16} />
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      {renderIDarrConfigurationSection()}
    </>
  )

  return (
    <div className={`page-container idarr-page${configLoaded ? ' toggle-animations-enabled' : ''}`}>
      <div className="idarr-header">
        <h1>IDarr</h1>
        <p>Native in-app IDarr workflow tools</p>
      </div>

      <IDarrTabs activeTab={activeTab} onTabChange={handleIdarrTabChange} />

      {activeTab === 'IDarr' ? renderIDarrTabContent() : renderSettingsTabContent()}

      <UnsavedChangesModal
        isOpen={showUnsavedModal}
        onCancel={handleCancelDiscard}
        onDiscard={handleDiscardChanges}
      />

      <ConfirmDialog
        isOpen={Boolean(maintenanceActionConfirm)}
        title={maintenanceActionConfirm?.title || 'Confirm Action'}
        message={maintenanceActionConfirm?.message || ''}
        confirmText={maintenanceActionConfirm?.confirmText || 'Confirm'}
        cancelText="Cancel"
        variant={maintenanceActionConfirm?.variant || 'warning'}
        onConfirm={confirmMaintenanceAction}
        onCancel={closeMaintenanceConfirmation}
      />

      <ConfirmDialog
        isOpen={pendingAssetDriveToggleIndex !== null}
        title="Enable Assets Drive?"
        message="Assets drives behave differently from poster drives. IDarr will scan logos/, backgrounds/ and squareart/ subfolders instead of the root folder, and season-suffix hints are disabled — type detection relies on ID tags and TMDB lookup only. Make sure your sync folder contains these subfolders before running."
        confirmText="Enable Assets Drive"
        cancelText="Cancel"
        variant="warning"
        onConfirm={() => {
          if (pendingAssetDriveToggleIndex !== null) {
            setSyncTargetDriveType(pendingAssetDriveToggleIndex, 'asset')
          }
          setPendingAssetDriveToggleIndex(null)
        }}
        onCancel={() => setPendingAssetDriveToggleIndex(null)}
      />

      <ConfirmDialog
        isOpen={pendingPsdDriveToggleIndex !== null}
        title="Enable PSD Drive?"
        message="PSD drives behave differently from poster drives. IDarr will scan the sync folder directly (no logos/, backgrounds/ or squareart/ subfolders) using asset-style matching — season-suffix hints are disabled, so type detection relies on ID tags and TMDB lookup only. This will turn off Assets Drive for this target."
        confirmText="Enable PSD Drive"
        cancelText="Cancel"
        variant="warning"
        onConfirm={() => {
          if (pendingPsdDriveToggleIndex !== null) {
            setSyncTargetDriveType(pendingPsdDriveToggleIndex, 'psd')
          }
          setPendingPsdDriveToggleIndex(null)
        }}
        onCancel={() => setPendingPsdDriveToggleIndex(null)}
      />

      {cardPreviewUrl && (
        <div className="modal-overlay" onClick={() => setCardPreviewUrl(null)}>
          <div className="modal-content resolver-preview-modal">
            <img src={cardPreviewUrl} alt="Conflict file preview" className={`resolver-preview-image${isTransparentImage(cardPreviewUrl) ? ' idarr-checkered' : ''}`} />
          </div>
        </div>
      )}
      {renderResolverModal()}
      {renderResolverPreviewModal()}
      {renderTargetedPruneModal()}
      {renderIgnoredEditorModal()}
      {renderActionConfirmModal()}
    </div>
  )
}

export default IDarr
