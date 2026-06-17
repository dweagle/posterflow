/**
 * post-poster
 *
 * Accepts a multipart image upload from a verified poster maker and posts
 * it as a file attachment to the Discord thread for that request.
 *
 * Verifies the maker token signature, then re-checks the Discord guild role
 * live (via bot API) before accepting the upload — so revoking the Discord
 * role immediately blocks future uploads even if the token hasn't expired.
 *
 * Required Supabase secrets:
 *   DISCORD_BOT_TOKEN
 *   DISCORD_GUILD_ID
 *   DISCORD_MAKER_ROLE_ID
 *   DISCORD_JWT_SECRET
 *   SUPABASE_URL              (auto-provided)
 *   SUPABASE_SERVICE_ROLE_KEY (auto-provided)
 *
 * Expected multipart/form-data fields:
 *   token       - Signed maker token from discord-oauth
 *   request_id  - UUID of the poster_request row
 *   file        - Image file (JPEG / PNG / WebP, max 8 MB)
 */

const BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID')!
const MAKER_ROLE_ID = Deno.env.get('DISCORD_MAKER_ROLE_ID')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

const MAX_FILE_SIZE = 8 * 1024 * 1024 // 8 MB — Discord's limit for regular bots
const MAX_FILES = 10 // Discord's attachment limit per message
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

// ── Token verification ─────────────────────────────────────────────────────

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

// ── Response helpers ───────────────────────────────────────────────────────

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

// ── Handler ────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response(null, { headers: CORS })
  if (req.method !== 'POST') return json({ error: 'Method not allowed' }, 405)

  // Parse multipart form
  let form: FormData
  try {
    form = await req.formData()
  } catch {
    return json({ error: 'Expected multipart/form-data' }, 400)
  }

  const token = form.get('token') as string | null
  const requestId = form.get('request_id') as string | null
  const files = form.getAll('file') as File[]

  if (!token || !requestId || files.length === 0) {
    return json({ error: 'Missing required fields: token, request_id, file' }, 400)
  }
  if (files.length > MAX_FILES) {
    return json({ error: `Too many files — Discord allows a maximum of ${MAX_FILES} images per message` }, 400)
  }

  // ── Verify token ──────────────────────────────────────────────────────────
  const payload = await verifyToken(token)
  if (!payload) return json({ error: 'Invalid or expired token — please reconnect with Discord' }, 401)
  if (!payload.is_maker) return json({ error: 'Poster Maker role required' }, 403)

  // ── Validate each file ────────────────────────────────────────────────────
  for (const file of files) {
    if (!ALLOWED_TYPES.has(file.type)) {
      return json({ error: `"${file.name}" must be a JPEG, PNG, or WebP image` }, 400)
    }
    if (file.size > MAX_FILE_SIZE) {
      return json({ error: `"${file.name}" exceeds the 8 MB limit` }, 400)
    }
  }

  // ── Live role re-check via bot API ────────────────────────────────────────
  const memberResp = await fetch(
    `https://discord.com/api/v10/guilds/${GUILD_ID}/members/${payload.discord_user_id}`,
    { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
  )
  if (!memberResp.ok) {
    return json({ error: 'Could not verify Discord role — try again shortly' }, 403)
  }
  const member = await memberResp.json()
  if (!(member.roles as string[]).includes(MAKER_ROLE_ID)) {
    return json({ error: 'Your Poster Maker role was not found — contact a moderator' }, 403)
  }

  // ── Look up the Discord thread for this request ───────────────────────────
  const rowResp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}&select=discord_message_id,title`,
    {
      headers: {
        apikey: SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
      },
    },
  )
  const rows = await rowResp.json()
  const row = rows?.[0]

  if (!row?.discord_message_id) {
    return json({ error: 'No Discord thread found for this request' }, 404)
  }

  // ── Post image(s) to Discord thread ──────────────────────────────────────
  const label = files.length === 1
    ? `🖼️ Poster uploaded by **${payload.discord_username}**`
    : `🖼️ ${files.length} posters uploaded by **${payload.discord_username}**`

  // Rebuilt per attempt — a FormData's file parts are consumed when streamed.
  const buildForm = () => {
    const form = new FormData()
    form.append(
      'payload_json',
      JSON.stringify({
        content: label,
        attachments: files.map((f, i) => ({ id: i, filename: f.name || `poster_${i + 1}.jpg` })),
      }),
    )
    files.forEach((f, i) => {
      form.append(`files[${i}]`, f, f.name || `poster_${i + 1}.jpg`)
    })
    return form
  }

  const channelUrl = `https://discord.com/api/v10/channels/${row.discord_message_id}/messages`

  // Discord rate-limits messages per channel, so a quick second batch can get a
  // 429. Retry rate limits (honoring retry_after) and transient 5xx instead of
  // failing outright, which is what dropped a multi-batch (>10 file) upload.
  const MAX_ATTEMPTS = 4
  let discordResp: Response | null = null
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    discordResp = await fetch(channelUrl, {
      method: 'POST',
      headers: { Authorization: `Bot ${BOT_TOKEN}` },
      body: buildForm(),
    })
    if (discordResp.ok) break

    const retryable = discordResp.status === 429 || discordResp.status >= 500
    if (!retryable || attempt === MAX_ATTEMPTS) break

    let waitMs = 500 * 2 ** (attempt - 1)
    if (discordResp.status === 429) {
      const body = await discordResp.clone().json().catch(() => null)
      const retryAfterSec = body?.retry_after ?? Number(discordResp.headers.get('retry-after')) ?? 1
      waitMs = Math.ceil(retryAfterSec * 1000) + 250
    }
    await new Promise((resolve) => setTimeout(resolve, waitMs))
  }

  if (!discordResp || !discordResp.ok) {
    const status = discordResp?.status ?? 0
    const err = discordResp ? await discordResp.text().catch(() => '') : ''
    console.error('[post-poster] Discord upload failed:', status, err)
    return json(
      {
        error: status === 429
          ? 'Discord is rate limiting uploads — wait a moment and retry'
          : 'Failed to post image to Discord',
      },
      status === 429 ? 429 : 502,
    )
  }

  return json({ success: true })
})
