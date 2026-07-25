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
  poster_url?: string
  imdb_id?: string
  tvdb_id?: number | null
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
  poster_url?: string
  imdb_id?: string
  tvdb_id?: number | null
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
  // True when resolved directly from a carried *arr id (pinned to the top).
  auto_matched?: boolean
}

export type TmdbSearchFilter = 'all' | 'movie' | 'tv' | 'collection'

export interface TmdbSearchIds {
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
}

// Map a poster-manager item's media type onto a TMDB search filter, so opening
// Maker Tools can pre-filter to the known type instead of "all". Returns null for
// types with no matching filter (e.g. person), leaving it on "all".
export function mediaTypeToTmdbFilter(t: string | null | undefined): TmdbSearchFilter | null {
  switch ((t || '').toLowerCase()) {
    case 'movie': return 'movie'
    case 'show':
    case 'series':
    case 'tv':
    case 'season': return 'tv'
    case 'collection': return 'collection'
    default: return null
  }
}

export const searchTmdb = async (q: string, type: TmdbSearchFilter = 'all', ids?: TmdbSearchIds): Promise<TmdbSearchResult[]> => {
  const params = new URLSearchParams({ q, type })
  if (ids?.tmdb_id) params.set('tmdb_id', String(ids.tmdb_id))
  if (ids?.tvdb_id) params.set('tvdb_id', String(ids.tvdb_id))
  if (ids?.imdb_id) params.set('imdb_id', ids.imdb_id)
  return getData<TmdbSearchResult[]>(`/api/maker-tools/tmdb/search?${params.toString()}`)
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

/** The image sources the gallery can browse. TMDB is always present; TVDB needs a configured key. */
export type ImageSource = 'tmdb' | 'tvdb'

/**
 * Same response shape as the TMDB gallery, so both sources render through one code path.
 * TV items carry a tvdb_id from TMDB's external_ids; movies never do, so the server resolves
 * those from the imdb_id instead. Returns empty lists when the title has no TVDB entry.
 */
export const getTvdbImages = async (
  item: { media_type: string; tvdb_id?: number | null; imdb_id?: string | null },
  language: string = 'en+textless',
): Promise<TmdbImagesResponse> => {
  const params = new URLSearchParams({ media_type: item.media_type, language })
  if (item.tvdb_id) params.set('tvdb_id', String(item.tvdb_id))
  if (item.imdb_id) params.set('imdb_id', item.imdb_id)
  return getData<TmdbImagesResponse>(`/api/maker-tools/tvdb/images?${params.toString()}`)
}

export const getTvdbSeasonImages = async (tvdb_id: number, season_number: number, language: string = 'en+textless'): Promise<TmdbImagesResponse> => {
  return getData<TmdbImagesResponse>(`/api/maker-tools/tvdb/season-images?tvdb_id=${tvdb_id}&season_number=${season_number}&language=${encodeURIComponent(language)}`)
}

export const getTvdbImageProxyUrl = (url: string): string => {
  return `/api/maker-tools/tvdb/image-proxy?url=${encodeURIComponent(url)}`
}

/** Origin country/countries (ISO 3166-1 alpha-2, preference-ordered) of a movie/TV item. */
export const getTmdbOriginCountry = async (tmdb_id: number, media_type: string): Promise<string[]> => {
  const data = await getData<{ countries: string[] }>(
    `/api/maker-tools/tmdb/origin-country?tmdb_id=${tmdb_id}&media_type=${encodeURIComponent(media_type)}`,
  )
  return data.countries ?? []
}

export type ArtworkSubtype = 'logo' | 'background' | 'squareart'
// 'poster' is a listable/crop source, not a savable subtype.
export type ArtworkListType = ArtworkSubtype | 'poster'

export interface ArtworkCandidate {
  source: 'tmdb' | 'gracenote'
  ref: string
  width: number | null
  height: number | null
  off_white_pct?: number | null   // logos only, when evaluated
  is_white?: boolean | null
}

export interface ArtworkCandidatesResponse {
  logos: ArtworkCandidate[]
  backgrounds: ArtworkCandidate[]
  squareart: ArtworkCandidate[]
  posters: ArtworkCandidate[]
  plex_available: boolean          // false = no Plex token → no square art from Plex
}

export interface ArtworkItemRef {
  tmdb_id: number
  media_type: 'movie' | 'tv' | 'collection'
  title: string
  year?: string | number | null
  tvdb_id?: number | null
  imdb_id?: string | null
}

export const getArtworkCandidates = async (
  item: ArtworkItemRef,
  types: ArtworkListType[] = ['logo', 'background', 'squareart', 'poster'],
  evaluateWhite = true,
): Promise<ArtworkCandidatesResponse> => {
  const params = new URLSearchParams({
    tmdb_id: String(item.tmdb_id),
    media_type: item.media_type,
    title: item.title,
    types: types.join(','),
    evaluate_white: String(evaluateWhite),
  })
  if (item.year) params.set('year', String(item.year))
  if (item.tvdb_id) params.set('tvdb_id', String(item.tvdb_id))
  if (item.imdb_id) params.set('imdb_id', item.imdb_id)
  return getData<ArtworkCandidatesResponse>(`/api/artwork-finder/candidates?${params.toString()}`)
}

export interface AddArtworkRequest extends ArtworkItemRef {
  sync_target_index: number
  subtype: ArtworkSubtype
  source: 'tmdb' | 'gracenote'
  ref: string
  make_white?: boolean
  confirm_overwrite?: boolean   // set true after the user confirms overwriting an existing file
}

export interface AddArtworkResponse {
  success: boolean
  status: 'added' | 'exists'    // 'exists' = a same-named file is already there, needs confirm
  written: string
  subfolder: string
  archived: boolean
  source_dir: string
}

export const addArtwork = async (req: AddArtworkRequest): Promise<AddArtworkResponse> => {
  return postData<AddArtworkResponse>('/api/artwork-finder/add', req)
}

export interface CropArtworkRequest extends ArtworkItemRef {
  sync_target_index: number
  source: 'tmdb' | 'gracenote'
  ref: string
  x: number       // crop rect in the SOURCE image's own pixels
  y: number
  size: number    // square side
  confirm_overwrite?: boolean
}

/** Crop a chosen image (poster/background) to a square and save it as the item's square art. */
export const cropArtworkSquare = async (req: CropArtworkRequest): Promise<AddArtworkResponse> => {
  return postData<AddArtworkResponse>('/api/artwork-finder/crop-square', req)
}

export type ArtworkPullSource = 'poster_drives' | 'libraries' | 'scope' | 'list'

export interface ArtworkPullRequest {
  sync_target_index: number
  source: ArtworkPullSource
  drive_ids?: number[]
  paste?: string
  types: ArtworkSubtype[]
  min_backdrop_width?: number
  make_white_logos?: boolean
  skip_unreleased?: boolean
  force_refetch?: boolean
  sync_after_run?: boolean
  dry_run?: boolean
}

/** Queue a background job that pulls missing artwork for a set of items into the scope. */
export const startArtworkPull = async (req: ArtworkPullRequest): Promise<{ job_id: number; message: string }> => {
  return postData<{ job_id: number; message: string }>('/api/artwork-finder/batch-pull', req)
}

export interface ArtworkScopeItem {
  title: string
  year: number | null
  media_type: 'movie' | 'tv' | 'collection'
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  missing: ArtworkSubtype[]
}

/** Enumerate the scope's items with what each is missing (the browsable scope-backfill view). */
export const getArtworkScopeItems = async (syncTargetIndex: number): Promise<{ items: ArtworkScopeItem[]; total: number; scope_label: string }> => {
  return getData(`/api/artwork-finder/scope-items?sync_target_index=${syncTargetIndex}`)
}

/** Proxy URL for previewing a Gracenote (*.plex.tv) image (square art / clear logo). */
export const getGracenoteImageProxyUrl = (url: string): string => {
  return `/api/artwork-finder/gracenote-image-proxy?url=${encodeURIComponent(url)}`
}

/** Download a TMDB gallery image with a canonical `Title (Year) {ids}[ - Season N][ - tag].ext`
 * filename (built server-side by IDarr's writer). role: poster | backdrop | logo. */
export const getArtworkTaggedDownloadUrl = (
  filePath: string,
  role: 'poster' | 'backdrop' | 'logo',
  item: { tmdb_id: number; media_type: string; title: string; year?: string | number | null; tvdb_id?: number | null; imdb_id?: string | null },
  season?: number | null,
): string => {
  const params = new URLSearchParams({
    path: filePath,
    role: role === 'backdrop' ? 'background' : role,
    title: item.title,
    media_type: item.media_type,
    tmdb_id: String(item.tmdb_id),
  })
  if (item.year) params.set('year', String(item.year))
  if (item.tvdb_id) params.set('tvdb_id', String(item.tvdb_id))
  if (item.imdb_id) params.set('imdb_id', item.imdb_id)
  if (season != null) params.set('season', String(season))
  return `/api/artwork-finder/tmdb-download?${params.toString()}`
}

export interface PsdExportRequest {
  title: string
  year: string
  tmdb_id?: string
  tvdb_id?: string
  imdb_id?: string
  media_type?: string
  style?: string   // "CL2K" | "MM2K" — picks the template + export/image folder; blank → CL2K
  poster_paths: string[]
  backdrop_paths: string[]
  logo_paths: string[]
  use_existing?: boolean
  confirm_overwrite?: boolean
  poster_layer_names?: string[]   // per-poster convention names aligned to poster_paths (s1/s0/main/show/c; '' = untagged)
  backdrop_layer_names?: string[]  // same, aligned to backdrop_paths — a tagged backdrop keeps backdrop fit but gets a variant name
}

/** Returned when the server saved the PSD to an export folder — open in Photopea. */
export interface PsdExportSaved {
  mode: 'photopea'
  filename: string
  psdUrl: string   // absolute HTTPS URL, ready to pass to Photopea (carries ?style=)
  openPhotopea: boolean
  style: string    // resolved "CL2K" | "MM2K" — used to build the save / JPG-export URLs
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
 * Build the `poster_layer_names` list for an export, aligned 1:1 with `posterPaths`.
 * A tagged poster contributes its convention name (s1/s0/main/show/c); an untagged one contributes
 * '' so the backend keeps the default title-based layer name. Alignment is what lets the backend
 * name each injected poster layer correctly.
 */
export function posterLayerNames(posterPaths: string[], tags: Record<string, string>): string[] {
  return posterPaths.map((p) => tags[p] ?? '')
}

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
      const json = JSON.parse(text) as { filename: string; psd_url: string; open_photopea: boolean; style?: string }
      const absoluteUrl = `${window.location.origin}${json.psd_url}`
      return { mode: 'photopea', filename: json.filename, psdUrl: absoluteUrl, openPhotopea: json.open_photopea ?? false, style: json.style || 'CL2K' }
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

// Same-tab mode retains the launched Photopea window so later exports can add themselves as new
// DOCUMENTS in that one tab instead of spawning a tab each. Lost if the Posterflow page reloads
// (module state resets) — the next export then just launches a fresh tab.
let ppWin: Window | null = null
let ppOnError: ((msg: string) => void) | null = null
let ppListenerAttached = false

// Photopea echoes app.open failures back to us (the opener/OE) as "PFLOPENERR:<e>". Surface the
// latest caller's handler so a blocked LAN fetch / CORS / expired token isn't silent.
const ensurePpListener = (): void => {
  if (ppListenerAttached) return
  ppListenerAttached = true
  window.addEventListener('message', (e) => {
    if (typeof e.data === 'string' && e.data.indexOf('PFLOPENERR:') === 0 && ppOnError) {
      ppOnError(e.data.slice('PFLOPENERR:'.length))
    }
  })
}

/**
 * Open the exported PSD in TOP-LEVEL Photopea with the Posterflow "Seasons" plugin attached.
 *
 * New-tab mode (sameTab=false, default): each export opens its own Photopea tab. Photopea fetches
 * the PSD itself (files:[url]) and opens it on startup; a launch `script` renames the doc to the
 * full filename; the plugin panel (environment.plugins) provides the season buttons, the PSD save,
 * and the JPG export.
 *
 * Same-tab mode (sameTab=true): the FIRST export launches Photopea exactly as above but the window
 * handle is retained; every later export postMessages a script telling that live Photopea to
 * `app.open(psdUrl)` as a NEW document in the same tab. This reuses the local-network permission
 * granted at first launch — no new tab, no new prompt — so two styles of one title (or a re-exported
 * title with freshly added images) sit side by side as separate docs.
 *
 * Needs the user to allow Photopea's "local network access" prompt (public photopea.com reaching
 * the LAN/localhost server) + CORS on the PSD GET. Photopea API: https://www.photopea.com/api/
 */
export const openPhotopeaWithPsd = (
  psdUrl: string,
  filename: string,
  style = 'CL2K',
  sameTab = false,
  onError?: (msg: string) => void,
): void => {
  const docName = filename.replace(/\.psd$/i, '')
  // style rides on the save URL so the 💾 save-back and JPG export land in the SAME style's folder
  // the PSD was exported to.
  const saveUrl = `${window.location.origin}/api/maker-tools/psd-exports/${encodeURIComponent(filename)}?style=${encodeURIComponent(style)}`

  // ── Same-tab reuse: add a document to the already-open Photopea ──
  if (sameTab && ppWin && !ppWin.closed) {
    ppOnError = onError ?? null
    // Reuse the session's granted LNA + CORS: Photopea fetches the URL itself, just like files:[url]
    // did for the first doc. Always open a NEW doc — never re-activate an already-open copy: Use
    // Existing regenerates the file server-side, so a matching open doc is STALE and must reload.
    // Saves route off Photopea's own Document.source (the URL, holding the full filename + style), so
    // we don't set source. The name set here is BEST-EFFORT — app.open fetches async, so the doc often
    // isn't loaded yet, and Photopea re-derives the name from the %-encoded URL basename as it loads.
    // The plugin's ~1.2s watcher re-asserts the real name from Document.source; that's the reliable fix.
    const openScript = `try{`
      + `var nm=${JSON.stringify(docName)};`
      + `var d=app.open(${JSON.stringify(psdUrl)},null,false);`
      + `try{if(d&&d.name!==nm)d.name=nm;}catch(_n){}`
      + `app.echoToOE('PFLOPENED:new');`
      + `}catch(e){app.echoToOE('PFLOPENERR:'+e);}`
    ppWin.postMessage(openScript, '*')
    ppWin.focus()
    return
  }

  // ── Fresh launch (new-tab mode, or the first doc of a same-tab session) ──
  const params = new URLSearchParams({ save: saveUrl, name: docName, style })
  const pluginUrl = `${window.location.origin}/photopea-plugin.html?${params.toString()}`
  // icon: Posterflow's gradient logo as an inlined data URI (a colored logo, so no "===" theme-
  // recolor prefix). Inlined rather than a remote URL so it survives mixed-content/CORS/LNA blocking.
  // The published gallery "PosterFlow" uses a SOLID-color version of the same mark, so the two are
  // easy to tell apart (gradient = in-app "PosterFlow (App)"; solid = installed gallery one).
  // w/h: fix the panel to 184px wide — fits 5 season chips per row.
  const icon = pluginIcon
  // Photopea fetches the PSD itself (files:[url]) and opens it during startup — it loads as the
  // editor boots. Photopea names the doc from the URL basename (trimmed + now %-encoded since the path
  // is quoted), so the launch `script` renames it to the full filename. That set is BEST-EFFORT
  // (Photopea can re-derive/clobber the name post-load); the plugin's ~1.2s watcher re-asserts the real
  // name from Document.source, which is what reliably fixes the tab (Photopea-side timers can't loop a
  // rename past the clobber). Save routing rides on Photopea's own Document.source (the URL), so we
  // don't set source here. Requires the user to ALLOW Photopea's "local network access" prompt + CORS
  // on the PSD GET (we send it). On a password-protected instance psd_url carries a signed, file-scoped
  // ?token= the GET validates, since Photopea can't send the app Bearer header.
  const config = {
    files: [psdUrl],
    script: `try{var d=app.activeDocument;if(d&&d.name!==${JSON.stringify(docName)})d.name=${JSON.stringify(docName)};}catch(e){}`,
    environment: { plugins: [{ name: 'PosterFlow (App)', url: pluginUrl, icon, w: 184, h: 420 }] },
  }
  const w = window.open(`https://www.photopea.com#${encodeURIComponent(JSON.stringify(config))}`, '_blank')
  if (sameTab) {
    ppWin = w
    ppOnError = onError ?? null
    ensurePpListener()
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