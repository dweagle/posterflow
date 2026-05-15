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