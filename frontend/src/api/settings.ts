import { getData, postData } from './http'

export interface Setting {
  key: string
  value: string
  description?: string
}

export interface TestResult {
  success: boolean
  message: string
  version?: string
}

export interface DiscordNotificationFeatureConfig {
  enabled: boolean
  on_success: boolean
  on_error: boolean
  include_summary: boolean
  include_details: boolean
}

export interface DiscordNotificationConfig {
  enabled: boolean
  webhook_url: string
  features: Record<string, DiscordNotificationFeatureConfig>
}

export interface RevealSensitiveSettingRequest {
  setting_key: string
  field?: string
  instance_name?: string
  index?: number
}

export interface RevealSensitiveSettingResponse {
  setting_key: string
  field?: string
  value: string
}

// API functions
export const checkSetupComplete = async (): Promise<boolean> => {
  try {
    const data = await getData<{ setup_complete: boolean }>('/api/setup/status')
    return data.setup_complete
  } catch (error) {
    return false
  }
}

export const saveSettings = async (settings: Record<string, string>) => {
  return postData('/api/settings/bulk', settings)
}

export const getSettings = async (): Promise<Record<string, string>> => {
  return getData('/api/settings/')
}

export const revealSensitiveSetting = async (
  payload: RevealSensitiveSettingRequest,
): Promise<RevealSensitiveSettingResponse> => {
  return postData('/api/settings/reveal', payload)
}

export const getDiscordNotificationConfig = async (): Promise<DiscordNotificationConfig> => {
  return getData('/api/settings/notifications/discord')
}

export const saveDiscordNotificationConfig = async (config: DiscordNotificationConfig) => {
  return postData('/api/settings/notifications/discord', config)
}

export const testDiscordNotification = async (config: DiscordNotificationConfig): Promise<{ success: boolean; message: string }> => {
  return postData('/api/settings/notifications/discord/test', config)
}

export const saveBulkSettings = saveSettings

export interface ServiceAccountUploadResponse {
  message: string
  path: string
}

export const uploadServiceAccountJson = async (file: File): Promise<ServiceAccountUploadResponse> => {
  const formData = new FormData()
  formData.append('file', file)

  return postData('/api/settings/service-account/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
}

// Test connections
export const testPlex = async (url: string, token: string): Promise<TestResult> => {
  return postData('/api/test/plex', { url, token })
}

export const testSonarr = async (url: string, api_key: string): Promise<TestResult> => {
  return postData('/api/test/sonarr', { url, api_key })
}

export const testRadarr = async (url: string, api_key: string): Promise<TestResult> => {
  return postData('/api/test/radarr', { url, api_key })
}

export interface PlexLibrary {
  title: string
  key: string
  type: string
  enabled: boolean
}

export const getPlexLibraries = async (url: string, token: string): Promise<{ success: boolean; libraries: PlexLibrary[] }> => {
  return postData('/api/test/plex/libraries', { url, token })
}

// Plex Library Config
export interface PlexLibraryConfig {
  instance_name: string
  libraries: PlexLibrary[]
}

export interface MakerIdarrConfig {
  sync_targets: MakerIdarrSyncTarget[]
  tmdb_api_key: string
  auto_rename_quick_add: boolean
  auto_upload_quick_add: boolean
  remove_non_image_files: boolean
  show_unmatched: boolean
  pending_matches: boolean
  skip_collections: boolean
  limit: number | null
  frequency_days: number
  tvdb_frequency: number
}

export interface MakerIdarrSyncTarget {
  personal_drive_id: string
  source_dir: string
  label?: string
  scope_token?: string
}

export interface MakerIdarrLastRun {
  completed_at?: string
  stats?: Record<string, unknown>
  details?: {
    timestamp?: string
    enrichment?: {
      items_considered?: number
      tmdb_assigned?: number
      tvdb_assigned?: number
      imdb_assigned?: number
      cache_prefilled?: number
      freshness_skipped?: number
      tvdb_rehydrate_calls?: number
      errors?: number
      confidence_high?: number
      confidence_medium?: number
      confidence_low?: number
      confidence_unknown?: number
      match_reason_counts?: Record<string, number>
    }
    enriched_items?: Array<{
      title?: string
      type?: string
      year?: number
      before?: Record<string, unknown>
      after?: Record<string, unknown>
    }>
    unmatched_items?: Array<{
      title?: string
      type?: string
      year?: number
    }>
    duplicate_conflicts?: Array<{
      timestamp?: string
      source_path?: string
      target_path?: string
      resolution?: string
      archived_path?: string
      action_mode?: string
      dry_run?: string
    }>
    duplicate_log_csv?: string
    file_operations?: Array<{
      operation?: string
      from_path?: string
      to_path?: string
      status?: string
      revert_supported?: boolean
      reason?: string
    }>
  }
  warnings?: string[]
  unmatched_count?: number
}

export interface MakerIdarrPendingItem {
  asset_key: string
  title: string
  year?: number | null
  type: string
  pending_reason?: string | null
  preview_url?: string | null
  created_at?: string | null
  updated_at?: string | null
  source_filenames?: string[] | null
  suggested_ids?: {
    tmdb_id?: number | null
    tvdb_id?: number | null
    imdb_id?: string | null
  }
  cache_context?: {
    matched?: boolean
    last_checked_at?: string | null
    candidate_refreshed_at?: string | null
  }
  history?: {
    unmatched_run_count?: number
    last_unmatched_at?: string | null
  }
  candidates?: MakerIdarrPendingCandidate[]
  candidate_reviews?: Record<string, { status?: 'accept' | 'reject'; reviewed_at?: string; note?: string | null }>
  resolution_history?: MakerIdarrResolutionEvent[]
}

export interface ResolveMakerIdarrPendingPayload {
  asset_key: string
  action: 'resolve' | 'dismiss' | 'ignore'
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  sync_target_index?: number
}

export interface MakerIdarrPendingCandidate {
  tmdb_id: number
  tvdb_id?: number | null
  imdb_id?: string | null
  poster_url?: string | null
  title: string
  year?: number | null
  overview?: string
  popularity?: number
  vote_average?: number
  media_type: 'movie' | 'show' | 'collection'
  match_reason?: {
    score?: number
    reasons?: string[]
    summary?: string
  }
  review?: {
    status?: 'accept' | 'reject'
    reviewed_at?: string
    note?: string | null
  }
}

export interface MakerIdarrResolutionEvent {
  resolved_at?: string
  source?: 'manual' | 'candidate' | string
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  candidate_reason?: {
    score?: number
    reasons?: string[]
    summary?: string
  } | null
}

export interface MakerIdarrPendingCandidatesResponse {
  asset_key: string
  title: string
  year?: number | null
  type: string
  candidates: MakerIdarrPendingCandidate[]
  candidate_reviews?: Record<string, { status?: 'accept' | 'reject'; reviewed_at?: string; note?: string | null }>
  resolution_history?: MakerIdarrResolutionEvent[]
}

export interface MakerIdarrIgnoredItem {
  asset_key: string
  title: string
  year?: number | null
  type: string
  created_at?: string | null
}

export interface MakerIdarrIgnoredBulkPayload {
  titles: string[]
  sync_target_index?: number
}

export interface MakerIdarrIgnoredBulkResponse {
  success: boolean
  requested_titles: number
  resolved_entries: number
  added?: number
  total: number
}

export interface MakerIdarrCacheStats {
  total: number
  matched: number
  unmatched: number
  never_checked: number
}

export interface MakerIdarrCacheMaintenancePayload {
  action: 'clear_all' | 'prune_unmatched' | 'purge_stale' | 'prune_targeted'
  days?: number
  title?: string
  asset_key?: string
  tmdb_id?: number
  tvdb_id?: number
  imdb_id?: string
  sync_target_index?: number
}

export interface MakerIdarrPurgedCacheItem {
  title: string
  year?: number | null
  asset_key: string
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
}

export interface MakerIdarrRevertPayload {
  dry_run?: boolean
  sync_target_index?: number
}

export interface MakerIdarrUploadResponse {
  success: boolean
  source_dir: string
  uploaded_count: number
  skipped_count: number
  uploaded: string[]
  skipped: string[]
}

export const getPlexLibraryConfigs = async (): Promise<{ configs: PlexLibraryConfig[] }> => {
  return getData('/api/settings/plex-libraries')
}

export const savePlexLibraryConfig = async (config: PlexLibraryConfig) => {
  return postData('/api/settings/plex-libraries', config)
}

export const getGdriveStoragePath = async (): Promise<{ path: string }> => {
  return getData('/api/settings/gdrive-storage')
}

export const saveGdriveStoragePath = async (path: string): Promise<{ path: string }> => {
  return postData('/api/settings/gdrive-storage', { path })
}

export const getMakerIdarrConfig = async (): Promise<MakerIdarrConfig> => {
  return getData('/api/idarr/')
}

export const saveMakerIdarrConfig = async (config: MakerIdarrConfig) => {
  return postData('/api/idarr/', config)
}

export const uploadMakerIdarrFiles = async (
  syncTargetIndex: number,
  files: File[],
): Promise<MakerIdarrUploadResponse> => {
  const formData = new FormData()
  formData.append('sync_target_index', String(syncTargetIndex))
  files.forEach((file) => formData.append('files', file))

  return postData('/api/idarr/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  })
}

export const getMakerIdarrLastRun = async (syncTargetIndex?: number): Promise<MakerIdarrLastRun> => {
  return getData('/api/idarr/last-run', {
    params: syncTargetIndex === undefined ? undefined : { sync_target_index: syncTargetIndex },
  })
}

export const getMakerIdarrPendingMatches = async (syncTargetIndex?: number): Promise<{ items: MakerIdarrPendingItem[] }> => {
  return getData('/api/idarr/pending-matches', {
    params: syncTargetIndex === undefined ? undefined : { sync_target_index: syncTargetIndex },
  })
}

export const clearMakerIdarrPendingMatches = async (syncTargetIndex?: number): Promise<{ success: boolean; deleted: number }> => {
  return postData('/api/idarr/pending-matches/clear-all', undefined, {
    params: syncTargetIndex === undefined ? undefined : { sync_target_index: syncTargetIndex },
  })
}

export const resolveMakerIdarrPendingMatch = async (payload: ResolveMakerIdarrPendingPayload): Promise<{ success: boolean; action: string; asset_key: string }> => {
  return postData('/api/idarr/pending-matches/resolve', payload)
}

export const getMakerIdarrPendingCandidates = async (payload: {
  asset_key?: string
  title: string
  year?: number | null
  type: string
  sync_target_index?: number
}): Promise<MakerIdarrPendingCandidatesResponse> => {
  return postData('/api/idarr/pending-matches/candidates', payload)
}

export const reviewMakerIdarrPendingCandidate = async (payload: {
  asset_key: string
  tmdb_id: number
  action: 'accept' | 'reject' | 'clear'
  note?: string
  sync_target_index?: number
}): Promise<{
  success: boolean
  asset_key: string
  tmdb_id: number
  action: string
  candidate_reviews: Record<string, { status?: 'accept' | 'reject'; reviewed_at?: string; note?: string | null }>
}> => {
  return postData('/api/idarr/pending-matches/candidates/review', payload)
}

export const getMakerIdarrCacheStats = async (syncTargetIndex?: number): Promise<MakerIdarrCacheStats> => {
  return getData('/api/idarr/cache/stats', {
    params: syncTargetIndex === undefined ? undefined : { sync_target_index: syncTargetIndex },
  })
}

export const runMakerIdarrCacheMaintenance = async (
  payload: MakerIdarrCacheMaintenancePayload,
): Promise<{ success: boolean; action: string; deleted: number; remaining: number; purged_items?: MakerIdarrPurgedCacheItem[] }> => {
  return postData('/api/idarr/cache/maintenance', payload)
}

export const revertMakerIdarrLatestRun = async (
  payload: MakerIdarrRevertPayload = {},
): Promise<{ success: boolean; dry_run: boolean; run_id: number; reverted: number; skipped: number; errors: number }> => {
  return postData('/api/idarr/revert-latest', payload)
}

export const getMakerIdarrIgnoredTitles = async (syncTargetIndex?: number): Promise<{ items: MakerIdarrIgnoredItem[] }> => {
  return getData('/api/idarr/ignored-titles', {
    params: syncTargetIndex === undefined ? undefined : { sync_target_index: syncTargetIndex },
  })
}

export const addMakerIdarrIgnoredTitle = async (payload: {
  title: string
  year?: number | null
  type: string
  asset_key?: string
  sync_target_index?: number
}): Promise<{ success: boolean; asset_key: string }> => {
  return postData('/api/idarr/ignored-titles/add', payload)
}

export const removeMakerIdarrIgnoredTitle = async (payload: {
  title: string
  year?: number | null
  type: string
  asset_key?: string
  sync_target_index?: number
}): Promise<{ success: boolean; asset_key: string; restored_pending?: boolean }> => {
  return postData('/api/idarr/ignored-titles/remove', payload)
}

export const importMakerIdarrIgnoredTitles = async (
  payload: MakerIdarrIgnoredBulkPayload,
): Promise<MakerIdarrIgnoredBulkResponse> => {
  return postData('/api/idarr/ignored-titles/import', payload)
}

export const replaceMakerIdarrIgnoredTitles = async (
  payload: MakerIdarrIgnoredBulkPayload,
): Promise<MakerIdarrIgnoredBulkResponse> => {
  return postData('/api/idarr/ignored-titles/replace', payload)
}
