import { useState, useCallback, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  Eraser,
  FileDown,
  FolderOpen,
  Globe,
  Image,
  Layers,
  Clapperboard as MovieIcon,
  Search,
  Tag,
  Trash2,
  Tv,
  X,
} from 'lucide-react'
import {
  type TmdbSearchResult,
  type TmdbImage,
  type TmdbImagesResponse,
  type TmdbTvDetails,
  type PosterAvailability,
  openPhotopeaWithPsd,
  exportToPsd,
  posterLayerNames,
  uploadPsdToExportFolder,
  getSeasonImages,
  getTmdbImages,
  type ImageSource,
  getTvdbImages,
  getTvdbSeasonImages,
  getArtworkTaggedDownloadUrl, // canonical download names
  getTmdbOriginCountry,
  getTvDetails,
  getApiErrorMessage,
  getSettings,
} from '../../api/client'
import { useToast } from '../Toast'
import PosterDriveSearchModal from '../PosterDriveSearchModal'
import ServiceLinks from './ServiceLinks'
import { useCardOverview } from '../../hooks/useCardOverview'
import tmdbIcon from '../../assets/service-icons/tmdb.png'
import tvdbIcon from '../../assets/service-icons/tvdb.png'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// value = Apple storefront id (matches Ben Dodson's region dropdown 1:1),
// iso = ISO 3166-1 alpha-2 used to auto-match a TMDB origin_country.
const APPLE_TV_STOREFRONTS = [
  { value: '143441', label: 'United States of America', iso: 'US' },
  { value: '143444', label: 'United Kingdom', iso: 'GB' },
  { value: '143460', label: 'Australia', iso: 'AU' },
  { value: '143455', label: 'Canada', iso: 'CA' },
  { value: '143442', label: 'France', iso: 'FR' },
  { value: '143443', label: 'Germany', iso: 'DE' },
  { value: '143450', label: 'Italy', iso: 'IT' },
  { value: '143462', label: 'Japan', iso: 'JP' },
  { value: '143452', label: 'Netherlands', iso: 'NL' },
  { value: '143461', label: 'New Zealand', iso: 'NZ' },
  { value: '143457', label: 'Norway', iso: 'NO' },
  { value: '143454', label: 'Spain', iso: 'ES' },
  { value: '143456', label: 'Sweden', iso: 'SE' },
  { value: '143459', label: 'Switzerland', iso: 'CH' },
  { value: '143563', label: 'Algeria', iso: 'DZ' },
  { value: '143564', label: 'Angola', iso: 'AO' },
  { value: '143538', label: 'Anguilla', iso: 'AI' },
  { value: '143540', label: 'Antigua & Barbuda', iso: 'AG' },
  { value: '143505', label: 'Argentina', iso: 'AR' },
  { value: '143524', label: 'Armenia', iso: 'AM' },
  { value: '143445', label: 'Austria', iso: 'AT' },
  { value: '143568', label: 'Azerbaijan', iso: 'AZ' },
  { value: '143559', label: 'Bahrain', iso: 'BH' },
  { value: '143490', label: 'Bangladesh', iso: 'BD' },
  { value: '143541', label: 'Barbados', iso: 'BB' },
  { value: '143565', label: 'Belarus', iso: 'BY' },
  { value: '143446', label: 'Belgium', iso: 'BE' },
  { value: '143555', label: 'Belize', iso: 'BZ' },
  { value: '143542', label: 'Bermuda', iso: 'BM' },
  { value: '143556', label: 'Bolivia', iso: 'BO' },
  { value: '143525', label: 'Botswana', iso: 'BW' },
  { value: '143503', label: 'Brazil', iso: 'BR' },
  { value: '143543', label: 'British Virgin Islands', iso: 'VG' },
  { value: '143560', label: 'Brunei', iso: 'BN' },
  { value: '143526', label: 'Bulgaria', iso: 'BG' },
  { value: '143544', label: 'Cayman Islands', iso: 'KY' },
  { value: '143483', label: 'Chile', iso: 'CL' },
  { value: '143465', label: 'China', iso: 'CN' },
  { value: '143501', label: 'Colombia', iso: 'CO' },
  { value: '143495', label: 'Costa Rica', iso: 'CR' },
  { value: '143527', label: "Cote D'Ivoire", iso: 'CI' },
  { value: '143494', label: 'Croatia', iso: 'HR' },
  { value: '143557', label: 'Cyprus', iso: 'CY' },
  { value: '143489', label: 'Czech Republic', iso: 'CZ' },
  { value: '143458', label: 'Denmark', iso: 'DK' },
  { value: '143545', label: 'Dominica', iso: 'DM' },
  { value: '143508', label: 'Dominican Rep.', iso: 'DO' },
  { value: '143509', label: 'Ecuador', iso: 'EC' },
  { value: '143516', label: 'Egypt', iso: 'EG' },
  { value: '143506', label: 'El Salvador', iso: 'SV' },
  { value: '143518', label: 'Estonia', iso: 'EE' },
  { value: '143447', label: 'Finland', iso: 'FI' },
  { value: '143573', label: 'Ghana', iso: 'GH' },
  { value: '143448', label: 'Greece', iso: 'GR' },
  { value: '143546', label: 'Grenada', iso: 'GD' },
  { value: '143504', label: 'Guatemala', iso: 'GT' },
  { value: '143553', label: 'Guyana', iso: 'GY' },
  { value: '143510', label: 'Honduras', iso: 'HN' },
  { value: '143463', label: 'Hong Kong', iso: 'HK' },
  { value: '143482', label: 'Hungary', iso: 'HU' },
  { value: '143558', label: 'Iceland', iso: 'IS' },
  { value: '143467', label: 'India', iso: 'IN' },
  { value: '143476', label: 'Indonesia', iso: 'ID' },
  { value: '143449', label: 'Ireland', iso: 'IE' },
  { value: '143491', label: 'Israel', iso: 'IL' },
  { value: '143511', label: 'Jamaica', iso: 'JM' },
  { value: '143528', label: 'Jordan', iso: 'JO' },
  { value: '143517', label: 'Kazakstan', iso: 'KZ' },
  { value: '143529', label: 'Kenya', iso: 'KE' },
  { value: '143466', label: 'Korea, Republic Of', iso: 'KR' },
  { value: '143493', label: 'Kuwait', iso: 'KW' },
  { value: '143519', label: 'Latvia', iso: 'LV' },
  { value: '143497', label: 'Lebanon', iso: 'LB' },
  { value: '143522', label: 'Liechtenstein', iso: 'LI' },
  { value: '143520', label: 'Lithuania', iso: 'LT' },
  { value: '143451', label: 'Luxembourg', iso: 'LU' },
  { value: '143515', label: 'Macau', iso: 'MO' },
  { value: '143530', label: 'Macedonia', iso: 'MK' },
  { value: '143531', label: 'Madagascar', iso: 'MG' },
  { value: '143473', label: 'Malaysia', iso: 'MY' },
  { value: '143488', label: 'Maldives', iso: 'MV' },
  { value: '143532', label: 'Mali', iso: 'ML' },
  { value: '143521', label: 'Malta', iso: 'MT' },
  { value: '143533', label: 'Mauritius', iso: 'MU' },
  { value: '143468', label: 'Mexico', iso: 'MX' },
  { value: '143523', label: 'Moldova, Republic Of', iso: 'MD' },
  { value: '143547', label: 'Montserrat', iso: 'MS' },
  { value: '143484', label: 'Nepal', iso: 'NP' },
  { value: '143512', label: 'Nicaragua', iso: 'NI' },
  { value: '143534', label: 'Niger', iso: 'NE' },
  { value: '143561', label: 'Nigeria', iso: 'NG' },
  { value: '143562', label: 'Oman', iso: 'OM' },
  { value: '143477', label: 'Pakistan', iso: 'PK' },
  { value: '143485', label: 'Panama', iso: 'PA' },
  { value: '143513', label: 'Paraguay', iso: 'PY' },
  { value: '143507', label: 'Peru', iso: 'PE' },
  { value: '143474', label: 'Philippines', iso: 'PH' },
  { value: '143478', label: 'Poland', iso: 'PL' },
  { value: '143453', label: 'Portugal', iso: 'PT' },
  { value: '143498', label: 'Qatar', iso: 'QA' },
  { value: '143487', label: 'Romania', iso: 'RO' },
  { value: '143469', label: 'Russia', iso: 'RU' },
  { value: '143479', label: 'Saudi Arabia', iso: 'SA' },
  { value: '143535', label: 'Senegal', iso: 'SN' },
  { value: '143500', label: 'Serbia', iso: 'RS' },
  { value: '143464', label: 'Singapore', iso: 'SG' },
  { value: '143496', label: 'Slovakia', iso: 'SK' },
  { value: '143499', label: 'Slovenia', iso: 'SI' },
  { value: '143472', label: 'South Africa', iso: 'ZA' },
  { value: '143486', label: 'Sri Lanka', iso: 'LK' },
  { value: '143548', label: 'St. Kitts & Nevis', iso: 'KN' },
  { value: '143549', label: 'St. Lucia', iso: 'LC' },
  { value: '143550', label: 'St. Vincent & The Grenadines', iso: 'VC' },
  { value: '143554', label: 'Suriname', iso: 'SR' },
  { value: '143470', label: 'Taiwan', iso: 'TW' },
  { value: '143572', label: 'Tanzania', iso: 'TZ' },
  { value: '143475', label: 'Thailand', iso: 'TH' },
  { value: '143539', label: 'The Bahamas', iso: 'BS' },
  { value: '143551', label: 'Trinidad & Tobago', iso: 'TT' },
  { value: '143536', label: 'Tunisia', iso: 'TN' },
  { value: '143480', label: 'Turkey', iso: 'TR' },
  { value: '143552', label: 'Turks & Caicos', iso: 'TC' },
  { value: '143537', label: 'Uganda', iso: 'UG' },
  { value: '143492', label: 'Ukraine', iso: 'UA' },
  { value: '143481', label: 'United Arab Emirates', iso: 'AE' },
  { value: '143514', label: 'Uruguay', iso: 'UY' },
  { value: '143566', label: 'Uzbekistan', iso: 'UZ' },
  { value: '143502', label: 'Venezuela', iso: 'VE' },
  { value: '143471', label: 'Vietnam', iso: 'VN' },
  { value: '143571', label: 'Yemen', iso: 'YE' },
]

// ISO 3166-1 alpha-2 → Apple storefront id, for auto-selecting the region from origin country.
const STOREFRONT_BY_ISO = new Map(APPLE_TV_STOREFRONTS.map((s) => [s.iso, s.value]))

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

// Season image caches are per source — the same season number has different artwork on each.
const seasonKey = (source: ImageSource, seasonNumber: number) => `${source}:s${seasonNumber}`

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type PsdConfig = {
  // CL2K (the default style). Existing keys.
  exportFolder: string
  templatePath: string
  imageExportFolder: string   // blank = download image exports in-browser; set = save to this server folder
  // MM2K counterparts. Blank = that style downloads in-browser (no CL2K fallback).
  exportFolderMm2k: string
  templatePathMm2k: string
  imageExportFolderMm2k: string
  openPhotopea: boolean        // shared toggle across both styles
  sameTab: boolean             // reuse one Photopea tab (separate docs) instead of a new tab each export
  // Not PSD-related, but this is the settings-derived config every card already receives:
  // gates the gallery's TheTVDB source tab on a configured API key.
  tvdbEnabled: boolean
}

/** Empty config used before settings load (shared by every consumer). */
export const EMPTY_PSD_CONFIG: PsdConfig = {
  exportFolder: '', templatePath: '', imageExportFolder: '',
  exportFolderMm2k: '', templatePathMm2k: '', imageExportFolderMm2k: '',
  openPhotopea: false, sameTab: false, tvdbEnabled: false,
}

/** Derive the read-only PSD config from a settings map (shared by every consumer). */
export function derivePsdConfig(s: Record<string, string>): PsdConfig {
  return {
    exportFolder: (s.psd_export_folder || '').trim(),
    templatePath: (s.psd_template_path || '').trim(),
    imageExportFolder: (s.psd_image_export_folder || '').trim(),
    exportFolderMm2k: (s.psd_export_folder_mm2k || '').trim(),
    templatePathMm2k: (s.psd_template_path_mm2k || '').trim(),
    imageExportFolderMm2k: (s.psd_image_export_folder_mm2k || '').trim(),
    openPhotopea: (s.psd_open_photopea || '').trim().toLowerCase() === 'true',
    sameTab: (s.psd_photopea_same_tab || '').trim().toLowerCase() === 'true',
    // tvdb_api_key is sensitive, so it comes back masked when set — presence is all we need.
    tvdbEnabled: !!(s.tvdb_api_key || '').trim(),
  }
}

export type TmdbItemCardProps = {
  item: TmdbSearchResult
  posterAvailability?: PosterAvailability
  /** True once the drive availability check has run, so "none found" can show a red X. */
  posterAvailabilityChecked?: boolean
  psdConfig?: PsdConfig
  /** Poster style from the request's tag — picks the template + export/image folder. Undefined → CL2K. */
  posterStyle?: 'CL2K' | 'MM2K'
  hidePoster?: boolean
  hideTitle?: boolean
  galleryPortalId?: string
  /** Bump this (e.g. when the parent request is completed) to auto-close the image gallery. */
  collapseSignal?: number
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TmdbItemCard({ item, posterAvailability, posterAvailabilityChecked, psdConfig: psdConfigProp, posterStyle, hidePoster, hideTitle, galleryPortalId, collapseSignal }: TmdbItemCardProps) {
  const { showToast } = useToast()

  // No TMDB match (sentinel tmdb_id 0/null): hide the TMDB-only chrome (gallery, id chip) but
  // still let the user export a blank/existing PSD, named by title + year.
  const hasTmdb = (item.tmdb_id ?? 0) > 0
  const overview = useCardOverview(item)

  // Gallery state. Images are cached per source, so TMDB stays the default load and TheTVDB is
  // only ever called once the user actually clicks its tab — after that, switching is instant.
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [imageSource, setImageSource] = useState<ImageSource>('tmdb')
  const [imagesBySource, setImagesBySource] = useState<Partial<Record<ImageSource, TmdbImagesResponse>>>({})
  const [loadingSource, setLoadingSource] = useState<ImageSource | null>(null)
  const galleryImages = imagesBySource[imageSource] ?? null
  const galleryLoading = loadingSource === imageSource
  const [activeGalleryTab, setActiveGalleryTab] = useState<'posters' | 'backdrops' | 'logos' | 'season-posters'>('posters')
  const [galleryLanguage, setGalleryLanguage] = useState('en+textless')
  const [galleryPreview, setGalleryPreview] = useState<TmdbImage | null>(null)
  const [galleryPreviewIsLogo, setGalleryPreviewIsLogo] = useState(false)
  // remember what the previewed image is, so its Download button
  // can request the correctly-tagged filename.
  const [galleryPreviewRole, setGalleryPreviewRole] = useState<'poster' | 'backdrop' | 'logo'>('poster')
  const [galleryPreviewSeason, setGalleryPreviewSeason] = useState<number | null>(null)

  // PSD export state
  const [psdSelection, setPsdSelection] = useState<{ posters: string[]; backdrops: string[]; logos: string[] }>({ posters: [], backdrops: [], logos: [] })
  const [psdExporting, setPsdExporting] = useState(false)
  const [psdNotFound, setPsdNotFound] = useState<{ expectedFilename: string } | null>(null)
  const [psdOverwriteConfirm, setPsdOverwriteConfirm] = useState<{ filename: string } | null>(null)
  const [psdUploading, setPsdUploading] = useState(false)
  const [posterTags, setPosterTags] = useState<Record<string, string>>({})   // file_path → convention name (poster OR backdrop images)
  const [tagTarget, setTagTarget] = useState<{ path: string; backdrop: boolean } | null>(null)   // image whose tag popup is open
  const [tagDecade, setTagDecade] = useState(1)   // Tag popup: which season decade (1, 11, 21…) is expanded

  // Export style: defaults to the request's style, but the toolbar toggle can override it so an
  // MM2K request can also be built in CL2K (and vice-versa). Each style has its own template +
  // export/image folder, so the two versions coexist as separate files. Re-syncs if the prop changes.
  const [exportStyle, setExportStyle] = useState<'CL2K' | 'MM2K'>(posterStyle ?? 'CL2K')
  useEffect(() => { setExportStyle(posterStyle ?? 'CL2K') }, [posterStyle])

  // PSD settings: use prop if provided, else fetch once
  const [psdConfig, setPsdConfig] = useState<PsdConfig>(psdConfigProp ?? EMPTY_PSD_CONFIG)
  useEffect(() => {
    if (psdConfigProp !== undefined) {
      setPsdConfig(psdConfigProp)
    }
  }, [psdConfigProp])
  useEffect(() => {
    if (psdConfigProp === undefined) {
      getSettings().then((s) => setPsdConfig(derivePsdConfig(s))).catch(() => { /* non-blocking */ })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // TV details + season images
  const [tvDetails, setTvDetails] = useState<TmdbTvDetails | null>(null)
  const [tvDetailsLoading, setTvDetailsLoading] = useState(false)
  const [selectedSeason, setSelectedSeason] = useState<number | null>(null)
  const [seasonImages, setSeasonImages] = useState<Record<string, TmdbImagesResponse>>({})
  const [seasonImagesLoading, setSeasonImagesLoading] = useState<Record<string, boolean>>({})

  // Apple TV artwork: storefront auto-detected from the title's country of origin
  const [appleTvStorefront, setAppleTvStorefront] = useState('143441')
  const appleTvOriginFetchedRef = useRef(false)  // origin country resolved once per card

  // Poster lightbox
  const [previewPoster, setPreviewPoster] = useState<string | null>(null)

  // In-place drive poster search
  const [driveSearchOpen, setDriveSearchOpen] = useState(false)

  // Portal target for gallery panel (when galleryPortalId is set)
  const [galleryPortalEl, setGalleryPortalEl] = useState<Element | null>(null)
  useEffect(() => {
    if (galleryPortalId) setGalleryPortalEl(document.getElementById(galleryPortalId))
  }, [galleryPortalId])

  // Collapse the image gallery when the parent bumps the signal (e.g. request marked complete).
  useEffect(() => {
    if (collapseSignal) setGalleryOpen(false)
  }, [collapseSignal])

  // Eagerly fetch TV details on mount so season/specials badges render immediately
  useEffect(() => {
    if (item.media_type !== 'tv' || (item.tmdb_id ?? 0) <= 0) return
    void ensureTvDetails()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.tmdb_id])

  // Resolve the title's origin country once (on first hover/focus of the link) and pre-select
  // the matching Apple storefront, so the link opens the right region by click time.
  const ensureAppleTvStorefront = useCallback(() => {
    if (appleTvOriginFetchedRef.current) return
    if ((item.tmdb_id ?? 0) <= 0) return
    if (item.media_type !== 'movie' && item.media_type !== 'tv') return
    appleTvOriginFetchedRef.current = true
    getTmdbOriginCountry(item.tmdb_id, item.media_type)
      .then((countries) => {
        for (const iso of countries) {
          const storefront = STOREFRONT_BY_ISO.get(iso.toUpperCase())
          if (storefront) { setAppleTvStorefront(storefront); break }
        }
      })
      .catch(() => { /* keep the current default */ })
  }, [item.tmdb_id, item.media_type])

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

  // downloads are named canonically by the server
  // (Title (Year) {ids}[ - Season N][ - logo|background].ext), matching the rest of the app.
  const handleGalleryDownload = useCallback(async (filePath: string, role: 'poster' | 'backdrop' | 'logo' = 'poster', season?: number | null) => {
    try {
      const resp = await fetch(getArtworkTaggedDownloadUrl(filePath, role, item, season))
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const cd = resp.headers.get('Content-Disposition') || ''
      const m = /filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i.exec(cd)
      const filename = m ? decodeURIComponent(m[1] || m[2]) : (filePath.split('/').filter(Boolean).pop() ?? 'artwork.jpg')
      const objectUrl = URL.createObjectURL(await resp.blob())
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
  }, [item, showToast])

  const ensureTvDetails = useCallback(async () => {
    if (tvDetails || tvDetailsLoading) return
    setTvDetailsLoading(true)
    try {
      const details = await getTvDetails(item.tmdb_id, item.tvdb_id)
      setTvDetails(details)
    } catch {
      // non-blocking
    } finally {
      setTvDetailsLoading(false)
    }
  }, [item.tmdb_id, item.tvdb_id, tvDetails, tvDetailsLoading])

  const fetchSeasonImages = useCallback(async (seasonNumber: number) => {
    setSelectedSeason(seasonNumber)
    const sk = seasonKey(imageSource, seasonNumber)
    if (seasonImages[sk]) return
    setSeasonImagesLoading((prev) => ({ ...prev, [sk]: true }))
    try {
      const data = imageSource === 'tmdb'
        ? await getSeasonImages(item.tmdb_id, seasonNumber, galleryLanguage)
        : await getTvdbSeasonImages(item.tvdb_id ?? 0, seasonNumber, galleryLanguage)
      setSeasonImages((prev) => ({ ...prev, [sk]: data }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load season images'), 'error')
    } finally {
      setSeasonImagesLoading((prev) => ({ ...prev, [sk]: false }))
    }
  }, [item.tmdb_id, item.tvdb_id, imageSource, galleryLanguage, seasonImages, showToast])

  // Fetch one source's images and cache them; already-cached sources are a no-op unless forced
  // (a language change, which invalidates every cache). Returns whether that source now has
  // images to show, so callers can back out instead of leaving an empty panel open.
  const loadSource = useCallback(async (source: ImageSource, opts?: { language?: string; force?: boolean }) => {
    const language = opts?.language ?? galleryLanguage
    if (!opts?.force && imagesBySource[source]) return true
    setLoadingSource(source)
    try {
      const data = source === 'tmdb'
        ? await getTmdbImages(item.tmdb_id, item.media_type, language)
        : await getTvdbImages(item, language)
      setImagesBySource((prev) => ({ ...prev, [source]: data }))
      // Keep the current tab when the new source has content for it, else fall to the first
      // tab that does — so switching sources never lands on a needlessly empty grid.
      setActiveGalleryTab((cur) => {
        if (cur === 'season-posters') return cur
        if (data[cur].length > 0) return cur
        return data.posters.length > 0 ? 'posters' : data.backdrops.length > 0 ? 'backdrops' : 'logos'
      })
      return true
    } catch (error) {
      const label = source === 'tmdb' ? 'TMDB' : 'TheTVDB'
      showToast(getApiErrorMessage(error, `Failed to load ${label} images`), 'error')
      return false
    } finally {
      setLoadingSource((cur) => (cur === source ? null : cur))
    }
  }, [galleryLanguage, imagesBySource, item, showToast])

  const toggleGallery = useCallback(async () => {
    if (galleryOpen) {
      setGalleryOpen(false)
      return
    }
    setGalleryOpen(true)
    if (item.media_type === 'tv') void ensureTvDetails()
    // Nothing to render if the load fails, so close again rather than showing an empty panel.
    if (!(await loadSource(imageSource))) setGalleryOpen(false)
  }, [galleryOpen, imageSource, item.media_type, ensureTvDetails, loadSource])

  const handleSourceChange = useCallback(async (source: ImageSource) => {
    if (source === imageSource) return
    const previous = imageSource
    setImageSource(source)
    setSelectedSeason(null)
    // A failed switch would leave the panel blank — stay on the source that still has images.
    if (!(await loadSource(source))) setImageSource(previous)
  }, [imageSource, loadSource])

  const handleGalleryLanguageChange = useCallback(async (newLang: string) => {
    setGalleryLanguage(newLang)
    // Every cache is language-scoped; drop them all and refetch only the source in view. The
    // other reloads the next time its tab is clicked.
    setImagesBySource({})
    setSeasonImages({})
    setSeasonImagesLoading({})
    setSelectedSeason(null)
    if (galleryOpen && !(await loadSource(imageSource, { language: newLang, force: true }))) {
      setGalleryOpen(false)
    }
  }, [galleryOpen, imageSource, loadSource])

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

  const handlePsdExport = useCallback(async (useExisting = false, confirmOverwrite = false) => {
    // No selection is required — a New Export yields a blank template, Use Existing opens the saved
    // PSD. The server owns conflict detection: one call returns 'exists' (a New Export would
    // overwrite a saved title) or 'not-found' (Use Existing has no file), so we don't pre-check.
    setPsdExporting(true)
    try {
      const result = await exportToPsd(
        {
          title: item.title,
          year: item.year ?? '',
          tmdb_id: item.tmdb_id ? String(item.tmdb_id) : '',
          tvdb_id: item.tvdb_id != null ? String(item.tvdb_id) : '',
          imdb_id: item.imdb_id ?? '',
          media_type: item.media_type ?? '',
          style: exportStyle,
          poster_paths: psdSelection.posters,
          backdrop_paths: psdSelection.backdrops,
          logo_paths: psdSelection.logos,
          // Per-image tag names, aligned to poster_paths / backdrop_paths ('' = untagged → default name).
          poster_layer_names: posterLayerNames(psdSelection.posters, posterTags),
          backdrop_layer_names: posterLayerNames(psdSelection.backdrops, posterTags),
          use_existing: useExisting,
          confirm_overwrite: confirmOverwrite,
        },
        item.title,
        item.year ?? '',
      )
      if (result.mode === 'exists') {
        setPsdOverwriteConfirm({ filename: result.existingFilename })
        return
      }
      if (result.mode === 'not-found') {
        setPsdNotFound({ expectedFilename: result.expectedFilename })
        return
      }
      if (result.mode === 'photopea') {
        if (result.openPhotopea) {
          // Photopea fetches the exported PSD itself (files:[url]) and the plugin panel adds the
          // seasons / save / JPG buttons. Works on http LAN once the user allows Photopea's
          // one-time "local network access" prompt. In same-tab mode later exports are added as
          // separate documents in the one open Photopea tab (onError surfaces a failed app.open).
          openPhotopeaWithPsd(result.psdUrl, result.filename, result.style, psdConfig.sameTab,
            (msg) => showToast(`Photopea couldn't open the PSD: ${msg}`, 'error'))
          showToast(
            psdConfig.sameTab
              ? `Adding ${result.filename} to Photopea…`
              : `Opening ${result.filename} in Photopea…`,
            'success',
          )
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
  }, [item.title, item.year, item.tmdb_id, item.tvdb_id, item.imdb_id, item.media_type, exportStyle, psdSelection, posterTags, psdConfig.sameTab, showToast])

  // Tag a specific poster with a plugin-convention name (s1/s0/main/show/c). Tagging just STORES the
  // name (and selects the poster); it's applied when the user later hits New/Use-Existing Export,
  // which names that poster's injected layer so the plugin's ⚡ batch recognizes it.
  const handleTag = useCallback((filePath: string, name: string, backdrop: boolean) => {
    setPosterTags((prev) => ({ ...prev, [filePath]: name }))
    // A tagged poster is injected as a poster layer (cover-fill); a tagged backdrop stays a backdrop
    // layer (scaled to canvas height, no crop) — so route it into the matching selection.
    setPsdSelection((prev) => {
      const key = backdrop ? 'backdrops' : 'posters'
      return prev[key].includes(filePath) ? prev : { ...prev, [key]: [...prev[key], filePath] }
    })
    setTagTarget(null)
  }, [])

  // Removing a tag also drops the image from its selection — tagging is what put it there (important
  // for backdrops, whose thumbnail has no separate select button to remove it).
  const handleUntag = useCallback((filePath: string, backdrop: boolean) => {
    setPosterTags((prev) => { const next = { ...prev }; delete next[filePath]; return next })
    setPsdSelection((prev) => {
      const key = backdrop ? 'backdrops' : 'posters'
      return { ...prev, [key]: prev[key].filter((p) => p !== filePath) }
    })
    setTagTarget(null)
  }, [])

  // Reset the whole PSD picker: every P/B/L selection and every tag.
  const handleClearPsdSelection = useCallback(() => {
    setPsdSelection({ posters: [], backdrops: [], logos: [] })
    setPosterTags({})
    setTagTarget(null)
  }, [])

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

  // Export folder shown in the conflict/not-found hints — the one for the selected export style.
  const activeExportFolder = exportStyle === 'MM2K' ? psdConfig.exportFolderMm2k : psdConfig.exportFolder

  // A TMDB "Miniseries" replaces the generic "Series" badge rather than adding a second one.
  const isMiniseries = item.media_type === 'tv' && tvDetails?.series_type === 'Miniseries'

  // `display` null = the item has no such id, which still gets a chip so the row stays a
  // fixed three across. `copy` is the bare id, without the "#" the label shows.
  const idChips = [
    { label: 'TMDB', display: hasTmdb ? `#${item.tmdb_id}` : null, copy: String(item.tmdb_id ?? '') },
    { label: 'IMDB', display: item.imdb_id || null, copy: item.imdb_id ?? '' },
    { label: 'TVDB', display: item.tvdb_id ? `#${item.tvdb_id}` : null, copy: String(item.tvdb_id ?? '') },
  ]

  // The season picker follows whichever source's images are being browsed: each provider can
  // list a season the other doesn't (TMDB often carries one more for an airing show), and
  // pinning the chips to one source would make the other's season posters unreachable. Falls
  // back to the preferred list when the active source has no seasons of its own.
  const seasonList = !tvDetails
    ? []
    : (imageSource === 'tvdb' ? tvDetails.tvdb_seasons : tvDetails.tmdb_seasons).length > 0
      ? (imageSource === 'tvdb' ? tvDetails.tvdb_seasons : tvDetails.tmdb_seasons)
      : tvDetails.seasons

  const galleryTabs: Array<{ id: 'posters' | 'backdrops' | 'logos' | 'season-posters'; label: string; count: number | null }> =
    galleryImages
      ? [
          { id: 'posters', label: 'Posters', count: galleryImages.posters.length },
          { id: 'backdrops', label: 'Backdrops', count: galleryImages.backdrops.length },
          { id: 'logos', label: 'Logos', count: galleryImages.logos.length },
          ...(item.media_type === 'tv' ? [{ id: 'season-posters' as const, label: 'Seasons', count: null }] : []),
        ]
      : []

  // Drive availability indicator shown next to "Search Drives": green check with a
  // per-style/season tooltip when posters exist, red X once a check found none.
  const hasDrivePosters = !!posterAvailability && posterAvailability.length > 0
  const availabilityIndicator = hasDrivePosters ? (
    <span className="tmdb-poster-available" aria-label="Poster available in synced drives">
      <Check size={11} />
      <span className="tmdb-poster-available-tooltip">
        <span className="tmdb-poster-available-header">Available in synced drives</span>
        <span className="tmdb-poster-available-note">As of last sync</span>
        {posterAvailability?.map((entry) => (
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
  ) : posterAvailabilityChecked ? (
    <span className="tmdb-poster-unavailable" aria-label="No poster in synced drives" title="No poster found in synced drives">
      <X size={11} />
    </span>
  ) : null

  // The availability cap and "Search Drives" button render as one pill, with the
  // check/X sitting flush on the left so they read as a single control.
  const driveSearchControl = (
    <span className={`tmdb-drive-search-group${availabilityIndicator ? ' has-cap' : ''}`}>
      {availabilityIndicator}
      <button
        type="button"
        className="tmdb-drive-search-btn"
        onClick={() => setDriveSearchOpen(true)}
        title="Search synced drive poster folders for this title"
      >
        <Search size={12} /> Search Drives
      </button>
    </span>
  )

  // Style toggle + New/Use-Existing export buttons — shared by the gallery bar and, when there's
  // no TMDB match, the standalone group on the card body (see hasTmdb branch below).
  const psdExportControls = (
    <>
      <div className="tmdb-psd-style-toggle" role="group" aria-label="Export poster style">
        {(['CL2K', 'MM2K'] as const).map((s) => (
          <button
            key={s}
            type="button"
            className={`tmdb-psd-style-opt${exportStyle === s ? ' active' : ''}`}
            onClick={() => setExportStyle(s)}
            disabled={psdExporting}
            title={posterStyle === s ? `${s} — the requested style` : `Export as ${s}`}
          >
            {s}
            {posterStyle === s && <span className="tmdb-psd-style-req" title="Requested style" />}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="tmdb-psd-export-btn tmdb-psd-export-btn--new"
        onClick={() => void handlePsdExport(false)}
        disabled={psdExporting}
        title="Create a new PSD from the selected images, or a blank template if none are selected"
      >
        <FileDown size={13} />
        {psdExporting ? 'Exporting…' : 'New Export'}
      </button>
      <button
        type="button"
        className="tmdb-psd-export-btn tmdb-psd-export-btn--existing"
        onClick={() => void handlePsdExport(true)}
        disabled={psdExporting}
        title="Open an existing PSD from your export folder and add any selected images to it"
      >
        <Layers size={13} />
        {psdExporting ? 'Exporting…' : 'Use Existing PSD'}
      </button>
    </>
  )

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

        {/* Left side: poster, title, year, overview. Omitted entirely when the host card already
            shows all of it (request cards), so the box isn't pushed off a blank column. */}
        {(!hideTitle || item.overview) && (
          <div className="tmdb-result-info">
            <div className="tmdb-result-title-row">
              {!hideTitle && <span className="tmdb-result-title">{item.title}</span>}
              {!hideTitle && item.year && <span className="tmdb-result-year">{item.year}</span>}
              {!hideTitle && item.auto_matched && (
                <span className="tmdb-matched-badge" title="Matched from your *arr metadata by id">
                  <Check size={11} /> Matched
                </span>
              )}
            </div>

            {overview && <p className="tmdb-result-overview">{overview}</p>}
          </div>
        )}

          {/* Right side: everything actionable, at a fixed width so it's identical on every tab. */}
          <div className="tmdb-result-box">
          <div className="tmdb-result-meta">
            <span className={`badge ${item.media_type === 'movie' ? 'badge-blue' : item.media_type === 'tv' ? (isMiniseries ? 'badge-purple' : 'badge-green') : 'badge-orange'}`}>
              {item.media_type === 'movie' ? <MovieIcon size={12} /> : item.media_type === 'tv' ? <Tv size={12} /> : <FolderOpen size={12} />}
              {item.media_type === 'movie' ? 'Movie' : item.media_type === 'tv' ? (isMiniseries ? 'Miniseries' : 'Series') : 'Collection'}
            </span>
            {item.media_type === 'tv' && tvDetails && (
              <>
                <span
                  className="badge badge-grey"
                  title={tvDetails.season_source === 'tvdb'
                    ? 'Season count from TheTVDB — the same source Sonarr uses'
                    : 'Season count from TMDB'}
                >
                  <Layers size={11} /> {tvDetails.season_count} season{tvDetails.season_count !== 1 ? 's' : ''}
                </span>
                {tvDetails.seasons.some((s) => s.season_number === 0) && (
                  <span className="badge badge-grey">Specials</span>
                )}
              </>
            )}
            {driveSearchControl}
          </div>

          <ServiceLinks item={item} appleTvStorefront={appleTvStorefront} onAppleTvIntent={ensureAppleTvStorefront} />

          {/* ID chips — all three always render, so the row keeps its shape whichever ids the
              item happens to carry. Present ones copy on click; missing ones say so. */}
          <div className="tmdb-result-ids tmdb-result-ids--compact">
            {idChips.map(({ label, display, copy }) => (
              display
                ? (
                  <button
                    key={label}
                    type="button"
                    className="tmdb-id-chip"
                    onClick={() => copyToClipboard(copy)}
                    title={`Copy ${label} ID`}
                  >
                    <Copy size={10} />{label}&nbsp;{display}
                  </button>
                )
                : (
                  <span key={label} className="tmdb-id-chip tmdb-id-chip--empty" title={`No ${label} ID`}>
                    No {label} ID
                  </span>
                )
            ))}
          </div>

          {/* Title / link text — click to copy. */}
          <div className="tmdb-result-copyrow">
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

          {hasTmdb ? (
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
          ) : (
            // No TMDB match → no image gallery; expose the export buttons directly so a blank
            // (or existing) PSD can still be made.
            <div className="tmdb-psd-export-group tmdb-psd-export-group--standalone">
              {psdExportControls}
            </div>
          )}
          </div>
      </div>

      {/* Gallery panel */}
      {galleryOpen && (galleryImages || galleryLoading) && (() => { const _panel = (
        <div className="tmdb-gallery-panel">
          <div className="tmdb-gallery-tabs">
            {psdConfig.tvdbEnabled && (
              <div className="tmdb-gallery-sources" role="group" aria-label="Image source">
                {([['tmdb', 'TMDB', tmdbIcon], ['tvdb', 'TVDB', tvdbIcon]] as const).map(([id, label, icon]) => (
                  <button
                    key={id}
                    type="button"
                    className={`tmdb-gallery-source tmdb-gallery-source--${id}${imageSource === id ? ' active' : ''}`}
                    onClick={() => void handleSourceChange(id)}
                    disabled={loadingSource !== null}
                    title={`Browse ${label} images`}
                    aria-label={`Browse ${label} images`}
                  >
                    <img className="tmdb-gallery-source-icon" src={icon} alt="" />
                  </button>
                ))}
              </div>
            )}
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
            {(() => {
              const hasSelection =
                psdSelection.posters.length + psdSelection.backdrops.length + psdSelection.logos.length > 0 ||
                Object.keys(posterTags).length > 0
              return (
                <button
                  type="button"
                  className="tmdb-gallery-clear-btn"
                  onClick={handleClearPsdSelection}
                  disabled={!hasSelection}
                  title="Clear all selected posters and tags"
                >
                  <Eraser size={13} />
                  Clear
                </button>
              )
            })()}
            <div className="tmdb-psd-export-group">
              {psdExportControls}
            </div>
          </div>

          {tagTarget && (() => {
            const cur = posterTags[tagTarget.path]
            return (
              <div className="tmdb-tag-overlay" onClick={() => setTagTarget(null)}>
                <div className="tmdb-tag-popup" onClick={(e) => e.stopPropagation()}>
                  <div className="tmdb-tag-head">
                    <span>Tag {tagTarget.backdrop ? 'background' : 'poster'} as…</span>
                    <button type="button" className="tmdb-tag-close" onClick={() => setTagTarget(null)} aria-label="Close"><X size={15} /></button>
                  </div>
                  <div className="tmdb-tag-roles">
                    {([['main', 'Movie'], ['show', 'Show'], ['s0', 'Specials'], ['c', 'Collection']] as const).map(([val, label]) => (
                      <button key={val} type="button" className={`tmdb-tag-chip role${cur === val ? ' active' : ''}`} onClick={() => handleTag(tagTarget.path, val, tagTarget.backdrop)}>{label}</button>
                    ))}
                  </div>
                  <div className="tmdb-tag-sub">Season</div>
                  <div className="tmdb-tag-decades">
                    {[1, 11, 21, 31, 41, 51].map((d) => (
                      <button key={d} type="button" className={`tmdb-tag-chip decade${tagDecade === d ? ' active' : ''}`} onClick={() => setTagDecade(d)}>{d}–{d + 9}</button>
                    ))}
                  </div>
                  <div className="tmdb-tag-seasons">
                    {Array.from({ length: 10 }, (_, i) => tagDecade + i).map((n) => (
                      <button key={n} type="button" className={`tmdb-tag-chip num${cur === `s${n}` ? ' active' : ''}`} onClick={() => handleTag(tagTarget.path, `s${n}`, tagTarget.backdrop)}>{n}</button>
                    ))}
                  </div>
                  {cur && (
                    <button type="button" className="tmdb-tag-untag" onClick={() => handleUntag(tagTarget.path, tagTarget.backdrop)}>Remove tag “{cur}”</button>
                  )}
                </div>
              </div>
            )
          })()}

          {!galleryImages
            ? <p className="tmdb-gallery-empty">Loading {imageSource === 'tmdb' ? 'TMDB' : 'TheTVDB'} images…</p>
            : imageSource === 'tvdb' && galleryImages.posters.length === 0
              && galleryImages.backdrops.length === 0 && galleryImages.logos.length === 0
            // Common for movies — plenty aren't in TVDB at all, so say that rather than
            // showing an empty tab the user has to interpret.
            ? <p className="tmdb-gallery-empty">No TheTVDB artwork for this title.</p>
            : activeGalleryTab === 'season-posters'
            ? (
              <div className="tmdb-season-picker">
                {tvDetailsLoading
                  ? <p className="tmdb-gallery-empty">Loading seasons…</p>
                  : seasonList.length === 0
                    ? <p className="tmdb-gallery-empty">No seasons available.</p>
                    : (
                      <>
                        <div className="tmdb-season-chips">
                          {seasonList.map((s) => (
                            <button
                              key={s.season_number}
                              type="button"
                              className={`tmdb-season-chip${selectedSeason === s.season_number ? ' active' : ''}`}
                              onClick={() => void fetchSeasonImages(s.season_number)}
                              disabled={seasonImagesLoading[seasonKey(imageSource, s.season_number)]}
                            >
                              {s.season_number === 0 ? 'Specials' : `S${String(s.season_number).padStart(2, '0')}`}
                            </button>
                          ))}
                        </div>
                        {selectedSeason != null && (() => {
                          const sk = seasonKey(imageSource, selectedSeason)
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
                                        onClick={() => { setGalleryPreview(img); setGalleryPreviewIsLogo(false); setGalleryPreviewRole('poster'); setGalleryPreviewSeason(selectedSeason) }}
                                        title="Preview full size"
                                      >
                                        <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                                      </button>
                                      <div className="tmdb-thumb-actions">
                                        <button
                                          type="button"
                                          className={`tmdb-psd-select-btn${isSelected ? ' selected' : ''}`}
                                          onClick={() => togglePsdSelection('poster', img.file_path)}
                                          title={isSelected ? 'Deselect poster' : 'Select as Poster'}
                                        >
                                          {isSelected ? <span>{selIdx + 1}</span> : <span>P</span>}
                                        </button>
                                        <button
                                          type="button"
                                          className={`tmdb-psd-tag-btn${posterTags[img.file_path] ? ' tagged' : ''}`}
                                          onClick={() => { setTagTarget({ path: img.file_path, backdrop: false }); setTagDecade(1) }}
                                          title={posterTags[img.file_path] ? `Tagged “${posterTags[img.file_path]}” — click to change` : 'Tag this image as a poster variant (s1/s0/main/show/collection) for the plugin batch'}
                                        >
                                          {posterTags[img.file_path] ?? <Tag size={11} />}
                                        </button>
                                        {posterTags[img.file_path] && (
                                          <button
                                            type="button"
                                            className="tmdb-psd-untag-btn"
                                            onClick={() => handleUntag(img.file_path, false)}
                                            title="Remove tag"
                                          >
                                            <Trash2 size={12} />
                                          </button>
                                        )}
                                      </div>
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
                                          onClick={() => void handleGalleryDownload(img.file_path, 'poster', selectedSeason)}
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
                            onClick={() => { setGalleryPreview(img); setGalleryPreviewIsLogo(activeGalleryTab === 'logos'); setGalleryPreviewRole(role); setGalleryPreviewSeason(null) }}
                            title="Preview full size"
                          >
                            <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" />
                          </button>
                          <div className="tmdb-thumb-actions">
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
                            {(role === 'poster' || role === 'backdrop') && (
                              <button
                                type="button"
                                className={`tmdb-psd-tag-btn${posterTags[img.file_path] ? ' tagged' : ''}`}
                                onClick={() => { setTagTarget({ path: img.file_path, backdrop: role === 'backdrop' }); setTagDecade(1) }}
                                title={posterTags[img.file_path] ? `Tagged “${posterTags[img.file_path]}” — click to change` : 'Tag this image as a poster variant (s1/s0/main/show/collection) for the plugin batch'}
                              >
                                {posterTags[img.file_path] ?? <Tag size={11} />}
                              </button>
                            )}
                            {(role === 'poster' || role === 'backdrop') && posterTags[img.file_path] && (
                              <button
                                type="button"
                                className="tmdb-psd-untag-btn"
                                onClick={() => handleUntag(img.file_path, role === 'backdrop')}
                                title="Remove tag"
                              >
                                <Trash2 size={12} />
                              </button>
                            )}
                          </div>
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
                              onClick={() => void handleGalleryDownload(img.file_path, role)}
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
                onClick={() => void handleGalleryDownload(galleryPreview.file_path, galleryPreviewRole, galleryPreviewSeason)}
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
                A PSD for this title already exists in your export folder:
              </p>
              <div className="psd-not-found-filename">
                <code>{psdOverwriteConfirm.filename}</code>
              </div>
              {activeExportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{activeExportFolder}</code>
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
              {activeExportFolder && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Export folder:</span>
                  <code>{activeExportFolder}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#aaa', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Place the file in your export folder{activeExportFolder ? ' shown above' : ''}, or use the button below to upload it directly from your computer.
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

      {/* In-place drive poster search */}
      {driveSearchOpen && (
        <PosterDriveSearchModal
          initialQuery={item.title}
          onClose={() => setDriveSearchOpen(false)}
        />
      )}
    </div>
  )
}
