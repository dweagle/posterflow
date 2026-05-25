import { getData, postData } from './http'

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
    movies: Array<{ title: string; year: number; instance: string }>
    series: Array<{ title: string; year: number; missing_seasons: number[]; missing_main_poster: boolean; instance: string }>
    collections: Array<{ title: string; year: number; instance: string }>
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
  media_type: 'movie' | 'show' | 'collection'
  match_reason: string
}

export const searchUnmatchedTmdb = async (params: {
  title: string
  year: number | null
  type: 'movie' | 'show' | 'collection'
}): Promise<{ candidates: TmdbCandidate[] }> => {
  return postData('/api/posterflow/unmatched-tmdb-search', params)
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

export interface FlowConfig {
  idarr: IdarrFlowJobConfig
  sync_drives: FlowJobConfig
  rename_posters: FlowJobConfig
  detect_unmatched: FlowJobConfig
  border_replacer: FlowJobConfig
  plex_upload: FlowJobConfig
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
}

export const getFlowConfig = async (): Promise<FlowConfig> => {
  return getData('/api/posterflow/flow/config')
}

export const saveFlowConfig = async (config: FlowConfig) => {
  return postData('/api/posterflow/flow/config', config)
}

export const runFlow = async (options?: FlowRunOptions): Promise<FlowResult> => {
  const payload = options?.dry_run ? { dry_run: true } : undefined
  return postData('/api/posterflow/flow/run', payload)
}

export interface FallbackItem {
  title: string
  year: number | null
  type: 'movie' | 'show' | 'collection'
  season?: number | null
}

export interface PosterStyleStats {
  style_counts: Record<string, number>
  style_fallbacks: Record<string, FallbackItem[]>
}

export const getPosterStats = async (): Promise<PosterStyleStats> => {
  return getData('/api/posterflow/stats')
}
