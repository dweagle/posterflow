import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Check, ChevronDown, ChevronUp, CircleHelp, Clapperboard, Clapperboard as MovieIcon, Copy, Download, ExternalLink, FolderOpen, Globe, Image, Info, Layers, Monitor, Paintbrush, Play, Plus, Save, Search, SlidersHorizontal, Sparkles, Trash2, Tv } from 'lucide-react'
import {
  getApiErrorMessage,
  Drive,
  getDrives,
  getMakerMonitorConfig,
  getMakerMonitorLastResult,
  getSeasonImages,
  getTmdbImages,
  getTmdbImageProxyUrl,
  getTvDetails,
  MakerMonitorConfig,
  MakerMonitorRunResponse,
  runMakerMonitor,
  saveMakerMonitorConfig,
  searchTmdb,
  TmdbImage,
  TmdbImagesResponse,
  TmdbSearchFilter,
  TmdbSearchResult,
  TmdbTvDetails,
} from '../api/client'
import { useToast } from '../components/Toast'
import { useUnmatched } from '../contexts/UnmatchedContext'
import './MakerTools.css'

type ResultTab = string
type DiscoveryTab = 'series' | 'movies'
type MainTab = 'monitor' | 'tmdb-search'

const APPLE_TV_STOREFRONTS = [
  { value: '143441', label: 'United States of America' },
  { value: '143444', label: 'United Kingdom' },
  { value: '143460', label: 'Australia' },
  { value: '143455', label: 'Canada' },
  { value: '143442', label: 'France' },
  { value: '143443', label: 'Germany' },
  { value: '143450', label: 'Italy' },
  { value: '143462', label: 'Japan' },
  { value: '143452', label: 'Netherlands' },
  { value: '143461', label: 'New Zealand' },
  { value: '143457', label: 'Norway' },
  { value: '143454', label: 'Spain' },
  { value: '143456', label: 'Sweden' },
  { value: '143459', label: 'Switzerland' },
  { value: '143563', label: 'Algeria' },
  { value: '143564', label: 'Angola' },
  { value: '143538', label: 'Anguilla' },
  { value: '143540', label: 'Antigua & Barbuda' },
  { value: '143505', label: 'Argentina' },
  { value: '143524', label: 'Armenia' },
  { value: '143445', label: 'Austria' },
  { value: '143568', label: 'Azerbaijan' },
  { value: '143559', label: 'Bahrain' },
  { value: '143490', label: 'Bangladesh' },
  { value: '143541', label: 'Barbados' },
  { value: '143565', label: 'Belarus' },
  { value: '143446', label: 'Belgium' },
  { value: '143555', label: 'Belize' },
  { value: '143542', label: 'Bermuda' },
  { value: '143556', label: 'Bolivia' },
  { value: '143525', label: 'Botswana' },
  { value: '143503', label: 'Brazil' },
  { value: '143543', label: 'British Virgin Islands' },
  { value: '143560', label: 'Brunei' },
  { value: '143526', label: 'Bulgaria' },
  { value: '143544', label: 'Cayman Islands' },
  { value: '143483', label: 'Chile' },
  { value: '143465', label: 'China' },
  { value: '143501', label: 'Colombia' },
  { value: '143495', label: 'Costa Rica' },
  { value: '143527', label: "Cote D'Ivoire" },
  { value: '143494', label: 'Croatia' },
  { value: '143557', label: 'Cyprus' },
  { value: '143489', label: 'Czech Republic' },
  { value: '143458', label: 'Denmark' },
  { value: '143545', label: 'Dominica' },
  { value: '143508', label: 'Dominican Rep.' },
  { value: '143509', label: 'Ecuador' },
  { value: '143516', label: 'Egypt' },
  { value: '143506', label: 'El Salvador' },
  { value: '143518', label: 'Estonia' },
  { value: '143447', label: 'Finland' },
  { value: '143573', label: 'Ghana' },
  { value: '143448', label: 'Greece' },
  { value: '143546', label: 'Grenada' },
  { value: '143504', label: 'Guatemala' },
  { value: '143553', label: 'Guyana' },
  { value: '143510', label: 'Honduras' },
  { value: '143463', label: 'Hong Kong' },
  { value: '143482', label: 'Hungary' },
  { value: '143558', label: 'Iceland' },
  { value: '143467', label: 'India' },
  { value: '143476', label: 'Indonesia' },
  { value: '143449', label: 'Ireland' },
  { value: '143491', label: 'Israel' },
  { value: '143511', label: 'Jamaica' },
  { value: '143528', label: 'Jordan' },
  { value: '143517', label: 'Kazakstan' },
  { value: '143529', label: 'Kenya' },
  { value: '143466', label: 'Korea, Republic Of' },
  { value: '143493', label: 'Kuwait' },
  { value: '143519', label: 'Latvia' },
  { value: '143497', label: 'Lebanon' },
  { value: '143522', label: 'Liechtenstein' },
  { value: '143520', label: 'Lithuania' },
  { value: '143451', label: 'Luxembourg' },
  { value: '143515', label: 'Macau' },
  { value: '143530', label: 'Macedonia' },
  { value: '143531', label: 'Madagascar' },
  { value: '143473', label: 'Malaysia' },
  { value: '143488', label: 'Maldives' },
  { value: '143532', label: 'Mali' },
  { value: '143521', label: 'Malta' },
  { value: '143533', label: 'Mauritius' },
  { value: '143468', label: 'Mexico' },
  { value: '143523', label: 'Moldova, Republic Of' },
  { value: '143547', label: 'Montserrat' },
  { value: '143484', label: 'Nepal' },
  { value: '143512', label: 'Nicaragua' },
  { value: '143534', label: 'Niger' },
  { value: '143561', label: 'Nigeria' },
  { value: '143562', label: 'Oman' },
  { value: '143477', label: 'Pakistan' },
  { value: '143485', label: 'Panama' },
  { value: '143513', label: 'Paraguay' },
  { value: '143507', label: 'Peru' },
  { value: '143474', label: 'Philippines' },
  { value: '143478', label: 'Poland' },
  { value: '143453', label: 'Portugal' },
  { value: '143498', label: 'Qatar' },
  { value: '143487', label: 'Romania' },
  { value: '143469', label: 'Russia' },
  { value: '143479', label: 'Saudi Arabia' },
  { value: '143535', label: 'Senegal' },
  { value: '143500', label: 'Serbia' },
  { value: '143464', label: 'Singapore' },
  { value: '143496', label: 'Slovakia' },
  { value: '143499', label: 'Slovenia' },
  { value: '143472', label: 'South Africa' },
  { value: '143486', label: 'Sri Lanka' },
  { value: '143548', label: 'St. Kitts & Nevis' },
  { value: '143549', label: 'St. Lucia' },
  { value: '143550', label: 'St. Vincent & The Grenadines' },
  { value: '143554', label: 'Suriname' },
  { value: '143470', label: 'Taiwan' },
  { value: '143572', label: 'Tanzania' },
  { value: '143475', label: 'Thailand' },
  { value: '143539', label: 'The Bahamas' },
  { value: '143551', label: 'Trinidad & Tobago' },
  { value: '143536', label: 'Tunisia' },
  { value: '143480', label: 'Turkey' },
  { value: '143552', label: 'Turks & Caicos' },
  { value: '143537', label: 'Uganda' },
  { value: '143492', label: 'Ukraine' },
  { value: '143481', label: 'United Arab Emirates' },
  { value: '143514', label: 'Uruguay' },
  { value: '143566', label: 'Uzbekistan' },
  { value: '143502', label: 'Venezuela' },
  { value: '143471', label: 'Vietnam' },
  { value: '143571', label: 'Yemen' },
]

const TMDB_IMAGE_LANGUAGES = [
  { value: 'all', label: 'All Languages' },
  { value: 'en+textless', label: 'English + Textless' },
  { value: 'en', label: 'English' },
  { value: 'ar', label: 'Arabic' },
  { value: 'zh', label: 'Chinese' },
  { value: 'cs', label: 'Czech' },
  { value: 'da', label: 'Danish' },
  { value: 'nl', label: 'Dutch' },
  { value: 'fi', label: 'Finnish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'el', label: 'Greek' },
  { value: 'he', label: 'Hebrew' },
  { value: 'hi', label: 'Hindi' },
  { value: 'hu', label: 'Hungarian' },
  { value: 'id', label: 'Indonesian' },
  { value: 'it', label: 'Italian' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'no', label: 'Norwegian' },
  { value: 'pl', label: 'Polish' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'ro', label: 'Romanian' },
  { value: 'ru', label: 'Russian' },
  { value: 'sk', label: 'Slovak' },
  { value: 'es', label: 'Spanish' },
  { value: 'sv', label: 'Swedish' },
  { value: 'th', label: 'Thai' },
  { value: 'tr', label: 'Turkish' },
  { value: 'uk', label: 'Ukrainian' },
  { value: 'vi', label: 'Vietnamese' },
]

const DEFAULT_MONITOR_CONFIG: MakerMonitorConfig = {
  tmdb_api_key: '',
  lookahead_days: 21,
  missing_retention_days: 2,
  drive_ids: [],
  enable_discovery: true,
  discovery_popularity: 1,
  discovery_vote_count: 0,
  discovery_max_results: 25,
  discovery_languages: ['en', 'ko', 'ja', 'zh', 'es'],
}

const cloneMonitorConfig = (value: MakerMonitorConfig): MakerMonitorConfig => ({
  tmdb_api_key: String(value.tmdb_api_key || ''),
  lookahead_days: Number(value.lookahead_days || 21),
  missing_retention_days: Number.isFinite(Number(value.missing_retention_days)) ? Math.max(0, Number(value.missing_retention_days)) : 2,
  drive_ids: Array.isArray(value.drive_ids)
    ? value.drive_ids.map((driveId) => Number(driveId)).filter((driveId) => Number.isFinite(driveId) && driveId > 0)
    : [],
  enable_discovery: Boolean(value.enable_discovery),
  discovery_popularity: Number.isFinite(Number(value.discovery_popularity)) ? Number(value.discovery_popularity) : 1,
  discovery_vote_count: Number.isFinite(Number(value.discovery_vote_count)) ? Number(value.discovery_vote_count) : 0,
  discovery_max_results: Number.isFinite(Number(value.discovery_max_results)) ? Number(value.discovery_max_results) : 25,
  discovery_languages: Array.isArray(value.discovery_languages)
    ? value.discovery_languages.map((item) => String(item).trim().toLowerCase()).filter(Boolean)
    : ['en', 'ko', 'ja', 'zh', 'es'],
})

function MakerTools() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState<MainTab>('tmdb-search')
  const [drives, setDrives] = useState<Drive[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [startedJobId, setStartedJobId] = useState<number | null>(null)
  const [config, setConfig] = useState<MakerMonitorConfig>(DEFAULT_MONITOR_CONFIG)
  const [modalConfig, setModalConfig] = useState<MakerMonitorConfig>(DEFAULT_MONITOR_CONFIG)
  const [showConfigModal, setShowConfigModal] = useState(false)
  const [result, setResult] = useState<MakerMonitorRunResponse | null>(null)
  const [resultTab, setResultTab] = useState<ResultTab>('')
  const [discoveryTab, setDiscoveryTab] = useState<DiscoveryTab>('series')
  const [modalDiscoveryLanguagesInput, setModalDiscoveryLanguagesInput] = useState('en, ko, ja, zh, es')
  const [tmdbQuery, setTmdbQuery] = useState('')
  const [tmdbFilter, setTmdbFilter] = useState<TmdbSearchFilter>('all')
  const [tmdbSearching, setTmdbSearching] = useState(false)
  const [tmdbResults, setTmdbResults] = useState<TmdbSearchResult[] | null>(null)
  const [tmdbError, setTmdbError] = useState<string | null>(null)
  const [tmdbPreviewPoster, setTmdbPreviewPoster] = useState<string | null>(null)
  const [tmdbHelpExpanded, setTmdbHelpExpanded] = useState(false)
  // Image gallery: key = `${media_type}-${tmdb_id}`
  const [galleryOpenKey, setGalleryOpenKey] = useState<string | null>(null)
  const [galleryData, setGalleryData] = useState<Record<string, TmdbImagesResponse>>({})
  const [galleryLoading, setGalleryLoading] = useState<Record<string, boolean>>({})
  const [galleryTab, setGalleryTab] = useState<Record<string, 'posters' | 'backdrops' | 'logos' | 'season-posters'>>({})
  const [galleryPreview, setGalleryPreview] = useState<TmdbImage | null>(null)
  const [galleryLanguage, setGalleryLanguage] = useState('en+textless')
  // TV show seasons
  const [tvDetails, setTvDetails] = useState<Record<string, TmdbTvDetails>>({})
  const [tvDetailsLoading, setTvDetailsLoading] = useState<Record<string, boolean>>({})
  const [selectedSeason, setSelectedSeason] = useState<Record<string, number>>({})
  const [seasonImages, setSeasonImages] = useState<Record<string, TmdbImagesResponse>>({})
  const [seasonImagesLoading, setSeasonImagesLoading] = useState<Record<string, boolean>>({})
  const { showToast } = useToast()

  const handleGalleryDownload = async (filePath: string) => {
    const url = getTmdbImageProxyUrl(filePath)
    const filename = filePath.split('/').filter(Boolean).pop() ?? 'poster.jpg'
    try {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const blob = await resp.blob()
      const objectUrl = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = objectUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(objectUrl)
    } catch {
      showToast('Failed to download image', 'error')
    }
  }

  const { jobs } = useUnmatched()
  const completionHandledRef = useRef(false)
  const prevIsMonitorJobActiveRef = useRef(false)
  const tmdbCacheRef = useRef<Map<string, TmdbSearchResult[]>>(new Map())
  // Apple TV artwork popup
  const [appleTvPopupKey, setAppleTvPopupKey] = useState<string | null>(null)
  const [appleTvStorefront, setAppleTvStorefront] = useState(() =>
    localStorage.getItem('apple-tv-storefront') ?? '143441'
  )

  const isMonitorJobActive = useMemo(() => {
    return jobs.some((job) => {
      if (job.job_type !== 'maker_monitor') {
        return false
      }
      return job.status === 'pending' || job.status === 'running'
    })
  }, [jobs])

  const startedMonitorJob = useMemo(() => {
    if (!startedJobId) {
      return null
    }
    return jobs.find((job) => job.id === startedJobId) || null
  }, [jobs, startedJobId])

  const applyMonitorResult = (monitorResult: MakerMonitorRunResponse) => {
    setResult(monitorResult)

    if (monitorResult.libraries.length > 0) {
      const first = monitorResult.libraries[0]
      setResultTab(`lib-${first.library_name}-${first.library_type}`)
    } else if ((monitorResult.discovery?.shows.length || 0) + (monitorResult.discovery?.movies.length || 0) > 0) {
      setResultTab('discovery')
    }
  }

  const refreshMonitorLastResult = async () => {
    const lastResult = await getMakerMonitorLastResult()
    if (lastResult && typeof lastResult === 'object' && 'libraries' in lastResult) {
      applyMonitorResult(lastResult as MakerMonitorRunResponse)
    }
  }

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true)
        const [fetched, driveList] = await Promise.all([
          getMakerMonitorConfig(),
          getDrives(),
        ])
        const normalizedConfig = cloneMonitorConfig(fetched)
        setConfig(normalizedConfig)
        setModalConfig(normalizedConfig)
        setModalDiscoveryLanguagesInput(normalizedConfig.discovery_languages.join(', '))
        setDrives(driveList)

        try {
          await refreshMonitorLastResult()
        } catch {
          // Non-blocking: page still works without persisted result
        }
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to load Maker Monitor config'), 'error')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [showToast])

  const addDriveSelection = () => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids.push(0)
      return next
    })
  }

  const removeDriveSelection = (index: number) => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids = next.drive_ids.filter((_, itemIndex) => itemIndex !== index)
      return next
    })
  }

  const updateDriveSelection = (index: number, selectedDriveId: number) => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids[index] = selectedDriveId
      return next
    })
  }

  const openConfigModal = () => {
    const normalized = cloneMonitorConfig(config)
    setModalConfig(normalized)
    setModalDiscoveryLanguagesInput(normalized.discovery_languages.join(', '))
    setShowConfigModal(true)
  }

  const closeConfigModal = () => {
    setShowConfigModal(false)
  }

  const handleSaveConfig = async () => {
    try {
      setSaving(true)
      const normalizedPayload = cloneMonitorConfig(modalConfig)
      const saved = await saveMakerMonitorConfig(normalizedPayload)
      const normalized = cloneMonitorConfig(saved)
      setConfig(normalized)
      setModalConfig(normalized)
      setModalDiscoveryLanguagesInput(normalized.discovery_languages.join(', '))
      setShowConfigModal(false)
      showToast('Monitor configuration saved', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save monitor configuration'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleRunMonitor = async () => {
    if (isMonitorJobActive) {
      showToast('Monitor is already running', 'error')
      return
    }

    try {
      setRunning(true)
      completionHandledRef.current = false
      const response = await runMakerMonitor({ config, save_config: true })
      setStartedJobId(response.job_id)
      showToast(response.message || 'Monitor scan queued in background', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to run monitor scan'), 'error')
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => {
    if (!startedMonitorJob || completionHandledRef.current) {
      return
    }

    if (startedMonitorJob.status === 'completed') {
      completionHandledRef.current = true
      void (async () => {
        try {
          await refreshMonitorLastResult()
        } catch {
          // non-blocking; preserve current UI result if fetch fails
        } finally {
          showToast('Monitor scan completed', 'success')
          setStartedJobId(null)
        }
      })()
      return
    }

    if (startedMonitorJob.status === 'failed') {
      completionHandledRef.current = true
      showToast(startedMonitorJob.error || 'Monitor scan failed', 'error')
      setStartedJobId(null)
    }
  }, [showToast, startedMonitorJob])

  useEffect(() => {
    const wasActive = prevIsMonitorJobActiveRef.current
    prevIsMonitorJobActiveRef.current = isMonitorJobActive

    if (wasActive && !isMonitorJobActive) {
      void refreshMonitorLastResult().catch(() => {})
    }
  }, [isMonitorJobActive])

  // Close Apple TV popup on outside click
  useEffect(() => {
    if (!appleTvPopupKey) return
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('.apple-tv-popup-wrapper')) {
        setAppleTvPopupKey(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [appleTvPopupKey])

  const sortedLibraryResults = useMemo(() => {
    if (!result) {
      return []
    }
    return [...result.libraries].sort((left, right) => left.library_name.localeCompare(right.library_name))
  }, [result])

  const availableDrives = useMemo(() => {
    return drives
      .filter((drive) => !drive.is_deprecated)
      .sort((left, right) => left.name.localeCompare(right.name))
  }, [drives])

  const selectedDriveIdSet = useMemo(() => {
    return new Set(modalConfig.drive_ids.filter((driveId) => driveId > 0))
  }, [modalConfig.drive_ids])

  const discoveryTotals = useMemo(() => {
    const shows = result?.discovery?.shows || []
    const movies = result?.discovery?.movies || []
    return {
      shows,
      movies,
      total: shows.length + movies.length,
    }
  }, [result])

  const activeResultTab = useMemo(() => {
    if (!result) {
      return ''
    }

    if (resultTab) {
      return resultTab
    }

    if (sortedLibraryResults.length > 0) {
      return `lib-${sortedLibraryResults[0].library_name}`
    }

    if (discoveryTotals.total > 0) {
      return 'discovery'
    }

    return ''
  }, [discoveryTotals.total, result, resultTab, sortedLibraryResults])

  const updateDiscoveryLanguages = (rawValue: string) => {
    setModalDiscoveryLanguagesInput(rawValue)
    const languages = rawValue
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)

    setModalConfig((previous) => ({
      ...previous,
      discovery_languages: Array.from(new Set(languages)),
    }))
  }

  const handleTmdbSearch = async (queryOverride?: string, filterOverride?: TmdbSearchFilter) => {
    const query = (queryOverride ?? tmdbQuery).trim()
    if (!query) return
    const filter = filterOverride ?? tmdbFilter
    const cacheKey = `${filter}::${query.toLowerCase()}`
    const cached = tmdbCacheRef.current.get(cacheKey)
    if (cached) {
      setTmdbResults(cached)
      setTmdbError(null)
      return
    }
    setTmdbSearching(true)
    setTmdbError(null)
    setTmdbResults(null)
    try {
      const results = await searchTmdb(query, filter)
      tmdbCacheRef.current.set(cacheKey, results)
      setTmdbResults(results)
    } catch (error) {
      setTmdbError(getApiErrorMessage(error, 'Search failed'))
    } finally {
      setTmdbSearching(false)
    }
  }

  const handleTmdbFilterChange = (filter: TmdbSearchFilter) => {
    setTmdbFilter(filter)
    if (tmdbQuery.trim()) {
      void handleTmdbSearch(undefined, filter)
    }
  }

  const copyToClipboard = (text: string) => {
    const doFallback = () => {
      try {
        const el = document.createElement('textarea')
        el.value = text
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        showToast('Copied to clipboard', 'success')
      } catch {
        showToast('Failed to copy', 'error')
      }
    }

    if (navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(text).then(() => {
        showToast('Copied to clipboard', 'success')
      }).catch(doFallback)
    } else {
      doFallback()
    }
  }

  const ensureTvDetails = async (key: string, tmdbId: number) => {
    if (tvDetails[key] || tvDetailsLoading[key]) return
    setTvDetailsLoading((prev) => ({ ...prev, [key]: true }))
    try {
      const details = await getTvDetails(tmdbId)
      setTvDetails((prev) => ({ ...prev, [key]: details }))
    } catch {
      // non-blocking — season info is optional
    } finally {
      setTvDetailsLoading((prev) => ({ ...prev, [key]: false }))
    }
  }

  const fetchSeasonImages = async (galleryKey: string, tmdbId: number, seasonNumber: number) => {
    setSelectedSeason((prev) => ({ ...prev, [galleryKey]: seasonNumber }))
    const sk = `${galleryKey}-s${seasonNumber}`
    if (seasonImages[sk]) return  // already cached
    setSeasonImagesLoading((prev) => ({ ...prev, [sk]: true }))
    try {
      const data = await getSeasonImages(tmdbId, seasonNumber, galleryLanguage)
      setSeasonImages((prev) => ({ ...prev, [sk]: data }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load season images'), 'error')
    } finally {
      setSeasonImagesLoading((prev) => ({ ...prev, [sk]: false }))
    }
  }

  const toggleGallery = async (item: TmdbSearchResult) => {
    const key = `${item.media_type}-${item.tmdb_id}`
    if (galleryOpenKey === key) {
      setGalleryOpenKey(null)
      return
    }
    setGalleryOpenKey(key)
    // Fetch TV details lazily when gallery opens
    if (item.media_type === 'tv') void ensureTvDetails(key, item.tmdb_id)
    if (galleryData[key]) return  // already loaded
    setGalleryLoading((prev) => ({ ...prev, [key]: true }))
    try {
      const data = await getTmdbImages(item.tmdb_id, item.media_type, galleryLanguage)
      setGalleryData((prev) => ({ ...prev, [key]: data }))
      // Default tab: posters if any, else backdrops, else logos
      const defaultTab = data.posters.length > 0 ? 'posters' : data.backdrops.length > 0 ? 'backdrops' : 'logos'
      setGalleryTab((prev) => ({ ...prev, [key]: defaultTab }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load images'), 'error')
      setGalleryOpenKey(null)
    } finally {
      setGalleryLoading((prev) => ({ ...prev, [key]: false }))
    }
  }

  const handleGalleryLanguageChange = async (newLang: string) => {
    setGalleryLanguage(newLang)
    // Clear all cached gallery + season image data so next open uses the new language
    setGalleryData({})
    setSeasonImages({})
    // If a gallery is currently open, re-fetch it with the new language
    if (galleryOpenKey) {
      const dashIdx = galleryOpenKey.indexOf('-')
      const mediaType = galleryOpenKey.slice(0, dashIdx)
      const tmdbId = Number(galleryOpenKey.slice(dashIdx + 1))
      setGalleryLoading((prev) => ({ ...prev, [galleryOpenKey]: true }))
      try {
        const data = await getTmdbImages(tmdbId, mediaType, newLang)
        setGalleryData({ [galleryOpenKey]: data })
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reload images'), 'error')
      } finally {
        setGalleryLoading((prev) => ({ ...prev, [galleryOpenKey]: false }))
      }
    }
  }

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const groupedDiscoveryItems = useMemo(() => {
    const source = discoveryTab === 'series' ? discoveryTotals.shows : discoveryTotals.movies
    const grouped = new Map<string, typeof source>()

    source
      .slice()
      .sort((left, right) => String(left.date || '').localeCompare(String(right.date || '')))
      .forEach((item) => {
        const language = String(item.language || 'EN').toUpperCase()
        const existing = grouped.get(language) || []
        existing.push(item)
        grouped.set(language, existing)
      })

    return grouped
  }, [discoveryTab, discoveryTotals.movies, discoveryTotals.shows])

  return (
    <div className="page-container maker-tools-page">
      <div className="maker-header">
        <h1>Maker Tools</h1>
        <p>Independent maker workflow tools and utilities.</p>
      </div>

      <div className="maker-tools-tabs" role="tablist" aria-label="Maker tools tabs">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'tmdb-search'}
          className={activeTab === 'tmdb-search' ? 'active' : ''}
          onClick={() => setActiveTab('tmdb-search')}
        >
          <Search size={16} /> TMDB Search
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'monitor'}
          className={activeTab === 'monitor' ? 'active' : ''}
          onClick={() => setActiveTab('monitor')}
        >
          <Monitor size={16} /> Monitor
        </button>
      </div>

      {activeTab === 'monitor' && (
      <div className="maker-tools-panel">
        <div className="toolbar">
          <div className="toolbar-title">
            <h2>Season Premieres Monitor</h2>
            <div className="toolbar-info">
              <Info size={16} />
              <div className="toolbar-tooltip">Tracks upcoming season premieres and highlights missing posters. Results appear below and persist after refresh.</div>
            </div>
          </div>
          <div className="action-buttons">
            <div className="btn-pair">
              <button className="btn-toolbar btn-toolbar-link" type="button" onClick={openSchedulingSettings} disabled={saving || loading || running || isMonitorJobActive}>
                Scheduling
              </button>
              <button className="btn-toolbar btn-toolbar-link" type="button" onClick={openNotificationSettings} disabled={saving || loading || running || isMonitorJobActive}>
                Discord
              </button>
            </div>
            <button className="btn-toolbar" type="button" onClick={openConfigModal} disabled={saving || loading || running || isMonitorJobActive}>
              <SlidersHorizontal size={16} /> Configure
            </button>
            <button className="btn-toolbar btn-primary" type="button" onClick={handleRunMonitor} disabled={saving || loading || running || isMonitorJobActive}>
              <Play size={16} /> {(running || isMonitorJobActive) ? 'Running...' : 'Run Monitor'}
            </button>
          </div>
        </div>

        {result && (
          <div className="maker-results">
            <p className="maker-range">Range: {result.range_start} → {result.range_end}</p>

            <div className="maker-result-tabs" role="tablist" aria-label="Monitor result tabs">
              {sortedLibraryResults.map((libraryResult) => {
                const tabKey = `lib-${libraryResult.library_name}-${libraryResult.library_type}`
                return (
                  <button
                    key={tabKey}
                    type="button"
                    className={activeResultTab === tabKey ? 'active' : ''}
                    onClick={() => setResultTab(tabKey)}
                  >
                    {libraryResult.library_name} ({libraryResult.library_type})
                  </button>
                )
              })}

              {discoveryTotals.total > 0 && (
                <button
                  type="button"
                  className={activeResultTab === 'discovery' ? 'active' : ''}
                  onClick={() => setResultTab('discovery')}
                >
                  <Sparkles size={15} /> New Releases
                </button>
              )}
            </div>

            {sortedLibraryResults.map((libraryResult) => {
              const tabKey = `lib-${libraryResult.library_name}-${libraryResult.library_type}`
              if (activeResultTab !== tabKey) {
                return null
              }

              const postersReady = Math.max(0, libraryResult.premieres_found - libraryResult.posters_needed)

              return (
                <div className="maker-result-panel" key={tabKey}>
                  <div className="maker-library-stats">
                    <div className="stat-card"><span>{libraryResult.total_scanned}</span><small>Unique Shows</small></div>
                    <div className="stat-card"><span>{libraryResult.premieres_found}</span><small>Premieres Found</small></div>
                    <div className="stat-card"><span>{libraryResult.posters_needed}</span><small>Posters Needed</small></div>
                    <div className="stat-card"><span>{postersReady}</span><small>Ready to Go</small></div>
                  </div>

                  <div className="maker-show-list full-width">
                    {libraryResult.shows.length === 0 && <p className="muted">No upcoming premieres found in this drive.</p>}

                    {libraryResult.shows
                      .slice()
                      .sort((left, right) => String(left.date || '').localeCompare(String(right.date || '')))
                      .map((show) => (
                        <div className={`maker-show-item ${show.poster_exists ? 'ready' : 'todo'}`} key={`${libraryResult.library_name}-${show.tmdb_id}-${show.season_number}`}>
                          <div className="maker-show-main">
                            <a href={show.homepage} target="_blank" rel="noreferrer">{show.name}</a>
                            <span>{show.season_number === 0 ? 'Specials' : `Season ${show.season_number}`} starts: {show.date}</span>
                          </div>
                          <div className="maker-badges">
                            <span className="badge badge-grey">Season Premiere</span>
                            <span className={`badge ${show.poster_exists ? 'badge-green' : 'badge-orange'}`}>
                              {show.poster_exists ? <Check size={13} /> : <Paintbrush size={13} />}
                              {show.poster_exists ? 'Poster Ready' : 'Needs Poster'}
                            </span>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )
            })}

            {activeResultTab === 'discovery' && discoveryTotals.total > 0 && (
              <div className="maker-result-panel">
                <div className="maker-library-stats">
                  <div className="stat-card"><span>{discoveryTotals.shows.length}</span><small>New Series</small></div>
                  <div className="stat-card"><span>{discoveryTotals.movies.length}</span><small>New Movies</small></div>
                  <div className="stat-card"><span>{discoveryTotals.total}</span><small>Total Found</small></div>
                </div>

                <div className="maker-subtabs">
                  <button type="button" className={discoveryTab === 'series' ? 'active' : ''} onClick={() => setDiscoveryTab('series')}>
                    <Tv size={15} /> Series
                  </button>
                  <button type="button" className={discoveryTab === 'movies' ? 'active' : ''} onClick={() => setDiscoveryTab('movies')}>
                    <Clapperboard size={15} /> Movies
                  </button>
                </div>

                {Array.from(groupedDiscoveryItems.entries()).map(([language, items]) => (
                  <div className="maker-language-group" key={`${discoveryTab}-${language}`}>
                    <h4>{language}</h4>
                    <div className="maker-show-list full-width">
                      {items.map((item) => (
                        <div className={`maker-show-item ${item.statuses.some((status) => status.have || status.synced) ? 'ready' : 'todo'}`} key={`${discoveryTab}-${item.type}-${item.homepage}`}>
                          <div className="maker-show-main">
                            <div className="maker-show-title-row">
                              <a href={item.homepage} target="_blank" rel="noreferrer">{item.name}</a>
                            </div>
                            <span>Release: {item.date || 'Unknown'} • Pop: {Math.round(Number(item.popularity || 0))}</span>
                          </div>

                          <div className="maker-badges wrap">
                            {(() => {
                              const hasAnyFound = item.statuses.some((status) => status.have || status.synced)

                              return item.statuses.map((status) => {
                                if (status.have) {
                                  const sourceLabel = status.have_sources.length > 0 ? ` (${status.have_sources.join(', ')})` : ''
                                  return <span className="badge badge-green" key={`${item.homepage}-${status.type}`}><Check size={13} /> {status.type}{sourceLabel}</span>
                                }
                                if (status.synced) {
                                  const sourceLabel = status.synced_sources.length > 0 ? ` (${status.synced_sources.join(', ')})` : ''
                                  return <span className="badge badge-blue" key={`${item.homepage}-${status.type}`}><Sparkles size={13} /> {status.type}{sourceLabel}</span>
                                }

                                if (hasAnyFound) {
                                  return <span className="badge badge-grey" key={`${item.homepage}-${status.type}`}>{status.type}</span>
                                }

                                return <span className="badge badge-orange" key={`${item.homepage}-${status.type}`}><Paintbrush size={13} /> {status.type}</span>
                              })
                            })()}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      )}

      {activeTab === 'tmdb-search' && (
        <div className="maker-tools-panel">
          <div className="tmdb-search-panel">
            <div className="tmdb-search-help">
              <button
                type="button"
                className="tmdb-search-help-toggle"
                onClick={() => setTmdbHelpExpanded((v) => !v)}
                aria-expanded={tmdbHelpExpanded}
              >
                <Info size={14} />
                <span>How to search</span>
                <span className={`tmdb-help-chevron${tmdbHelpExpanded ? ' expanded' : ''}`}>›</span>
              </button>
              {tmdbHelpExpanded && (
                <div className="tmdb-search-help-body">
                  <p>Search TMDB for movies, TV shows, and collections to look up IDs and metadata.</p>
                  <ul>
                    <li><strong>By title</strong> — <code>The Office</code></li>
                    <li><strong>With year</strong> — <code>The Office 2005</code> (narrows results to that release year)</li>
                    <li><strong>Filter by type</strong> — use the All / Movies / TV Shows / Collections buttons below</li>
                    <li><strong>ID chips</strong> — click any TMDB / IMDB / TVDB chip to copy just the ID number</li>
                    <li><strong>Poster</strong> — click the poster thumbnail to enlarge it</li>
                  </ul>
                </div>
              )}
            </div>
            <div className="tmdb-search-bar">
              <Search size={18} className="tmdb-search-icon" />
              <input
                type="text"
                className="tmdb-search-input"
                placeholder="Search movies, TV shows, and collections..."
                value={tmdbQuery}
                onChange={(e) => setTmdbQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleTmdbSearch() }}
                autoFocus
              />
              <button
                className="btn-toolbar btn-primary"
                type="button"
                onClick={() => void handleTmdbSearch()}
                disabled={tmdbSearching || !tmdbQuery.trim()}
              >
                {tmdbSearching ? 'Searching...' : 'Search'}
              </button>
            </div>

            <div className="tmdb-filter-bar">
              {(['all', 'movie', 'tv', 'collection'] as TmdbSearchFilter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`tmdb-filter-btn${tmdbFilter === f ? ' active' : ''}`}
                  onClick={() => handleTmdbFilterChange(f)}
                >
                  {f === 'all' && 'All'}
                  {f === 'movie' && <><MovieIcon size={13} /> Movies</>}
                  {f === 'tv' && <><Tv size={13} /> TV Shows</>}
                  {f === 'collection' && <><FolderOpen size={13} /> Collections</>}
                </button>
              ))}
            </div>

            <p className="tmdb-attribution">
              This product uses the TMDB API but is not endorsed or certified by TMDB.{' '}
              <a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer">themoviedb.org</a>
            </p>

            {tmdbError && <p className="tmdb-error">{tmdbError}</p>}

            {tmdbResults !== null && (
              tmdbResults.length === 0
                ? <p className="tmdb-empty">No results found.</p>
                : (
                  <div className="tmdb-results-grid">
                    {tmdbResults.map((item) => {
                      const galleryKey = `${item.media_type}-${item.tmdb_id}`
                      const isGalleryOpen = galleryOpenKey === galleryKey
                      const galleryImages = galleryData[galleryKey]
                      const activeGalleryTab = galleryTab[galleryKey] ?? 'posters'
                      const details = tvDetails[galleryKey]
                      const galleryTabs: Array<{ id: 'posters' | 'backdrops' | 'logos' | 'season-posters'; label: string; count: number | null }> = galleryImages
                        ? [
                            { id: 'posters', label: 'Posters', count: galleryImages.posters.length },
                            { id: 'backdrops', label: 'Backdrops', count: galleryImages.backdrops.length },
                            { id: 'logos', label: 'Logos', count: galleryImages.logos.length },
                            ...(item.media_type === 'tv' ? [{ id: 'season-posters' as const, label: 'Seasons', count: null }] : []),
                          ]
                        : []
                      return (
                        <div key={galleryKey} className="tmdb-result-wrapper">
                          <div className="tmdb-result-card">
                            <div
                              className={`tmdb-poster${item.poster_url ? ' tmdb-poster--clickable' : ''}`}
                              onClick={() => { if (item.poster_url) setTmdbPreviewPoster(item.poster_url) }}
                            >
                              {item.poster_url
                                ? <img src={item.poster_url} alt={item.title} loading="lazy" />
                                : (
                                  <div className="tmdb-poster-placeholder">
                                    {item.media_type === 'movie' ? <MovieIcon size={32} /> : item.media_type === 'tv' ? <Tv size={32} /> : <FolderOpen size={32} />}
                                  </div>
                                )
                              }
                            </div>
                            <div className="tmdb-result-info">
                              <div className="tmdb-result-title-row">
                                <span className="tmdb-result-title">{item.title}</span>
                                {item.year && <span className="tmdb-result-year">{item.year}</span>}
                              </div>

                              <div className="tmdb-result-meta">
                                <span className={`badge ${item.media_type === 'movie' ? 'badge-blue' : item.media_type === 'tv' ? 'badge-grey' : 'badge-orange'}`}>
                                  {item.media_type === 'movie' ? <MovieIcon size={12} /> : item.media_type === 'tv' ? <Tv size={12} /> : <FolderOpen size={12} />}
                                  {item.media_type === 'movie' ? 'Movie' : item.media_type === 'tv' ? 'Series' : 'Collection'}
                                </span>
                                {item.media_type === 'tv' && details && (
                                  <>
                                    <span className="badge badge-grey">
                                      <Layers size={11} /> {details.season_count} season{details.season_count !== 1 ? 's' : ''}
                                    </span>
                                    {details.seasons.some((s) => s.season_number === 0) && (
                                      <span className="badge badge-grey">Specials</span>
                                    )}
                                  </>
                                )}
                              </div>

                              <div className="tmdb-result-ids">
                                <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(String(item.tmdb_id))} title="Copy TMDB ID">TMDB&nbsp;#{item.tmdb_id}</button>
                                {item.imdb_id && <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(item.imdb_id!)} title="Copy IMDB ID">IMDB&nbsp;{item.imdb_id}</button>}
                                {item.tvdb_id && <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(String(item.tvdb_id))} title="Copy TVDB ID">TVDB&nbsp;#{item.tvdb_id}</button>}
                              </div>

                              <div className="tmdb-result-links">
                                <a className="tmdb-result-link" href={item.homepage} target="_blank" rel="noreferrer">
                                  <ExternalLink size={12} /> TMDB
                                </a>
                                {item.imdb_id && (
                                  <a className="tmdb-result-link" href={`https://www.imdb.com/title/${item.imdb_id}/`} target="_blank" rel="noreferrer">
                                    <ExternalLink size={12} /> IMDB
                                  </a>
                                )}
                                {item.tvdb_id && (
                                  <a className="tmdb-result-link" href={`https://thetvdb.com/?id=${item.tvdb_id}&tab=series`} target="_blank" rel="noreferrer">
                                    <ExternalLink size={12} /> TVDB
                                  </a>
                                )}
                                <div className="apple-tv-popup-wrapper">
                                  <button
                                    type="button"
                                    className={`tmdb-result-link apple-tv-popup-trigger${appleTvPopupKey === galleryKey ? ' active' : ''}`}
                                    onClick={() => {
                                      if (appleTvPopupKey === galleryKey) {
                                        setAppleTvPopupKey(null)
                                      } else {
                                        setAppleTvPopupKey(galleryKey)
                                      }
                                    }}
                                    title="Find Apple TV artwork"
                                  >
                                    <ExternalLink size={12} /> Apple TV Art
                                  </button>
                                  {appleTvPopupKey === galleryKey && (
                                    <div className="apple-tv-popup">
                                      <div className="apple-tv-popup-row">
                                        <span className="apple-tv-popup-label">Country</span>
                                        <select
                                          className="apple-tv-storefront-select"
                                          value={appleTvStorefront}
                                          onChange={(e) => {
                                            setAppleTvStorefront(e.target.value)
                                            localStorage.setItem('apple-tv-storefront', e.target.value)
                                          }}
                                        >
                                          {APPLE_TV_STOREFRONTS.map((sf) => (
                                            <option key={sf.value} value={sf.value}>{sf.label}</option>
                                          ))}
                                        </select>
                                      </div>
                                      <a
                                        className="apple-tv-open-btn"
                                        href={`https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=${encodeURIComponent(item.title)}&storefront=${appleTvStorefront}`}
                                        target="_blank"
                                        rel="noreferrer"
                                        onClick={() => setAppleTvPopupKey(null)}
                                      >
                                        <ExternalLink size={12} /> Open
                                      </a>
                                    </div>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  className="tmdb-copy-btn"
                                  onClick={() => copyToClipboard(item.title)}
                                  title="Copy title"
                                >
                                  <Copy size={12} /> Title
                                </button>
                                <button
                                  type="button"
                                  className="tmdb-copy-btn"
                                  onClick={() => copyToClipboard(item.homepage)}
                                  title="Copy TMDB link"
                                >
                                  <Copy size={12} /> Link
                                </button>
                              </div>

                              {item.overview && <p className="tmdb-result-overview">{item.overview}</p>}

                              <button
                                type="button"
                                className={`tmdb-gallery-toggle${isGalleryOpen ? ' open' : ''}`}
                                onClick={() => void toggleGallery(item)}
                                disabled={galleryLoading[galleryKey]}
                              >
                                <Image size={13} />
                                {galleryLoading[galleryKey]
                                  ? 'Loading images…'
                                  : isGalleryOpen
                                    ? <><ChevronUp size={13} /> Hide images</>
                                    : <><ChevronDown size={13} /> Browse images</>
                                }
                              </button>

                            </div>
                          </div>

                          {isGalleryOpen && galleryImages && (
                            <div className="tmdb-gallery-panel">
                              <div className="tmdb-gallery-tabs">
                                {galleryTabs.map((t) => (
                                  <button
                                    key={t.id}
                                    type="button"
                                    className={`tmdb-gallery-tab${activeGalleryTab === t.id ? ' active' : ''}`}
                                    onClick={() => {
                                      if (t.id === 'season-posters') {
                                        setGalleryTab((prev) => ({ ...prev, [galleryKey]: 'season-posters' }))
                                      } else if (t.count != null && t.count > 0) {
                                        setGalleryTab((prev) => ({ ...prev, [galleryKey]: t.id }))
                                      }
                                    }}
                                    disabled={t.id !== 'season-posters' && (t.count == null || t.count === 0)}
                                  >
                                    {t.label}{t.count != null && <span className="tmdb-gallery-tab-count">{t.count}</span>}
                                  </button>
                                ))}
                                <div className="tmdb-gallery-lang-wrapper">
                                  <Globe size={13} className="tmdb-gallery-lang-icon" />
                                  <select
                                    className="tmdb-gallery-lang-select"
                                    value={galleryLanguage}
                                    onChange={(e) => void handleGalleryLanguageChange(e.target.value)}
                                    title="Image language preference"
                                  >
                                    {TMDB_IMAGE_LANGUAGES.map((lang) => (
                                      <option key={lang.value} value={lang.value}>{lang.label}</option>
                                    ))}
                                  </select>
                                </div>
                              </div>
                              {activeGalleryTab === 'season-posters'
                                ? (
                                  <div className="tmdb-season-picker">
                                    {tvDetailsLoading[galleryKey]
                                      ? <p className="tmdb-gallery-empty">Loading seasons…</p>
                                      : !details || details.seasons.length === 0
                                        ? <p className="tmdb-gallery-empty">No seasons available.</p>
                                        : (
                                          <>
                                            <div className="tmdb-season-chips">
                                              {details.seasons.map((s) => (
                                                <button
                                                  key={s.season_number}
                                                  type="button"
                                                  className={`tmdb-season-chip${selectedSeason[galleryKey] === s.season_number ? ' active' : ''}`}
                                                  onClick={() => void fetchSeasonImages(galleryKey, item.tmdb_id, s.season_number)}
                                                  disabled={seasonImagesLoading[`${galleryKey}-s${s.season_number}`]}
                                                >
                                                  {s.season_number === 0 ? 'Specials' : `S${String(s.season_number).padStart(2, '0')}`}
                                                </button>
                                              ))}
                                            </div>
                                            {selectedSeason[galleryKey] != null && (() => {
                                              const sk = `${galleryKey}-s${selectedSeason[galleryKey]}`
                                              const sImgs = seasonImages[sk]
                                              if (seasonImagesLoading[sk]) return <p className="tmdb-gallery-empty">Loading posters…</p>
                                              if (!sImgs || sImgs.posters.length === 0) return <p className="tmdb-gallery-empty">No posters available for this season.</p>
                                              return (
                                                <div className="tmdb-gallery-grid tmdb-gallery-grid--posters">
                                                  {sImgs.posters.map((img) => (
                                                    <div key={img.file_path} className="tmdb-gallery-item">
                                                      <button
                                                        type="button"
                                                        className="tmdb-gallery-thumb-btn"
                                                        onClick={() => setGalleryPreview(img)}
                                                        title="Preview full size"
                                                      >
                                                        <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                                                      </button>
                                                      <div className="tmdb-gallery-item-meta">
                                                        <div className="tmdb-gallery-meta-row">
                                                          {img.language === null
                                                            ? <span className="tmdb-gallery-lang">TL</span>
                                                            : img.language
                                                              ? <span className="tmdb-gallery-lang">{img.language.toUpperCase()}</span>
                                                              : null
                                                          }
                                                          <span className="tmdb-gallery-dims">{img.width}×{img.height}</span>
                                                          <button
                                                            type="button"
                                                            className="tmdb-gallery-dl"
                                                            title="Download"
                                                            onClick={() => void handleGalleryDownload(img.file_path)}
                                                          >
                                                            <Download size={12} />
                                                          </button>
                                                        </div>
                                                      </div>
                                                    </div>
                                                  ))}
                                                </div>
                                              )
                                            })()}
                                          </>
                                        )
                                    }
                                  </div>
                                )
                                : galleryImages[activeGalleryTab as 'posters' | 'backdrops' | 'logos'].length === 0
                                  ? <p className="tmdb-gallery-empty">No {activeGalleryTab} available.</p>
                                  : (
                                    <div className={`tmdb-gallery-grid tmdb-gallery-grid--${activeGalleryTab}`}>
                                      {galleryImages[activeGalleryTab as 'posters' | 'backdrops' | 'logos'].map((img) => (
                                        <div key={img.file_path} className="tmdb-gallery-item">
                                          <button
                                            type="button"
                                            className="tmdb-gallery-thumb-btn"
                                            onClick={() => setGalleryPreview(img)}
                                            title="Preview full size"
                                          >
                                            <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                                          </button>
                                          <div className="tmdb-gallery-item-meta">
                                            <div className="tmdb-gallery-meta-row">
                                              {img.language === null
                                                ? <span className="tmdb-gallery-lang">TL</span>
                                                : img.language
                                                  ? <span className="tmdb-gallery-lang">{img.language.toUpperCase()}</span>
                                                  : null
                                              }
                                              <span className="tmdb-gallery-dims">{img.width}×{img.height}</span>
                                              <button
                                                type="button"
                                                className="tmdb-gallery-dl"
                                                title="Download"
                                                onClick={() => void handleGalleryDownload(img.file_path)}
                                              >
                                                <Download size={12} />
                                              </button>
                                            </div>
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  )
                              }
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )
            )}
          </div>
        </div>
      )}

      {tmdbPreviewPoster && (
        <div className="tmdb-lightbox-overlay" onClick={() => setTmdbPreviewPoster(null)}>
          <img
            className="tmdb-lightbox-img"
            src={tmdbPreviewPoster}
            alt="Poster preview"
            onClick={(e) => e.stopPropagation()}
          />
          <button type="button" className="tmdb-lightbox-close" onClick={() => setTmdbPreviewPoster(null)}>×</button>
        </div>
      )}

      {galleryPreview && (
        <div className="tmdb-lightbox-overlay" onClick={() => setGalleryPreview(null)}>
          <div className="tmdb-gallery-lightbox" onClick={(e) => e.stopPropagation()}>
            <img
              className="tmdb-gallery-lightbox-img"
              src={galleryPreview.url_full}
              alt="Preview"
            />
            <div className="tmdb-gallery-lightbox-actions">
              {galleryPreview.language && <span className="tmdb-gallery-lang">{galleryPreview.language.toUpperCase()}</span>}
              <span className="tmdb-gallery-dims">{galleryPreview.width}×{galleryPreview.height}</span>
              <button
                type="button"
                className="btn-toolbar btn-primary"
                style={{ fontSize: '0.82rem', padding: '0.35rem 0.75rem' }}
                onClick={() => void handleGalleryDownload(galleryPreview.file_path)}
              >
                <Download size={13} /> Download
              </button>
            </div>
          </div>
          <button type="button" className="tmdb-lightbox-close" onClick={() => setGalleryPreview(null)}>×</button>
        </div>
      )}

      {showConfigModal && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Monitor Configuration</h2>
              <button className="modal-close" onClick={closeConfigModal}>×</button>
            </div>
            <div className="modal-body">
              <div className="maker-grid">
                <div className="maker-card">
                  <h3>General</h3>
                  <p style={{ margin: '0.25rem 0 0.75rem', fontSize: '0.8rem', color: '#888' }}>
                    TMDB API key is managed in{' '}
                    <a
                      href="/settings"
                      onClick={(e) => { e.preventDefault(); navigate('/settings') }}
                      style={{ color: '#64b5f6' }}
                    >
                      Settings → General → API Keys
                    </a>
                  </p>
                  <label>
                    Lookahead Days
                    <input
                      type="number"
                      min={1}
                      value={Number.isFinite(modalConfig.lookahead_days) ? modalConfig.lookahead_days : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            lookahead_days: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          lookahead_days: Number.isFinite(nextValue) && nextValue > 0 ? nextValue : previous.lookahead_days,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Missing Retention Days
                    <input
                      type="number"
                      min={0}
                      value={Number.isFinite(modalConfig.missing_retention_days) ? modalConfig.missing_retention_days : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            missing_retention_days: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          missing_retention_days: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.missing_retention_days,
                        }))
                      }}
                    />
                    <small className="muted">Keeps missing season premieres visible from previous runs for this many days. 0 = disable carryover.</small>
                  </label>
                  <label className="maker-checkbox-row">
                    <input
                      type="checkbox"
                      checked={Boolean(modalConfig.enable_discovery)}
                      onChange={(event) => setModalConfig((previous) => ({ ...previous, enable_discovery: event.target.checked }))}
                    />
                    <span>
                      Enable New Releases discovery
                      <small className="muted" style={{ display: 'block', marginTop: '2px' }}>Monitor upcoming movie and TV show releases. Adds a tab alongside monitored drives for browsing new and upcoming TMDB titles.</small>
                    </span>
                  </label>
                </div>

                <div className="maker-card">
                  <div className="maker-card-header-row">
                    <h3>Monitor Drives</h3>
                    <button className="btn-toolbar" type="button" onClick={addDriveSelection}>
                      <Plus size={16} /> Add Drive
                    </button>
                  </div>
                  <div className="maker-list">
                    {modalConfig.drive_ids.length === 0 && <p className="muted">No monitor drives selected.</p>}
                    {modalConfig.drive_ids.map((driveId, index) => {
                      const disabledIds = new Set(selectedDriveIdSet)
                      if (driveId > 0) {
                        disabledIds.delete(driveId)
                      }

                      return (
                        <div className="maker-list-item maker-drive-item" key={`drive-${index}`}>
                          <select
                            value={driveId > 0 ? String(driveId) : ''}
                            onChange={(event) => updateDriveSelection(index, Number(event.target.value))}
                          >
                            <option value="">Select a synced drive...</option>
                            {availableDrives.map((drive) => (
                              <option key={drive.id} value={drive.id} disabled={disabledIds.has(drive.id)}>
                                {drive.display_name || drive.name} ({drive.style_type})
                              </option>
                            ))}
                          </select>
                          <button className="btn-toolbar btn-danger" type="button" onClick={() => removeDriveSelection(index)}>
                            <Trash2 size={16} />
                          </button>
                        </div>
                      )
                    })}
                    {availableDrives.length === 0 && (
                      <p className="muted">No drives available.</p>
                    )}
                  </div>
                </div>

                <div className="maker-card">
                  <h3>Discovery</h3>
                  <label>
                    Minimum Popularity
                    <input
                      type="number"
                      min={0}
                      step="0.1"
                      value={Number.isFinite(modalConfig.discovery_popularity) ? modalConfig.discovery_popularity : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_popularity: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_popularity: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.discovery_popularity,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Minimum Vote Count
                    <input
                      type="number"
                      min={0}
                      value={Number.isFinite(modalConfig.discovery_vote_count) ? modalConfig.discovery_vote_count : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_vote_count: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_vote_count: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.discovery_vote_count,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Max Results Per Language
                    <input
                      type="number"
                      min={1}
                      value={Number.isFinite(modalConfig.discovery_max_results) ? modalConfig.discovery_max_results : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_max_results: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_max_results: Number.isFinite(nextValue) && nextValue > 0 ? nextValue : previous.discovery_max_results,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    <span className="maker-label-row">
                      Languages (comma-separated)
                      <button
                        type="button"
                        className="maker-help-button"
                        aria-label="Available language codes: en (English), ko (Korean), ja (Japanese), zh (Chinese), es (Spanish), fr (French), de (German), it (Italian), ru (Russian), hi (Hindi), th (Thai)"
                        title="Available: en (English), ko (Korean), ja (Japanese), zh (Chinese), es (Spanish), fr (French), de (German), it (Italian), ru (Russian), hi (Hindi), th (Thai)"
                      >
                        <CircleHelp size={14} />
                      </button>
                    </span>
                    <input
                      type="text"
                      value={modalDiscoveryLanguagesInput}
                      onChange={(event) => updateDiscoveryLanguages(event.target.value)}
                      placeholder="en, ko, ja, zh, es"
                    />
                  </label>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={closeConfigModal} disabled={saving}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleSaveConfig} disabled={saving || loading}>
                <Save size={16} /> {saving ? 'Saving...' : 'Save Configuration'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default MakerTools
