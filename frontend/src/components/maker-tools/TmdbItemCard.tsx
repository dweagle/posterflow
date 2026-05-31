import { useState, useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  ExternalLink,
  FileDown,
  FolderOpen,
  Globe,
  Image,
  Layers,
  Clapperboard as MovieIcon,
  Tv,
} from 'lucide-react'
import {
  type TmdbSearchResult,
  type TmdbImage,
  type TmdbImagesResponse,
  type TmdbTvDetails,
  type PosterAvailability,
  checkPsdExists,
  exportToPsd,
  uploadPsdToExportFolder,
  getSeasonImages,
  getTmdbImages,
  getTmdbImageProxyUrl,
  getTvDetails,
  getApiErrorMessage,
  getSettings,
} from '../../api/client'
import { useToast } from '../Toast'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PsdConfig = {
  exportFolder: string
  templatePath: string
  openPhotopea: boolean
}

export type TmdbItemCardProps = {
  item: TmdbSearchResult
  posterAvailability?: PosterAvailability
  psdConfig?: PsdConfig
  hidePoster?: boolean
  hideTitle?: boolean
  galleryPortalId?: string
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TmdbItemCard({ item, posterAvailability, psdConfig: psdConfigProp, hidePoster, hideTitle, galleryPortalId }: TmdbItemCardProps) {
  const { showToast } = useToast()

  // Gallery state
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [galleryImages, setGalleryImages] = useState<TmdbImagesResponse | null>(null)
  const [galleryLoading, setGalleryLoading] = useState(false)
  const [activeGalleryTab, setActiveGalleryTab] = useState<'posters' | 'backdrops' | 'logos' | 'season-posters'>('posters')
  const [galleryLanguage, setGalleryLanguage] = useState('en+textless')
  const [galleryPreview, setGalleryPreview] = useState<TmdbImage | null>(null)
  const [galleryPreviewIsLogo, setGalleryPreviewIsLogo] = useState(false)

  // PSD export state
  const [psdSelection, setPsdSelection] = useState<{ posters: string[]; backdrops: string[]; logos: string[] }>({ posters: [], backdrops: [], logos: [] })
  const [psdExporting, setPsdExporting] = useState(false)
  const [psdNotFound, setPsdNotFound] = useState<{ expectedFilename: string } | null>(null)
  const [psdOverwriteConfirm, setPsdOverwriteConfirm] = useState<{ filename: string } | null>(null)
  const [psdUploading, setPsdUploading] = useState(false)

  // PSD settings: use prop if provided, else fetch once
  const [psdConfig, setPsdConfig] = useState<PsdConfig>(psdConfigProp ?? { exportFolder: '', templatePath: '', openPhotopea: false })
  useEffect(() => {
    if (psdConfigProp !== undefined) {
      setPsdConfig(psdConfigProp)
    }
  }, [psdConfigProp])
  useEffect(() => {
    if (psdConfigProp === undefined) {
      getSettings().then((s) => {
        setPsdConfig({
          exportFolder: (s.psd_export_folder || '').trim(),
          templatePath: (s.psd_template_path || '').trim(),
          openPhotopea: (s.psd_open_photopea || '').trim().toLowerCase() === 'true',
        })
      }).catch(() => { /* non-blocking */ })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // TV details + season images
  const [tvDetails, setTvDetails] = useState<TmdbTvDetails | null>(null)
  const [tvDetailsLoading, setTvDetailsLoading] = useState(false)
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null)
  const [seasonImages, setSeasonImages] = useState<Record<string, TmdbImagesResponse>>({})
  const [seasonImagesLoading, setSeasonImagesLoading] = useState<Record<string, boolean>>({})

  // Apple TV popup
  const [appleTvPopupOpen, setAppleTvPopupOpen] = useState(false)
  const [appleTvStorefront, setAppleTvStorefront] = useState(() =>
    localStorage.getItem('apple-tv-storefront') ?? '143441'
  )

  // Poster lightbox
  const [previewPoster, setPreviewPoster] = useState<string | null>(null)

  // Portal target for gallery panel (when galleryPortalId is set)
  const [galleryPortalEl, setGalleryPortalEl] = useState<Element | null>(null)
  useEffect(() => {
    if (galleryPortalId) setGalleryPortalEl(document.getElementById(galleryPortalId))
  }, [galleryPortalId])

  // Eagerly fetch TV details on mount so season/specials badges render immediately
  useEffect(() => {
    if (item.media_type !== 'tv') return
    void ensureTvDetails()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.tmdb_id])

  // Close Apple TV popup on outside click
  useEffect(() => {
    if (!appleTvPopupOpen) return
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (!target.closest('.apple-tv-popup-wrapper')) setAppleTvPopupOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [appleTvPopupOpen])

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

  const copyToClipboard = useCallback((text: string) => {
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
  }, [showToast])

  const handleGalleryDownload = useCallback(async (filePath: string) => {
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
  }, [showToast])

  const ensureTvDetails = useCallback(async () => {
    if (tvDetails || tvDetailsLoading) return
    setTvDetailsLoading(true)
    try {
      const details = await getTvDetails(item.tmdb_id)
      setTvDetails(details)
    } catch {
      // non-blocking
    } finally {
      setTvDetailsLoading(false)
    }
  }, [item.tmdb_id, tvDetails, tvDetailsLoading])

  const fetchSeasonImages = useCallback(async (seasonNumber: number) => {
    setSelectedSeason(seasonNumber)
    const sk = `s${seasonNumber}`
    if (seasonImages[sk]) return
    setSeasonImagesLoading((prev) => ({ ...prev, [sk]: true }))
    try {
      const data = await getSeasonImages(item.tmdb_id, seasonNumber, galleryLanguage)
      setSeasonImages((prev) => ({ ...prev, [sk]: data }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load season images'), 'error')
    } finally {
      setSeasonImagesLoading((prev) => ({ ...prev, [sk]: false }))
    }
  }, [item.tmdb_id, galleryLanguage, seasonImages, showToast])

  const toggleGallery = useCallback(async () => {
    if (galleryOpen) {
      setGalleryOpen(false)
      return
    }
    setGalleryOpen(true)
    if (item.media_type === 'tv') void ensureTvDetails()
    if (galleryImages) return
    setGalleryLoading(true)
    try {
      const data = await getTmdbImages(item.tmdb_id, item.media_type, galleryLanguage)
      setGalleryImages(data)
      const defaultTab = data.posters.length > 0 ? 'posters' : data.backdrops.length > 0 ? 'backdrops' : 'logos'
      setActiveGalleryTab(defaultTab)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load images'), 'error')
      setGalleryOpen(false)
    } finally {
      setGalleryLoading(false)
    }
  }, [galleryOpen, galleryImages, galleryLanguage, item.tmdb_id, item.media_type, ensureTvDetails, showToast])

  const handleGalleryLanguageChange = useCallback(async (newLang: string) => {
    setGalleryLanguage(newLang)
    setGalleryImages(null)
    setSeasonImages({})
    if (galleryOpen) {
      setGalleryLoading(true)
      try {
        const data = await getTmdbImages(item.tmdb_id, item.media_type, newLang)
        setGalleryImages(data)
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to reload images'), 'error')
      } finally {
        setGalleryLoading(false)
      }
    }
  }, [galleryOpen, item.tmdb_id, item.media_type, showToast])

  const togglePsdSelection = useCallback((role: 'poster' | 'backdrop' | 'logo', filePath: string) => {
    setPsdSelection((prev) => {
      if (role === 'logo') {
        const already = prev.logos.includes(filePath)
        return { ...prev, logos: already ? prev.logos.filter((l) => l !== filePath) : [...prev.logos, filePath] }
      }
      if (role === 'backdrop') {
        const already = prev.backdrops.includes(filePath)
        return { ...prev, backdrops: already ? prev.backdrops.filter((b) => b !== filePath) : [...prev.backdrops, filePath] }
      }
      const already = prev.posters.includes(filePath)
      return { ...prev, posters: already ? prev.posters.filter((p) => p !== filePath) : [...prev.posters, filePath] }
    })
  }, [])

  const handlePsdExport = useCallback(async (useExisting = false, confirmed = false) => {
    if (!psdSelection.posters.length && !psdSelection.backdrops.length && !psdSelection.logos.length) return
    if (!useExisting && !confirmed) {
      const safeName = item.title.replace(/[<>:"/\\|?*]/g, '').trim()
      const expectedFilename = item.year ? `${safeName} (${item.year}).psd` : `${safeName}.psd`
      const exists = await checkPsdExists(expectedFilename)
      if (exists) {
        setPsdOverwriteConfirm({ filename: expectedFilename })
        return
      }
    }
    setPsdExporting(true)
    try {
      const result = await exportToPsd(
        {
          title: item.title,
          year: item.year ?? '',
          poster_paths: psdSelection.posters,
          backdrop_paths: psdSelection.backdrops,
          logo_paths: psdSelection.logos,
          use_existing: useExisting,
        },
        item.title,
        item.year ?? '',
      )
      if (result.mode === 'not-found') {
        setPsdNotFound({ expectedFilename: result.expectedFilename })
        return
      }
      if (result.mode === 'photopea') {
        if (result.openPhotopea) {
          const saveUrl = `${window.location.origin}/api/maker-tools/psd-exports/${encodeURIComponent(result.filename)}`
          const config = { files: [result.psdUrl], server: { version: 1, url: saveUrl, formats: ['psd:true'] } }
          const photopea = `https://www.photopea.com#${encodeURIComponent(JSON.stringify(config))}`
          window.open(photopea, '_blank')
          showToast(`PSD opened in Photopea: ${result.filename}`, 'success')
        } else {
          showToast(`PSD saved: ${result.filename}`, 'success')
        }
      } else {
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
      const msg = err instanceof Error ? err.message : 'Failed to export PSD'
      showToast(msg, 'error')
    } finally {
      setPsdExporting(false)
    }
  }, [item.title, item.year, psdSelection, showToast])

  const handlePsdNotFoundUpload = useCallback(async (file: File) => {
    if (!psdNotFound) return
    const { expectedFilename } = psdNotFound
    setPsdUploading(true)
    try {
      await uploadPsdToExportFolder(file, expectedFilename)
      setPsdNotFound(null)
      showToast(`PSD uploaded as "${expectedFilename}" — adding poster layers…`, 'success')
      await handlePsdExport(true)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Upload failed'
      showToast(msg, 'error')
    } finally {
      setPsdUploading(false)
    }
  }, [psdNotFound, showToast, handlePsdExport])

  // -------------------------------------------------------------------------
  // Derived values
  // -------------------------------------------------------------------------

  const galleryTabs: Array<{ id: 'posters' | 'backdrops' | 'logos' | 'season-posters'; label: string; count: number | null }> =
    galleryImages
      ? [
          { id: 'posters', label: 'Posters', count: galleryImages.posters.length },
          { id: 'backdrops', label: 'Backdrops', count: galleryImages.backdrops.length },
          { id: 'logos', label: 'Logos', count: galleryImages.logos.length },
          ...(item.media_type === 'tv' ? [{ id: 'season-posters' as const, label: 'Seasons', count: null }] : []),
        ]
      : []

  const hasPsdSelection = !!(psdSelection.posters.length || psdSelection.backdrops.length || psdSelection.logos.length)

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="tmdb-result-wrapper">
      <div className="tmdb-result-card">
        {!hidePoster && (
          <div
            className={`tmdb-poster${item.poster_url ? ' tmdb-poster--clickable' : ''}`}
            onClick={() => { if (item.poster_url) setPreviewPoster(item.poster_url) }}
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
        )}

        <div className="tmdb-result-info">
          <div className="tmdb-result-title-row">
            {!hideTitle && <span className="tmdb-result-title">{item.title}</span>}
            {!hideTitle && item.year && <span className="tmdb-result-year">{item.year}</span>}
            {!hideTitle && posterAvailability && posterAvailability.length > 0 && (
              <span className="tmdb-poster-available" aria-label="Poster available in synced drives">
                <Check size={11} />
                <span className="tmdb-poster-available-tooltip">
                  <span className="tmdb-poster-available-header">Available in synced drives</span>
                  <span className="tmdb-poster-available-note">As of last sync</span>
                  {posterAvailability.map((entry) => (
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
            <span className={`badge ${item.media_type === 'movie' ? 'badge-blue' : item.media_type === 'tv' ? 'badge-green' : 'badge-orange'}`}>
              {item.media_type === 'movie' ? <MovieIcon size={12} /> : item.media_type === 'tv' ? <Tv size={12} /> : <FolderOpen size={12} />}
              {item.media_type === 'movie' ? 'Movie' : item.media_type === 'tv' ? 'Series' : 'Collection'}
            </span>
            {item.media_type === 'tv' && tvDetails && (
              <>
                <span className="badge badge-grey">
                  <Layers size={11} /> {tvDetails.season_count} season{tvDetails.season_count !== 1 ? 's' : ''}
                </span>
                {tvDetails.seasons.some((s) => s.season_number === 0) && (
                  <span className="badge badge-grey">Specials</span>
                )}
              </>
            )}
            {hideTitle && posterAvailability && posterAvailability.length > 0 && (
              <span className="tmdb-poster-available" aria-label="Poster available in synced drives">
                <Check size={11} />
                <span className="tmdb-poster-available-tooltip">
                  <span className="tmdb-poster-available-header">Available in synced drives</span>
                  <span className="tmdb-poster-available-note">As of last sync</span>
                  {posterAvailability.map((entry) => (
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

          <div className="tmdb-result-ids">
            <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(String(item.tmdb_id))} title="Copy TMDB ID">TMDB&nbsp;#{item.tmdb_id}</button>
            {item.imdb_id && <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(item.imdb_id!)} title="Copy IMDB ID">IMDB&nbsp;{item.imdb_id}</button>}
            {item.tvdb_id && <button type="button" className="tmdb-id-chip" onClick={() => copyToClipboard(String(item.tvdb_id))} title="Copy TVDB ID">TVDB&nbsp;#{item.tvdb_id}</button>}
          </div>

          <div className="tmdb-result-links">
            {item.homepage && (
              <a className="tmdb-result-link" href={item.homepage} target="_blank" rel="noreferrer">
                <ExternalLink size={12} /> TMDB
              </a>
            )}
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
                className={`tmdb-result-link apple-tv-popup-trigger${appleTvPopupOpen ? ' active' : ''}`}
                onClick={() => setAppleTvPopupOpen((o) => !o)}
                title="Find Apple TV artwork"
              >
                <ExternalLink size={12} /> Apple TV Art
              </button>
              {appleTvPopupOpen && (
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
                    onClick={() => setAppleTvPopupOpen(false)}
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
            {item.homepage && (
              <button
                type="button"
                className="tmdb-copy-btn"
                onClick={() => copyToClipboard(item.homepage)}
                title="Copy TMDB link"
              >
                <Copy size={12} /> Link
              </button>
            )}
          </div>

          {item.overview && <p className="tmdb-result-overview">{item.overview}</p>}

          <button
            type="button"
            className={`tmdb-gallery-toggle${galleryOpen ? ' open' : ''}`}
            onClick={() => void toggleGallery()}
            disabled={galleryLoading}
          >
            <Image size={13} />
            {galleryLoading
              ? 'Loading images…'
              : galleryOpen
                ? <><ChevronUp size={13} /> Hide images</>
                : <><ChevronDown size={13} /> Browse images</>
            }
          </button>
        </div>
      </div>

      {/* Gallery panel */}
      {galleryOpen && galleryImages && (() => { const _panel = (
        <div className="tmdb-gallery-panel">
          <div className="tmdb-gallery-tabs">
            {galleryTabs.map((t) => (
              <button
                key={t.id}
                type="button"
                className={`tmdb-gallery-tab${activeGalleryTab === t.id ? ' active' : ''}`}
                onClick={() => {
                  if (t.id === 'season-posters') {
                    setActiveGalleryTab('season-posters')
                  } else if (t.count != null && t.count > 0) {
                    setActiveGalleryTab(t.id)
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
            {hasPsdSelection && (
              <div className="tmdb-psd-export-group">
                <button
                  type="button"
                  className="tmdb-psd-export-btn tmdb-psd-export-btn--new"
                  onClick={() => void handlePsdExport(false)}
                  disabled={psdExporting}
                  title="Create a new PSD from the selected images"
                >
                  <FileDown size={13} />
                  {psdExporting ? 'Exporting…' : 'New Export'}
                </button>
                <button
                  type="button"
                  className="tmdb-psd-export-btn tmdb-psd-export-btn--existing"
                  onClick={() => void handlePsdExport(true)}
                  disabled={psdExporting}
                  title="Add selected images to an existing PSD in your export folder"
                >
                  <Layers size={13} />
                  {psdExporting ? 'Exporting…' : 'Use Existing PSD'}
                </button>
              </div>
            )}
          </div>

          {activeGalleryTab === 'season-posters'
            ? (
              <div className="tmdb-season-picker">
                {tvDetailsLoading
                  ? <p className="tmdb-gallery-empty">Loading seasons…</p>
                  : !tvDetails || tvDetails.seasons.length === 0
                    ? <p className="tmdb-gallery-empty">No seasons available.</p>
                    : (
                      <>
                        <div className="tmdb-season-chips">
                          {tvDetails.seasons.map((s) => (
                            <button
                              key={s.season_number}
                              type="button"
                              className={`tmdb-season-chip${selectedSeason === s.season_number ? ' active' : ''}`}
                              onClick={() => void fetchSeasonImages(s.season_number)}
                              disabled={seasonImagesLoading[`s${s.season_number}`]}
                            >
                              {s.season_number === 0 ? 'Specials' : `S${String(s.season_number).padStart(2, '0')}`}
                            </button>
                          ))}
                        </div>
                        {selectedSeason != null && (() => {
                          const sk = `s${selectedSeason}`
                          const sImgs = seasonImages[sk]
                          if (seasonImagesLoading[sk]) return <p className="tmdb-gallery-empty">Loading posters…</p>
                          if (!sImgs || sImgs.posters.length === 0) return <p className="tmdb-gallery-empty">No posters available for this season.</p>
                          return (
                            <div className="tmdb-gallery-grid tmdb-gallery-grid--posters">
                              {sImgs.posters.map((img) => {
                                const selIdx = psdSelection.posters.indexOf(img.file_path)
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
                                        onClick={() => togglePsdSelection('poster', img.file_path)}
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
                      ? psdSelection.posters.indexOf(img.file_path)
                      : role === 'backdrop'
                        ? psdSelection.backdrops.indexOf(img.file_path)
                        : psdSelection.logos.indexOf(img.file_path)
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
                            onClick={() => togglePsdSelection(role, img.file_path)}
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
      ); return galleryPortalId && galleryPortalEl ? createPortal(_panel, galleryPortalEl) : _panel })()} 

      {/* Poster lightbox */}
      {previewPoster && (
        <div className="tmdb-lightbox-overlay" onClick={() => setPreviewPoster(null)}>
          <img
            className="tmdb-lightbox-img"
            src={previewPoster}
            alt="Poster preview"
            onClick={(e) => e.stopPropagation()}
          />
          <button type="button" className="tmdb-lightbox-close" onClick={() => setPreviewPoster(null)}>×</button>
        </div>
      )}

      {/* Gallery image lightbox */}
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

      {/* PSD overwrite confirm modal */}
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
              {psdConfig.exportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{psdConfig.exportFolder}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#ffb74d', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Continuing will overwrite it with a fresh PSD. Any edits you have made to the existing file will be lost.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setPsdOverwriteConfirm(null)}>Cancel</button>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', background: '#f44336' }}
                onClick={() => {
                  setPsdOverwriteConfirm(null)
                  void handlePsdExport(false, true)
                }}
              >
                Overwrite
              </button>
            </div>
          </div>
        </div>
      )}

      {/* PSD not found modal */}
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
              {psdConfig.exportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{psdConfig.exportFolder}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#aaa', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Place the file in your export folder{psdConfig.exportFolder ? ' shown above' : ''}, or use the button below to upload it directly from your computer.
                After uploading, the export will run automatically.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setPsdNotFound(null)}>Cancel</button>
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
    </div>
  )
}
