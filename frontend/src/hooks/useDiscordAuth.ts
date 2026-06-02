import { useState, useEffect, useCallback } from 'react'

const STORAGE_KEY = 'posterflow_discord_auth'
const RESULT_KEY = 'discord_oauth_result'
const SUPABASE_PROJECT = 'qwudwkxfqowjtisdlplv'
const OAUTH_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/discord-oauth`
const POST_POSTER_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/post-poster`
const UPDATE_STATUS_URL = `https://${SUPABASE_PROJECT}.supabase.co/functions/v1/update-request-status`

interface DiscordAuth {
  token: string
  discord_user_id: string
  discord_username: string
  is_maker: boolean
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
    const payload = JSON.parse(atob(token.slice(0, dot))) as DiscordAuth
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null
    return {
      discord_user_id: payload.discord_user_id,
      discord_username: payload.discord_username,
      is_maker: payload.is_maker,
      exp: payload.exp,
    }
  } catch {
    return null
  }
}

export function useDiscordAuth() {
  const [auth, setAuth] = useState<DiscordAuth | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [connectError, setConnectError] = useState<string | null>(null)

  // Load persisted auth on mount
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return
    try {
      const parsed = JSON.parse(stored) as DiscordAuth
      const fields = parseStoredToken(parsed.token)
      if (fields) setAuth({ ...fields, token: parsed.token })
      else localStorage.removeItem(STORAGE_KEY)
    } catch {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [])

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

  const uploadPoster = useCallback(
    async (requestId: string, files: File[]): Promise<void> => {
      if (!auth?.token) throw new Error('Not connected to Discord')

      const form = new FormData()
      form.append('token', auth.token)
      form.append('request_id', requestId)
      for (const file of files) {
        form.append('file', file)
      }

      const resp = await fetch(POST_POSTER_URL, { method: 'POST', body: form })
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}))
        throw new Error((data as { error?: string }).error ?? 'Upload failed')
      }
    },
    [auth],
  )

  const updateRequestStatus = useCallback(
    async (
      requestId: string,
      action: 'claim' | 'complete' | 'reject' | 'close',
      message?: string,
    ): Promise<{ status: string; claimed_by: string | null; fulfilled_by: string | null }> => {
      if (!auth?.token) throw new Error('Not connected to Discord')

      const resp = await fetch(UPDATE_STATUS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: auth.token, request_id: requestId, action, ...(message ? { message } : {}) }),
      })
      const data = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        throw new Error((data as { error?: string }).error ?? `Action "${action}" failed`)
      }
      return data as { status: string; claimed_by: string | null; fulfilled_by: string | null }
    },
    [auth],
  )

  return {
    isConnected: auth !== null,
    isMaker: auth?.is_maker ?? false,
    username: auth?.discord_username ?? null,
    discordUserId: auth?.discord_user_id ?? null,
    token: auth?.token ?? null,
    connecting,
    connectError,
    login,
    logout,
    uploadPoster,
    updateRequestStatus,
  }
}
