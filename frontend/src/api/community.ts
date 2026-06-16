import { getData, postData } from './http'

export interface CommunityRequest {
  id: string
  tmdb_id: number | null
  media_type: 'movie' | 'show' | 'season' | 'collection' | 'person'
  title: string
  year: number | null
  season_number: number | null
  poster_path: string | null
  imdb_id: string | null
  tvdb_id: number | null
  status: 'pending' | 'in_progress' | 'fulfilled' | 'rejected'
  claimed_by: string | null
  claimed_by_discord_id: string | null
  fulfilled_by: string | null
  requested_by: string | null
  requested_by_discord_id: string | null
  discord_thread_url: string | null
  notes?: string | null
  style_tags?: string[] | null
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
  ping_discord_id?: string | null  // stores username; resolved to ID by notify-discord
  // Signed Discord token from useDiscordAuth — required; the server derives
  // the requester's Discord identity from it.
  discord_token: string
}

export interface SubmitRequestResponse {
  status: 'created' | 'already_requested'
  request_id: string
}

export const getCommunityRequestCount = (): Promise<{ count: number }> =>
  getData('/api/community/requests/count')

// The connected user's own active-request counts, for the requester sidebar badges.
export const getMyCommunityRequestCounts = (discordId: string): Promise<{ pending: number; in_progress: number }> =>
  getData(`/api/community/requests/my-counts?discord_id=${encodeURIComponent(discordId)}`)

export const getCommunityRequests = (
  params?: Record<string, string | number>,
): Promise<{ requests: CommunityRequest[] }> =>
  getData('/api/community/requests', { params })

export const submitCommunityRequest = (payload: SubmitRequestPayload): Promise<SubmitRequestResponse> =>
  postData('/api/community/requests', payload)
