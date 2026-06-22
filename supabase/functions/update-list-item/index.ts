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
 *   remove   publisher only (added_by_discord_id == caller); hard-deletes the row
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
// remove: publisher or guild owner (per item). remove_mine: caller clears all their own.
const VALID_ACTIONS = new Set([...MAKER_ACTIONS, 'remove', 'remove_mine'])

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
    const payload = JSON.parse(atob(b64)) as MakerPayload
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
  added_by_discord_id: string | null
  claimed_by_discord_id: string | null
}

async function fetchItem(id: string): Promise<ListItemRow | null> {
  const resp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_list_items?id=eq.${id}&select=id,status,added_by_discord_id,claimed_by_discord_id&limit=1`,
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

async function deleteItem(filter: string): Promise<boolean> {
  const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?${filter}`, {
    method: 'DELETE',
    headers: SB_HEADERS,
  })
  if (!resp.ok) {
    console.error('[update-list-item] DELETE failed:', await resp.text())
    return false
  }
  const rows = await resp.json() as unknown[]
  return rows.length > 0
}

// Bulk delete — returns the number of rows removed, or -1 on error.
async function deleteMany(filter: string): Promise<number> {
  const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?${filter}`, {
    method: 'DELETE',
    headers: SB_HEADERS,
  })
  if (!resp.ok) {
    console.error('[update-list-item] bulk DELETE failed:', await resp.text())
    return -1
  }
  const rows = await resp.json() as unknown[]
  return rows.length
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

  let body: { token?: string; item_id?: string; action?: string }
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

  // ── Bulk: caller clears all of their own published items (no item_id) ─────────
  if (action === 'remove_mine') {
    const count = await deleteMany(`added_by_discord_id=eq.${user.discord_user_id}`)
    if (count < 0) return json({ error: 'Failed to clear your items' }, 500)
    return json({ ok: true, removed: count })
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

  // ── Remove: the publisher, or the Discord server (guild) owner ───────────────
  if (action === 'remove') {
    const isPublisher = row.added_by_discord_id === user.discord_user_id
    let allowed = isPublisher
    if (!allowed) {
      const ownerId = await getGuildOwnerId()
      allowed = user.discord_user_id === ownerId
    }
    if (!allowed) {
      return json({ error: 'Only the publisher or the server owner can remove this item' }, 403)
    }
    // Publisher delete stays scoped to their own id (backstop); owner may delete any.
    const filter = isPublisher
      ? `id=eq.${item_id}&added_by_discord_id=eq.${user.discord_user_id}`
      : `id=eq.${item_id}`
    const ok = await deleteItem(filter)
    if (!ok) return json({ error: 'Item could not be removed — it may already be gone' }, 409)
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
