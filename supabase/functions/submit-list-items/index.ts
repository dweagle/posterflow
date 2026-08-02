/**
 * submit-list-items
 *
 * Bulk-publish a worklist to the community "Lists" tab. A connected Discord
 * user pushes their unmatched assets or a poster-style fallback list; makers
 * then claim and complete the items from the PosterFlow UI.
 *
 * Every submission must include a signed Discord token (issued by
 * discord-oauth). The publisher's identity is taken from the verified token —
 * never from client-supplied fields. Writes use the service role key, so anon
 * has no write access to poster_list_items (enforced by RLS).
 *
 * Abuse limits (no extra tables — kept simple):
 *   MAX_ITEMS_PER_SUBMIT  per call
 *   MAX_OPEN_ITEMS        open/in-progress rows per publisher total
 *
 * Dedup: skips items the publisher already has open (by tmdb_id+media_type+
 * season for TMDB items, by title+media_type for custom items). The DB also has
 * a partial unique index as a backstop.
 *
 * Show/season merge: a show item covers its seasons — a series poster is never
 * made without its season posters, so a show and its season set never coexist
 * as two rows (same rule the requests board enforces). Season items attach to
 * an active show row, and a show item upgrades an active season row in place
 * (keeping its wanters — the full set covers what they need). Standalone season
 * items (set exists, a season poster is missing) are untouched.
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
const DISCORD_BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN') ?? ''
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID') ?? ''

// Require the publisher to be in the Discord server. Same secret (and same
// default-off behaviour) as submit-request.
const REQUIRE_GUILD_MEMBER =
  (Deno.env.get('DISCORD_REQUIRE_GUILD_MEMBER') ?? '').trim().toLowerCase() === 'true'

const MAX_ITEMS_PER_SUBMIT = 200
const MAX_OPEN_ITEMS = 1000

const VALID_MEDIA_TYPES = new Set(['movie', 'show', 'season', 'collection'])
const VALID_SOURCES = new Set(['unmatched', 'style_fallback'])

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
}

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...CORS },
  })

// ── Token verification (same as submit-request) ─────────────────────────────

interface DiscordTokenPayload {
  discord_user_id: string
  discord_username: string
  is_maker: boolean
  exp: number
}

// Live membership check via the bot API — see the note in submit-request.
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

// ── Item normalization ──────────────────────────────────────────────────────

const str = (v: unknown, max: number): string | null => {
  if (typeof v !== 'string') return null
  const t = v.trim()
  return t ? t.slice(0, max) : null
}
const int = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? Math.trunc(v) : null

// ── Field validation (mirrors submit-request) ───────────────────────────────
// This function is the only boundary a submission crosses, so every field is
// constrained to what the app could have produced. Unlike submit-request this
// is a bulk call, so a bad value drops that field rather than failing 200 good
// items alongside it.

// poster_path renders as an <img src> on every user's board — an unrestricted
// URL is a tracking pixel aimed at the community. App values are TMDB/TVDB only.
const POSTER_HOSTS = new Set([
  'image.tmdb.org',
  'artworks.thetvdb.com',
  'www.themoviedb.org',
  'media.themoviedb.org',
])

// Short forms — styleTag() strips the " Style" suffix before this is checked.
const STYLE_TAGS = new Set(['CL2K', 'MM2K', 'Anime Movie', 'Anime TV'])

const posterUrl = (v: unknown): string | null => {
  if (typeof v !== 'string' || !v) return null
  try {
    const u = new URL(v)
    return u.protocol === 'https:' && POSTER_HOSTS.has(u.hostname.toLowerCase()) ? v : null
  } catch {
    return null
  }
}

// Ranges wide enough for anything real; some seasons are numbered by year.
const inRange = (v: number | null, min: number, max: number): number | null =>
  v != null && v >= min && v <= max ? v : null

const imdbId = (v: unknown): string | null =>
  typeof v === 'string' && /^tt\d{5,12}$/.test(v) ? v : null

// Label for the submitted_via column (same as submit-request): the app's own
// identifier if it sent one, otherwise the User-Agent. A record, not a check.
function clientLabel(client: unknown, userAgent: string | null): string {
  const raw = typeof client === 'string' && client.trim() ? client : userAgent
  if (!raw) return 'unknown'
  // deno-lint-ignore no-control-regex
  return raw.replace(/[\x00-\x1f\x7f]/g, ' ').trim().slice(0, 100) || 'unknown'
}

// Community list items store the short style tag ('CL2K' / 'MM2K'). Requests keep
// the 'CL2K Style' form (notify-discord maps that exact string to a Discord forum
// tag), so a client may send either — strip a trailing " Style" so the list is
// uniform regardless of which flow published the item.
const styleTag = (v: unknown): string | null => {
  const s = str(v, 40)
  const stripped = s ? s.replace(/\s+Style$/i, '') : s
  return stripped && STYLE_TAGS.has(stripped) ? stripped : null
}

interface NormalizedItem {
  tmdb_id: number | null
  media_type: string
  title: string
  year: number | null
  season_number: number | null
  poster_path: string | null
  imdb_id: string | null
  tvdb_id: number | null
  style_tag: string | null
  source: string
  notes: string | null
}

function normalizeItem(raw: Record<string, unknown>): NormalizedItem | null {
  const media_type = typeof raw.media_type === 'string' ? raw.media_type : ''
  const title = str(raw.title, 200)
  if (!VALID_MEDIA_TYPES.has(media_type) || !title) return null
  const source = typeof raw.source === 'string' && VALID_SOURCES.has(raw.source) ? raw.source : 'unmatched'
  return {
    tmdb_id: inRange(int(raw.tmdb_id), 1, 20_000_000),
    media_type,
    title,
    year: inRange(int(raw.year), 1870, 2200),
    season_number: inRange(int(raw.season_number), 0, 2200),
    poster_path: posterUrl(raw.poster_path),
    imdb_id: imdbId(raw.imdb_id),
    tvdb_id: inRange(int(raw.tvdb_id), 1, 20_000_000),
    style_tag: styleTag(raw.style_tag),
    source,
    notes: str(raw.notes, 1000),
  }
}

// Dedup key — TMDB items keyed by id+type+season, custom items by title+type+season.
function dedupKey(it: { tmdb_id: number | null; media_type: string; season_number: number | null; title: string }): string {
  return it.tmdb_id != null
    ? `t:${it.tmdb_id}:${it.media_type}:${it.season_number ?? ''}`
    : `c:${it.media_type}:${it.title.toLowerCase()}:${it.season_number ?? ''}`
}

// ── Show/season merge helpers ───────────────────────────────────────────────

// Dedup keys of an item's show-level / season-set twin for the same media.
const showTwinKey = (it: { tmdb_id: number | null; title: string }): string =>
  it.tmdb_id != null ? `t:${it.tmdb_id}:show:` : `c:show:${it.title.toLowerCase()}:`
const seasonTwinKey = (it: { tmdb_id: number | null; title: string }): string =>
  it.tmdb_id != null ? `t:${it.tmdb_id}:season:` : `c:season:${it.title.toLowerCase()}:`

// Drop the machine "Seasons: 1, 2, 3" first line when a season row is upgraded
// to show-level — the whole set gets made, so the per-season detail is stale.
function stripSeasonsLine(notes: string | null): string | null {
  if (!notes) return null
  const lines = notes.split('\n')
  if (!lines[0].startsWith('Seasons: ')) return notes
  const rest = lines.slice(1).join('\n').trim()
  return rest || null
}

// ── Handler ──────────────────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS })
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405)

  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json({ error: 'Invalid JSON body' }, 400)
  }

  // Where the publish came from, recorded on each new row it creates.
  const submittedVia = clientLabel(body.client, req.headers.get('user-agent'))

  const token = typeof body.token === 'string' ? body.token : ''
  const user = await verifyToken(token)
  if (!user) {
    return json(
      { error: 'Discord authentication required — reconnect your Discord account and try again' },
      401,
    )
  }

  if (REQUIRE_GUILD_MEMBER && !(await isGuildMember(user.discord_user_id))) {
    return json(
      { error: 'Publishing is limited to members of the PosterFlow Discord — join the server and try again.' },
      403,
    )
  }

  const rawItems = Array.isArray(body.items) ? body.items : null
  if (!rawItems || rawItems.length === 0) {
    return json({ error: 'No items to add' }, 400)
  }
  if (rawItems.length > MAX_ITEMS_PER_SUBMIT) {
    return json({ error: `Too many items — add at most ${MAX_ITEMS_PER_SUBMIT} at a time` }, 400)
  }

  // Normalize + drop invalid, then de-duplicate within the batch itself.
  const seen = new Set<string>()
  let normalized: NormalizedItem[] = []
  for (const raw of rawItems) {
    if (typeof raw !== 'object' || raw === null) continue
    const item = normalizeItem(raw as Record<string, unknown>)
    if (!item) continue
    const key = dedupKey(item)
    if (seen.has(key)) continue
    seen.add(key)
    normalized.push(item)
  }
  if (normalized.length === 0) {
    return json({ error: 'No valid items to add' }, 400)
  }

  // A show item covers its seasons (a series poster is never made without
  // them), so drop any season item whose show is also in this batch.
  const showKeys = new Set(normalized.filter((n) => n.media_type === 'show').map((n) => dedupKey(n)))
  normalized = normalized.filter((it) => !(it.media_type === 'season' && showKeys.has(showTwinKey(it))))

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  })

  // One row per poster now (shared across everyone who wants it); this user is
  // attached as a "wanter". dedupKey is the in-memory media key (matches the DB
  // unique indexes), letting us map a batch item to its existing row.
  type KeyedRow = { id: string; tmdb_id: number | null; media_type: string; season_number: number | null; title: string; notes: string | null; status: string }
  const ROW_COLS = 'id, tmdb_id, media_type, season_number, title, notes, status'
  const rowByKey = new Map<string, KeyedRow>()
  const indexRows = (rows: KeyedRow[] | null) => {
    for (const r of rows ?? []) rowByKey.set(dedupKey(r), r)
  }

  // Find existing poster rows for the batch (active + fulfilled).
  const tmdbIds = [...new Set(normalized.filter((i) => i.tmdb_id != null).map((i) => i.tmdb_id as number))]
  if (tmdbIds.length) {
    const { data, error } = await supabase
      .from('poster_list_items')
      .select(ROW_COLS)
      .in('status', ['open', 'in_progress', 'fulfilled'])
      .in('tmdb_id', tmdbIds)
    if (error) {
      console.error('[submit-list-items] existing (tmdb) fetch failed:', error)
      return json({ error: 'Internal error' }, 500)
    }
    indexRows(data)
  }
  const customTitles = new Set(normalized.filter((i) => i.tmdb_id == null).map((i) => i.title.toLowerCase()))
  if (customTitles.size) {
    const { data, error } = await supabase
      .from('poster_list_items')
      .select(ROW_COLS)
      .in('status', ['open', 'in_progress', 'fulfilled'])
      .is('tmdb_id', null)
    if (error) {
      console.error('[submit-list-items] existing (custom) fetch failed:', error)
      return json({ error: 'Internal error' }, 500)
    }
    indexRows((data ?? []).filter((r) => customTitles.has((r.title ?? '').toLowerCase())))
  }

  // Merge batch items into rows already on the board. A season item with no row
  // of its own attaches to the show's active row (the show item covers its
  // seasons); a show item with no row of its own upgrades the show's active
  // season row in place, keeping its wanters — the full set covers their need.
  const isActive = (r: KeyedRow | undefined): r is KeyedRow =>
    r != null && (r.status === 'open' || r.status === 'in_progress')
  for (const it of normalized) {
    if (rowByKey.has(dedupKey(it))) continue
    if (it.media_type === 'season') {
      const show = rowByKey.get(showTwinKey(it))
      if (isActive(show)) rowByKey.set(dedupKey(it), show)
    } else if (it.media_type === 'show') {
      const season = rowByKey.get(seasonTwinKey(it))
      if (isActive(season)) {
        // The media_type guard is the race backstop: if a concurrent publisher
        // already created a show row, this is a no-op and the normal
        // create/re-read path below takes over.
        const { data: upgraded, error } = await supabase
          .from('poster_list_items')
          .update({ media_type: 'show', notes: stripSeasonsLine(season.notes) })
          .eq('id', season.id)
          .eq('media_type', 'season')
          .select('id')
        if (error) {
          console.error('[submit-list-items] season→show upgrade failed:', error)
        } else if (upgraded?.length) {
          season.media_type = 'show'
          rowByKey.set(dedupKey(it), season)
        }
      }
    }
  }

  // Create poster rows for items that don't exist yet. The partial unique index
  // is the race backstop: if a concurrent publisher created the same poster, the
  // insert fails and we re-read so we can still attach this user as a wanter.
  const toCreate = normalized.filter((it) => !rowByKey.has(dedupKey(it)))
  if (toCreate.length) {
    const newRows = toCreate.map((it) => ({ ...it, status: 'open', submitted_via: submittedVia }))
    const { data: created, error: createErr } = await supabase
      .from('poster_list_items')
      .insert(newRows)
      .select(ROW_COLS)
    if (createErr) {
      console.error('[submit-list-items] create failed, re-reading:', createErr)
      const retryTmdb = [...new Set(toCreate.filter((i) => i.tmdb_id != null).map((i) => i.tmdb_id as number))]
      if (retryTmdb.length) {
        const { data } = await supabase
          .from('poster_list_items')
          .select(ROW_COLS)
          .in('status', ['open', 'in_progress', 'fulfilled'])
          .in('tmdb_id', retryTmdb)
        indexRows(data)
      }
      // (custom-title races are rare; any still-unresolved items are skipped.)
    } else {
      indexRows(created)
    }
  }

  // Resolve every batch item to a poster id; abuse cap on total posters wanted.
  const itemIds = [...new Set(
    normalized.map((it) => rowByKey.get(dedupKey(it))?.id).filter((id): id is string => typeof id === 'string'),
  )]
  if (itemIds.length === 0) {
    return json({ inserted: 0, skipped: normalized.length })
  }

  // The source the caller added each poster from, so reconciliation can later use
  // THIS user's context rather than the row's first-creator source.
  const sourceByItemId = new Map<string, string>()
  for (const it of normalized) {
    const id = rowByKey.get(dedupKey(it))?.id
    if (id && !sourceByItemId.has(id)) sourceByItemId.set(id, it.source)
  }

  const { count: wanterCount } = await supabase
    .from('poster_list_wanters')
    .select('id', { count: 'exact', head: true })
    .eq('discord_id', user.discord_user_id)
  if ((wanterCount ?? 0) + itemIds.length > MAX_OPEN_ITEMS) {
    return json(
      { error: `That would exceed your ${MAX_OPEN_ITEMS}-item list limit. Remove some items first.` },
      429,
    )
  }

  // Which posters does this user already want? (for an accurate inserted/skipped)
  const { data: alreadyRows } = await supabase
    .from('poster_list_wanters')
    .select('item_id')
    .eq('discord_id', user.discord_user_id)
    .in('item_id', itemIds)
  const already = new Set((alreadyRows ?? []).map((r) => r.item_id as string))
  const newItemIds = itemIds.filter((id) => !already.has(id))

  if (newItemIds.length) {
    const { error: wanterErr } = await supabase
      .from('poster_list_wanters')
      .upsert(
        newItemIds.map((id) => ({ item_id: id, discord_id: user.discord_user_id, name: user.discord_username, source: sourceByItemId.get(id) ?? null })),
        { onConflict: 'item_id,discord_id', ignoreDuplicates: true },
      )
    if (wanterErr) {
      console.error('[submit-list-items] wanter upsert failed:', wanterErr)
      return json({ error: 'Failed to add items' }, 500)
    }
  }

  return json({ inserted: newItemIds.length, skipped: normalized.length - newItemIds.length })
})
