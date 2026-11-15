import { useState, useEffect, useCallback } from 'react'
import {
  storeDiscordIdentity, clearCommunityCache, completeClaim, uploadClaimExports, uploadClaimFiles,
  type ClaimUploadProgress, type ClaimUploadResult, type ClaimUploadSettings, type ClaimWhere, type CommunityRequest, type ExportRef, type RequestMatch,
} from '../api/community'
import { getApiErrorMessage } from '../api/client'

const STORAGE_KEY = 'posterflow_discord_auth'
const RESULT_KEY = 'discord_oauth_result'
const SUPABASE_PROJECT = 'qwudwkxfqowjtisdlplv'
const OAUTH_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/discord-oauth`
const UPDATE_STATUS_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/update-request-status`
const UPDATE_LIST_ITEM_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/update-list-item`

const UPLOAD_PART_FILES = 10     // Discord takes ten posters a message; a request this size also passes any reverse proxy
const UPLOAD_PART_GAP_MS = 1500  // between Discord messages, for the channel's rate limit

// An upload that stopped partway, by row. The same posters sent again go on from the part that failed,
// so nothing is posted twice.
interface UnfinishedUpload { signature: string; batch: string; done: number; result: ClaimUploadResult }
const unfinishedUploads = new Map<string, UnfinishedUpload>()

const uploadSignature = (files: File[] | ExportRef[], settings: ClaimUploadSettings): string =>
  JSON.stringify([settings.action, settings.drive, files.map((f) => (f instanceof File ? [f.name, f.size, f.lastModified] : [f.source, f.name]))])

// What the parts of one upload did, added up.
function addUploadResult(total: ClaimUploadResult, part: ClaimUploadResult): void {
  total.posted += part.posted
  total.messages += part.messages
  total.idarr += part.idarr
  total.drive = part.drive || total.drive
  total.idarr_job = part.idarr_job ?? total.idarr_job
  total.did = part.did
  for (const warning of part.warnings) if (!total.warnings.includes(warning)) total.warnings.push(warning)
}

// A call to Posterflow's server, failing with the server's own reason.
async function inServerWords<T>(call: Promise<T>, fallback: string): Promise<T> {
  try {
    return await call
  } catch (err) {
    throw new Error(getApiErrorMessage(err, fallback))
  }
}

interface DiscordAuth {
  token: string
  discord_user_id: string
  discord_username: string
  is_maker: boolean
  is_owner: boolean
  // Holds one of the server's configured moderator roles (DISCORD_MOD_ROLE_IDS).
  is_mod: boolean
  exp: number
}

interface OAuthResult {
  token: string | null
  nonce: string
  error: string | null
}

function generateNonce(): string {
  const arr = new Uint8Array(16)
  crypto.getRandomValues(arr)
  return Array.from(arr, (b) => b.toString(16).padStart(2, '0')).join('')
}

function parseStoredToken(token: string): Omit<DiscordAuth, 'token'> | null {
  try {
    const dot = token.lastIndexOf('.')
    if (dot === -1) return null
    // New tokens are UTF-8; older tokens were Latin1 — fall back so their names survive.
    const b64 = token.slice(0, dot)
    let raw: string
    try {
      raw = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    } catch {
      raw = atob(b64)
    }
    const payload = JSON.parse(raw) as DiscordAuth
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null
    return {
      discord_user_id: payload.discord_user_id,
      discord_username: payload.discord_username,
      is_maker: payload.is_maker,
      is_owner: payload.is_owner ?? false,
      is_mod: payload.is_mod ?? false,
      exp: payload.exp,
    }
  } catch {
    return null
  }
}

// Read synchronously so the first render already reflects a logged-in user
// (an effect-based restore flashes the logged-out UI for one frame).
function loadStoredAuth(): DiscordAuth | null {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (!stored) return null
  try {
    const parsed = JSON.parse(stored) as DiscordAuth
    const fields = parseStoredToken(parsed.token)
    if (fields) return { ...fields, token: parsed.token }
  } catch {
    // fall through to cleanup
  }
  localStorage.removeItem(STORAGE_KEY)
  return null
}

export function useDiscordAuth() {
  const [auth, setAuth] = useState<DiscordAuth | null>(loadStoredAuth)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

  // Persist the connected identity + token to the backend so headless scan jobs
  // can reconcile this user's own community-list items. Idempotent; fires on
  // fresh login, on restore-from-storage, and when the token refreshes. Best-effort.
  useEffect(() => {
    if (!auth?.discord_user_id || !auth.token) return
    storeDiscordIdentity(auth.discord_user_id, auth.discord_username, auth.token).catch(() => {})
  }, [auth?.discord_user_id, auth?.discord_username, auth?.token])

  const login = useCallback(() => {
    if (connecting) return
    setConnecting(true)
    setConnectError(null)

    const nonce = generateNonce()
    const state = btoa(JSON.stringify({ nonce, origin: window.location.origin }))
    localStorage.removeItem(RESULT_KEY)

    const popupUrl = `${OAUTH_URL}?state=${encodeURIComponent(state)}`

    const popup = window.open(popupUrl, 'discord-oauth', 'width=500,height=700,popup=1')

    if (!popup) {
      console.warn('[Discord OAuth] Popup was blocked by browser')
      setConnecting(false)
      setConnectError('Popup was blocked — please allow popups for this page and try again.')
      return
    }

    let ticks = 0
    const pollResult = setInterval(() => {
      ticks++
      const raw = localStorage.getItem(RESULT_KEY)

      if (!raw) {
        if (popup.closed) {
          console.warn('[Discord OAuth] Popup closed without writing a result (ticks:', ticks, ')')
          clearInterval(pollResult)
          setConnecting(false)
        }
        return
      }

      clearInterval(pollResult)
      localStorage.removeItem(RESULT_KEY)
      setConnecting(false)

      try {
        const result = JSON.parse(raw) as OAuthResult
        if (result.nonce !== nonce) {
          setConnectError('Authentication failed — nonce mismatch. Please try again.')
          return
        }
        if (result.error || !result.token) {
          setConnectError(`Discord authorization failed (${result.error ?? 'no token'}) — please try again.`)
          return
        }
        const fields = parseStoredToken(result.token)
        if (!fields) {
          setConnectError('Invalid authentication token — please try again.')
          return
        }
        const newAuth: DiscordAuth = { ...fields, token: result.token }
        setAuth(newAuth)
        localStorage.setItem(STORAGE_KEY, JSON.stringify(newAuth))
      } catch (e) {
        console.error('[Discord OAuth] Failed to parse result:', e)
        setConnectError('Authentication failed — please try again.')
      }
    }, 300)
  }, [connecting])

  const logout = useCallback(() => {
    setAuth(null)
    setConnectError(null)
    localStorage.removeItem(STORAGE_KEY)
  }, [])

  // Upload posters for a request or list item: files from this computer, or exports the server
  // already has (ticked in the upload picker). `settings` is what Upload is set to on screen.
  // Posterflow's server does it, as this login. An IDarr-only upload needs no login.
  // They go ten a request (one Discord message each), so no request is large or slow; `onProgress`
  // hears which one is going out when there is more than one.
  const uploadForClaim = useCallback(
    async (where: ClaimWhere, id: string, files: File[] | ExportRef[], settings: ClaimUploadSettings,
      onProgress?: (progress: ClaimUploadProgress) => void): Promise<ClaimUploadResult> => {
      const token = auth?.token ?? null
      const fromComputer = files[0] instanceof File
      const of = Math.max(1, Math.ceil(files.length / UPLOAD_PART_FILES))
      const key = `${where}/${id}`, signature = uploadSignature(files, settings)
      const kept = unfinishedUploads.get(key)
      const upload: UnfinishedUpload = kept && kept.signature === signature ? kept
        : { signature, batch: generateNonce(), done: 0, result: { posted: 0, messages: 0, idarr: 0, drive: '', idarr_job: null, warnings: [], did: '' } }
      unfinishedUploads.set(key, upload)
      for (let n = upload.done + 1; n <= of; n++) {
        const these = files.slice((n - 1) * UPLOAD_PART_FILES, n * UPLOAD_PART_FILES)
        const part = of > 1 ? { batch: upload.batch, part: n, of } : null
        if (of > 1) onProgress?.({ message: n, of })
        const did = await inServerWords(
          fromComputer
            ? uploadClaimFiles(where, id, these as File[], settings, token, part)
            : uploadClaimExports(where, id, these as ExportRef[], settings, token, part),
          'Upload failed',
        )
        addUploadResult(upload.result, did)
        upload.done = n
        if (n < of && did.posted) await new Promise((resolve) => setTimeout(resolve, UPLOAD_PART_GAP_MS))
      }
      unfinishedUploads.delete(key)
      return upload.result
    },
    [auth],
  )

  // Request status actions go to the community service, except Complete: Posterflow's server does
  // that one, the same action the editor panels' Complete runs.
  const updateRequestStatus = useCallback(
    async (
      requestId: string,
      action: 'claim' | 'complete' | 'reject' | 'close' | 'remove' | 'release',
      message?: string,
    ): Promise<{ status: string; claimed_by: string | null; fulfilled_by: string | null }> => {
      if (!auth?.token) throw new Error('Not connected to Discord')
      if (action === 'complete') return inServerWords(completeClaim('requests', requestId, auth.token), 'Action "complete" failed')

      const resp = await fetch(UPDATE_STATUS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: auth.token, request_id: requestId, action, ...(message ? { message } : {}) }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error((data as { error?: string }).error ?? `Action "${action}" failed`)
      }
      // The edge function mutated Supabase behind the backend's back — drop the
      // backend's read cache so the next reload reflects it.
      void clearCommunityCache().catch(() => {})
      return data as { status: string; claimed_by: string | null; fulfilled_by: string | null }
    },
    [auth],
  )

  // Owner/moderator edits that return the whole updated row. Same endpoint as
  // the status actions.
  const moderateRequest = useCallback(
    async (requestId: string, action: 'match' | 'upgrade', extra: Record<string, unknown> = {}): Promise<CommunityRequest> => {
      if (!auth?.token) throw new Error('Not connected to Discord')

      const resp = await fetch(UPDATE_STATUS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: auth.token, request_id: requestId, action, ...extra }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error((data as { error?: string }).error ?? `Action "${action}" failed`)
      }
      void clearCommunityCache().catch(() => {})
      return (data as { request: CommunityRequest }).request
    },
    [auth],
  )

  // Link an id-less (custom) request to a TMDB/TVDB entry.
  const matchRequest = useCallback(
    (requestId: string, match: RequestMatch) => moderateRequest(requestId, 'match', { match }),
    [moderateRequest],
  )

  // Turn an active season request into a show-level one (series + all seasons).
  const upgradeRequest = useCallback(
    (requestId: string) => moderateRequest(requestId, 'upgrade'),
    [moderateRequest],
  )

  // Community list-item actions — mirrors updateRequestStatus, different endpoint (Complete goes
  // through Posterflow's server here too).
  // 'remove' is publisher-or-owner; 'remove_mine' (no itemId) clears all the
  // caller's own items; the maker actions require the Maker role.
  const updateListItem = useCallback(
    async (
      itemId: string | null,
      action: 'claim' | 'complete' | 'release' | 'reject' | 'remove' | 'remove_mine',
    ): Promise<{ status?: string; claimed_by?: string | null; claimed_by_discord_id?: string | null; removed?: boolean | number }> => {
      if (!auth?.token) throw new Error('Not connected to Discord')
      if (action === 'complete' && itemId) return inServerWords(completeClaim('list-items', itemId, auth.token), 'Action "complete" failed')

      const resp = await fetch(UPDATE_LIST_ITEM_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: auth.token, item_id: itemId ?? undefined, action }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error((data as { error?: string }).error ?? `Action "${action}" failed`)
      }
      void clearCommunityCache().catch(() => {})
      return data as { status?: string; claimed_by?: string | null; claimed_by_discord_id?: string | null; removed?: boolean | number }
    },
    [auth],
  )

  return {
    isConnected: auth !== null,
    isMaker: auth?.is_maker ?? false,
    isOwner: auth?.is_owner ?? false,
    isMod: auth?.is_mod ?? false,
    username: auth?.discord_username ?? null,
    discordUserId: auth?.discord_user_id ?? null,
    token: auth?.token ?? null,
    connecting,
    connectError,
    login,
    logout,
    uploadForClaim,
    updateRequestStatus,
    matchRequest,
    upgradeRequest,
    updateListItem,
  }
}
