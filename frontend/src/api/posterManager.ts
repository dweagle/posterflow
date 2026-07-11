import client, { getData, postData, putData, deleteData } from './http'

// Poster types and functions
export interface PosterConfig {
  destination: string
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

export interface FlowConfig {
  idarr: IdarrFlowJobConfig
  sync_drives: FlowJobConfig
  rename_posters: FlowJobConfig
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
