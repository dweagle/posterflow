/**
 * update-request-status
 *
 * Allows a verified poster maker to claim, complete, or reject a poster
 * request directly from the PosterFlow UI (without going to Discord).
 *
 * Flow:
 *   1. Verify the signed maker token (HMAC-SHA256, issued by discord-oauth)
 *   2. Re-check the Discord guild role live so revoking the role blocks access
 *   3. Fetch the current request row to validate the action against its status
 *   4. Update the poster_requests row in Supabase
 *   5. Post a status message to the Discord thread (if one exists)
 *
 * Collision guard:
 *   - "claim" is only allowed when status = 'pending' (atomic update prevents races)
 *   - "complete" is only allowed when status = 'in_progress'
 *   - "reject" is allowed on 'pending' or 'in_progress'
 *
 * Required Supabase secrets:
 *   DISCORD_BOT_TOKEN
 *   DISCORD_GUILD_ID
 *   DISCORD_MAKER_ROLE_ID
 *   DISCORD_JWT_SECRET
 *   SUPABASE_URL              (auto-provided)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-provided)
 *
 * Request body (JSON):
 *   token      - Signed maker token from discord-oauth
 *   request_id - UUID of the poster_request row
 *   action     - "claim" | "complete" | "reject"
 */

const BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID')!
const MAKER_ROLE_ID = Deno.env.get('DISCORD_MAKER_ROLE_ID')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

const VALID_ACTIONS = new Set(['claim', 'complete', 'reject', 'close', 'remove', 'release'])

// ── Token verification (same as post-poster) ───────────────────────────────

interface MakerPayload {
  discord_user_id: string
  discord_username: string
  is_maker: boolean
  exp: number
}

async function verifyToken(token: string): Promise<MakerPayload | null> {
  const dot = token.lastIndexOf('.')
  if (dot === -1) return null
  const b64 = token.slice(0, dot)
  const sig = token.slice(dot + 1)

  let key: CryptoKey
  try {
    key = await crypto.subtle.importKey(
      'raw',
      new TextEncoder().encode(JWT_SECRET),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify'],
    )
  } catch {
    return null
  }

  const sigBytes = new Uint8Array((sig.match(/.{1,2}/g) ?? []).map((b) => parseInt(b, 16)))
  const valid = await crypto.subtle.verify('HMAC', key, sigBytes, new TextEncoder().encode(b64))
  if (!valid) return null

  try {
    // New tokens are UTF-8; older tokens were Latin1 — fall back so their names survive.
    let raw: string
    try {
      raw = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    } catch {
      raw = atob(b64)
    }
    const payload = JSON.parse(raw) as MakerPayload
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null
    return payload
  } catch {
    return null
  }
}

// ── CORS / response helpers ────────────────────────────────────────────────

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  })
}

// ── Supabase helpers ───────────────────────────────────────────────────────

const SB_HEADERS = {
  apikey: SUPABASE_SERVICE_ROLE_KEY,
  Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
  'Content-Type': 'application/json',
  Prefer: 'return=representation',
}

interface RequestRow {
  id: string
  status: string
  tmdb_id: number | null
  media_type: string
  season_number: number | null
  discord_message_id: string | null
  title: string
  requested_by_discord_id: string | null
  claimed_by_discord_id: string | null
  created_at: string | null
}

async function fetchRequest(requestId: string): Promise<RequestRow | null> {
  const resp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}&select=id,status,tmdb_id,media_type,season_number,discord_message_id,title,requested_by_discord_id,claimed_by_discord_id,created_at&limit=1`,
    { headers: SB_HEADERS },
  )
  if (!resp.ok) return null
  const rows = await resp.json() as RequestRow[]
  return rows[0] ?? null
}

// Hard-delete a request row (used by the requester/owner "remove" action).
async function deleteRequestRow(requestId: string): Promise<boolean> {
  const resp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}`,
    { method: 'DELETE', headers: SB_HEADERS },
  )
  if (!resp.ok) {
    console.error('[update-request-status] DELETE failed:', await resp.text())
    return false
  }
  return true
}

// Best-effort refund of the requester's per-user daily submission slot. Only
// meaningful when the request was created on the current UTC day (the counter
// resets daily). Calls the decrement_user_limit RPC (applied out-of-band).
async function refundUserLimit(discordUserId: string, createdAt: string | null): Promise<void> {
  if (!discordUserId || !createdAt) return
  const created = new Date(createdAt)
  const now = new Date()
  const sameUtcDay =
    created.getUTCFullYear() === now.getUTCFullYear() &&
    created.getUTCMonth() === now.getUTCMonth() &&
    created.getUTCDate() === now.getUTCDate()
  if (!sameUtcDay) return
  try {
    const resp = await fetch(`${SUPABASE_URL}/rest/v1/rpc/decrement_user_limit`, {
      method: 'POST',
      headers: SB_HEADERS,
      body: JSON.stringify({ p_user_id: discordUserId }),
    })
    if (!resp.ok) console.error('[update-request-status] refundUserLimit failed:', await resp.text())
  } catch (e) {
    console.error('[update-request-status] refundUserLimit error:', e)
  }
}

async function updateRequest(
  requestId: string,
  patch: Record<string, unknown>,
  statusCondition: string,
): Promise<boolean> {
  // Conditional update — only applies if status matches the expected value.
  // This prevents race conditions (two makers claiming at the same time).
  const resp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}&status=eq.${statusCondition}`,
    {
      method: 'PATCH',
      headers: SB_HEADERS,
      body: JSON.stringify(patch),
    },
  )
  if (!resp.ok) {
    console.error('[update-request-status] Supabase PATCH failed:', await resp.text())
    return false
  }
  const rows = await resp.json() as unknown[]
  // If no rows returned, the status condition didn't match — another maker got there first
  return rows.length > 0
}

// Cross-sync: a fulfilled request means the poster now exists, so mark every
// matching community-list item (any owner) fulfilled — keeps it out of the
// active worklist while preserving history/credit. Best-effort; never blocks
// the request completion.
async function fulfillMatchingListItems(
  row: RequestRow,
  makerName: string,
  makerId: string,
): Promise<void> {
  try {
    const params = new URLSearchParams()
    params.set('media_type', `eq.${row.media_type}`)
    params.set('status', 'in.(open,in_progress)')
    if (row.tmdb_id != null) {
      params.set('tmdb_id', `eq.${row.tmdb_id}`)
      if (row.media_type === 'season' && row.season_number != null) {
        params.set('season_number', `eq.${row.season_number}`)
      } else {
        params.set('season_number', 'is.null')
      }
    } else {
      params.set('tmdb_id', 'is.null')
      params.set('title', `ilike.${row.title}`)
    }
    const now = new Date().toISOString()
    const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?${params}`, {
      method: 'PATCH',
      headers: { ...SB_HEADERS, Prefer: 'return=minimal' },
      body: JSON.stringify({
        status: 'fulfilled',
        fulfilled_by: makerName,
        fulfilled_by_discord_id: makerId,
        fulfilled_at: now,
        updated_at: now,
      }),
    })
    if (!resp.ok) console.error('[update-request-status] list mirror failed:', await resp.text())
  } catch (e) {
    console.error('[update-request-status] list mirror error:', e)
  }
}

// ── Discord helpers ────────────────────────────────────────────────────────

async function postThreadMessage(
  channelId: string,
  content: string,
  allowedMentions?: Record<string, unknown>,
): Promise<void> {
  const body: Record<string, unknown> = { content }
  if (allowedMentions) body.allowed_mentions = allowedMentions
  const resp = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
    method: 'POST',
    headers: {
      Authorization: `Bot ${BOT_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    console.error('[update-request-status] Discord message post failed:', await resp.text())
  }
}

/**
 * Update the starter message of a Discord forum thread to reflect the new status.
 * Fetches the current embed first so we can update the Status field and color
 * while preserving all other embed content (title, links, thumbnail, footer, etc.).
 * In forum threads the starter message ID equals the thread channel ID.
 */
async function updateStarterMessage(
  threadId: string,
  action: string,
  makerName: string,
  components: unknown[],
): Promise<void> {
  // Fetch the current starter message to get the existing embed
  const msgResp = await fetch(
    `https://discord.com/api/v10/channels/${threadId}/messages/${threadId}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!msgResp.ok) {
    console.error('[update-request-status] Failed to fetch starter message:', await msgResp.text())
    return
  }
  const msg = await msgResp.json() as { embeds?: Record<string, unknown>[] }
  const originalEmbed: Record<string, unknown> = msg.embeds?.[0] ?? {}

  const statusLabel =
    action === 'claim'   ? `🎨 Claimed by ${makerName}` :
    action === 'reject'  ? `❌ Rejected by ${makerName}` :
    action === 'release' ? `🟢 Open for claims` :
    `✅ Completed by ${makerName}`
  const newColor =
    action === 'claim'   ? 0xffb74d :
    action === 'reject'  ? 0x9e9e9e :
    action === 'release' ? 0x64b5f6 :
    0x4caf50

  const updatedEmbed = {
    ...originalEmbed,
    color: newColor,
    fields: ((originalEmbed.fields ?? []) as { name: string; value: string; inline?: boolean }[]).map((f) =>
      f.name === 'Status' ? { ...f, value: statusLabel } : f
    ),
  }

  const patchResp = await fetch(
    `https://discord.com/api/v10/channels/${threadId}/messages/${threadId}`,
    {
      method: 'PATCH',
      headers: {
        Authorization: `Bot ${BOT_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ embeds: [updatedEmbed], components }),
    },
  )
  if (!patchResp.ok) {
    console.error('[update-request-status] Discord embed PATCH failed:', await patchResp.text())
  }
}

async function checkMakerRole(discordUserId: string): Promise<boolean> {
  const resp = await fetch(
    `https://discord.com/api/v10/guilds/${GUILD_ID}/members/${discordUserId}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!resp.ok) return false
  const member = await resp.json() as { roles: string[] }
  return member.roles.includes(MAKER_ROLE_ID)
}

// The Discord server owner is exempt from the claimer-only completion rule.
async function getGuildOwnerId(): Promise<string | null> {
  const resp = await fetch(
    `https://discord.com/api/v10/guilds/${GUILD_ID}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!resp.ok) return null
  const guild = await resp.json() as { owner_id?: string }
  return guild.owner_id ?? null
}

// ── Handler ────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS })
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405)

  let body: { token?: string; request_id?: string; action?: string; message?: string }
  try {
    body = await req.json()
  } catch {
    return json({ error: 'Invalid JSON body' }, 400)
  }

  const { token, request_id, action, message } = body

  if (!token || !request_id || !action) {
    return json({ error: 'Missing required fields: token, request_id, action' }, 400)
  }
  if (!VALID_ACTIONS.has(action)) {
    return json({ error: 'Invalid action — must be claim, complete, reject, close, remove, or release' }, 400)
  }
  // request_id is interpolated into PostgREST query strings, so require a strict
  // UUID — this blocks query-parameter injection via crafted ids.
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(request_id)) {
    return json({ error: 'Invalid request_id' }, 400)
  }

  // ── Step 1: Verify token signature and expiry ──────────────────────────────
  const maker = await verifyToken(token)
  if (!maker) {
    return json({ error: 'Invalid or expired token — please reconnect your Discord account' }, 401)
  }

  // ── Close action: archives the Discord thread, no status change ───────────
  if (action === 'close') {
    const row = await fetchRequest(request_id)
    if (!row) return json({ error: 'Request not found' }, 404)

    const { discord_message_id, requested_by_discord_id } = row
    if (maker.discord_user_id !== requested_by_discord_id) {
      return json({ error: 'Only the person who submitted this request can archive it' }, 403)
    }

    // Persist the archive so the UI keeps showing "Archived" after a reload (the
    // Discord lock below is best-effort/background; this is the source of truth).
    const archiveResp = await fetch(`${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${request_id}`, {
      method: 'PATCH',
      headers: { ...SB_HEADERS, Prefer: 'return=minimal' },
      body: JSON.stringify({ thread_archived_at: new Date().toISOString() }),
    })
    if (!archiveResp.ok) {
      console.error('[update-request-status] archive flag persist failed:', await archiveResp.text())
    }

    if (discord_message_id) {
      const safeMessage = typeof message === 'string' ? message.trim().slice(0, 300) : ''
      const bgTask = (async () => {
        if (safeMessage) {
          await postThreadMessage(discord_message_id, `\n${safeMessage}\n`)
          await new Promise((resolve) => setTimeout(resolve, 400))
        }
        await postThreadMessage(
          discord_message_id,
          `🔒 Thread archived by ${maker.discord_username} (requester).`,
        )
        await new Promise((resolve) => setTimeout(resolve, 500))
        const lockResp = await fetch(`https://discord.com/api/v10/channels/${discord_message_id}`, {
          method: 'PATCH',
          headers: { Authorization: `Bot ${BOT_TOKEN}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ locked: true, archived: true }),
        })
        if (!lockResp.ok) {
          console.error('[update-request-status] archive lock failed:', await lockResp.text())
        }
      })().catch(console.error)
      try {
        // @ts-ignore
        EdgeRuntime.waitUntil(bgTask)
      } catch {
        await bgTask
      }
    }

    return json({ ok: true })
  }

  // ── Remove action: requester (or server owner) deletes the request ────────
  if (action === 'remove') {
    const row = await fetchRequest(request_id)
    if (!row) return json({ error: 'Request not found' }, 404)

    const isRequester = maker.discord_user_id === row.requested_by_discord_id
    let allowed = isRequester
    if (!allowed) {
      const ownerId = await getGuildOwnerId()
      allowed = maker.discord_user_id === ownerId
    }
    if (!allowed) {
      return json({ error: 'Only the requester or server owner can remove this request' }, 403)
    }
    if (row.status === 'fulfilled') {
      return json({ error: 'A completed request can no longer be removed' }, 409)
    }

    // Delete first — if this fails (e.g. missing privilege) we bail *before*
    // touching Discord, so we never archive a thread for a request that's still here.
    const deleted = await deleteRequestRow(request_id)
    if (!deleted) return json({ error: 'Failed to remove the request' }, 500)

    // Refund the requester's per-user daily slot (only if removed the same UTC day).
    await refundUserLimit(row.requested_by_discord_id ?? '', row.created_at)

    // Lock + archive the Discord thread with a note (we keep the thread, like Archive).
    if (row.discord_message_id) {
      const channelId = row.discord_message_id
      const bgTask = (async () => {
        await postThreadMessage(
          channelId,
          `🗑️ Request removed by ${maker.discord_username}${isRequester ? ' (requester)' : ' (moderator)'}.`,
        )
        await new Promise((resolve) => setTimeout(resolve, 500))
        const lockResp = await fetch(`https://discord.com/api/v10/channels/${channelId}`, {
          method: 'PATCH',
          headers: { Authorization: `Bot ${BOT_TOKEN}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ locked: true, archived: true }),
        })
        if (!lockResp.ok) console.error('[update-request-status] remove archive failed:', await lockResp.text())
      })().catch(console.error)
      try {
        // @ts-ignore - EdgeRuntime is a Supabase-specific global
        EdgeRuntime.waitUntil(bgTask)
      } catch {
        await bgTask
      }
    }

    return json({ ok: true, removed: true })
  }

  // ── Release action: the claiming maker (or owner) un-claims it ────────────
  if (action === 'release') {
    const row = await fetchRequest(request_id)
    if (!row) return json({ error: 'Request not found' }, 404)
    if (row.status !== 'in_progress') {
      return json({ error: 'Only a claimed (in-progress) request can be released' }, 409)
    }
    const isClaimer = maker.discord_user_id === row.claimed_by_discord_id
    let allowed = isClaimer
    if (!allowed) {
      const ownerId = await getGuildOwnerId()
      allowed = maker.discord_user_id === ownerId
    }
    if (!allowed) {
      return json({ error: 'Only the maker who claimed this request can release it' }, 403)
    }

    const released = await updateRequest(
      request_id,
      { status: 'pending', claimed_by: null, claimed_by_discord_id: null },
      'in_progress',
    )
    if (!released) return json({ error: 'This request was just updated — please refresh' }, 409)

    // Revert the Discord post to open: restore the buttons and post a note.
    if (row.discord_message_id) {
      const channelId = row.discord_message_id
      const freshComponents = [{
        type: 1,
        components: [
          { type: 2, style: 2, label: 'Claim Poster', custom_id: `claim:${request_id}`, emoji: { name: '🎨' } },
          { type: 2, style: 3, label: 'Mark Complete', custom_id: `complete:${request_id}:`, emoji: { name: '✅' } },
          { type: 2, style: 4, label: '✕ Reject', custom_id: `reject:${request_id}` },
        ],
      }]
      const bgTask = Promise.all([
        postThreadMessage(channelId, `🟢 **Released by ${maker.discord_username}** — open for claims again.`),
        updateStarterMessage(channelId, 'release', maker.discord_username, freshComponents),
      ]).catch(console.error)
      try {
        // @ts-ignore - EdgeRuntime is a Supabase-specific global
        EdgeRuntime.waitUntil(bgTask)
      } catch {
        await bgTask
      }
    }

    return json({ ok: true, status: 'pending', claimed_by: null, claimed_by_discord_id: null })
  }

  // ── Step 2: Re-check Discord role live (maker actions only) ───────────────
  const hasMakerRole = await checkMakerRole(maker.discord_user_id)
  if (!hasMakerRole) {
    return json({ error: 'You no longer have the Poster Maker role' }, 403)
  }

  // ── Step 3: Fetch current request ─────────────────────────────────────────
  const row = await fetchRequest(request_id)
  if (!row) {
    return json({ error: 'Request not found' }, 404)
  }

  // ── Step 4: Validate action against current status ────────────────────────
  const { status, discord_message_id, requested_by_discord_id, claimed_by_discord_id } = row

  if (action === 'claim' && status !== 'pending') {
    const msg = status === 'in_progress'
      ? 'This request has already been claimed by another maker'
      : 'This request is no longer available to claim'
    return json({ error: msg }, 409)
  }
  if (action === 'complete' && status !== 'in_progress') {
    return json({ error: 'Only in-progress requests can be marked as complete' }, 409)
  }
  // Completion is restricted to the maker who claimed it — except the server owner.
  if (action === 'complete' && claimed_by_discord_id !== maker.discord_user_id) {
    const ownerId = await getGuildOwnerId()
    if (maker.discord_user_id !== ownerId) {
      return json({ error: 'Only the maker who claimed this request can mark it complete' }, 403)
    }
  }
  if (action === 'reject' && !['pending', 'in_progress'].includes(status)) {
    return json({ error: 'This request cannot be rejected in its current state' }, 409)
  }

  // ── Step 5: Build the database patch ──────────────────────────────────────
  const now = new Date().toISOString()
  const closeAt = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString()

  const patch: Record<string, unknown> = {}
  let discordMessage = ''

  if (action === 'claim') {
    patch.status = 'in_progress'
    patch.claimed_by = maker.discord_username
    patch.claimed_by_discord_id = maker.discord_user_id
    discordMessage = `🎨 **Claimed by ${maker.discord_username}** via PosterFlow — working on it!`
  } else if (action === 'complete') {
    patch.status = 'fulfilled'
    patch.fulfilled_by = maker.discord_username
    patch.fulfilled_at = now
    patch.close_at = closeAt
    const requesterPing = requested_by_discord_id ? ` <@${requested_by_discord_id}>` : ''
    discordMessage = `✅ **Fulfilled by ${maker.discord_username}** via PosterFlow. This thread will automatically close in **24 hours**. If you have any issues, please ask a moderator to reopen it.${requesterPing}`
  } else {
    // reject
    patch.status = 'rejected'
    patch.fulfilled_at = now
    patch.close_at = closeAt
    const rejectPing = requested_by_discord_id ? ` <@${requested_by_discord_id}>` : ''
    discordMessage = `❌ **Rejected by ${maker.discord_username}** via PosterFlow. This thread will automatically close in **24 hours**. If you have any issues, please ask a moderator to reopen it.${rejectPing}`
  }

  // ── Step 6: Conditional update (prevents claim race conditions) ────────────
  const expectedStatus = action === 'complete' ? 'in_progress' : action === 'claim' ? 'pending' : status
  const updated = await updateRequest(request_id, patch, expectedStatus)
  if (!updated) {
    return json({ error: 'This request was just updated by another maker — please refresh' }, 409)
  }

  // Cross-sync: clear any matching community-list items now the poster exists.
  if (action === 'complete') {
    await fulfillMatchingListItems(row, maker.discord_username, maker.discord_user_id)
  }

  // ── Step 7: Update Discord thread ────────────────────────────────────────
  if (discord_message_id) {
    const discordTasks: Promise<void>[] = []

    // Post a status message in the thread (includes requester ping on complete/reject)
    if (discordMessage) {
      const allowedMentions = ((action === 'complete' || action === 'reject') && requested_by_discord_id)
        ? { parse: [], users: [requested_by_discord_id] }
        : undefined
      discordTasks.push(postThreadMessage(discord_message_id, discordMessage, allowedMentions))
    }

    // Update the embed's components (buttons) so Discord reflects the new state.
    // In forum threads, the starter message ID equals the thread channel ID.
    // Mirror the same component logic used by discord-interactions when handling
    // button clicks so the Discord UI stays consistent.
    let updatedComponents: unknown[]
    if (action === 'claim') {
      // Disable Claim, keep Complete (encode claimer's user ID) and Reject
      updatedComponents = [{
        type: 1,
        components: [
          {
            type: 2,
            style: 2,
            label: 'Claim Poster',
            custom_id: `claim:${request_id}`,
            emoji: { name: '🎨' },
            disabled: true,
          },
          {
            type: 2,
            style: 3,
            label: 'Mark Complete',
            custom_id: `complete:${request_id}:${maker.discord_user_id}`,
            emoji: { name: '✅' },
          },
          {
            type: 2,
            style: 4,
            label: '✕ Reject',
            custom_id: `reject:${request_id}`,
          },
        ],
      }]
    } else if (action === 'complete') {
      // Remove all buttons — request is fulfilled
      updatedComponents = []
    } else {
      // reject — restore fresh buttons (same as discord-interactions reject behaviour)
      updatedComponents = [{
        type: 1,
        components: [
          {
            type: 2,
            style: 2,
            label: 'Claim Poster',
            custom_id: `claim:${request_id}`,
            emoji: { name: '🎨' },
          },
          {
            type: 2,
            style: 3,
            label: 'Mark Complete',
            custom_id: `complete:${request_id}:`,
            emoji: { name: '✅' },
          },
          {
            type: 2,
            style: 4,
            label: '✕ Reject',
            custom_id: `reject:${request_id}`,
          },
        ],
      }]
    }
    discordTasks.push(updateStarterMessage(discord_message_id, action, maker.discord_username, updatedComponents))

    // Keep the edge function alive until Discord calls complete so the ping is always sent
    const bgTask = Promise.all(discordTasks).catch(console.error)
    try {
      // @ts-ignore - EdgeRuntime is a Supabase-specific global
      EdgeRuntime.waitUntil(bgTask)
    } catch {
      // EdgeRuntime not available — await directly so calls still complete
      await bgTask
    }
  }

  return json({
    ok: true,
    status: patch.status,
    claimed_by: patch.claimed_by ?? null,
    fulfilled_by: patch.fulfilled_by ?? null,
  })
})
