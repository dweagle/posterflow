import { getData, postData } from './http'
// Inlined as a data URI (Vite ?inline) so Photopea renders it without a network fetch.
// A remote-URL icon at our origin is passive mixed content on an http LAN instance — Chrome
// auto-upgrades it to https, the upgrade fails (no TLS), and the button shows with no image.
import pluginIcon from '../assets/photopea-plugin-icon.png?inline'

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

/** Count of monitored items needing posters from the last scan (for the sidebar badge). */
export const getMakerMonitorNeededCount = async (): Promise<{ count: number }> => {
  return getData<{ count: number }>('/api/maker-tools/monitor/needed-count')
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
  series_type: string | null  // TMDB "type": Scripted, Miniseries, Documentary, Reality, etc.
}

export const getTvDetails = async (tmdb_id: number): Promise<TmdbTvDetails> => {
  return getData<TmdbTvDetails>(`/api/maker-tools/tmdb/tv-details?tmdb_id=${tmdb_id}`)
}

export const getSeasonImages = async (tmdb_id: number, season_number: number, language: string = 'en+textless'): Promise<TmdbImagesResponse> => {
  return getData<TmdbImagesResponse>(`/api/maker-tools/tmdb/season-images?tmdb_id=${tmdb_id}&season_number=${season_number}&language=${encodeURIComponent(language)}`)
}

/** Origin country/countries (ISO 3166-1 alpha-2, preference-ordered) of a movie/TV item. */
export const getTmdbOriginCountry = async (tmdb_id: number, media_type: string): Promise<string[]> => {
  const data = await getData<{ countries: string[] }>(
    `/api/maker-tools/tmdb/origin-country?tmdb_id=${tmdb_id}&media_type=${encodeURIComponent(media_type)}`,
  )
  return data.countries ?? []
}

export interface PsdExportRequest {
  title: string
  year: string
  tmdb_id?: string
  tvdb_id?: string
  imdb_id?: string
  media_type?: string
  poster_paths: string[]
  backdrop_paths: string[]
  logo_paths: string[]
  use_existing?: boolean
  confirm_overwrite?: boolean
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

/** Returned for a New Export when a PSD for this title already exists and overwrite isn't confirmed. */
export interface PsdExportExists {
  mode: 'exists'
  existingFilename: string
}

export type PsdExportResult = PsdExportSaved | PsdExportDownload | PsdExportNotFound | PsdExportExists

/**
 * Export selected TMDB images as a layered PSD. The server owns conflict detection,
 * so a single call returns one of:
 *
 * - mode='photopea'  — saved to the export folder (JSON {filename, psd_url}).
 * - mode='download'  — no export folder configured; raw PSD bytes streamed back.
 * - mode='not-found' — use_existing=true but no matching PSD exists (409→404 here).
 * - mode='exists'    — New Export would overwrite an existing title and confirm_overwrite
 *                      is not set; caller should confirm then retry with confirm_overwrite.
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

    // Blob download path. Prefer the server's Content-Disposition filename so the download is
    // named identically to a saved export (ID-tagged the way IDarr would name it); fall back to
    // the tagless title/year when the header is absent.
    const safeName = titleForFilename.replace(/[<>:"/\\|?*]/g, '').trim()
    let filename = yearForFilename ? `${safeName} (${yearForFilename}).psd` : `${safeName}.psd`
    const disposition = (resp.headers['content-disposition'] as string | undefined) ?? ''
    const dispositionMatch = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
    if (dispositionMatch?.[1]) {
      try {
        filename = decodeURIComponent(dispositionMatch[1].trim())
      } catch {
        filename = dispositionMatch[1].trim()
      }
    }
    return { mode: 'download', blob: resp.data as Blob, filename }
  } catch (err: unknown) {
    const axiosErr = err as { response?: { status?: number; data?: unknown }; message?: string }
    const rawData = axiosErr?.response?.data
    if (rawData instanceof Blob) {
      const text = await rawData.text()
      let parsed: Record<string, unknown> | undefined
      try { parsed = JSON.parse(text) as Record<string, unknown> } catch { /* ignore */ }
      // 404 not-found and 409 exists are structured responses, not real errors
      if (axiosErr?.response?.status === 404 && parsed?.not_found === true) {
        return { mode: 'not-found', expectedFilename: (parsed.expected_filename as string) ?? '' }
      }
      if (axiosErr?.response?.status === 409 && parsed?.exists === true) {
        return { mode: 'exists', existingFilename: (parsed.existing_filename as string) ?? '' }
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
 * Open TOP-LEVEL Photopea with the exported PSD and the Posterflow "Seasons" plugin attached.
 * Photopea fetches the PSD itself (files:[url]) and opens it on startup; a launch `script`
 * renames the doc to the full filename, and the plugin panel (environment.plugins) provides the
 * season buttons, the PSD save, and the JPG export.
 *
 * Needs the user to allow Photopea's "local network access" prompt (public photopea.com reaching
 * the LAN/localhost server) + CORS on the PSD GET. Photopea API: https://www.photopea.com/api/
 */
export const openPhotopeaWithPsd = (psdUrl: string, filename: string): void => {
  const saveUrl = `${window.location.origin}/api/maker-tools/psd-exports/${encodeURIComponent(filename)}`
  const params = new URLSearchParams({ save: saveUrl, name: filename.replace(/\.psd$/i, '') })
  const pluginUrl = `${window.location.origin}/photopea-plugin.html?${params.toString()}`
  // icon: Posterflow's logo as an inlined data URI (a colored logo, so no "===" theme-recolor
  // prefix). Inlined rather than a remote URL so it survives mixed-content/CORS/LNA blocking.
  // w/h: fix the panel to 184px wide — fits 5 season chips per row.
  const icon = pluginIcon
  // Photopea fetches the PSD itself (files:[url]) and opens it during startup — it loads as the
  // editor boots. Photopea trims the doc name out of the URL (dropping the "(year) {ids}" part), so we pass a launch `script`
  // (runs once after the file loads) that renames the doc to the full export filename — the tab,
  // the JPG export, and the plugin's save guard all rely on that name. Requires the user to ALLOW
  // Photopea's "local network access" prompt + CORS on the PSD GET (we send it). On a
  // password-protected instance psd_url carries a signed, file-scoped ?token= the GET validates,
  // since Photopea can't send the app Bearer header.
  const docName = filename.replace(/\.psd$/i, '')
  const config = {
    files: [psdUrl],
    script: `try{if(app.documents.length>0)app.activeDocument.name=${JSON.stringify(docName)}}catch(e){}`,
    environment: { plugins: [{ name: 'Posterflow Seasons', url: pluginUrl, icon, w: 184, h: 420 }] },
  }
  window.open(`https://www.photopea.com#${encodeURIComponent(JSON.stringify(config))}`, '_blank')
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