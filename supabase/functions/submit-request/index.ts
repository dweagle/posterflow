/**
 * submit-request
 *
 * Single entry point for all community poster request submissions.
 *
 * Every submission must include a signed Discord token (issued by
 * discord-oauth). The requester's identity is taken from the verified token —
 * never from client-supplied fields — so rate limits are enforced per Discord
 * account and cannot be spoofed.
 *
 * The submit_poster_request RPC has anon/authenticated execute revoked —
 * only this function (running with the service role) can call it.
 *
 * Rate limits (both enforced server-side, per UTC day):
 *   USER_DAILY_LIMIT submissions per Discord account
 *     (request_user_limits via check_and_increment_user_limit)
 *   IP_DAILY_LIMIT submissions per IP, as a backstop
 *     (request_ip_limits via check_and_increment_ip_limit)
 *
 * Required Supabase secrets:
 *   DISCORD_JWT_SECRET        (same secret discord-oauth signs with)
 *   SUPABASE_URL              (auto-provided)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-provided)
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!

// Per-Discord-account daily limit — the primary limit.
const USER_DAILY_LIMIT = 5

// Per-IP daily backstop. Submissions arrive via the PosterFlow backend, so
// this IP is the instance server's IP — effectively a per-instance cap. Set
// above the per-user limit so a user can delete and re-submit their 5/day
// without the per-instance backstop blocking them.
const IP_DAILY_LIMIT = 10

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

// ── Token verification (same as update-request-status / post-poster) ────────

interface DiscordTokenPayload {
  discord_user_id: string
  discord_username: string
  is_maker: boolean
  exp: number
}

async function verifyToken(token: string): Promise<DiscordTokenPayload | null> {
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
    const decoded = new TextDecoder().decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    const payload = JSON.parse(decoded) as DiscordTokenPayload
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null
    if (!payload.discord_user_id) return null
    return payload
  } catch {
    return null
  }
}

// ── Cross-sync with community lists ──────────────────────────────────────────
// A formal request supersedes the community-list entry for that poster, so
// creating one removes the SHARED list row (for everyone who wanted it — the
// list is now one row per poster). Only an `open` row is cleared; a row a maker
// has already claimed from the list (in_progress) is left so the request can't
// yank in-progress work. Deleting the row cascades its wanters. Best-effort.
async function clearListEntryForRequest(
  // The remote supabase-js import's default generics don't line up with the
  // inferred client instance, so type just the query builder we use here.
  supabase: { from: (table: string) => any },
  body: Record<string, unknown>,
): Promise<void> {
  try {
    // Find the matching OPEN shared list row(s) for this media.
    let q = supabase
      .from('poster_list_items')
      .select('id')
      .eq('media_type', body.p_media_type as string)
      .eq('status', 'open')
    if (body.p_tmdb_id != null) {
      q = q.eq('tmdb_id', body.p_tmdb_id as number)
      // Season requests match on season number; everything else has none.
      if (body.p_media_type === 'season' && body.p_season_number != null) {
        q = q.eq('season_number', body.p_season_number as number)
      } else {
        q = q.is('season_number', null)
      }
    } else {
      // Custom item (no TMDB id) — match by exact title, case-insensitive.
      q = q.is('tmdb_id', null).ilike('title', String(body.p_title ?? ''))
    }
    const { data: rows, error: selErr } = await q
    if (selErr) {
      console.error('[submit-request] list clear lookup failed:', selErr)
      return
    }
    const ids = (rows ?? []).map((r: { id: string }) => r.id)
    if (ids.length === 0) return
    // Delete the wanters; the orphan-cleanup trigger then removes the now-empty
    // open poster row(s). Going through wanters (not deleting the poster row
    // directly) avoids the cascade re-firing the orphan trigger.
    const { error: delErr } = await supabase.from('poster_list_wanters').delete().in('item_id', ids)
    if (delErr) console.error('[submit-request] list clear failed:', delErr)
  } catch (e) {
    console.error('[submit-request] list clear error:', e)
  }
}

// ── Handler ──────────────────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405 })
  }

  // Parse payload
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json({ error: 'Invalid JSON body' }, 400)
  }

  // ── Verify the signed Discord token ────────────────────────────────────────
  const token = typeof body.token === 'string' ? body.token : ''
  delete body.token
  const user = await verifyToken(token)
  if (!user) {
    return json(
      { error: 'Discord authentication required — reconnect your Discord account and try again' },
      401,
    )
  }

  // Validate required fields before consuming any rate-limit quota
  const validTypes = ['movie', 'show', 'season', 'collection', 'person']
  if (!body.p_media_type || !validTypes.includes(body.p_media_type as string)) {
    return json({ error: 'Invalid media_type' }, 400)
  }
  if (!body.p_title || typeof body.p_title !== 'string' || !body.p_title.trim()) {
    return json({ error: 'Title is required' }, 400)
  }

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  })

  // ── Per-Discord-account limit (primary) ────────────────────────────────────
  const { data: userAllowed, error: userLimitErr } = await supabase.rpc(
    'check_and_increment_user_limit',
    { p_user_id: user.discord_user_id, p_limit: USER_DAILY_LIMIT },
  )
  if (userLimitErr) {
    console.error('User rate limit check failed:', userLimitErr)
    return json({ error: 'Internal error' }, 500)
  }
  if (!userAllowed) {
    return json(
      { error: `Daily request limit reached (${USER_DAILY_LIMIT} per day per Discord account). Try again tomorrow.` },
      429,
    )
  }

  // ── Per-IP backstop ─────────────────────────────────────────────────────────
  // Use cf-connecting-ip (set by Cloudflare, not settable by the client).
  // x-forwarded-for is intentionally NOT used as the primary source because
  // clients can send arbitrary values in that header.
  // Fall back to the last entry in x-forwarded-for only if cf-connecting-ip
  // is absent — the last entry is appended by the outermost proxy (Cloudflare)
  // and cannot be injected by the client.
  const ip =
    req.headers.get('cf-connecting-ip') ??
    req.headers.get('x-forwarded-for')?.split(',').at(-1)?.trim() ??
    'unknown'

  const { data: ipAllowed, error: ipLimitErr } = await supabase.rpc(
    'check_and_increment_ip_limit',
    { p_ip: ip, p_limit: IP_DAILY_LIMIT },
  )
  if (ipLimitErr) {
    console.error('IP rate limit check failed:', ipLimitErr)
    return json({ error: 'Internal error' }, 500)
  }
  if (!ipAllowed) {
    return json(
      { error: `Daily submission limit reached (${IP_DAILY_LIMIT} per day). Try again tomorrow.` },
      429,
    )
  }

  // Truncate free-text fields to prevent oversized payloads
  if (typeof body.p_title === 'string') body.p_title = body.p_title.trim().slice(0, 200)
  if (typeof body.p_notes === 'string') body.p_notes = body.p_notes.trim().slice(0, 1000)

  // Requester identity comes from the verified token, never the client.
  const requestedByDiscordId = user.discord_user_id
  if (typeof body.p_requested_by === 'string' && body.p_requested_by.trim()) {
    body.p_requested_by = body.p_requested_by.trim().slice(0, 100)
  } else {
    body.p_requested_by = user.discord_username
  }

  // Store ping_discord_username: any reasonable Discord username (2–32 non-whitespace chars)
  const rawPingUsername = typeof body.p_ping_discord_id === 'string' ? body.p_ping_discord_id.trim() : null
  const pingDiscordUsername = rawPingUsername && rawPingUsername.length >= 2 && rawPingUsername.length <= 32 ? rawPingUsername : null
  delete body.p_ping_discord_id

  // Call the RPC with the service role key.
  // anon and authenticated roles have had EXECUTE revoked on this function,
  // so this is the only valid path for submissions.
  const { data, error } = await supabase.rpc('submit_poster_request', body)

  if (error) {
    console.error('RPC error:', error)
    // RAISE EXCEPTION in the RPC surfaces as P0001 — treat as a conflict
    if (error.code === 'P0001' || error.message?.toLowerCase().includes('already')) {
      return json({ error: error.message }, 409)
    }
    // 23505 = unique_violation: RPC didn't handle the duplicate itself.
    // Look up the existing row and return it as a normal duplicate response.
    if (error.code === '23505') {
      let dupQuery = supabase
        .from('poster_requests')
        .select('id')
        .eq('tmdb_id', body.p_tmdb_id)
        .eq('media_type', body.p_media_type)
      if (body.p_season_number != null) {
        dupQuery = dupQuery.eq('season_number', body.p_season_number)
      } else {
        dupQuery = dupQuery.is('season_number', null)
      }
      const { data: existing } = await dupQuery.limit(1).single()
      if (existing?.id) {
        return json({ is_new: false, request_id: existing.id })
      }
      return json({ error: 'Request already exists' }, 409)
    }
    return json({ error: 'Submission failed' }, 500)
  }

  // Store the requester's Discord ID and optional ping username on the new row.
  // Only set when this is a brand-new request (not a duplicate vote).
  if (data?.is_new && data?.request_id) {
    const updatePayload: Record<string, string> = {
      requested_by_discord_id: requestedByDiscordId,
    }
    if (pingDiscordUsername) updatePayload.ping_discord_username = pingDiscordUsername
    const { error: updateErr } = await supabase
      .from('poster_requests')
      .update(updatePayload)
      .eq('id', data.request_id)
    if (updateErr) {
      console.error('Failed to store discord IDs:', updateErr)
    }
  }

  // Cross-sync: a formal request supersedes the community-list entry, so clear
  // the shared list row (for everyone who wanted it). Runs on new requests and
  // duplicate votes alike.
  await clearListEntryForRequest(supabase, body)

  return json(data)
})
