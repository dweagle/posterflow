import { useState, useCallback, useEffect, useMemo, useRef, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Crop as CropIcon,
  Download,
  Eraser,
  FileDown,
  FolderDown,
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
  enqueuePhotoshopOpen,
  exportToPsd,
  posterLayerNames,
  uploadPsdToExportFolder,
  getSeasonImages,
  getTmdbImages,
  type ImageSource,
  getTvdbImages,
  getTvdbSeasonImages,
  getFanartImages,
  getFanartSeasonImages,
  getAppleImages,
  getAppleSeasonImages,
  getArtworkTaggedDownloadUrl, // canonical download names
  saveGalleryArtworkToFolder,
  type ArtworkSubtype,
  getTvDetails,
  getApiErrorMessage,
  getSettings,
} from '../../api/client'
import { useToast } from '../Toast'
import { useAppleTvStorefront } from '../../hooks/useAppleTvStorefront'
import PosterDriveSearchModal from '../PosterDriveSearchModal'
import SquareCropModal from './SquareCropModal'
import ServiceLinks from './ServiceLinks'
import ReminderToggle from './ReminderToggle'
import { useCardOverview } from '../../hooks/useCardOverview'
import tmdbIcon from '../../assets/service-icons/tmdb.png'
import tvdbIcon from '../../assets/service-icons/tvdb.png'
import fanartIcon from '../../assets/service-icons/fanart.png'
import appleTvIcon from '../../assets/service-icons/appletv.png'
import { MATCHED_BY_ID_GENERIC } from '../../utils/mediaServer'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// TV details shared across cards; keyed "tmdb:tvdb", failed fetches evicted
const tvDetailsCache = new Map<string, Promise<TmdbTvDetails>>()

export const TMDB_IMAGE_LANGUAGES = [
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
// Tiles keep one box per grid (2:3 posters, 16:9 backdrops) so rows line up; an image far from that
// shape — Apple TV's squares and 4:3 / portrait heroes — is shown whole inside it instead of cropped.
const TILE_BOX = { poster: 2 / 3, backdrop: 16 / 9 } as const
const tileStyle = (img: TmdbImage, role: keyof typeof TILE_BOX): CSSProperties | undefined =>
  img.width > 0 && img.height > 0 && Math.abs(img.width / img.height - TILE_BOX[role]) / TILE_BOX[role] > 0.15
    ? { objectFit: 'contain' } : undefined

// Apple TV keeps its own shapes; a background export folder expects 16:9, so its 4:3 hero can't go there.
const isWidescreen = (img: TmdbImage): boolean => img.height > 0 && Math.abs(img.width / img.height - 16 / 9) < 0.01

const SOURCE_LABEL: Record<ImageSource, string> = { tmdb: 'TMDB', tvdb: 'TheTVDB', fanart: 'fanart.tv', apple: 'Apple TV' }
const EMPTY_IMAGES: TmdbImagesResponse = { posters: [], backdrops: [], logos: [] }

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
  defaultEditor: 'photopea' | 'photoshop'  // where the export buttons send a saved PSD by default
  // Artwork export folders configured → gallery images get save-to-folder / crop-to-square
  // buttons that write files named for artwork drives.
  logoFolderSet: boolean
  backgroundFolderSet: boolean
  squareartFolderSet: boolean
  // Not PSD-related, but this is the settings-derived config every card already receives:
  // gates the gallery's TheTVDB and fanart.tv source tabs on a configured API key, and Apple TV's
  // on its Settings switch.
  tvdbEnabled: boolean
  fanartEnabled: boolean
  appleEnabled: boolean
}

/** Empty config used before settings load (shared by every consumer). */
export const EMPTY_PSD_CONFIG: PsdConfig = {
  exportFolder: '', templatePath: '', imageExportFolder: '',
  exportFolderMm2k: '', templatePathMm2k: '', imageExportFolderMm2k: '',
  openPhotopea: false, sameTab: false, defaultEditor: 'photopea',
  logoFolderSet: false, backgroundFolderSet: false, squareartFolderSet: false, tvdbEnabled: false, fanartEnabled: false, appleEnabled: false,
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
    defaultEditor: (s.psd_default_editor || '').trim().toLowerCase() === 'photoshop' ? 'photoshop' : 'photopea',
    logoFolderSet: !!(s.artwork_logo_export_folder || '').trim(),
    backgroundFolderSet: !!(s.background_export_folder || '').trim(),
    squareartFolderSet: !!(s.squareart_export_folder || '').trim(),
    // The keys are sensitive, so they come back masked when set — presence is all we need.
    tvdbEnabled: !!(s.tvdb_api_key || '').trim(),
    fanartEnabled: !!(s.fanart_api_key || '').trim(),
    appleEnabled: (s.apple_artwork_enabled || '').trim().toLowerCase() !== 'false',
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
  hideOverview?: boolean
  galleryPortalId?: string
  /** Bump this (e.g. when the parent request is completed) to auto-close the image gallery. */
  collapseSignal?: number
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function TmdbItemCard({ item, posterAvailability, posterAvailabilityChecked, psdConfig: psdConfigProp, posterStyle, hidePoster, hideTitle, hideOverview, galleryPortalId, collapseSignal }: TmdbItemCardProps) {
  const { showToast } = useToast()

  // No TMDB match (sentinel tmdb_id 0/null): hide the TMDB-only chrome (id chip, TMDB tab) but
  // still let the user export a blank/existing PSD, named by title + year.
  const hasTmdb = (item.tmdb_id ?? 0) > 0
  const hasTvdb = (item.tvdb_id ?? 0) > 0
  const { overview, posterUrl: tmdbPosterUrl } = useCardOverview(item, !hideOverview)
  // Display-only fallback chain; the thumb proxy is never published
  const posterUrl = item.poster_url || tmdbPosterUrl || item.thumb_url || ''

  // Gallery state. Images are cached per source, so TMDB stays the default load and TheTVDB /
  // fanart.tv are only ever called once the user clicks their tab — after that, switching is instant.
  const [galleryOpen, setGalleryOpen] = useState(false)
  const [imageSource, setImageSource] = useState<ImageSource>('tmdb')
  const [imagesBySource, setImagesBySource] = useState<Partial<Record<ImageSource, TmdbImagesResponse>>>({})
  const [loadingSource, setLoadingSource] = useState<ImageSource | null>(null)
  // Set when every source failed to load: the panel opens empty so the export buttons stay reachable.
  const [galleryEmptyFallback, setGalleryEmptyFallback] = useState(false)
  const galleryImages = imagesBySource[imageSource] ?? (galleryEmptyFallback ? EMPTY_IMAGES : null)
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
  const [tagYear, setTagYear] = useState('')      // Tag popup: season-year entry ("2015" → tag "s2015")

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

  // Per-export editor target — starts on the settings default, flippable next to the export buttons.
  const [psdEditor, setPsdEditor] = useState<'photopea' | 'photoshop'>('photopea')
  useEffect(() => { setPsdEditor(psdConfig.defaultEditor) }, [psdConfig.defaultEditor])
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

  // Each source is keyed by a different id, so a title TMDB doesn't carry (a TheTVDB-only show, or a
  // TMDB id that turns out not to be a TV entry) still gets a gallery from whichever sources can be
  // asked. Only these are offered as tabs and tried on open; none at all → the standalone export group.
  // A show whose details came back with no TMDB entry (TheTVDB's remote id pointing at a movie)
  // drops the TMDB tab rather than failing on it first.
  const tmdbUsable = hasTmdb && tvDetails?.tmdb_found !== false
  const gallerySources = useMemo<ImageSource[]>(() => {
    if (item.media_type === 'collection') return hasTmdb ? ['tmdb'] : []   // the other three have no collection entity
    const out: ImageSource[] = []
    if (tmdbUsable) out.push('tmdb')
    if (psdConfig.tvdbEnabled && (hasTvdb || (item.media_type === 'movie' && !!item.imdb_id))) out.push('tvdb')
    if (psdConfig.fanartEnabled && (item.media_type === 'tv' ? hasTvdb : hasTmdb || !!item.imdb_id)) out.push('fanart')
    if (psdConfig.appleEnabled && !!item.title) out.push('apple')
    return out
  }, [item.media_type, item.title, item.imdb_id, hasTmdb, tmdbUsable, hasTvdb, psdConfig.tvdbEnabled, psdConfig.fanartEnabled, psdConfig.appleEnabled])
  const hasGallery = gallerySources.length > 0
  // What the other sources are asked with: a TMDB id known to be dead is left out so fanart.tv and
  // Apple TV don't chase it (Apple's storefront hints come from TMDB). Exports and downloads keep
  // the item's real ids — filenames carry what the library carries.
  const sourceItem = useMemo(() => (tmdbUsable ? item : { ...item, tmdb_id: 0 }), [item, tmdbUsable])
  // The default source is the first that can be asked; one dropped by a settings change hands over
  // to the next while the panel is closed.
  useEffect(() => {
    if (!galleryOpen && hasGallery && !gallerySources.includes(imageSource)) setImageSource(gallerySources[0])
  }, [galleryOpen, hasGallery, gallerySources, imageSource])

  const appleTv = useAppleTvStorefront(sourceItem)

  // Save-artwork-to-folder state (gallery logos/backdrops/poster crops → the subtype's configured
  // export folder, artwork-drive names). Keys are `${subtype}:${file_path}`.
  const [artworkSaving, setArtworkSaving] = useState<Record<string, boolean>>({})
  const [artworkSaved, setArtworkSaved] = useState<Record<string, string>>({})   // key → written filename
  const [artworkOverwriteConfirm, setArtworkOverwriteConfirm] = useState<{ subtype: ArtworkSubtype; path: string; filename: string; crop?: { x: number; y: number; size: number } } | null>(null)
  const [squareCropTarget, setSquareCropTarget] = useState<TmdbImage | null>(null)   // poster open in the crop modal

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

  // Eagerly fetch TV details on mount so season/specials badges render immediately. A show with
  // only a TheTVDB id gets its seasons from there.
  useEffect(() => {
    if (item.media_type !== 'tv' || (!hasTmdb && !hasTvdb)) return
    void ensureTvDetails()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.tmdb_id, item.tvdb_id])

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

  // Save a gallery image straight into the subtype's configured export folder, named for artwork
  // drives (Title (Year) {ids} - logo.png / - background.jpg / - squareart.jpg) so it can be
  // dropped into an artwork scope as-is. Square art carries the crop rect from the crop modal.
  const handleSaveArtwork = useCallback(async (subtype: ArtworkSubtype, filePath: string, opts?: { confirmOverwrite?: boolean; crop?: { x: number; y: number; size: number } }) => {
    const key = `${subtype}:${filePath}`
    const label = subtype === 'squareart' ? 'square art' : subtype
    setArtworkSaving((m) => ({ ...m, [key]: true }))
    try {
      const res = await saveGalleryArtworkToFolder(item, filePath, subtype, opts)
      if (res.status === 'exists') {
        setArtworkOverwriteConfirm({ subtype, path: filePath, filename: res.written, crop: opts?.crop })
        return
      }
      setArtworkSaved((m) => ({ ...m, [key]: res.written }))
      if (subtype === 'squareart') setSquareCropTarget(null)
      showToast(`Saved ${res.written} to the ${label} folder`, 'success')
    } catch (err) {
      showToast(getApiErrorMessage(err, `Failed to save ${label}`), 'error')
    } finally {
      setArtworkSaving((m) => ({ ...m, [key]: false }))
    }
  }, [item, showToast])

  const ensureTvDetails = useCallback(async () => {
    if (tvDetails || tvDetailsLoading || (!hasTmdb && !hasTvdb)) return
    setTvDetailsLoading(true)
    try {
      // Shared in-flight/result cache: many cards mount at once (and StrictMode
      // double-mounts in dev), so identical ids collapse to one request
      const key = `${item.tmdb_id}:${item.tvdb_id ?? 0}`
      let pending = tvDetailsCache.get(key)
      if (!pending) {
        pending = getTvDetails(item.tmdb_id, item.tvdb_id)
        tvDetailsCache.set(key, pending)
        pending.catch(() => tvDetailsCache.delete(key))
      }
      setTvDetails(await pending)
    } catch {
      // non-blocking
    } finally {
      setTvDetailsLoading(false)
    }
  }, [item.tmdb_id, item.tvdb_id, hasTmdb, hasTvdb, tvDetails, tvDetailsLoading])

  const fetchSeasonImages = useCallback(async (seasonNumber: number) => {
    setSelectedSeason(seasonNumber)
    const sk = seasonKey(imageSource, seasonNumber)
    if (seasonImages[sk]) return
    setSeasonImagesLoading((prev) => ({ ...prev, [sk]: true }))
    try {
      const data = imageSource === 'tmdb'
        ? await getSeasonImages(item.tmdb_id, seasonNumber, galleryLanguage)
        : imageSource === 'tvdb'
          ? await getTvdbSeasonImages(item.tvdb_id ?? 0, seasonNumber, galleryLanguage)
          : imageSource === 'fanart'
            ? await getFanartSeasonImages(item.tvdb_id ?? 0, seasonNumber, galleryLanguage)
            : await getAppleSeasonImages(sourceItem, seasonNumber, galleryLanguage)
      setSeasonImages((prev) => ({ ...prev, [sk]: data }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to load season images'), 'error')
    } finally {
      setSeasonImagesLoading((prev) => ({ ...prev, [sk]: false }))
    }
  }, [item.tmdb_id, item.tvdb_id, sourceItem, imageSource, galleryLanguage, seasonImages, showToast])

  // Which tab to show for a freshly loaded source: keep the current one when it has content, else
  // the first that does — so a switch never lands on a needlessly empty grid.
  const settleTab = useCallback((data: TmdbImagesResponse) => {
    setActiveGalleryTab((cur) => {
      if (cur === 'season-posters') return cur
      if (data[cur].length > 0) return cur
      return data.posters.length > 0 ? 'posters' : data.backdrops.length > 0 ? 'backdrops' : 'logos'
    })
  }, [])

  // Fetch one source's images and cache them; a cached source comes straight back unless forced
  // (a language change, which invalidates every cache). Null when the load failed; `quiet` keeps
  // the failure out of the toasts (the message is left in lastLoadError for the caller).
  const lastLoadError = useRef<string | null>(null)
  const loadSource = useCallback(async (source: ImageSource, opts?: { language?: string; force?: boolean; quiet?: boolean }): Promise<TmdbImagesResponse | null> => {
    const language = opts?.language ?? galleryLanguage
    const cached = imagesBySource[source]
    if (!opts?.force && cached) return cached
    setLoadingSource(source)
    try {
      const data = source === 'tmdb'
        ? await getTmdbImages(item.tmdb_id, item.media_type, language)
        : source === 'tvdb'
          ? await getTvdbImages(sourceItem, language)
          : source === 'fanart'
            ? await getFanartImages(sourceItem, language)
            : await getAppleImages(sourceItem, language)
      setImagesBySource((prev) => ({ ...prev, [source]: data }))
      setGalleryEmptyFallback(false)
      return data
    } catch (error) {
      lastLoadError.current = getApiErrorMessage(error, `Failed to load ${SOURCE_LABEL[source]} images`)
      if (!opts?.quiet) showToast(lastLoadError.current, 'error')
      return null
    } finally {
      setLoadingSource((cur) => (cur === source ? null : cur))
    }
  }, [galleryLanguage, imagesBySource, item.tmdb_id, item.media_type, sourceItem, showToast])

  const toggleGallery = useCallback(async () => {
    if (galleryOpen) {
      setGalleryOpen(false)
      setGalleryEmptyFallback(false)
      return
    }
    if (item.media_type === 'tv') void ensureTvDetails()
    // Load before opening, so the panel appears with its grid in one go instead of as an empty
    // shell that pushes the cards below down and then grows. A source that fails (a TMDB id that
    // isn't a TV entry) hands over quietly to the next; only when none loads is the failure
    // shown, and the panel still opens empty so the export buttons stay reachable.
    const order = gallerySources.includes(imageSource)
      ? [imageSource, ...gallerySources.filter((s) => s !== imageSource)]
      : gallerySources
    for (const source of order) {
      const data = await loadSource(source, { quiet: true })
      if (!data) continue
      setImageSource(source)
      setSelectedSeason(null)
      settleTab(data)
      setGalleryOpen(true)
      return
    }
    showToast(lastLoadError.current ?? 'No image source could be loaded for this title', 'error')
    setGalleryEmptyFallback(true)
    setGalleryOpen(true)
  }, [galleryOpen, imageSource, gallerySources, item.media_type, ensureTvDetails, loadSource, settleTab, showToast])

  // The grid in view stays put until the new source has loaded, then source and tab swap in one
  // render — no collapse to a loading line and back, so the cards below don't jump.
  const handleSourceChange = useCallback(async (source: ImageSource) => {
    if (source === imageSource) return
    const data = await loadSource(source)
    if (!data) return   // a failed switch stays on the source that still has images
    setImageSource(source)
    setSelectedSeason(null)
    settleTab(data)
  }, [imageSource, loadSource, settleTab])

  const handleGalleryLanguageChange = useCallback(async (newLang: string) => {
    setGalleryLanguage(newLang)
    setSeasonImages({})
    setSeasonImagesLoading({})
    setSelectedSeason(null)
    // Every cache is language-scoped. With the panel open, the grid in view stays put until its
    // refetch lands; the other sources reload the next time their tab is clicked.
    if (!galleryOpen) {
      setImagesBySource({})
      return
    }
    const data = await loadSource(imageSource, { language: newLang, force: true })
    if (!data) {
      setImagesBySource({})
      setGalleryOpen(false)
      return
    }
    setImagesBySource({ [imageSource]: data })
    settleTab(data)
  }, [galleryOpen, imageSource, loadSource, settleTab])

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
        if (!result.openPhotopea) {
          showToast(`PSD saved: ${result.filename}`, 'success')
        } else if (psdEditor === 'photoshop') {
          // Queue for the native Photoshop panel — it polls the server and opens the PSD itself
          // (the browser can't launch Photoshop). The panel needs its server connection configured.
          await enqueuePhotoshopOpen(result.filename, result.style, result.filename.replace(/\.psd$/i, ''))
          showToast(`Queued ${result.filename} for Photoshop…`, 'success')
        } else {
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
  }, [item.title, item.year, item.tmdb_id, item.tvdb_id, item.imdb_id, item.media_type, exportStyle, psdSelection, posterTags, psdConfig.sameTab, psdEditor, showToast])

  // Tag a specific poster with a plugin-convention name (s1/s0/main/show/c/cls/s<year>). Tagging just STORES the
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
  // pinning the chips to one source would make the other's season posters unreachable. fanart.tv
  // is keyed by TheTVDB id, so it follows TVDB's seasons. Falls back to the preferred list when
  // the active source has no seasons of its own.
  const seasonList = !tvDetails
    ? []
    : (imageSource === 'tmdb' ? tvDetails.tmdb_seasons : tvDetails.tvdb_seasons).length > 0
      ? (imageSource === 'tmdb' ? tvDetails.tmdb_seasons : tvDetails.tvdb_seasons)
      : tvDetails.seasons

  // Landing on the Seasons tab loads the first real season straight away (Specials only when
  // that's all there is) instead of showing bare chips. The pick is cleared by a source or
  // language change, so this also refills it for the new source / language.
  const firstSeasonNumber = seasonList.find((sn) => sn.season_number >= 1)?.season_number
    ?? seasonList[0]?.season_number ?? null
  useEffect(() => {
    if (!galleryOpen || activeGalleryTab !== 'season-posters' || selectedSeason != null || firstSeasonNumber == null) return
    void fetchSeasonImages(firstSeasonNumber)
  }, [galleryOpen, activeGalleryTab, selectedSeason, firstSeasonNumber, fetchSeasonImages])

  // Air-date state for the seasons badge edge: red = a numbered season is listed with no air
  // dates yet (announced/TBA), green = every one is dated. Unknowns stay uncolored so a failed
  // lookup never reads as "all clear"; specials don't count — they're not "a season on the way".
  const seasonAirFlags = (tvDetails?.seasons ?? [])
    .filter((s) => s.season_number > 0)
    .map((s) => s.has_air_date)
  const seasonAirState = seasonAirFlags.some((f) => f === false)
    ? 'unaired'
    : seasonAirFlags.length > 0 && seasonAirFlags.every((f) => f === true)
      ? 'aired'
      : null

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
      {psdConfig.openPhotopea && (
        <div className="tmdb-psd-style-toggle" role="group" aria-label="Open exports in">
          {([['photopea', 'Pea'], ['photoshop', 'PS']] as const).map(([val, label]) => (
            <button
              key={val}
              type="button"
              className={`tmdb-psd-style-opt${psdEditor === val ? ' active' : ''}`}
              onClick={() => setPsdEditor(val)}
              disabled={psdExporting}
              title={val === 'photopea'
                ? 'Open exports in Photopea (browser tab)'
                : 'Queue exports for the Photoshop panel — it downloads and opens them automatically'}
            >
              {label}
            </button>
          ))}
        </div>
      )}
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
            className={`tmdb-poster${posterUrl ? ' tmdb-poster--clickable' : ''}`}
            onClick={() => { if (posterUrl) setPreviewPoster(posterUrl) }}
          >
            {posterUrl
              ? <img src={posterUrl} alt={item.title} loading="lazy" />
              : (
                <div className="tmdb-poster-placeholder">
                  {item.media_type === 'movie' ? <MovieIcon size={32} /> : item.media_type === 'tv' ? <Tv size={32} /> : <FolderOpen size={32} />}
                </div>
              )
            }
          </div>
        )}

        {/* Left side: poster, title, year, overview. Hosts that show the title themselves pass hideTitle;
            those that want no description either (request cards) add hideOverview, so the box isn't pushed off a blank column. */}
        {(!hideTitle || overview) && (
          <div className="tmdb-result-info">
            <div className="tmdb-result-title-row">
              {!hideTitle && <span className="tmdb-result-title">{item.title}</span>}
              {!hideTitle && item.year && <span className="tmdb-result-year">{item.year}</span>}
              {!hideTitle && item.auto_matched && (
                <span className="tmdb-matched-badge" title={MATCHED_BY_ID_GENERIC}>
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
                  className={`badge badge-grey${seasonAirState ? ` badge-air-${seasonAirState}` : ''}`}
                  title={(tvDetails.season_source === 'tvdb'
                    ? 'Season count from TheTVDB — the same source Sonarr uses'
                    : 'Season count from TMDB')
                    + (seasonAirState === 'unaired' ? '\nRed edge: a listed season has no air dates yet (TBA)' : '')
                    + (seasonAirState === 'aired' ? '\nGreen edge: every season has air dates' : '')}
                >
                  <Layers size={11} /> {tvDetails.season_count} season{tvDetails.season_count !== 1 ? 's' : ''}
                </span>
                {tvDetails.seasons.some((s) => s.season_number === 0) && (
                  <span className="badge badge-grey">Specials</span>
                )}
              </>
            )}
            {driveSearchControl}
            <ReminderToggle kind="poster" item={item} />
          </div>

          <ServiceLinks item={item} appleTv={appleTv} />

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

          {hasGallery ? (
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
            // No source can be asked for this title → no image gallery; expose the export buttons
            // directly so a blank (or existing) PSD can still be made.
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
            {gallerySources.length > 1 && (
              <div className="tmdb-gallery-sources" role="group" aria-label="Image source">
                {([['tmdb', tmdbIcon], ['tvdb', tvdbIcon], ['fanart', fanartIcon], ['apple', appleTvIcon]] as const)
                  .filter(([id]) => gallerySources.includes(id))
                  .map(([id, icon]) => (
                  <button
                    key={id}
                    type="button"
                    className={`tmdb-gallery-source tmdb-gallery-source--${id}${imageSource === id ? ' active' : ''}${loadingSource === id ? ' tmdb-gallery-source--pending' : ''}`}
                    onClick={() => void handleSourceChange(id)}
                    disabled={loadingSource !== null}
                    title={`Browse ${SOURCE_LABEL[id]} images`}
                    aria-label={`Browse ${SOURCE_LABEL[id]} images`}
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
                    {([['main', 'Movie'], ['show', 'Show'], ['s0', 'Specials'], ['c', 'Collection'], ['cls', 'Limited Series']] as const).map(([val, label]) => (
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
                  <div className="tmdb-tag-sub">Season year</div>
                  <div className="tmdb-tag-year">
                    <input
                      type="number"
                      className="tmdb-tag-yearinput"
                      placeholder="e.g. 2015"
                      value={tagYear}
                      onChange={(e) => setTagYear(e.target.value)}
                    />
                    <button
                      type="button"
                      className={`tmdb-tag-chip num${cur === `s${tagYear}` ? ' active' : ''}`}
                      disabled={!/^(19|20)\d\d$/.test(tagYear)}
                      onClick={() => handleTag(tagTarget.path, `s${tagYear}`, tagTarget.backdrop)}
                    >
                      Tag year
                    </button>
                  </div>
                  {cur && (
                    <button type="button" className="tmdb-tag-untag" onClick={() => handleUntag(tagTarget.path, tagTarget.backdrop)}>Remove tag “{cur}”</button>
                  )}
                </div>
              </div>
            )
          })()}

          {!galleryImages
            ? <p className="tmdb-gallery-empty">Loading {SOURCE_LABEL[imageSource]} images…</p>
            : galleryEmptyFallback
            ? <p className="tmdb-gallery-empty">No images could be loaded for this title.</p>
            : imageSource !== 'tmdb' && galleryImages.posters.length === 0
              && galleryImages.backdrops.length === 0 && galleryImages.logos.length === 0
            // Common for movies — plenty aren't in TVDB or fanart.tv at all, so say that rather
            // than showing an empty tab the user has to interpret.
            ? <p className="tmdb-gallery-empty">No {SOURCE_LABEL[imageSource]} artwork for this title.</p>
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
                                        <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" style={tileStyle(img, 'poster')} />
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
                                          title={posterTags[img.file_path] ? `Tagged “${posterTags[img.file_path]}” — click to change` : 'Tag this image as a poster variant (s1/s0/main/show/c/cls or a season year) for the plugin batch'}
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
                            <img src={img.url_thumb} alt="" loading="lazy" className="tmdb-gallery-thumb" style={role === 'logo' ? undefined : tileStyle(img, role)} />
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
                                title={posterTags[img.file_path] ? `Tagged “${posterTags[img.file_path]}” — click to change` : 'Tag this image as a poster variant (s1/s0/main/show/c/cls or a season year) for the plugin batch'}
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
                            {((role === 'logo' && psdConfig.logoFolderSet) || (role === 'backdrop' && psdConfig.backgroundFolderSet && (imageSource !== 'apple' || isWidescreen(img)))) && (() => {
                              const subtype: ArtworkSubtype = role === 'logo' ? 'logo' : 'background'
                              const key = `${subtype}:${img.file_path}`
                              return (
                                <button
                                  type="button"
                                  className="tmdb-gallery-save"
                                  title={artworkSaved[key] ? `Saved as ${artworkSaved[key]}` : `Save to the ${subtype} export folder, named for artwork drives`}
                                  onClick={() => void handleSaveArtwork(subtype, img.file_path)}
                                  disabled={!!artworkSaving[key] || !!artworkSaved[key]}
                                >
                                  {artworkSaved[key] ? <Check size={12} /> : <FolderDown size={12} />}
                                </button>
                              )
                            })()}
                            {role === 'poster' && psdConfig.squareartFolderSet && (
                              <button
                                type="button"
                                className="tmdb-gallery-save"
                                title="Crop into square art → saves to the square art export folder, named for artwork drives"
                                onClick={() => setSquareCropTarget(img)}
                              >
                                <CropIcon size={12} />
                              </button>
                            )}
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
              {((galleryPreviewRole === 'logo' && psdConfig.logoFolderSet) || (galleryPreviewRole === 'backdrop' && psdConfig.backgroundFolderSet && (imageSource !== 'apple' || isWidescreen(galleryPreview)))) && (() => {
                const subtype: ArtworkSubtype = galleryPreviewRole === 'logo' ? 'logo' : 'background'
                const key = `${subtype}:${galleryPreview.file_path}`
                return (
                  <button
                    type="button"
                    className="btn-toolbar"
                    style={{ fontSize: '0.82rem', padding: '0.35rem 0.75rem', color: '#4caf50' }}
                    title={`Save to the ${subtype} export folder, named for artwork drives`}
                    onClick={() => void handleSaveArtwork(subtype, galleryPreview.file_path)}
                    disabled={!!artworkSaving[key] || !!artworkSaved[key]}
                  >
                    {artworkSaved[key] ? <><Check size={13} /> Saved</> : <><FolderDown size={13} /> {subtype === 'logo' ? 'To Logo Folder' : 'To Background Folder'}</>}
                  </button>
                )
              })()}
              {galleryPreviewRole === 'poster' && galleryPreviewSeason == null && psdConfig.squareartFolderSet && (
                <button
                  type="button"
                  className="btn-toolbar"
                  style={{ fontSize: '0.82rem', padding: '0.35rem 0.75rem', color: '#4caf50' }}
                  title="Crop into square art → saves to the square art export folder, named for artwork drives"
                  onClick={() => { setSquareCropTarget(galleryPreview); setGalleryPreview(null) }}
                >
                  <CropIcon size={13} /> Crop → Square
                </button>
              )}
            </div>
          </div>
          <button type="button" className="tmdb-lightbox-close" onClick={() => setGalleryPreview(null)}>×</button>
        </div>
      )}

      {/* Crop a poster into square art → saves to the square art export folder */}
      {squareCropTarget && (
        <SquareCropModal
          imageUrl={squareCropTarget.url_full}
          title={item.year ? `${item.title} (${item.year})` : item.title}
          saving={!!artworkSaving[`squareart:${squareCropTarget.file_path}`]}
          onCancel={() => setSquareCropTarget(null)}
          onSave={(crop) => void handleSaveArtwork('squareart', squareCropTarget.file_path, { crop })}
        />
      )}

      {/* Artwork save overwrite confirm modal — after the crop modal so it stacks on top of it */}
      {artworkOverwriteConfirm && (() => {
        const label = artworkOverwriteConfirm.subtype === 'squareart' ? 'square art' : artworkOverwriteConfirm.subtype
        return (
          <div className="modal-overlay">
            <div className="modal-content schedule-modal">
              <div className="modal-header">
                <h2>Overwrite Existing {label === 'square art' ? 'Square Art' : label === 'logo' ? 'Logo' : 'Background'}?</h2>
                <button className="modal-close" onClick={() => setArtworkOverwriteConfirm(null)}>×</button>
              </div>
              <div className="modal-body">
                <p style={{ color: '#ccc', lineHeight: 1.6, marginBottom: '0.75rem' }}>
                  Your {label} export folder already has a file with this name:
                </p>
                <div className="psd-not-found-filename">
                  <code>{artworkOverwriteConfirm.filename}</code>
                </div>
                <p style={{ marginTop: '1rem', color: '#ffb74d', fontSize: '0.85rem', lineHeight: 1.6 }}>
                  Continuing will replace it with this {label}.
                </p>
              </div>
              <div className="modal-footer">
                <button className="btn-secondary" onClick={() => setArtworkOverwriteConfirm(null)}>Cancel</button>
                <button
                  className="btn-primary"
                  style={{ justifyContent: 'center', background: '#f44336' }}
                  onClick={() => {
                    const o = artworkOverwriteConfirm
                    setArtworkOverwriteConfirm(null)
                    void handleSaveArtwork(o.subtype, o.path, { confirmOverwrite: true, crop: o.crop })
                  }}
                >
                  Overwrite
                </button>
              </div>
            </div>
          </div>
        )
      })()}

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
