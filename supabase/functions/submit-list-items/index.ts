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

  // Existing items for this publisher. Dedup considers fulfilled too, so a
  // completed item can't be re-added as a fresh duplicate row; the open-items
  // cap, however, only counts items still on the working list.
  const { data: existing, error: existErr } = await supabase
    .from('poster_list_items')
    .select('tmdb_id, media_type, season_number, title, status')
    .eq('added_by_discord_id', user.discord_user_id)
    .in('status', ['open', 'in_progress', 'fulfilled'])
  if (existErr) {
    console.error('[submit-list-items] existing fetch failed:', existErr)
    return json({ error: 'Internal error' }, 500)
  }

  const existingKeys = new Set((existing ?? []).map((r) => dedupKey(r as never)))
  const toInsert = normalized.filter((it) => !existingKeys.has(dedupKey(it)))
  const skipped = normalized.length - toInsert.length

  if (toInsert.length === 0) {
    return json({ inserted: 0, skipped, message: 'All items are already on (or completed from) your list' })
  }

  const currentOpen = (existing ?? []).filter((r) => r.status === 'open' || r.status === 'in_progress').length
  if (currentOpen + toInsert.length > MAX_OPEN_ITEMS) {
    return json(
      { error: `That would exceed your ${MAX_OPEN_ITEMS}-item list limit. Complete or remove some items first.` },
      429,
    )
  }

  const rows = toInsert.map((it) => ({
    ...it,
    added_by: user.discord_username,
    added_by_discord_id: user.discord_user_id,
    status: 'open',
  }))

  const { data: inserted, error: insertErr } = await supabase
    .from('poster_list_items')
    .insert(rows)
    .select('id')
  if (insertErr) {
    console.error('[submit-list-items] insert failed:', insertErr)
    return json({ error: 'Failed to add items' }, 500)
  }

  return json({ inserted: inserted?.length ?? 0, skipped })
})
