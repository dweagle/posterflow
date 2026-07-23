import { API_URL, getData, postData } from './http'

export interface PlexUploadRunOptions {
  dry_run?: boolean
  reapply?: boolean
  remove_overlay_label?: boolean
  sync_before_upload?: boolean
  rename_before_upload?: boolean
  border_before_upload?: boolean
}

export interface PlexUploadRunResponse {
  success: boolean
  job_id: number
  status: string
  message: string
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
  rename_before_upload?: boolean
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
  artwork: boolean
  rename_then_upload: boolean
  adopt_existing_processed: boolean
  retry_attempts: number
  retry_delay_seconds: number
  upload_delay_ms: number
}

export interface PlexWebhookSettingsPayload {
  enabled: boolean
  remove_overlay_label: boolean
  artwork: boolean
  rename_then_upload: boolean
  adopt_existing_processed: boolean
  retry_attempts: number
  retry_delay_seconds: number
  upload_delay_ms: number
}

export interface PlexManualSettings {
  dry_run: boolean
  reapply: boolean
  remove_overlay_label: boolean
  sync_before_upload: boolean
  rename_before_upload: boolean
  border_before_upload: boolean
  upload_delay_ms: number
  /** Also governs the workflow's upload step; the webhook has its own artwork toggle. */
  upload_artwork: boolean
}

export interface PlexManualSettingsPayload {
  dry_run: boolean
  reapply: boolean
  remove_overlay_label: boolean
  sync_before_upload: boolean
  rename_before_upload: boolean
  border_before_upload: boolean
  upload_delay_ms: number
  upload_artwork: boolean
}

export interface PlexWebhookUnknownInstanceToken {
  count: number
  source: string
  last_seen: string | null
}

export interface PlexWebhookStats {
  received: number
  queued: number
  duplicates: number
  skipped_test: number
  skipped_cached: number
  skipped_no_asset: number
  rejected_disabled: number
  parse_errors: number
  internal_errors: number
  last_event_at: string | null
  last_queued_at: string | null
  last_error: string | null
  // Webhook ?instance= names that match no configured arr instance (stale URL after a rename)
  unknown_instance_tokens?: Record<string, PlexWebhookUnknownInstanceToken>
}

export interface PlexWebhookDedupeClearPayload {
  clear_all?: boolean
  instance?: string
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
  instance?: string | null
  source: string
  event_type: string
  media_type: 'movie' | 'series' | string
  media_id?: string | null
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

export interface PlexUploadInstanceMapEntry {
  plex_instance: string
  library_key: string
}

export type PlexUploadInstanceMap = Record<string, PlexUploadInstanceMapEntry[]>

export const getPlexUploadInstanceMap = async (): Promise<{ map: PlexUploadInstanceMap }> => {
  return getData('/api/settings/plex-upload-instance-map')
}

export const savePlexUploadInstanceMap = async (
  map: PlexUploadInstanceMap,
): Promise<{ message: string; map: PlexUploadInstanceMap }> => {
  return postData('/api/settings/plex-upload-instance-map', { map })
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
        sync_before_upload: options.sync_before_upload ?? false,
        rename_before_upload: options.rename_before_upload ?? false,
        border_before_upload: options.border_before_upload ?? false,
      }
    : undefined
  return postData('/api/posterflow/plex-upload/run', payload)
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

export interface PlexWebhookTokenResponse {
  token: string
  password_set: boolean
}

export const getPlexWebhookToken = async (): Promise<PlexWebhookTokenResponse> => {
  return getData('/api/posterflow/plex-upload/webhook-token')
}

export const regeneratePlexWebhookToken = async (): Promise<PlexWebhookTokenResponse & { success: boolean }> => {
  return postData('/api/posterflow/plex-upload/webhook-token/regenerate', {})
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
