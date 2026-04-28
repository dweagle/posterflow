import { API_URL, getData, postData } from './http'

export interface PlexUploadRunOptions {
  dry_run?: boolean
  reapply?: boolean
  remove_overlay_label?: boolean
}

export interface PlexUploadRunResponse {
  success: boolean
  job_id: number
  status: string
  message: string
}

export interface PlexUploadSourceSearchItem {
  media_type: 'movie' | 'series' | 'collection'
  title: string
  year: number | null
  season_number: number | null
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  source_file: string
  source_file_name: string
  preview_url: string
  drive_name: string
  drive_type: 'cl2k' | 'mm2k' | 'custom' | string
}

export interface PlexUploadSourceSearchResponse {
  query: string
  count: number
  items: PlexUploadSourceSearchItem[]
}

export interface PlexSingleUploadPayload {
  media_type: 'movie' | 'series' | 'collection'
  title: string
  year?: number | null
  season_number?: number | null
  tmdb_id?: number | null
  tvdb_id?: number | null
  imdb_id?: string | null
  plex_rating_key?: number | null
  dry_run?: boolean
  reapply?: boolean
  remove_overlay_label?: boolean
}

export interface PlexSearchItem {
  media_type: 'movie' | 'series' | 'collection'
  title: string
  year: number | null
  tmdb_id: number | null
  tvdb_id: number | null
  imdb_id: string | null
  plex_rating_key: number | null
  plex_instance: string
  library_name: string
  thumb_url: string | null
}

export interface PlexSearchResponse {
  query: string
  count: number
  items: PlexSearchItem[]
  error?: string
}

export interface PlexWebhookSettings {
  enabled: boolean
  remove_overlay_label: boolean
  rename_then_upload: boolean
  adopt_existing_processed: boolean
  retry_attempts: number
  retry_delay_seconds: number
}

export interface PlexWebhookSettingsPayload {
  enabled: boolean
  remove_overlay_label: boolean
  rename_then_upload: boolean
  adopt_existing_processed: boolean
  retry_attempts: number
  retry_delay_seconds: number
}

export interface PlexManualSettings {
  dry_run: boolean
  reapply: boolean
  remove_overlay_label: boolean
}

export interface PlexManualSettingsPayload {
  dry_run: boolean
  reapply: boolean
  remove_overlay_label: boolean
}

export interface PlexWebhookStats {
  received: number
  queued: number
  duplicates: number
  skipped_test: number
  skipped_cached: number
  rejected_disabled: number
  parse_errors: number
  internal_errors: number
  last_event_at: string | null
  last_queued_at: string | null
  last_error: string | null
}

export interface PlexWebhookDedupeClearPayload {
  clear_all?: boolean
  source?: string
  event_type?: string
  media_type?: 'movie' | 'series'
  title?: string
  year?: number
  season_number?: number
}

export interface PlexWebhookDedupeClearResponse {
  success: boolean
  removed: number
  remaining: number
  window_seconds: number
  cleared_all: boolean
}

export interface PlexWebhookDedupeEntry {
  source: string
  event_type: string
  media_type: 'movie' | 'series' | string
  title: string
  year: number | null
  season_number?: number | null
  dedupe_key: string
  last_seen_at: string
}

export interface PlexWebhookDedupeEntriesResponse {
  query: string
  media_type: 'movie' | 'series' | null
  count: number
  total: number
  window_seconds: number
  items: PlexWebhookDedupeEntry[]
}

export interface PlexUploadLibrary {
  title: string
  key: string
  type: string
  enabled: boolean
}

export interface PlexUploadLibraryConfig {
  instance_name: string
  libraries: PlexUploadLibrary[]
}

export interface PlexUploadLibraryOverrideSettings {
  enabled: boolean
  configs: PlexUploadLibraryConfig[]
  global_configs: PlexUploadLibraryConfig[]
}

export interface PlexUploadLibraryOverridePayload {
  enabled: boolean
  configs: PlexUploadLibraryConfig[]
}

export interface PlexUploadCacheEntry {
  file_path: string
  uploaded_to_libraries: string[]
  uploaded_to_library_keys: string[]
  uploaded_editions: string[]
  uploaded_media_types: string[]
}

export interface PlexUploadCacheSummary {
  entries_count: number
  total_library_refs: number
  total_edition_refs: number
  entries: PlexUploadCacheEntry[]
}

export interface PlexUploadCacheClearPayload {
  file_path?: string
}

export interface PlexUploadCacheEntriesQuery {
  q?: string
  limit?: number
  offset?: number
}

export interface PlexUploadCacheEntriesResponse {
  query: string
  total: number
  offset: number
  limit: number
  has_more: boolean
  entries: PlexUploadCacheEntry[]
}

export const runPlexUpload = async (options?: PlexUploadRunOptions): Promise<PlexUploadRunResponse> => {
  const payload = options
    ? {
        dry_run: options.dry_run ?? false,
        reapply: options.reapply ?? false,
        remove_overlay_label: options.remove_overlay_label ?? false,
      }
    : undefined
  return postData('/api/posterflow/plex-upload/run', payload)
}

export const searchPlexUploadSourcePosters = async (
  query: string,
  limit: number = 300,
): Promise<PlexUploadSourceSearchResponse> => {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
  })
  return getData(`/api/posterflow/plex-upload/source-search?${params.toString()}`)
}

export const runPlexSingleUpload = async (payload: PlexSingleUploadPayload): Promise<PlexUploadRunResponse> => {
  return postData('/api/posterflow/plex-upload/run-single', payload)
}

export const searchPlexItems = async (
  query: string,
  mediaType?: 'movie' | 'series' | 'collection',
  limit: number = 25,
): Promise<PlexSearchResponse> => {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  if (mediaType) params.set('media_type', mediaType)
  return getData(`/api/posterflow/plex-upload/plex-search?${params.toString()}`)
}

export const getPlexWebhookSettings = async (): Promise<PlexWebhookSettings> => {
  return getData('/api/posterflow/plex-upload/webhook-settings')
}

export const getPlexManualSettings = async (): Promise<PlexManualSettings> => {
  return getData('/api/posterflow/plex-upload/manual-settings')
}

export const savePlexManualSettings = async (payload: PlexManualSettingsPayload): Promise<PlexManualSettings & { success: boolean }> => {
  return postData('/api/posterflow/plex-upload/manual-settings', payload)
}

export const savePlexWebhookSettings = async (payload: PlexWebhookSettingsPayload): Promise<PlexWebhookSettings & { success: boolean }> => {
  return postData('/api/posterflow/plex-upload/webhook-settings', payload)
}

export const getPlexWebhookStats = async (): Promise<PlexWebhookStats> => {
  return getData('/api/posterflow/plex-upload/webhook-stats')
}

export const resetPlexWebhookStats = async (): Promise<PlexWebhookStats & { success: boolean }> => {
  return postData('/api/posterflow/plex-upload/webhook-stats/reset', {})
}

export const clearPlexWebhookDedupe = async (
  payload: PlexWebhookDedupeClearPayload,
): Promise<PlexWebhookDedupeClearResponse> => {
  return postData('/api/posterflow/plex-upload/webhook-dedupe/clear', payload)
}

export const getPlexWebhookDedupeEntries = async (
  query: string,
  mediaType?: 'movie' | 'series',
  limit: number = 25,
): Promise<PlexWebhookDedupeEntriesResponse> => {
  const params = new URLSearchParams({
    q: query,
    limit: String(limit),
  })
  if (mediaType) {
    params.set('media_type', mediaType)
  }
  return getData(`/api/posterflow/plex-upload/webhook-dedupe/entries?${params.toString()}`)
}

export const getPlexUploadLibraryOverrideSettings = async (): Promise<PlexUploadLibraryOverrideSettings> => {
  return getData('/api/posterflow/plex-upload/library-override')
}

export const savePlexUploadLibraryOverrideSettings = async (
  payload: PlexUploadLibraryOverridePayload,
): Promise<PlexUploadLibraryOverrideSettings & { success: boolean }> => {
  return postData('/api/posterflow/plex-upload/library-override', payload)
}

export const getPlexUploadCache = async (): Promise<PlexUploadCacheSummary> => {
  return getData('/api/posterflow/plex-upload/upload-cache')
}

export const getPlexUploadCacheEntries = async (
  params?: PlexUploadCacheEntriesQuery,
): Promise<PlexUploadCacheEntriesResponse> => {
  const query = new URLSearchParams()
  if (params?.q) {
    query.set('q', params.q)
  }
  if (typeof params?.limit === 'number') {
    query.set('limit', String(params.limit))
  }
  if (typeof params?.offset === 'number') {
    query.set('offset', String(params.offset))
  }

  const qs = query.toString()
  const path = qs
    ? `/api/posterflow/plex-upload/upload-cache/entries?${qs}`
    : '/api/posterflow/plex-upload/upload-cache/entries'
  return getData(path)
}

export const clearPlexUploadCache = async (
  payload?: PlexUploadCacheClearPayload,
): Promise<PlexUploadCacheSummary & { success: boolean; removed: number; cleared_file_path: string | null }> => {
  return postData('/api/posterflow/plex-upload/upload-cache/clear', payload)
}

export const downloadPlexUploadCacheExport = (): string => {
  return `${API_URL}/api/posterflow/plex-upload/upload-cache/export`
}
