import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Check, ChevronDown, ChevronUp, CircleHelp, Clapperboard, Clapperboard as MovieIcon, Copy, Download, ExternalLink, FileDown, FolderOpen, Globe, Image, Info, Layers, Monitor, Paintbrush, Play, Plus, Save, Search, SlidersHorizontal, Sparkles, Trash2, Tv } from 'lucide-react'
import {
  getApiErrorMessage,
  Drive,
  checkTmdbPosterAvailability,
  exportToPsd,
  uploadPsdToExportFolder,
  checkPsdExists,
  getDrives,
  getMakerMonitorConfig,
  getMakerMonitorLastResult,
  getSeasonImages,
  getSettings,
  getTmdbImages,
  getTmdbImageProxyUrl,
  getTvDetails,
  MakerMonitorConfig,
  MakerMonitorRunResponse,
  PosterAvailability,
  runMakerMonitor,
  saveBulkSettings,
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
  const location = useLocation()
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
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  // Image gallery: key = `${media_type}-${tmdb_id}`
  const [galleryOpenKey, setGalleryOpenKey] = useState<string | null>(null)
  const [galleryData, setGalleryData] = useState<Record<string, TmdbImagesResponse>>({})
  const [galleryLoading, setGalleryLoading] = useState<Record<string, boolean>>({})
  const [galleryTab, setGalleryTab] = useState<Record<string, 'posters' | 'backdrops' | 'logos' | 'season-posters'>>({})
  const [galleryPreview, setGalleryPreview] = useState<TmdbImage | null>(null)
  const [galleryPreviewIsLogo, setGalleryPreviewIsLogo] = useState(false)
  const [galleryLanguage, setGalleryLanguage] = useState('en+textless')
  // PSD export selections: key = galleryKey, value = { posters: ordered file_paths[], backdrops: ordered file_paths[], logos: ordered file_paths[] }
  const [psdSelections, setPsdSelections] = useState<Record<string, { posters: string[]; backdrops: string[]; logos: string[] }>>({})
  const [psdExporting, setPsdExporting] = useState<Record<string, boolean>>({})
  // PSD export settings
  const [psdExportFolder, setPsdExportFolder] = useState('')
  const [psdTemplatePath, setPsdTemplatePath] = useState('')
  const [psdOpenPhotopea, setPsdOpenPhotopea] = useState(false)
  const [showPsdConfigModal, setShowPsdConfigModal] = useState(false)
  // "Use Existing PSD" — not-found modal state
  const [psdNotFound, setPsdNotFound] = useState<{ galleryKey: string; title: string; year: string; expectedFilename: string } | null>(null)
  const [psdUploading, setPsdUploading] = useState(false)
  // "New Export" — overwrite confirmation state
  const [psdOverwriteConfirm, setPsdOverwriteConfirm] = useState<{ galleryKey: string; title: string; year: string; filename: string } | null>(null)
  // TV show seasons
  const [tvDetails, setTvDetails] = useState<Record<string, TmdbTvDetails>>({})
  const [tvDetailsLoading, setTvDetailsLoading] = useState<Record<string, boolean>>({})
  const [selectedSeason, setSelectedSeason] = useState<Record<string, number>>({})
  const [seasonImages, setSeasonImages] = useState<Record<string, TmdbImagesResponse>>({})
  const [seasonImagesLoading, setSeasonImagesLoading] = useState<Record<string, boolean>>({})
  const { showToast } = useToast()

  const togglePsdSelection = (galleryKey: string, role: 'poster' | 'backdrop' | 'logo', filePath: string) => {
    setPsdSelections((prev) => {
      const current = prev[galleryKey] ?? { posters: [], backdrops: [], logos: [] }
      if (role === 'logo') {
        const already = current.logos.includes(filePath)
        return {
          ...prev,
          [galleryKey]: {
            ...current,
            logos: already ? current.logos.filter((l) => l !== filePath) : [...current.logos, filePath],
          },
        }
      }
      if (role === 'backdrop') {
        const already = current.backdrops.includes(filePath)
        return {
          ...prev,
          [galleryKey]: {
            ...current,
            backdrops: already ? current.backdrops.filter((b) => b !== filePath) : [...current.backdrops, filePath],
          },
        }
      }
      // poster: toggle in ordered array
      const already = current.posters.includes(filePath)
      return {
        ...prev,
        [galleryKey]: {
          ...current,
          posters: already
            ? current.posters.filter((p) => p !== filePath)
            : [...current.posters, filePath],
        },
      }
    })
  }

  const handlePsdExport = async (galleryKey: string, title: string, year: string, useExisting = false, confirmed = false) => {
    const sel = psdSelections[galleryKey]
    if (!sel?.posters.length && !sel?.backdrops?.length && !sel?.logos?.length) return

    // For new exports: check if a PSD with the same name already exists and warn before overwriting.
    // Skip this check if the user has already confirmed the overwrite (confirmed=true).
    if (!useExisting && !confirmed) {
      const safeName = title.replace(/[<>:"/\\|?*]/g, '').trim()
      const expectedFilename = year ? `${safeName} (${year}).psd` : `${safeName}.psd`
      const exists = await checkPsdExists(expectedFilename)
      if (exists) {
        setPsdOverwriteConfirm({ galleryKey, title, year, filename: expectedFilename })
        return
      }
    }

    setPsdExporting((prev) => ({ ...prev, [galleryKey]: true }))
    try {
      const result = await exportToPsd(
        {
          title,
          year: year ?? '',
          poster_paths: sel.posters,
          backdrop_paths: sel.backdrops ?? [],
          logo_paths: sel.logos ?? [],
          use_existing: useExisting,
        },
        title,
        year ?? '',
      )

      if (result.mode === 'not-found') {
        setPsdNotFound({ galleryKey, title, year, expectedFilename: result.expectedFilename })
        return
      }

      if (result.mode === 'photopea') {
        // Server saved the PSD (to export folder or psd_cache)
        if (result.openPhotopea) {
          // Open the PSD in Photopea via hash config (required API for URL-sourced files).
          // server.url enables File→Save / Ctrl+S: Photopea POSTs the PSD back to that URL.
          // The hash value must be encodeURIComponent-encoded — per Photopea API docs.
          // formats:["psd:true"] restricts the POST body to PSD only (one file, at byte 2000).
          const saveUrl = `${window.location.origin}/api/maker-tools/psd-exports/${encodeURIComponent(result.filename)}`
          const config = { files: [result.psdUrl], server: { version: 1, url: saveUrl, formats: ['psd:true'] } }
          const photopea = `https://www.photopea.com#${encodeURIComponent(JSON.stringify(config))}`
          window.open(photopea, '_blank')
          showToast(`PSD opened in Photopea: ${result.filename}`, 'success')
        } else {
          showToast(`PSD saved: ${result.filename}`, 'success')
        }
      } else {
        // No export folder configured — trigger browser download
        const url = URL.createObjectURL(result.blob)
        const a = document.createElement('a')
        a.href = url
        a.download = result.filename
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
        showToast('PSD downloaded', 'success')
      }
    } catch (err) {
      console.error('PSD export failed:', err)
      const msg = err instanceof Error ? err.message : 'Failed to export PSD'
      showToast(msg, 'error')
    } finally {
      setPsdExporting((prev) => ({ ...prev, [galleryKey]: false }))
    }
  }

  const handlePsdNotFoundUpload = async (file: File) => {
    if (!psdNotFound) return
    const { galleryKey, title, year, expectedFilename } = psdNotFound
    setPsdUploading(true)
    try {
      await uploadPsdToExportFolder(file, expectedFilename)
      setPsdNotFound(null)
      showToast(`PSD uploaded as "${expectedFilename}" — adding poster layers…`, 'success')
      await handlePsdExport(galleryKey, title, year, true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Upload failed'
      showToast(msg, 'error')
    } finally {
      setPsdUploading(false)
    }
  }

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

  useEffect(() => {
    getSettings().then((settings) => {
      setPsdExportFolder((settings.psd_export_folder || '').trim())
      setPsdTemplatePath((settings.psd_template_path || '').trim())
      setPsdOpenPhotopea((settings.psd_open_photopea || '').trim().toLowerCase() === 'true')
    }).catch(() => {
      // Non-blocking: page still works with empty defaults
    })
  }, [])

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

  const openPsdConfigModal = () => setShowPsdConfigModal(true)
  const closePsdConfigModal = () => setShowPsdConfigModal(false)

  const handleSavePsdConfig = async () => {
    try {
      setSaving(true)
      await saveBulkSettings({
        psd_export_folder: psdExportFolder.trim(),
        psd_template_path: psdTemplatePath.trim(),
        psd_open_photopea: String(psdOpenPhotopea),
      })
      showToast('PSD settings saved', 'success')
      setShowPsdConfigModal(false)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save PSD settings'), 'error')
    } finally {
      setSaving(false)
    }
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
      void fetchPosterAvailability(cached)
      return
    }
    setTmdbSearching(true)
    setTmdbError(null)
    setTmdbResults(null)
    setPosterAvailability({})
    try {
      const results = await searchTmdb(query, filter)
      tmdbCacheRef.current.set(cacheKey, results)
      setTmdbResults(results)
      void fetchPosterAvailability(results)
    } catch (error) {
      setTmdbError(getApiErrorMessage(error, 'Search failed'))
    } finally {
      setTmdbSearching(false)
    }
  }

  const fetchPosterAvailability = async (results: TmdbSearchResult[]) => {
    if (results.length === 0) return
    try {
      const items = results.map((r) => ({ tmdb_id: r.tmdb_id, title: r.title, year: r.year, media_type: r.media_type }))
      const availability = await checkTmdbPosterAvailability(items)
      setPosterAvailability(availability)
    } catch {
      // Non-critical — silently ignore errors
    }
  }

  // Handle incoming navigation state from other pages (e.g. Poster Manager → Maker Tools search)
  useEffect(() => {
    const state = location.state as { tmdbSearch?: string } | null
    if (state?.tmdbSearch) {
      const query = state.tmdbSearch
      setTmdbQuery(query)
      setActiveTab('tmdb-search')
      void handleTmdbSearch(query)
      navigate(location.pathname, { replace: true, state: null })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearchOnTmdbTab = (name: string, date?: string) => {
    const year = date?.slice(0, 4) ?? ''
    const query = `${name} ${year}`.trim()
    setTmdbQuery(query)
    setActiveTab('tmdb-search')
    void handleTmdbSearch(query)
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
                            <a href={show.homepage} target="_blank" rel="noreferrer">{show.name}{show.first_air_year ? <> <span className="tmdb-result-year">{show.first_air_year}</span></> : ''}</a>
                            <span>{show.season_number === 0 ? 'Specials' : `Season ${show.season_number}`} starts: {show.date}</span>
                          </div>
                          <div className="maker-badges">
                            <span className="badge badge-grey">Season Premiere</span>
                            <span className={`badge ${show.poster_exists ? 'badge-green' : 'badge-orange'}`}>
                              {show.poster_exists ? <Check size={13} /> : <Paintbrush size={13} />}
                              {show.poster_exists ? 'Poster Ready' : 'Needs Poster'}
                            </span>
                            <button
                              type="button"
                              className="maker-tmdb-search-btn"
                              title="Search TMDB tab"
                              onClick={() => handleSearchOnTmdbTab(show.name, show.first_air_year || undefined)}
                            >
                              <Search size={12} /> Maker
                            </button>
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
                              <a href={item.homepage} target="_blank" rel="noreferrer">{item.name}{item.date?.slice(0, 4) ? <> <span className="tmdb-result-year">{item.date.slice(0, 4)}</span></> : ''}</a>
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
                            <button
                              type="button"
                              className="maker-tmdb-search-btn"
                              title="Search TMDB tab"
                              onClick={() => handleSearchOnTmdbTab(item.name, item.date)}
                            >
                              <Search size={12} /> Maker
                            </button>
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
          <div className="toolbar">
            <div className="toolbar-title">
              <h2>TMDB Search</h2>
              <div className="toolbar-info">
                <Info size={16} />
                <div className="toolbar-tooltip">Search TMDB for movies, TV shows, and collections. Browse posters, logos, and backdrops, then export directly to PSD.</div>
              </div>
            </div>
            <div className="action-buttons">
              <button className="btn-toolbar" type="button" onClick={openPsdConfigModal}>
                <SlidersHorizontal size={16} /> Configure
              </button>
            </div>
          </div>
          <div className="tmdb-search-panel">
            <div className="tmdb-search-help">
              <button
                type="button"
                className="tmdb-search-help-toggle"
                onClick={() => setTmdbHelpExpanded((v) => !v)}
                aria-expanded={tmdbHelpExpanded}
              >
                <Info size={14} />
                <span>How to search &amp; export PSDs</span>
                <span className={`tmdb-help-chevron${tmdbHelpExpanded ? ' expanded' : ''}`}>›</span>
              </button>
              {tmdbHelpExpanded && (
                <div className="tmdb-search-help-body">
                  <p>Search TMDB for movies, TV shows, and collections to look up IDs and metadata.</p>
                  <ul>
                    <li><strong>By title</strong> — <code>The Office</code></li>
                    <li><strong>With year</strong> — <code>The Office (2005)</code> or <code>The Office 2005</code> (narrows results to that release year)</li>
                    <li><strong>Filter by type</strong> — use the All / Movies / TV Shows / Collections buttons below</li>
                    <li><strong>ID chips</strong> — click any TMDB / IMDB / TVDB chip to copy just the ID number</li>
                    <li><strong>Poster</strong> — click the poster thumbnail to enlarge it</li>
                  </ul>
                  <p>To build a PSD file from the search results:</p>
                  <ul>
                    <li><strong>Select images</strong> — click <strong>P</strong> on any poster to add it to your export, or <strong>L</strong> on a logo</li>
                    <li><strong>Backdrops</strong> — switch to the Backdrops tab and click <strong>B</strong> to add a backdrop as a background layer (fit to height, no crop)</li>
                    <li><strong>Logo</strong> — switch to the Logos tab and click <strong>L</strong> to add a logo; it will be placed at the bottom, converted to white, and sized automatically based on its shape and density:
                      <ul>
                        <li><strong>Short/wide logos</strong> — logos with a low projected height are given a wider target width so they don't appear too small</li>
                        <li><strong>Sparse logos</strong> — logos with thin strokes or lots of transparent space are sized up slightly so delicate details remain visible</li>
                        <li><strong>Dense logos</strong> — logos with heavily filled or solid artwork are sized down, with wider logos shrinking more than narrower ones, to avoid an overpowering block of white</li>
                        <li><strong>Hard limits</strong> — no logo will exceed 800px wide; tall logos are also capped in height to stay within the lower third of the canvas</li>
                      </ul>
                    </li>
                    <li><strong>Export PSD</strong> — once you've selected images, two export buttons appear in the gallery toolbar:
                      <ul>
                        <li><strong>New Export</strong> — always creates a fresh PSD using your configured template (or the built-in default). Any file in the export folder with the same name is overwritten from scratch.</li>
                        <li><strong>Use Existing PSD</strong> — opens an already-saved PSD from your export folder and injects the new poster/logo/backdrop layers into it, preserving all your existing work (borders, text, effects). The file must be named <code>{'{'}title{'}'} ({'{'}year{'}'}).psd</code> and placed in the configured export folder. If no matching file is found, a prompt will guide you to either place the file there manually or upload it directly from your computer.</li>
                      </ul>
                    </li>
                    <li><strong>Open in Photopea</strong> — if the "Open in Photopea" toggle is enabled in Configure, the exported PSD will open directly in Photopea in a new tab instead of downloading</li>
                    <li><strong>Multiple posters</strong> — you can add more than one poster; each becomes its own layer in the PSD</li>
                  </ul>
                </div>
              )}
            </div>
            <div className="tmdb-search-bar">
              <Search size={18} className="tmdb-search-icon" />
              <input
                type="text"
                className="tmdb-search-input"
                placeholder="Search movies, TV shows, collections... add (year) to narrow results"
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
                                {posterAvailability[item.tmdb_id] && posterAvailability[item.tmdb_id].length > 0 && (
                                  <span className="tmdb-poster-available" aria-label="Poster available in synced drives">
                                    <Check size={11} />
                                    <span className="tmdb-poster-available-tooltip">
                                      <span className="tmdb-poster-available-header">Available in synced drives</span>
                                      <span className="tmdb-poster-available-note">As of last sync</span>
                                      {posterAvailability[item.tmdb_id].map((entry) => (
                                        <span key={entry.style} className="tmdb-poster-available-style-row">
                                          <span className="tmdb-poster-available-style">
                                            <Check size={10} /> {entry.style}
                                          </span>
                                          {entry.seasons.length > 0 && (
                                            <span className="tmdb-poster-available-seasons">
                                              {entry.seasons.length <= 5
                                                ? entry.seasons.map((s) => (
                                                    <span key={s} className="tmdb-poster-season-chip">S{s}</span>
                                                  ))
                                                : (
                                                    <span className="tmdb-poster-season-chip">
                                                      S{entry.seasons[0]} – S{entry.seasons[entry.seasons.length - 1]}
                                                    </span>
                                                  )
                                              }
                                            </span>
                                          )}
                                        </span>
                                      ))}
                                    </span>
                                  </span>
                                )}
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
                                        href={`https://bendodson.com/projects/apple-tv-movies-artwork-finder/pre-ios26/?query=${encodeURIComponent(item.title)}&storefront=${appleTvStorefront}${item.media_type === 'tv' ? '&type=tv' : item.media_type === 'movie' ? '&type=movies' : ''}`}
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
                                  onClick={() => copyToClipboard(item.year ? `${item.title} (${item.year})` : item.title)}
                                  title="Copy title with year"
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
                                {(() => {
                                  const sel = psdSelections[galleryKey]
                                  const hasSel = !!(sel?.posters.length || sel?.backdrops?.length || sel?.logos?.length)
                                  return hasSel ? (
                                    <div className="tmdb-psd-export-group">
                                      <button
                                        type="button"
                                        className="tmdb-psd-export-btn tmdb-psd-export-btn--new"
                                        onClick={() => void handlePsdExport(galleryKey, item.title, item.year, false)}
                                        disabled={psdExporting[galleryKey]}
                                        title="Create a new PSD from the selected images"
                                      >
                                        <FileDown size={13} />
                                        {psdExporting[galleryKey] ? 'Exporting…' : 'New Export'}
                                      </button>
                                      <button
                                        type="button"
                                        className="tmdb-psd-export-btn tmdb-psd-export-btn--existing"
                                        onClick={() => void handlePsdExport(galleryKey, item.title, item.year, true)}
                                        disabled={psdExporting[galleryKey]}
                                        title="Add selected images to an existing PSD in your export folder"
                                      >
                                        <Layers size={13} />
                                        {psdExporting[galleryKey] ? 'Exporting…' : 'Use Existing PSD'}
                                      </button>
                                    </div>
                                  ) : null
                                })()}
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
                                                  {sImgs.posters.map((img) => {
                                                    const selIdx = (psdSelections[galleryKey]?.posters ?? []).indexOf(img.file_path)
                                                    const isSelected = selIdx !== -1
                                                    return (
                                                      <div key={img.file_path} className="tmdb-gallery-item">
                                                        <div className="tmdb-gallery-thumb-wrapper">
                                                          <button
                                                            type="button"
                                                            className="tmdb-gallery-thumb-btn"
                                                            onClick={() => { setGalleryPreview(img); setGalleryPreviewIsLogo(false) }}
                                                            title="Preview full size"
                                                          >
                                                            <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                                                          </button>
                                                          <button
                                                            type="button"
                                                            className={`tmdb-psd-select-btn${isSelected ? ' selected' : ''}`}
                                                            onClick={() => togglePsdSelection(galleryKey, 'poster', img.file_path)}
                                                            title={isSelected ? 'Deselect poster' : 'Select as Poster'}
                                                          >
                                                            {isSelected ? <span>{selIdx + 1}</span> : <span>P</span>}
                                                          </button>
                                                        </div>
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
                                                    )
                                                  })}
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
                                      {galleryImages[activeGalleryTab as 'posters' | 'backdrops' | 'logos'].map((img) => {
                                        const role = activeGalleryTab === 'logos' ? 'logo' : activeGalleryTab === 'backdrops' ? 'backdrop' : 'poster'
                                        const selIdx = role === 'poster'
                                          ? (psdSelections[galleryKey]?.posters ?? []).indexOf(img.file_path)
                                          : role === 'backdrop'
                                            ? (psdSelections[galleryKey]?.backdrops ?? []).indexOf(img.file_path)
                                            : (psdSelections[galleryKey]?.logos ?? []).indexOf(img.file_path)
                                        const isSelected = selIdx !== -1
                                        return (
                                          <div key={img.file_path} className="tmdb-gallery-item">
                                            <div className="tmdb-gallery-thumb-wrapper">
                                              <button
                                                type="button"
                                                className="tmdb-gallery-thumb-btn"
                                                onClick={() => { setGalleryPreview(img); setGalleryPreviewIsLogo(activeGalleryTab === 'logos') }}
                                                title="Preview full size"
                                              >
                                                <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                                              </button>
                                              <button
                                                type="button"
                                                className={`tmdb-psd-select-btn${isSelected ? ' selected' : ''}`}
                                                onClick={() => togglePsdSelection(galleryKey, role, img.file_path)}
                                                title={isSelected ? `Deselect ${role}` : role === 'poster' ? 'Select as Poster' : role === 'backdrop' ? 'Select as Background' : 'Select as Logo'}
                                              >
                                                {isSelected
                                                  ? <span>{selIdx + 1}</span>
                                                  : <span>{role === 'poster' ? 'P' : role === 'backdrop' ? 'B' : 'L'}</span>
                                                }
                                              </button>
                                            </div>
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
                                        )
                                      })}
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
              className={`tmdb-gallery-lightbox-img${galleryPreviewIsLogo ? ' tmdb-gallery-lightbox-img--logo' : ''}`}
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

      {showPsdConfigModal && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>TMDB Search Settings</h2>
              <button className="modal-close" onClick={closePsdConfigModal}>×</button>
            </div>
            <div className="modal-body">
              <div className="maker-card">
                <label>
                  PSD Export Folder
                  <small className="muted" style={{ display: 'block', margin: '0.25rem 0 0.5rem' }}>
                    Optional. When set, exported PSD files are saved here in addition to the browser download.
                    Must be an absolute container-side path (e.g. <code>/config/psd_exports</code>).
                    Leave blank for download-only.
                  </small>
                  <input
                    type="text"
                    value={psdExportFolder}
                    onChange={(e) => setPsdExportFolder(e.target.value)}
                    placeholder="/config/psd_exports"
                  />
                </label>
                <label>
                  PSD Template File
                  <small className="muted" style={{ display: 'block', margin: '0.25rem 0 0.5rem' }}>
                    Override the bundled default PSD template with your own. Provide an absolute
                    container-side path to a <code>.psd</code> file (e.g. <code>/config/my_template.psd</code>).
                    The poster image is injected into the <strong>POSTER</strong> group and the logo into the <strong>LOGO</strong> group.
                    Leave blank to use the built-in default template.
                  </small>
                  <input
                    type="text"
                    value={psdTemplatePath}
                    onChange={(e) => setPsdTemplatePath(e.target.value)}
                    placeholder="/config/template.psd"
                  />
                </label>
                <div className="maker-setting-row">
                  <div>
                    <span style={{ fontWeight: 500 }}>Open in Photopea</span>
                    <small className="muted" style={{ display: 'block', marginTop: '0.2rem' }}>
                      When enabled, exported PSD files automatically open in{' '}
                      <a href="https://www.photopea.com" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>Photopea</a>{' '}
                      in a new tab. Requires PosterFlow to be accessible over HTTPS. If no export folder
                      is configured, files are saved temporarily to <code>/config/psd_cache</code>.
                    </small>
                  </div>
                  <label className="toggle-switch" style={{ flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={psdOpenPhotopea}
                      onChange={(e) => setPsdOpenPhotopea(e.target.checked)}
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={closePsdConfigModal} disabled={saving}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleSavePsdConfig} disabled={saving} style={{ justifyContent: 'center' }}>
                <Save size={16} /> {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {psdOverwriteConfirm && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Overwrite Existing PSD?</h2>
              <button className="modal-close" onClick={() => setPsdOverwriteConfirm(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ color: '#ccc', lineHeight: 1.6, marginBottom: '0.75rem' }}>
                A PSD with this name already exists in your export folder:
              </p>
              <div className="psd-not-found-filename">
                <code>{psdOverwriteConfirm.filename}</code>
              </div>
              {psdExportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{psdExportFolder}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#ffb74d', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Continuing will overwrite it with a fresh PSD. Any edits you have made to the existing file will be lost.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setPsdOverwriteConfirm(null)}>
                Cancel
              </button>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', background: '#f44336' }}
                onClick={() => {
                  const { galleryKey, title, year } = psdOverwriteConfirm
                  setPsdOverwriteConfirm(null)
                  void handlePsdExport(galleryKey, title, year, false, true)
                }}
              >
                Overwrite
              </button>
            </div>
          </div>
        </div>
      )}

      {psdNotFound && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>PSD Not Found</h2>
              <button className="modal-close" onClick={() => setPsdNotFound(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ marginBottom: '1rem', color: '#ccc', lineHeight: 1.6 }}>
                No existing PSD was found in your export folder. To use this feature the file must be named exactly:
              </p>
              <div className="psd-not-found-filename">
                <code>{psdNotFound.expectedFilename}</code>
              </div>
              {psdExportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{psdExportFolder}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#aaa', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Place the file in your export folder{psdExportFolder ? ' shown above' : ''}, or use the button below to upload it directly from your computer.
                After uploading, the export will run automatically.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setPsdNotFound(null)}>
                Cancel
              </button>
              <label className={`btn-primary psd-upload-label${psdUploading ? ' disabled' : ''}`}>
                <input
                  type="file"
                  accept=".psd"
                  style={{ display: 'none' }}
                  disabled={psdUploading}
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) void handlePsdNotFoundUpload(file)
                  }}
                />
                {psdUploading ? 'Uploading…' : 'Browse for PSD…'}
              </label>
            </div>
          </div>
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
              <button className="btn-primary" onClick={handleSaveConfig} disabled={saving || loading} style={{ justifyContent: 'center' }}>
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
