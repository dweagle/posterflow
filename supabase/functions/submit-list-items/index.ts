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
 * Required Supabase secrets:
 *   DISCORD_JWT_SECRET        (same secret discord-oauth signs with)
 *   SUPABASE_URL              (auto-provided)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-provided)
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!

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
    const payload = JSON.parse(atob(b64)) as DiscordTokenPayload
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
    tmdb_id: int(raw.tmdb_id),
    media_type,
    title,
    year: int(raw.year),
    season_number: int(raw.season_number),
    poster_path: str(raw.poster_path, 500),
    imdb_id: str(raw.imdb_id, 20),
    tvdb_id: int(raw.tvdb_id),
    style_tag: str(raw.style_tag, 40),
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

  const token = typeof body.token === 'string' ? body.token : ''
  const user = await verifyToken(token)
  if (!user) {
    return json(
      { error: 'Discord authentication required — reconnect your Discord account and try again' },
      401,
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
  const normalized: NormalizedItem[] = []
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

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  })

  // One row per poster now (shared across everyone who wants it); this user is
  // attached as a "wanter". dedupKey is the in-memory media key (matches the DB
  // unique indexes), letting us map a batch item to its existing row id.
  type KeyedRow = { id: string; tmdb_id: number | null; media_type: string; season_number: number | null; title: string }
  const idByKey = new Map<string, string>()
  const indexRows = (rows: KeyedRow[] | null) => {
    for (const r of rows ?? []) idByKey.set(dedupKey(r), r.id)
  }

  // Find existing poster rows for the batch (active + fulfilled).
  const tmdbIds = [...new Set(normalized.filter((i) => i.tmdb_id != null).map((i) => i.tmdb_id as number))]
  if (tmdbIds.length) {
    const { data, error } = await supabase
      .from('poster_list_items')
      .select('id, tmdb_id, media_type, season_number, title')
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
      .select('id, tmdb_id, media_type, season_number, title')
      .in('status', ['open', 'in_progress', 'fulfilled'])
      .is('tmdb_id', null)
    if (error) {
      console.error('[submit-list-items] existing (custom) fetch failed:', error)
      return json({ error: 'Internal error' }, 500)
    }
    indexRows((data ?? []).filter((r) => customTitles.has((r.title ?? '').toLowerCase())))
  }

  // Create poster rows for items that don't exist yet. The partial unique index
  // is the race backstop: if a concurrent publisher created the same poster, the
  // insert fails and we re-read so we can still attach this user as a wanter.
  const toCreate = normalized.filter((it) => !idByKey.has(dedupKey(it)))
  if (toCreate.length) {
    const newRows = toCreate.map((it) => ({ ...it, status: 'open' }))
    const { data: created, error: createErr } = await supabase
      .from('poster_list_items')
      .insert(newRows)
      .select('id, tmdb_id, media_type, season_number, title')
    if (createErr) {
      console.error('[submit-list-items] create failed, re-reading:', createErr)
      const retryTmdb = [...new Set(toCreate.filter((i) => i.tmdb_id != null).map((i) => i.tmdb_id as number))]
      if (retryTmdb.length) {
        const { data } = await supabase
          .from('poster_list_items')
          .select('id, tmdb_id, media_type, season_number, title')
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
    normalized.map((it) => idByKey.get(dedupKey(it))).filter((id): id is string => typeof id === 'string'),
  )]
  if (itemIds.length === 0) {
    return json({ inserted: 0, skipped: normalized.length })
  }

  // The source the caller added each poster from, so reconciliation can later use
  // THIS user's context rather than the row's first-creator source.
  const sourceByItemId = new Map<string, string>()
  for (const it of normalized) {
    const id = idByKey.get(dedupKey(it))
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
