import { getData, postData } from './http'

export interface CommunityRequest {
  id: string
  tmdb_id: number | null
  media_type: 'movie' | 'show' | 'season' | 'collection'
  title: string
  year: number | null
  season_number: number | null
  poster_path: string | null
  imdb_id: string | null
  tvdb_id: number | null
  status: 'pending' | 'in_progress' | 'fulfilled' | 'rejected'
  claimed_by: string | null
  fulfilled_by: string | null
  requested_by: string | null
  discord_thread_url: string | null
  notes?: string | null
  created_at: string
  updated_at: string
}

export interface SubmitRequestPayload {
  tmdb_id?: number | null
  media_type: string
  title: string
  year?: number | null
  season_number?: number | null
  poster_path?: string | null
  imdb_id?: string | null
  tvdb_id?: number | null
  notes?: string | null
  style_tags?: string[]
  requested_by?: string | null
}

export interface SubmitRequestResponse {
  status: 'created' | 'already_requested'
  request_id: string
}

export const getCommunityRequests = (
  params?: Record<string, string | number>,
): Promise<{ requests: CommunityRequest[] }> =>
  getData('/api/community/requests', { params })

export const submitCommunityRequest = (payload: SubmitRequestPayload): Promise<SubmitRequestResponse> =>
  postData('/api/community/requests', payload)
