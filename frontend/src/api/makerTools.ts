import { getData, postData } from './http'

export interface MakerMonitorConfig {
  tmdb_api_key: string
  lookahead_days: number
  missing_retention_days: number
  drive_ids: number[]
  enable_discovery: boolean
  discovery_popularity: number
  discovery_vote_count: number
  discovery_max_results: number
  discovery_languages: string[]
}

export interface MakerMonitorShowResult {
  tmdb_id: string
  name: string
  homepage: string
  season_number: number
  date: string
  first_air_year: string
  poster_exists: boolean
  external_sources: string[]
}

export interface MakerMonitorLibraryResult {
  library_name: string
  library_type: string
  total_scanned: number
  premieres_found: number
  posters_needed: number
  shows: MakerMonitorShowResult[]
}

export interface MakerMonitorRunResponse {
  lookahead_days: number
  range_start: string
  range_end: string
  total_scanned: number
  total_premieres: number
  total_needed: number
  libraries: MakerMonitorLibraryResult[]
  discovery?: MakerMonitorDiscoveryResult | null
}
export interface MakerDiscoveryTypeStatus {
  type: string
  have: boolean
  have_sources: string[]
  synced: boolean
  synced_sources: string[]
}

export interface MakerDiscoveryItem {
  name: string
  date: string
  popularity: number
  overview: string
  type: string
  homepage: string
  language: string
  statuses: MakerDiscoveryTypeStatus[]
}

export interface MakerMonitorDiscoveryResult {
  shows: MakerDiscoveryItem[]
  movies: MakerDiscoveryItem[]
}

export interface MakerMonitorRunRequest {
  config?: MakerMonitorConfig
  save_config?: boolean
}

export interface MakerMonitorRunQueuedResponse {
  job_id: number
  message: string
}

export const getMakerMonitorConfig = async (): Promise<MakerMonitorConfig> => {
  return getData<MakerMonitorConfig>('/api/maker-tools/monitor/config')
}

export const getMakerMonitorLastResult = async (): Promise<MakerMonitorRunResponse | Record<string, never>> => {
  return getData<MakerMonitorRunResponse | Record<string, never>>('/api/maker-tools/monitor/last-result')
}

export const saveMakerMonitorConfig = async (config: MakerMonitorConfig): Promise<MakerMonitorConfig> => {
  return postData<MakerMonitorConfig>('/api/maker-tools/monitor/config', config)
}

export const runMakerMonitor = async (payload: MakerMonitorRunRequest): Promise<MakerMonitorRunQueuedResponse> => {
  return postData<MakerMonitorRunQueuedResponse>('/api/maker-tools/monitor/run', payload)
}

export interface TmdbSearchResult {
  tmdb_id: number
  media_type: 'movie' | 'tv' | 'collection'
  title: string
  year: string
  overview: string
  poster_url: string
  homepage: string
  imdb_id: string | null
  tvdb_id: number | null
}

export type TmdbSearchFilter = 'all' | 'movie' | 'tv' | 'collection'

export const searchTmdb = async (q: string, type: TmdbSearchFilter = 'all'): Promise<TmdbSearchResult[]> => {
  return getData<TmdbSearchResult[]>(`/api/maker-tools/tmdb/search?q=${encodeURIComponent(q)}&type=${type}`)
}

export interface TmdbImage {
  file_path: string
  width: number
  height: number
  language: string | null
  vote_average: number
  url_thumb: string
  url_full: string
}

export interface TmdbImagesResponse {
  posters: TmdbImage[]
  backdrops: TmdbImage[]
  logos: TmdbImage[]
}

export const getTmdbImages = async (tmdb_id: number, media_type: string, language: string = 'en'): Promise<TmdbImagesResponse> => {
  return getData<TmdbImagesResponse>(`/api/maker-tools/tmdb/images?tmdb_id=${tmdb_id}&media_type=${media_type}&language=${encodeURIComponent(language)}`)
}

export const getTmdbImageProxyUrl = (file_path: string): string => {
  return `/api/maker-tools/tmdb/image-proxy?path=${encodeURIComponent(file_path)}`
}
export interface TmdbSeasonInfo {
  season_number: number
  name: string
  episode_count: number
  air_date: string | null
  poster_url: string | null
}

export interface TmdbTvDetails {
  season_count: number
  seasons: TmdbSeasonInfo[]
}

export const getTvDetails = async (tmdb_id: number): Promise<TmdbTvDetails> => {
  return getData<TmdbTvDetails>(`/api/maker-tools/tmdb/tv-details?tmdb_id=${tmdb_id}`)
}

export const getSeasonImages = async (tmdb_id: number, season_number: number, language: string = 'en+textless'): Promise<TmdbImagesResponse> => {
  return getData<TmdbImagesResponse>(`/api/maker-tools/tmdb/season-images?tmdb_id=${tmdb_id}&season_number=${season_number}&language=${encodeURIComponent(language)}`)
}

export interface PsdExportRequest {
  title: string
  year: string
  poster_paths: string[]
  backdrop_paths: string[]
  logo_paths: string[]
  use_existing?: boolean
}

/** Returned when the server saved the PSD to an export folder — open in Photopea. */
export interface PsdExportSaved {
  mode: 'photopea'
  filename: string
  psdUrl: string   // absolute HTTPS URL, ready to pass to Photopea
  openPhotopea: boolean
}

/** Returned when no export folder is configured — trigger a browser download. */
export interface PsdExportDownload {
  mode: 'download'
  blob: Blob
  filename: string
}

/** Returned when use_existing=true but no PSD with the expected name exists in the export folder. */
export interface PsdExportNotFound {
  mode: 'not-found'
  expectedFilename: string
}

export type PsdExportResult = PsdExportSaved | PsdExportDownload | PsdExportNotFound

/**
 * Export selected TMDB images as a layered PSD.
 *
 * - If the server has an export folder configured it saves to disk and returns
 *   JSON {filename, psd_url}. We surface this as mode='photopea'.
 * - Otherwise the server streams PSD bytes and we surface them as mode='download'.
 * - When use_existing=true and no file exists: mode='not-found' with the expected filename.
 *
 * Error bodies from non-2xx responses arrive as Blobs (responseType:'blob') and
 * are decoded here so the caller always gets a readable Error message.
 */
export const exportToPsd = async (
  payload: PsdExportRequest,
  titleForFilename: string,
  yearForFilename: string,
): Promise<PsdExportResult> => {
  const { default: axios } = await import('axios')
  try {
    const resp = await axios.post('/api/maker-tools/tmdb/psd-export', payload, {
      responseType: 'blob',
    })

    const contentType: string = (resp.headers['content-type'] as string | undefined) ?? ''

    if (contentType.includes('application/json')) {
      // Server saved to export folder — parse the JSON blob
      const text = await (resp.data as Blob).text()
      const json = JSON.parse(text) as { filename: string; psd_url: string; open_photopea: boolean }
      const absoluteUrl = `${window.location.origin}${json.psd_url}`
      return { mode: 'photopea', filename: json.filename, psdUrl: absoluteUrl, openPhotopea: json.open_photopea ?? false }
    }

    // Blob download path
    const safeName = titleForFilename.replace(/[<>:"/\\|?*]/g, '').trim()
    const filename = yearForFilename ? `${safeName} (${yearForFilename}).psd` : `${safeName}.psd`
    return { mode: 'download', blob: resp.data as Blob, filename }
  } catch (err: unknown) {
    const axiosErr = err as { response?: { status?: number; data?: unknown }; message?: string }
    const rawData = axiosErr?.response?.data
    if (rawData instanceof Blob) {
      const text = await rawData.text()
      let parsed: Record<string, unknown> | undefined
      try { parsed = JSON.parse(text) as Record<string, unknown> } catch { /* ignore */ }
      // 404 not-found is a structured response, not a real error
      if (axiosErr?.response?.status === 404 && parsed?.not_found === true) {
        return { mode: 'not-found', expectedFilename: (parsed.expected_filename as string) ?? '' }
      }
      const detail = typeof parsed?.detail === 'string' ? parsed.detail
        : text.trim().length > 0 && text.trim().length < 300 ? text.trim()
        : undefined
      throw new Error(detail ?? 'PSD export failed')
    }
    throw err
  }
}

/**
 * Check whether a PSD with the given filename already exists in the export folder.
 * Uses a HEAD request so no file bytes are transferred.
 */
export const checkPsdExists = async (filename: string): Promise<boolean> => {
  const { default: axios } = await import('axios')
  try {
    await axios.head(`/api/maker-tools/psd-exports/${encodeURIComponent(filename)}`)
    return true
  } catch {
    return false
  }
}

export interface PosterStyleEntry {
  style: string    // "MM2K" | "CL2K" | "Custom"
  seasons: number[] // sorted season numbers, empty for non-TV
}

/** PosterAvailability is a list of per-style entries for a TMDB item. */
export type PosterAvailability = PosterStyleEntry[]

export interface PosterCheckItem {
  tmdb_id: number
  title: string
  year: string
  media_type: string
}

/** Returns a map of tmdb_id -> per-style availability info. */
export const checkTmdbPosterAvailability = async (
  items: PosterCheckItem[],
): Promise<Record<number, PosterAvailability>> => {
  return postData<Record<number, PosterAvailability>>('/api/maker-tools/tmdb/poster-check', { items })
}

/**
 * Upload a local PSD file to the server's configured export folder.
 * The file is saved under the given `filename` (must match `{title} ({year}).psd`).
 */
export const uploadPsdToExportFolder = async (file: File, filename: string): Promise<void> => {
  const { default: axios } = await import('axios')
  await axios.put(`/api/maker-tools/psd-exports/${encodeURIComponent(filename)}`, file, {
    headers: { 'Content-Type': 'application/octet-stream' },
  })
}