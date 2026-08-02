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
 * Show/season merge (in the RPC, same rule as submit-list-items): a show-level
 * request covers its seasons, so a season submission folds into an active show
 * request, and a show submission upgrades the show's active season request in
 * place (season-set row preferred, else the oldest single-season row). On
 * upgrade the RPC returns { upgraded: true } and this function freshens the
 * request's existing Discord forum thread.
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
// Freshens a request's forum thread after a season→show upgrade, and backs the
// live membership re-check below.
const DISCORD_BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN') ?? ''
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID') ?? ''

// Require the requester to be in the Discord server. Off unless the secret is
// set to "true", so enabling it is a deliberate flip.
const REQUIRE_GUILD_MEMBER =
  (Deno.env.get('DISCORD_REQUIRE_GUILD_MEMBER') ?? '').trim().toLowerCase() === 'true'

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

// Live membership check via the bot API. Checked per submission rather than
// recorded in the token, which lasts 30 days — otherwise someone who left or was
// banned could keep submitting until it expired. Fails closed, matching the role
// re-check in post-poster.
async function isGuildMember(discordUserId: string): Promise<boolean> {
  if (!DISCORD_BOT_TOKEN || !GUILD_ID) return false
  try {
    const resp = await fetch(
      `https://discord.com/api/v10/guilds/${GUILD_ID}/members/${discordUserId}`,
      { headers: { Authorization: `Bot ${DISCORD_BOT_TOKEN}` } },
    )
    return resp.ok
  } catch {
    return false
  }
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
    // New tokens are UTF-8; older tokens were Latin1 — fall back so their names survive.
    let raw: string
    try {
      raw = new TextDecoder('utf-8', { fatal: true }).decode(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)))
    } catch {
      raw = atob(b64)
    }
    const payload = JSON.parse(raw) as DiscordTokenPayload
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) return null
    if (!payload.discord_user_id) return null
    return payload
  } catch {
    return null
  }
}

// ── Field validation ────────────────────────────────────────────────────────
// This function is the only boundary a submission crosses — the app's own checks
// are UI, and a script can skip them entirely. So every field the app can set is
// constrained here to what the app could actually have produced.

// poster_path is rendered as an <img src> on every user's requests board and as
// the Discord embed thumbnail, so an unrestricted URL here is a tracking pixel
// aimed at the whole community. The app only ever builds these from TMDB/TVDB.
const POSTER_HOSTS = new Set([
  'image.tmdb.org',
  'artworks.thetvdb.com',
  'www.themoviedb.org',
  'media.themoviedb.org',
])

// The vocabulary in posterStyles.ts, plus the bare forms older rows still use.
const STYLE_TAGS = new Set(['CL2K Style', 'MM2K Style', 'CL2K', 'MM2K', 'Anime Movie', 'Anime TV'])

function isPosterUrl(v: unknown): boolean {
  if (typeof v !== 'string' || !v) return false
  try {
    const u = new URL(v)
    return u.protocol === 'https:' && POSTER_HOSTS.has(u.hostname.toLowerCase())
  } catch {
    return false
  }
}

const isIntInRange = (v: unknown, min: number, max: number): boolean =>
  typeof v === 'number' && Number.isInteger(v) && v >= min && v <= max

// Label for the submitted_via column: the app's own identifier if it sent one,
// otherwise the User-Agent. Control characters stripped so the value stays
// readable in a query result.
function clientLabel(client: unknown, userAgent: string | null): string {
  const raw = typeof client === 'string' && client.trim() ? client : userAgent
  if (!raw) return 'unknown'
  // deno-lint-ignore no-control-regex
  return raw.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 100) || 'unknown'
}

// Ranges are wide enough for anything real — the live board spans tmdb_id
// 62–1.7M and years 1929–2026, and some seasons are numbered by year.
// Bad ids are rejected rather than nulled: tmdb_id drives dedupe and the
// show/season merge, so a junk value quietly splits a request from its twin.
function validateFields(body: Record<string, unknown>): string | null {
  // The app sends "" rather than null for an absent id or image — treat those as
  // absent so they neither fail validation nor land in the row as empty strings.
  for (const k of ['p_imdb_id', 'p_poster_path']) {
    if (typeof body[k] === 'string' && !(body[k] as string).trim()) body[k] = null
  }
  if (body.p_poster_path != null && !isPosterUrl(body.p_poster_path)) return 'poster_path'
  if (body.p_tmdb_id != null && !isIntInRange(body.p_tmdb_id, 1, 20_000_000)) return 'tmdb_id'
  if (body.p_tvdb_id != null && !isIntInRange(body.p_tvdb_id, 1, 20_000_000)) return 'tvdb_id'
  if (body.p_year != null && !isIntInRange(body.p_year, 1870, 2200)) return 'year'
  if (body.p_season_number != null && !isIntInRange(body.p_season_number, 0, 2200)) return 'season_number'
  if (
    body.p_imdb_id != null &&
    (typeof body.p_imdb_id !== 'string' || !/^tt\d{5,12}$/.test(body.p_imdb_id))
  ) {
    return 'imdb_id'
  }
  if (body.p_style_tags != null && !Array.isArray(body.p_style_tags)) return 'style_tags'
  return null
}

// The community poster style (CL2K/MM2K) in a request's tags / list item's tag.
// Mirrors the poster_request_style() SQL helper — CL2K wins if both appear.
function styleOf(tags: unknown): string {
  const t = Array.isArray(tags) ? tags : typeof tags === 'string' ? [tags] : []
  if (t.includes('CL2K Style') || t.includes('CL2K')) return 'CL2K'
  if (t.includes('MM2K Style') || t.includes('MM2K')) return 'MM2K'
  return ''
}

// ── Cross-sync with community lists ──────────────────────────────────────────
// A formal request supersedes the community-list entry for that poster, so
// creating one removes the SHARED list row (for everyone who wanted it — the
// list is now one row per poster). Style-aware: only an entry in the SAME style
// is superseded (an entry with no recognized style counts as any style — a
// CL2K request must not clear an MM2K entry or vice versa). Only an `open` row
// is cleared; a row a maker has already claimed from the list (in_progress) is
// left so the request can't yank in-progress work. Deleting the row cascades
// its wanters. Best-effort.
async function clearListEntryForRequest(
  // The remote supabase-js import's default generics don't line up with the
  // inferred client instance, so type just the query builder we use here.
  supabase: { from: (table: string) => any },
  body: Record<string, unknown>,
): Promise<void> {
  try {
    // Find the matching OPEN shared list row(s) for this media. A show-level
    // request covers its seasons, so it also supersedes the open season-set
    // list row (season_number NULL — single-season rows are left alone).
    const mediaTypes = body.p_media_type === 'show' ? ['show', 'season'] : [body.p_media_type as string]
    let q = supabase
      .from('poster_list_items')
      .select('id,style_tag')
      .in('media_type', mediaTypes)
      .eq('status', 'open')
    // Season requests match on season number; everything else has none.
    if (body.p_media_type === 'season' && body.p_season_number != null) {
      q = q.eq('season_number', body.p_season_number as number)
    } else {
      q = q.is('season_number', null)
    }
    if (body.p_tmdb_id != null) {
      q = q.eq('tmdb_id', body.p_tmdb_id as number)
    } else {
      // Custom item (no TMDB id) — match by exact title, case-insensitive.
      q = q.is('tmdb_id', null).ilike('title', String(body.p_title ?? ''))
    }
    const { data: rows, error: selErr } = await q
    if (selErr) {
      console.error('[submit-request] list clear lookup failed:', selErr)
      return
    }
    const reqStyle = styleOf(body.p_style_tags)
    const ids = (rows ?? [])
      .filter((r: { style_tag: string | null }) => {
        const s = styleOf(r.style_tag)
        return s === reqStyle || s === ''
      })
      .map((r: { id: string }) => r.id)
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

// ── Discord thread refresh on season→show upgrade ───────────────────────────
// When a show submission upgrades an open season request in place (the RPC's
// show/season merge), the request keeps the forum thread created back when it
// was a season request. Freshen it: drop the "— Seasons: …" suffix from the
// thread name, swap the Season forum tag for Show, fix the embed's Type/Notes
// fields, and leave a note saying who asked for the full show. Best-effort —
// a Discord hiccup never fails the submission.
async function refreshUpgradedThread(
  supabase: { from: (table: string) => any },
  requestId: string,
  upgraderName: string,
): Promise<void> {
  if (!DISCORD_BOT_TOKEN) return
  try {
    const { data: rows } = await supabase
      .from('poster_requests')
      .select('discord_message_id,title,year,notes')
      .eq('id', requestId)
      .limit(1)
    const row = rows?.[0] as
      | { discord_message_id: string | null; title: string; year: number | null; notes: string | null }
      | undefined
    const threadId = row?.discord_message_id
    if (!row || !threadId) return
    const auth = { Authorization: `Bot ${DISCORD_BOT_TOKEN}` }
    const jsonAuth = { ...auth, 'Content-Type': 'application/json' }

    // Thread name loses the season suffix; the Season forum tag flips to Show.
    const chResp = await fetch(`https://discord.com/api/v10/channels/${threadId}`, { headers: auth })
    if (chResp.ok) {
      const ch = (await chResp.json()) as { applied_tags?: string[] }
      const seasonTag = Deno.env.get('DISCORD_TAG_SEASON')
      const showTag = Deno.env.get('DISCORD_TAG_SHOW')
      let tags = (ch.applied_tags ?? []).filter((t) => t !== seasonTag)
      if (showTag && !tags.includes(showTag)) tags = [showTag, ...tags]
      const patchResp = await fetch(`https://discord.com/api/v10/channels/${threadId}`, {
        method: 'PATCH',
        headers: jsonAuth,
        body: JSON.stringify({
          name: row.year ? `${row.title} (${row.year})` : row.title,
          applied_tags: tags,
        }),
      })
      if (!patchResp.ok) {
        console.error('[submit-request] thread rename failed:', await patchResp.text())
      }
    }

    // Starter embed: Type becomes Show; Notes mirrors the row's stripped notes.
    // In forum threads the starter message ID equals the thread channel ID.
    const msgResp = await fetch(
      `https://discord.com/api/v10/channels/${threadId}/messages/${threadId}`,
      { headers: auth },
    )
    if (msgResp.ok) {
      const msg = (await msgResp.json()) as { embeds?: Record<string, unknown>[] }
      const embed = msg.embeds?.[0]
      if (embed) {
        let fields = ((embed.fields ?? []) as { name: string; value: string; inline?: boolean }[])
          .map((f) => (f.name === 'Type' ? { ...f, value: 'Show' } : f))
        fields = row.notes
          ? fields.map((f) => (f.name === 'Notes' ? { ...f, value: row.notes as string } : f))
          : fields.filter((f) => f.name !== 'Notes')
        const embedResp = await fetch(
          `https://discord.com/api/v10/channels/${threadId}/messages/${threadId}`,
          {
            method: 'PATCH',
            headers: jsonAuth,
            body: JSON.stringify({ embeds: [{ ...embed, fields }] }),
          },
        )
        if (!embedResp.ok) {
          console.error('[submit-request] embed refresh failed:', await embedResp.text())
        }
      }
    }

    const noteResp = await fetch(`https://discord.com/api/v10/channels/${threadId}/messages`, {
      method: 'POST',
      headers: jsonAuth,
      body: JSON.stringify({
        content: `⬆️ **${upgraderName}** requested the full show — upgraded to a show-level request (series + all season posters).`,
      }),
    })
    if (!noteResp.ok) {
      console.error('[submit-request] upgrade note failed:', await noteResp.text())
    }
  } catch (e) {
    console.error('[submit-request] upgraded-thread refresh failed:', e)
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

  // Where the submission came from, recorded on the row. The app stamps itself
  // ("posterflow/0.13.2"); a direct call to this function doesn't, so it falls
  // back to the User-Agent. This is a record, not a check — anyone can send any
  // value, so it separates "came through a PosterFlow instance" from "hit this
  // endpoint directly" and nothing more.
  const submittedVia = clientLabel(body.client, req.headers.get('user-agent'))
  delete body.client

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

  // Membership gate — checked before any quota is consumed.
  if (REQUIRE_GUILD_MEMBER && !(await isGuildMember(user.discord_user_id))) {
    return json(
      { error: 'Requests are limited to members of the PosterFlow Discord — join the server and try again.' },
      403,
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
  const badField = validateFields(body)
  if (badField) {
    return json({ error: `Invalid ${badField}` }, 400)
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

  // Unknown style tags are dropped rather than rejected — notify-discord already
  // ignores any it can't map, so filtering changes nothing for real traffic.
  if (Array.isArray(body.p_style_tags)) {
    body.p_style_tags = body.p_style_tags.filter((t) => typeof t === 'string' && STYLE_TAGS.has(t))
  }

  // Requester identity comes from the verified token, never the client.
  const requestedByDiscordId = user.discord_user_id
  // Display name only — attribution comes from requested_by_discord_id below.
  // Collapse whitespace so it can't be used to break the Discord embed layout.
  if (typeof body.p_requested_by === 'string' && body.p_requested_by.trim()) {
    body.p_requested_by = body.p_requested_by.replace(/\s+/g, ' ').trim().slice(0, 100)
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
      submitted_via: submittedVia,
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

  // A show submission that upgraded an open season request keeps that request's
  // Discord thread — freshen its name/tags/embed to show-level.
  if (data?.upgraded && data?.request_id) {
    await refreshUpgradedThread(supabase, data.request_id, user.discord_username)
  }

  // Cross-sync: a formal request supersedes the community-list entry, so clear
  // the shared list row (for everyone who wanted it). Runs on new requests and
  // duplicate votes alike.
  await clearListEntryForRequest(supabase, body)

  return json(data)
})
