/**
 * update-list-item
 *
 * Maker/publisher actions on a community list item (the "Lists" tab). Same
 * per-item claim lifecycle as a single poster request, but with no Discord
 * thread — everything happens in-app.
 *
 *   claim    maker only; open -> in_progress (atomic guard prevents collisions)
 *   complete maker who claimed it (server owner exempt); in_progress -> fulfilled
 *   release  the claiming maker (or owner); in_progress -> open
 *   reject   maker only; open|in_progress -> rejected
 *   remove   detach the caller as a wanter; guild owner can force-delete the
 *            row only when they aren't a wanter themselves
 *
 * Verifies the signed Discord token, then re-checks the live Discord guild role
 * for maker actions so a revoked role blocks access. Conditional updates make
 * claim/complete/release race-safe (mirrors update-request-status).
 *
 * Required Supabase secrets:
 *   DISCORD_BOT_TOKEN
 *   DISCORD_GUILD_ID
 *   DISCORD_MAKER_ROLE_ID
 *   DISCORD_JWT_SECRET
 *   SUPABASE_URL              (auto-provided)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-provided)
 */

const BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID')!
const MAKER_ROLE_ID = Deno.env.get('DISCORD_MAKER_ROLE_ID')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

const MAKER_ACTIONS = new Set(['claim', 'complete', 'release', 'reject'])
// remove: publisher or guild owner (per item). remove_mine: caller clears all
// their own. remove_ids: caller clears a specific set of their own. available_ids:
// like remove_ids but also flags the surviving rows "available in a drive" (the
// caller's workflow found the poster) — both used by headless reconciliation.
const VALID_ACTIONS = new Set([...MAKER_ACTIONS, 'remove', 'remove_mine', 'remove_ids', 'available_ids'])

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// ── Token verification (same as update-request-status) ──────────────────────

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

// ── CORS / response helpers ──────────────────────────────────────────────────

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

// ── Supabase helpers ─────────────────────────────────────────────────────────

const SB_HEADERS = {
  apikey: SUPABASE_SERVICE_ROLE_KEY,
  Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
  'Content-Type': 'application/json',
  Prefer: 'return=representation',
}

interface ListItemRow {
  id: string
  status: string
  tmdb_id: number | null
  media_type: string
  season_number: number | null
  title: string
  claimed_by_discord_id: string | null
}

async function fetchItem(id: string): Promise<ListItemRow | null> {
  const resp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_list_items?id=eq.${id}&select=id,status,tmdb_id,media_type,season_number,title,claimed_by_discord_id&limit=1`,
    { headers: SB_HEADERS },
  )
  if (!resp.ok) return null
  const rows = await resp.json() as ListItemRow[]
  return rows[0] ?? null
}

// Conditional PATCH — only applies when the extra filter (status/claimer) still
// matches. Returns true if a row was updated (false = another actor got there first).
async function patchItem(filter: string, patch: Record<string, unknown>): Promise<boolean> {
  const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?${filter}`, {
    method: 'PATCH',
    headers: SB_HEADERS,
    body: JSON.stringify(patch),
  })
  if (!resp.ok) {
    console.error('[update-list-item] PATCH failed:', await resp.text())
    return false
  }
  const rows = await resp.json() as unknown[]
  return rows.length > 0
}

// Detach wanters matching the filter; returns rows removed (or -1 on error). The
// orphan-cleanup trigger drops any now-wanterless open poster automatically, so
// removing your interest never deletes a row others still want.
async function deleteWanters(filter: string): Promise<number> {
  const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_wanters?${filter}`, {
    method: 'DELETE',
    headers: SB_HEADERS,
  })
  if (!resp.ok) {
    console.error('[update-list-item] wanter DELETE failed:', await resp.text())
    return -1
  }
  return (await resp.json() as unknown[]).length
}

// Post a plain note into a Discord forum thread. A request's starter message ID
// equals its thread channel ID. No pings — purely informational.
async function postThreadMessage(channelId: string, content: string): Promise<void> {
  const resp = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
    method: 'POST',
    headers: { Authorization: `Bot ${BOT_TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ content, allowed_mentions: { parse: [] } }),
  })
  if (!resp.ok) console.error('[update-list-item] thread note failed:', await resp.text())
}

// Cross-sync (soft): completing a list item means a matching poster may now
// exist, but requests are authoritative — we never close one automatically
// (the styles asked for can differ). Instead we (1) stamp each matching open
// request with list_completed_by/at so the request board shows a verify-&-close
// callout, and (2) drop a note in its Discord thread. A maker makes the final
// call. Best-effort; never blocks the list-item completion.
async function notifyMatchingRequests(row: ListItemRow, makerName: string): Promise<void> {
  try {
    // Shared media + status filter for the matching open request(s).
    const filter = new URLSearchParams()
    filter.set('media_type', `eq.${row.media_type}`)
    filter.set('status', 'in.(pending,in_progress)')
    if (row.tmdb_id != null) {
      filter.set('tmdb_id', `eq.${row.tmdb_id}`)
      if (row.media_type === 'season' && row.season_number != null) {
        filter.set('season_number', `eq.${row.season_number}`)
      } else {
        filter.set('season_number', 'is.null')
      }
    } else {
      filter.set('tmdb_id', 'is.null')
      filter.set('title', `ilike.${row.title}`)
    }

    // (1) Stamp the soft marker — status is left untouched.
    const now = new Date().toISOString()
    const stampResp = await fetch(`${SUPABASE_URL}/rest/v1/poster_requests?${filter}`, {
      method: 'PATCH',
      headers: { ...SB_HEADERS, Prefer: 'return=minimal' },
      body: JSON.stringify({ list_completed_by: makerName, list_completed_at: now }),
    })
    if (!stampResp.ok) console.error('[update-list-item] request stamp failed:', await stampResp.text())

    // (2) Post the FYI note in each matching request's Discord thread.
    const getParams = new URLSearchParams(filter)
    getParams.set('select', 'id,discord_message_id')
    const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_requests?${getParams}`, { headers: SB_HEADERS })
    if (!resp.ok) {
      console.error('[update-list-item] request match lookup failed:', await resp.text())
      return
    }
    const matches = await resp.json() as { id: string; discord_message_id: string | null }[]
    const note = `📋 **A matching poster was just completed from a community list** by ${makerName} via PosterFlow. Please verify it fits this request, then mark it complete if so.`
    await Promise.all(
      matches
        .filter((m) => m.discord_message_id)
        .map((m) => postThreadMessage(m.discord_message_id as string, note)),
    )
  } catch (e) {
    console.error('[update-list-item] request notify error:', e)
  }
}

// ── Discord helpers (same as update-request-status) ──────────────────────────

async function checkMakerRole(discordUserId: string): Promise<boolean> {
  const resp = await fetch(
    `https://discord.com/api/v10/guilds/${GUILD_ID}/members/${discordUserId}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!resp.ok) return false
  const member = await resp.json() as { roles: string[] }
  return member.roles.includes(MAKER_ROLE_ID)
}

async function getGuildOwnerId(): Promise<string | null> {
  const resp = await fetch(
    `https://discord.com/api/v10/guilds/${GUILD_ID}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!resp.ok) return null
  const guild = await resp.json() as { owner_id?: string }
  return guild.owner_id ?? null
}

// ── Handler ──────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS })
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405)

  let body: { token?: string; item_id?: string; action?: string; item_ids?: unknown }
  try {
    body = await req.json()
  } catch {
    return json({ error: 'Invalid JSON body' }, 400)
  }

  const { token, item_id, action } = body
  if (!token || !action) {
    return json({ error: 'Missing required fields: token, action' }, 400)
  }
  if (!VALID_ACTIONS.has(action)) {
    return json({ error: 'Invalid action' }, 400)
  }

  const user = await verifyToken(token)
  if (!user) {
    return json({ error: 'Invalid or expired token — please reconnect your Discord account' }, 401)
  }

  // ── Bulk: caller stops wanting all of their items ────────────────────────────
  if (action === 'remove_mine') {
    const count = await deleteWanters(`discord_id=eq.${encodeURIComponent(user.discord_user_id)}`)
    if (count < 0) return json({ error: 'Failed to clear your items' }, 500)
    return json({ ok: true, removed: count })
  }

  // ── Bulk by id: caller stops wanting a specific set ──────────────────────────
  // Used by the PosterFlow backend's headless reconciliation. Owner comes from
  // the verified token, so this only ever detaches the caller — it never deletes
  // a shared poster that other people still want (the trigger only drops a row
  // once its last wanter leaves and it's still open).
  if (action === 'remove_ids') {
    const ids = Array.isArray(body.item_ids)
      ? Array.from(new Set(
          body.item_ids.filter((v): v is string => typeof v === 'string' && UUID_RE.test(v)),
        )).slice(0, 500)
      : []
    if (ids.length === 0) return json({ ok: true, removed: 0 })
    const count = await deleteWanters(
      `discord_id=eq.${encodeURIComponent(user.discord_user_id)}&item_id=in.(${ids.join(',')})`,
    )
    if (count < 0) return json({ error: 'Failed to remove items' }, 500)
    return json({ ok: true, removed: count })
  }

  // ── Bulk by id: detach the caller AND flag the surviving rows "available in a
  //    drive" — the caller's workflow found the poster, so the people still
  //    wanting it should see it's made and will appear on their next run. ──────────
  if (action === 'available_ids') {
    const ids = Array.isArray(body.item_ids)
      ? Array.from(new Set(
          body.item_ids.filter((v): v is string => typeof v === 'string' && UUID_RE.test(v)),
        )).slice(0, 500)
      : []
    if (ids.length === 0) return json({ ok: true, removed: 0 })
    // Detach the caller; the returned rows tell us which items they actually wanted.
    const delResp = await fetch(
      `${SUPABASE_URL}/rest/v1/poster_list_wanters?discord_id=eq.${encodeURIComponent(user.discord_user_id)}&item_id=in.(${ids.join(',')})`,
      { method: 'DELETE', headers: SB_HEADERS },
    )
    if (!delResp.ok) {
      console.error('[update-list-item] available detach failed:', await delResp.text())
      return json({ error: 'Failed to update items' }, 500)
    }
    const detached = await delResp.json() as { item_id: string }[]
    const itemIds = [...new Set(detached.map((r) => r.item_id))]
    // Flag the rows that still exist (the trigger removed any the caller was the
    // last wanter of); a PATCH on a now-deleted id simply matches nothing.
    if (itemIds.length) {
      await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?id=in.(${itemIds.join(',')})`, {
        method: 'PATCH',
        headers: { ...SB_HEADERS, Prefer: 'return=minimal' },
        body: JSON.stringify({ available_in_drive: true }),
      })
    }
    return json({ ok: true, removed: itemIds.length })
  }

  // Per-item actions require a real id. item_id is interpolated into PostgREST
  // query strings, so require a strict UUID — blocks query-parameter injection.
  if (!item_id) {
    return json({ error: 'Missing required field: item_id' }, 400)
  }
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(item_id)) {
    return json({ error: 'Invalid item_id' }, 400)
  }

  const row = await fetchItem(item_id)
  if (!row) return json({ error: 'List item not found' }, 404)

  // ── Remove: detach the caller as a wanter (the poster stays for anyone else
  //    who wants it). The Discord server (guild) owner can force-delete the whole
  //    poster row outright — but only when they aren't a wanter themselves, so
  //    the owner dropping their own want never nukes everyone else's. ───────────
  if (action === 'remove') {
    // Always drop only the caller's own want first (scoped by their discord_id,
    // so it can't touch anyone else). The trigger deletes the row if they were
    // the last wanter of an open poster.
    const count = await deleteWanters(`item_id=eq.${item_id}&discord_id=eq.${encodeURIComponent(user.discord_user_id)}`)
    if (count < 0) return json({ error: 'Could not remove — please try again' }, 500)
    // If the caller actually had a want, this was a personal removal — done.
    if (count > 0) return json({ ok: true, removed: true })
    // The caller wasn't a wanter: treat this as a moderator force-remove. Only
    // the guild owner may delete a poster outright. Clear any remaining wanters
    // first so deleting the poster has nothing to cascade (which would otherwise
    // re-fire the orphan trigger), then delete any non-open remainder.
    const ownerId = await getGuildOwnerId()
    if (user.discord_user_id !== ownerId) {
      return json({ error: 'Only the server owner can remove a poster you do not want' }, 403)
    }
    await fetch(`${SUPABASE_URL}/rest/v1/poster_list_wanters?item_id=eq.${item_id}`, { method: 'DELETE', headers: SB_HEADERS })
    await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?id=eq.${item_id}`, { method: 'DELETE', headers: SB_HEADERS })
    return json({ ok: true, removed: true })
  }

  // ── Maker actions — re-check the live Discord role ──────────────────────────
  const hasMakerRole = await checkMakerRole(user.discord_user_id)
  if (!hasMakerRole) {
    return json({ error: 'You no longer have the Poster Maker role' }, 403)
  }

  const now = new Date().toISOString()

  if (action === 'claim') {
    if (row.status !== 'open') {
      return json({ error: 'This item has already been claimed by another maker' }, 409)
    }
    const ok = await patchItem(`id=eq.${item_id}&status=eq.open`, {
      status: 'in_progress',
      claimed_by: user.discord_username,
      claimed_by_discord_id: user.discord_user_id,
      updated_at: now,
    })
    if (!ok) return json({ error: 'This item was just claimed by another maker — please refresh' }, 409)
    return json({ ok: true, status: 'in_progress', claimed_by: user.discord_username, claimed_by_discord_id: user.discord_user_id })
  }

  if (action === 'complete') {
    if (row.status !== 'in_progress') {
      return json({ error: 'Only in-progress items can be marked complete' }, 409)
    }
    // Completion is restricted to the maker who claimed it — except the server owner.
    if (row.claimed_by_discord_id !== user.discord_user_id) {
      const ownerId = await getGuildOwnerId()
      if (user.discord_user_id !== ownerId) {
        return json({ error: 'Only the maker who claimed this item can mark it complete' }, 403)
      }
    }
    const ok = await patchItem(`id=eq.${item_id}&status=eq.in_progress`, {
      status: 'fulfilled',
      fulfilled_by: user.discord_username,
      fulfilled_by_discord_id: user.discord_user_id,
      fulfilled_at: now,
      updated_at: now,
    })
    if (!ok) return json({ error: 'This item was just updated — please refresh' }, 409)
    // Cross-sync (soft): notify any matching open request — never auto-close it.
    await notifyMatchingRequests(row, user.discord_username)
    return json({ ok: true, status: 'fulfilled', fulfilled_by: user.discord_username })
  }

  if (action === 'release') {
    if (row.status !== 'in_progress') {
      return json({ error: 'Only a claimed item can be released' }, 409)
    }
    // Only the claiming maker (or the server owner) may release it.
    if (row.claimed_by_discord_id !== user.discord_user_id) {
      const ownerId = await getGuildOwnerId()
      if (user.discord_user_id !== ownerId) {
        return json({ error: 'Only the maker who claimed this item can release it' }, 403)
      }
    }
    const ok = await patchItem(`id=eq.${item_id}&status=eq.in_progress`, {
      status: 'open',
      claimed_by: null,
      claimed_by_discord_id: null,
      updated_at: now,
    })
    if (!ok) return json({ error: 'This item was just updated — please refresh' }, 409)
    return json({ ok: true, status: 'open', claimed_by: null, claimed_by_discord_id: null })
  }

  // reject — open or in_progress -> rejected
  if (!['open', 'in_progress'].includes(row.status)) {
    return json({ error: 'This item cannot be rejected in its current state' }, 409)
  }
  const ok = await patchItem(`id=eq.${item_id}&status=in.(open,in_progress)`, {
    status: 'rejected',
    fulfilled_by: user.discord_username,
    fulfilled_by_discord_id: user.discord_user_id,
    fulfilled_at: now,
    updated_at: now,
  })
  if (!ok) return json({ error: 'This item was just updated — please refresh' }, 409)
  return json({ ok: true, status: 'rejected' })
})
