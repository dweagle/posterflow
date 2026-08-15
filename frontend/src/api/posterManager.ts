import client, { getData, postData, putData, deleteData } from './http'

// Poster types and functions
// Display fallback for the blank-destination default until GET /config reports the
// backend's actual one (default_destination) — native installs root it elsewhere.
export const DEFAULT_POSTER_DESTINATION = '/config/posters/assets'

export interface PosterConfig {
  destination: string
  default_destination?: string  // backend's blank-destination fallback, from GET /config
  is_docker?: boolean  // install type, from GET /config — drives volume-vs-folder help text
  asset_folders: boolean
  dry_run: boolean
  match_threshold: number
  action_type: string  // 'copy', 'move', 'hardlink', or 'symlink'
}

export interface PosterMatch {
  source_path: string
  media_title: string
  media_type: string
  media_year: number | null
  confidence: number
  destination_path: string
}

export interface DrivePriority {
  drive_ids: number[]
  enabled_styles: string[]
}

export interface UnmatchedStats {
  summary: {
    movies: { total: number; unmatched: number; percent_complete: number; released?: number; unreleased?: number }
    series: { total: number; unmatched: number; percent_complete: number; continuing?: number; upcoming?: number }
    seasons: { total: number; unmatched: number; percent_complete: number }
    collections: { total: number; unmatched: number; percent_complete: number }
    grand_total: { total: number; unmatched: number; percent_complete: number }
    by_library: {
      [libraryName: string]: {
        movies?: { total: number; unmatched: number; percent_complete: number }
        series?: { total: number; unmatched: number; percent_complete: number }
        collections?: { total: number; unmatched: number; percent_complete: number; library_type?: string }
      }
    }
  }
  unmatched: {
    movies: Array<{ title: string; year: number; instance: string; tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null; poster_url?: string | null; available?: boolean | null }>
    series: Array<{ title: string; year: number; missing_seasons: number[]; missing_main_poster: boolean; instance: string; tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null; poster_url?: string | null; available?: boolean | null }>
    collections: Array<{ title: string; year: number; instance: string; tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null; poster_url?: string | null; available?: boolean | null }>
  }
  last_run: string | null
}

export interface PosterSearchDrive {
  drive_id: string
  drive_name: string
  drive_type: 'cl2k' | 'mm2k' | 'custom'
  style_type: string
  is_custom: boolean
  poster_id: number
  image_url: string
}

export interface PosterSearchItem {
  poster_name: string
  drives: PosterSearchDrive[]
  drive_count: number
}

export interface PosterSearchResponse {
  query: string
  count: number
  items: PosterSearchItem[]
}

export const getPosterConfig = async (): Promise<PosterConfig> => {
  return getData('/api/posterflow/config')
}

export const savePosterConfig = async (config: PosterConfig) => {
  return postData('/api/posterflow/config', config)
}

export const startPosterRename = async (config: PosterConfig): Promise<{
  job_id: number;
  status: string;
  unmatched_detection?: UnmatchedStats;
}> => {
  return postData('/api/posterflow/rename', { config })
}

export const startAssetRename = async (dryRun: boolean = false): Promise<{
  jobs: Array<{ job_id: number; type: string }>;
  job_id?: number;
  status: string;
  message: string;
}> => {
  return postData('/api/posterflow/asset-rename', { dry_run: dryRun })
}

export interface BorderReplacerRunOptions {
  dry_run?: boolean
}

export const runBorderReplacer = async (options?: BorderReplacerRunOptions): Promise<{
  success: boolean;
  job_id: number;
  status: string;
  message: string;
}> => {
  const payload = options?.dry_run ? { dry_run: true } : undefined
  return postData('/api/posterflow/border-replacer/run', payload)
}

// --- Border overlay frames (bundled presets + user uploads) ---

export interface BorderOverlay {
  name: string
  source: 'preset' | 'user'
}

export const listBorderOverlays = async (): Promise<{ overlays: BorderOverlay[] }> => {
  return getData('/api/posterflow/border-replacer/overlays')
}

export const uploadBorderOverlay = async (
  file: File,
): Promise<{ success: boolean; name: string; source: string }> => {
  const formData = new FormData()
  formData.append('file', file)
  return postData('/api/posterflow/border-replacer/overlays/upload', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export const deleteBorderOverlay = async (name: string): Promise<{ success: boolean; deleted: string }> => {
  return deleteData(`/api/posterflow/border-replacer/overlays/${encodeURIComponent(name)}`)
}

export interface BorderPreviewParams {
  style: string
  color?: string
  border_width?: number
  gradient_colors?: string[]
  gradient_direction?: string
  overlay?: string
  remove_existing?: boolean
  inner_effect?: string
  inner_color?: string
  inner_opacity?: number
  inner_width?: number
  fade_width?: number
  passthrough?: boolean
}

// Render a sample drive poster with the given border options; returns a PNG blob
// the caller turns into an object URL (preview images need the auth header).
export const fetchBorderPreview = async (params: BorderPreviewParams): Promise<Blob> => {
  const query = new URLSearchParams()
  query.set('style', params.style)
  if (params.color) query.set('color', params.color)
  if (params.border_width != null) query.set('border_width', String(params.border_width))
  if (params.gradient_colors?.length) query.set('gradient_colors', params.gradient_colors.join(','))
  if (params.gradient_direction) query.set('gradient_direction', params.gradient_direction)
  if (params.overlay) query.set('overlay', params.overlay)
  if (params.remove_existing) query.set('remove_existing', 'true')
  if (params.inner_effect) query.set('inner_effect', params.inner_effect)
  if (params.inner_color) query.set('inner_color', params.inner_color)
  if (params.inner_opacity != null) query.set('inner_opacity', String(params.inner_opacity))
  if (params.inner_width != null) query.set('inner_width', String(params.inner_width))
  if (params.fade_width != null) query.set('fade_width', String(params.fade_width))
  if (params.passthrough) query.set('passthrough', 'true')
  const resp = await client.get(
    `/api/posterflow/border-replacer/preview?${query.toString()}`,
    { responseType: 'blob' },
  )
  return resp.data as Blob
}

export const getDrivePriority = async (): Promise<DrivePriority> => {
  return getData('/api/posterflow/priority')
}

export const saveDrivePriority = async (priority: DrivePriority) => {
  return postData('/api/posterflow/priority', priority)
}

export const getUnmatchedStats = async (): Promise<UnmatchedStats> => {
  return getData('/api/posterflow/unmatched-stats')
}

export const startUnmatchedDetection = async (): Promise<{ success: boolean; job_id: number; status: string; message: string }> => {
  return postData('/api/posterflow/detect-unmatched')
}

// One entry on the unmatched ignore list — items the user hid from unmatched
// detection. Identified by ids when available, else title+year.
export interface UnmatchedIgnoreItem {
  media_type: 'movie' | 'series' | 'collection'
  title: string
  year?: number | null
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
}

export const getUnmatchedIgnoreItems = async (): Promise<{ items: UnmatchedIgnoreItem[] }> => {
  return getData('/api/posterflow/unmatched-ignore-items')
}

export const addUnmatchedIgnoreItem = async (item: UnmatchedIgnoreItem): Promise<{ items: UnmatchedIgnoreItem[] }> => {
  return postData('/api/posterflow/unmatched-ignore-items', item)
}

export const removeUnmatchedIgnoreItem = async (item: UnmatchedIgnoreItem): Promise<{ items: UnmatchedIgnoreItem[] }> => {
  return postData('/api/posterflow/unmatched-ignore-items/remove', item)
}

export interface TmdbCandidate {
  tmdb_id: number
  tvdb_id: number | null
  imdb_id: string | null
  title: string
  year: number | null
  poster_url: string | null
  overview: string
  popularity: number
  media_type: 'movie' | 'show' | 'collection' | 'person'
  match_reason: string
  // True when this candidate was resolved directly from a carried *arr id
  // (exact, language-independent) rather than from the fuzzy title search.
  auto_matched?: boolean
}

export const searchUnmatchedTmdb = async (params: {
  title: string
  year: number | null
  type: 'movie' | 'show' | 'collection' | 'person'
  // Authoritative refs from Plex/*arr; when present the backend resolves the
  // exact TMDB entity by id and pins it to the top of the candidate list.
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
}): Promise<{ candidates: TmdbCandidate[] }> => {
  return postData('/api/posterflow/unmatched-tmdb-search', params)
}

// ---------------------------------------------------------------------------
// Unmatched match report ("why isn't this matching")
// ---------------------------------------------------------------------------

export interface MatchReportVerdict {
  level: 'problem' | 'ok' | 'info'
  code: string
  message: string
}

export interface MatchReportCandidate {
  title: string | null
  year: number | null
  type?: string | null
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  drive: string | null
  files: string[]
  season_numbers: number[]
  // True when the set includes a non-season (main poster) file.
  has_main?: boolean
  // Artwork reports only: which artwork types this box carries.
  artwork_types?: string[] | null
  // Tag-looking text the id parser rejected ("{tvdb-475672 }" etc.).
  malformed_tags?: string[]
  // Ids found on the files of an id-less nested asset (tag on the wrong level).
  file_ids?: { tmdb_id: number | null; tvdb_id: number | null; imdb_id: string | null } | null
  found_by: 'id' | 'title'
  matched: boolean
  reason: string
  newest_file: string | null
}

export interface MatchReportLibraryRecord {
  instance: string | null
  title: string | null
  year: number | null
  folder: string | null
  folder_has_year: boolean
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  monitored: boolean | null
  // Radarr: tba/announced/incinemas/released — Sonarr: upcoming/continuing/ended.
  status: string | null
  // Movies: has_file; series: has_episodes; collections: null.
  available: boolean | null
  alternate_titles: string[]
  seasons_with_episodes: number[]
}

// A resolved reference carries ids + title/year; otherwise `skipped`, `error`, or
// `missing` (Plex: reachable but the item isn't in any library) says why.
export interface MatchReportReference {
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  title?: string
  year?: number | null
  // Plex only: which library/instance the item was found in.
  library?: string | null
  instance?: string | null
  // TMDB/TVDB only: the source's alias list (capped; total says how many exist).
  alternate_titles?: string[]
  alternate_titles_total?: number
  skipped?: string
  error?: string
  missing?: string
}

export interface MatchReport {
  generated_at: string
  app_version: string
  item: {
    media_type: 'movies' | 'series' | 'collections'
    title: string
    year: number | null
    tmdb_id: number | null
    tvdb_id: number | null
    imdb_id: string | null
    missing_seasons: number[]
    missing_main?: boolean
    artwork_type?: 'logo' | 'background' | 'squareart' | null
  }
  verdicts: MatchReportVerdict[]
  library: {
    found: boolean
    records: MatchReportLibraryRecord[]
    effective_ids: { tmdb_id: number | null; tvdb_id: number | null; imdb_id: string | null }
    // Instance the effective ids came from (Sonarr/Radarr/Plex as named in Settings),
    // or "unmatched cache" when no live record was found.
    ids_source: string
    manual_entry: boolean
    on_ignore_list: boolean
  }
  reference: { tmdb: MatchReportReference; tvdb: MatchReportReference; plex: MatchReportReference }
  drives: {
    scanned: { name: string; style_type: string; last_synced: string | null; missing: boolean }[]
    total_assets: number
    error: string | null
  }
  candidates: { considered: number; shown: number; omitted: number; id_pool?: number; items: MatchReportCandidate[] }
  // Only-when-nothing-matched extras (also rendered in the text report).
  nonpriority_hits?: { title: string | null; year: number | null; drive: string | null; reason: string; files: string[] }[]
  unscannable?: { file: string; drive_dir: string; reason: string }[]
  close_titles?: { title: string | null; year: number | null; similarity: number; files: string[] }[]
  collection_id_note?: { title: string | null; files: string[] } | null
}

export interface MatchReportResponse {
  report: MatchReport
  report_text: string
  filename: string
}

export const fetchUnmatchedMatchReport = async (params: {
  media_type: 'movies' | 'series' | 'collections'
  title: string
  year?: number | null
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  missing_seasons?: number[] | null
  missing_main?: boolean
  artwork_type?: 'logo' | 'background' | 'squareart' | null
}): Promise<MatchReportResponse> => {
  return postData('/api/posterflow/unmatched-match-report', params)
}

// Save a generated report as a .txt download (dropped straight into Discord/forums).
export const downloadMatchReport = (response: MatchReportResponse): void => {
  const blob = new Blob([response.report_text], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = response.filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

// ---------------------------------------------------------------------------
// Manual Media Entries
// ---------------------------------------------------------------------------

export interface ManualMediaEntry {
  id: number
  title: string
  year: number | null
  media_type: 'movie' | 'series'
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  seasons: number[]
  created_at: string | null
}

export const listManualMedia = async (): Promise<{ entries: ManualMediaEntry[] }> => {
  return getData('/api/posterflow/manual-media')
}

export const addManualMedia = async (payload: {
  title: string
  year: number | null
  media_type: 'movie' | 'series'
  seasons_count: number | null
  include_specials: boolean
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
}): Promise<{ success: boolean; entry: ManualMediaEntry }> => {
  return postData('/api/posterflow/manual-media', payload)
}

export const deleteManualMedia = async (id: number): Promise<{ success: boolean; deleted_id: number }> => {
  return deleteData(`/api/posterflow/manual-media/${id}`)
}

export const searchPosters = async (query: string, limit: number = 200): Promise<PosterSearchResponse> => {
  return getData('/api/stats/poster-search', {
    params: {
      q: query,
      limit,
    },
  })
}

// Flow types and functions
export interface FlowJobConfig {
  enabled: boolean
  stop_on_error: boolean
}

export interface IdarrFlowJobConfig {
  enabled: boolean
  stop_on_error: boolean
  scope_indices: number[]
  sync_after_run: boolean
}

export interface CleanupFlowJobConfig {
  enabled: boolean
  delete_unknown: boolean
}

/** Sync Drives step. Poster and artwork drives are separate drive types, each opt-in. */
export interface SyncFlowJobConfig extends FlowJobConfig {
  posters: boolean
  artwork: boolean
}

export interface FlowConfig {
  idarr: IdarrFlowJobConfig
  sync_drives: SyncFlowJobConfig
  rename_assets: FlowJobConfig
  detect_unmatched: FlowJobConfig
  border_replacer: FlowJobConfig
  plex_upload: FlowJobConfig
  cleanup_assets: CleanupFlowJobConfig
}

export interface FlowResult {
  job_id: number
  success: boolean
  message?: string
  jobs_run?: Array<{
    job: string
    success: boolean
    drives_synced?: number | null
    stats?: {
      total_matched?: number
      [key: string]: unknown
    } | null
    unmatched_count?: number | null
    [key: string]: unknown
  }>
  jobs_skipped?: Array<{
    job: string
    reason: string
  }>
  jobs_failed?: Array<{
    job: string
    error: string
  }>
  unmatched_detection?: UnmatchedStats
}

export interface FlowRunOptions {
  dry_run?: boolean
  workflow_id?: number | null
}

export const getFlowConfig = async (): Promise<FlowConfig> => {
  return getData('/api/posterflow/flow/config')
}

export const saveFlowConfig = async (config: FlowConfig) => {
  return postData('/api/posterflow/flow/config', config)
}

export const runFlow = async (options?: FlowRunOptions): Promise<FlowResult> => {
  const payload: Record<string, unknown> = {}
  if (options?.dry_run) payload.dry_run = true
  if (options?.workflow_id != null) payload.workflow_id = options.workflow_id
  return postData('/api/posterflow/flow/run', Object.keys(payload).length > 0 ? payload : undefined)
}

// Saved workflows (named combinations of flow steps)
export interface Workflow {
  id: number
  name: string
  is_default: boolean
  config: FlowConfig
}

export const listWorkflows = async (): Promise<Workflow[]> => {
  return getData('/api/posterflow/workflows')
}

export const createWorkflow = async (name: string, config?: FlowConfig): Promise<Workflow> => {
  return postData('/api/posterflow/workflows', config ? { name, config } : { name })
}

export const updateWorkflow = async (
  id: number,
  updates: { name?: string; config?: FlowConfig; is_default?: boolean }
): Promise<Workflow> => {
  return putData(`/api/posterflow/workflows/${id}`, updates)
}

export const deleteWorkflow = async (id: number): Promise<void> => {
  await deleteData<void>(`/api/posterflow/workflows/${id}`)
}

export interface FallbackItem {
  title: string
  year: number | null
  type: 'movie' | 'show' | 'collection'
  season?: number | null
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  poster_url?: string | null
  available?: boolean | null
}

export interface PosterStyleStats {
  style_counts: Record<string, number>
  style_fallbacks: Record<string, FallbackItem[]>
}

export const getPosterStats = async (): Promise<PosterStyleStats> => {
  return getData('/api/posterflow/stats')
}
