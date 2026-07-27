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
  // Soft cross-sync marker set when a matching community LIST item is completed.
  // The request stays open; the board shows a "verify & close" callout.
  list_completed_by?: string | null
  list_completed_at?: string | null
  // Set when the requester archives the thread; keeps the "Archived" button state
  // across reloads.
  thread_archived_at?: string | null
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
  // 'upgraded' = an open season request for this series was upgraded to
  // show-level in place (a show request covers its seasons).
  status: 'created' | 'already_requested' | 'upgraded'
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

// Styles (CL2K/MM2K) an item has already been fulfilled in — powers the
// "already made" warning in the request modals. "" = fulfilled, style unknown.
export const getFulfilledRequestStyles = (
  params: Record<string, string | number>,
): Promise<{ styles: string[] }> =>
  getData('/api/community/requests/fulfilled-styles', { params })

// Persist the connected Discord identity + token so headless scan jobs can
// reconcile this user's own published list items (remove ones resolved locally).
// The token authorizes removal via the same edge function a manual remove uses.
export const storeDiscordIdentity = (
  discord_user_id: string,
  discord_username?: string | null,
  discord_token?: string | null,
): Promise<{ ok: boolean }> =>
  postData('/api/community/identity', { discord_user_id, discord_username, discord_token })

// ── Community Lists ──────────────────────────────────────────────────────────

export interface CommunityListItem {
  id: string
  tmdb_id: number | null
  media_type: 'movie' | 'show' | 'season' | 'collection'
  title: string
  year: number | null
  season_number: number | null
  poster_path: string | null
  imdb_id: string | null
  tvdb_id: number | null
  style_tag: string | null
  source: 'unmatched' | 'style_fallback' | null
  notes: string | null
  status: 'open' | 'in_progress' | 'fulfilled' | 'rejected'
  claimed_by: string | null
  claimed_by_discord_id: string | null
  fulfilled_by: string | null
  fulfilled_at: string | null
  created_at: string
  updated_at: string
  // Embedded by the backend read — who wants this poster ("wanted by N").
  poster_list_wanters?: ListItemWanter[] | null
  // A wanter's workflow found this poster made → it's in a drive; the rest will
  // get it on their next run.
  available_in_drive?: boolean
}

export interface ListItemWanter {
  discord_id: string
  name: string | null
}

// One item to publish to a community list. The publisher is attached as a wanter
// server-side from the verified Discord token, never from client fields.
export interface ListItemInput {
  tmdb_id?: number | null
  media_type: string
  title: string
  year?: number | null
  season_number?: number | null
  poster_path?: string | null
  imdb_id?: string | null
  tvdb_id?: number | null
  style_tag?: string | null
  source?: 'unmatched' | 'style_fallback'
  notes?: string | null
}

export interface SubmitListItemsPayload {
  items: ListItemInput[]
  discord_token: string
}

export interface SubmitListItemsResponse {
  inserted: number
  skipped: number
  message?: string
}

export interface CommunityListOwner {
  id: string
  name: string
  count: number
}

export const getCommunityListItems = (
  params?: Record<string, string | number>,
): Promise<{ items: CommunityListItem[]; total: number }> =>
  getData('/api/community/lists', { params })

export const getCommunityListOwners = (
  params?: Record<string, string | number>,
): Promise<{ owners: CommunityListOwner[] }> =>
  getData('/api/community/lists/owners', { params })

export const submitCommunityListItems = (payload: SubmitListItemsPayload): Promise<SubmitListItemsResponse> =>
  postData('/api/community/lists', payload)
