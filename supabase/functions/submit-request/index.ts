/**
 * submit-request
 *
 * Single entry point for all community poster request submissions.
 * Enforces server-side IP rate limiting so the local (per-instance) limit
 * cannot be bypassed by hitting Supabase directly.
 *
 * The submit_poster_request RPC has anon/authenticated execute revoked —
 * only this function (running with the service role) can call it.
 *
 * Rate limit: IP_DAILY_LIMIT submissions per IP per UTC day.
 * Tracked in the request_ip_limits table via check_and_increment_ip_limit().
 */

import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

// Server-side daily limit per IP — independent of the per-instance local limit.
// Set higher than the local limit (5) to accommodate shared IPs / households.
const IP_DAILY_LIMIT = 15

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

Deno.serve(async (req: Request) => {
  if (req.method !== 'POST') {
    return new Response('Method Not Allowed', { status: 405 })
  }

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

  const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false },
  })

  // Atomically check and increment the IP counter for today.
  // Returns true if the submission is allowed, false if the limit is exceeded.
  const { data: allowed, error: limitErr } = await supabase.rpc(
    'check_and_increment_ip_limit',
    { p_ip: ip, p_limit: IP_DAILY_LIMIT },
  )
  if (limitErr) {
    console.error('Rate limit check failed:', limitErr)
    return json({ error: 'Internal error' }, 500)
  }
  if (!allowed) {
    return json(
      { error: `Daily submission limit reached (${IP_DAILY_LIMIT} per day). Try again tomorrow.` },
      429,
    )
  }

  // Parse payload
  let body: Record<string, unknown>
  try {
    body = await req.json()
  } catch {
    return json({ error: 'Invalid JSON body' }, 400)
  }

  // Validate required fields (defense-in-depth; the backend also validates)
  const validTypes = ['movie', 'show', 'season', 'collection', 'person']
  if (!body.p_media_type || !validTypes.includes(body.p_media_type as string)) {
    return json({ error: 'Invalid media_type' }, 400)
  }
  if (!body.p_title || typeof body.p_title !== 'string' || !body.p_title.trim()) {
    return json({ error: 'Title is required' }, 400)
  }

  // Truncate free-text fields to prevent oversized payloads
  if (typeof body.p_title === 'string')        body.p_title        = body.p_title.trim().slice(0, 200)
  if (typeof body.p_notes === 'string')        body.p_notes        = body.p_notes.trim().slice(0, 1000)
  if (typeof body.p_requested_by === 'string') body.p_requested_by = body.p_requested_by.trim().slice(0, 100)

  // Extract and remove Discord ID fields before passing to the RPC — the RPC doesn't
  // accept these parameters. We store them via a separate UPDATE after insert.
  const requestedByDiscordId =
    typeof body.p_requested_by_discord_id === 'string'
      ? body.p_requested_by_discord_id.trim().slice(0, 30)
      : null
  delete body.p_requested_by_discord_id

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

  // Store the requester's Discord ID and optional ping ID on the new row.
  // Only set when this is a brand-new request (not a duplicate vote).
  if (data?.is_new && data?.request_id && (requestedByDiscordId || pingDiscordId)) {
    const updatePayload: Record<string, string> = {}
    if (requestedByDiscordId) updatePayload.requested_by_discord_id = requestedByDiscordId
    if (pingDiscordUsername) updatePayload.ping_discord_username = pingDiscordUsername
    const { error: updateErr } = await supabase
      .from('poster_requests')
      .update(updatePayload)
      .eq('id', data.request_id)
    if (updateErr) {
      console.error('Failed to store discord IDs:', updateErr)
    }
  }

  return json(data)
})
