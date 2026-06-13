/**
 * discord-oauth
 *
 * Handles Discord OAuth2 flow for poster maker verification.
 *   - Called without `code`: redirects the user to Discord's authorization page.
 *   - Called with `code` (Discord callback): exchanges for access token, checks
 *     guild role membership, issues a signed maker token, then redirects back
 *     to the PosterFlow app's /discord-callback route.
 *
 * The redirect approach is used instead of window.opener.postMessage because
 * Discord sets Cross-Origin-Opener-Policy: same-origin on its authorize page,
 * which severs window.opener before postMessage can be used.
 *
 * The `state` parameter carries base64(JSON {nonce, origin}) so this function
 * knows where to redirect after completing OAuth.
 *
 * Required Supabase secrets:
 *   DISCORD_CLIENT_ID
 *   DISCORD_CLIENT_SECRET
 *   DISCORD_GUILD_ID
 *   DISCORD_MAKER_ROLE_ID
 *   DISCORD_JWT_SECRET
 */

const CLIENT_ID = Deno.env.get('DISCORD_CLIENT_ID')!
const CLIENT_SECRET = Deno.env.get('DISCORD_CLIENT_SECRET')!
const GUILD_ID = Deno.env.get('DISCORD_GUILD_ID')!
const MAKER_ROLE_ID = Deno.env.get('DISCORD_MAKER_ROLE_ID')!
const JWT_SECRET = Deno.env.get('DISCORD_JWT_SECRET')!
const BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!

const REDIRECT_URI = 'https://qwudwkxfqowjtisdlplv.supabase.co/functions/v1/discord-oauth'

// ── Token helpers ──────────────────────────────────────────────────────────

async function signB64(b64: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(JWT_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(b64))
  return Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

async function createToken(payload: object): Promise<string> {
  const b64 = btoa(JSON.stringify(payload))
  const sig = await signB64(b64)
  return `${b64}.${sig}`
}

// ── Parse origin from encoded state ──────────────────────────────────────

interface StatePayload {
  nonce: string
  origin: string
}

function parseState(state: string): StatePayload | null {
  try {
    const parsed = JSON.parse(atob(state)) as StatePayload
    if (!parsed.nonce || !parsed.origin) return null
    // Only allow http/https origins — basic sanity check against open redirect
    const url = new URL(parsed.origin)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
    return parsed
  } catch {
    return null
  }
}

function callbackRedirect(origin: string, params: Record<string, string>): Response {
  // Use a static HTML file so the callback works without loading the full
  // React app (which has routing guards that would redirect away from any
  // React route before the component could write to localStorage).
  const url = new URL(`${origin}/discord-callback.html`)
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v)
  return Response.redirect(url.toString())
}

// ── Handler ────────────────────────────────────────────────────────────────

Deno.serve(async (req) => {
  const url = new URL(req.url)
  const code = url.searchParams.get('code')
  const error = url.searchParams.get('error')
  const state = url.searchParams.get('state') ?? ''

  // Parse the encoded state to get the nonce + caller origin
  const statePayload = parseState(state)

  // ── Step 1: No code yet — redirect to Discord OAuth ──────────────────────
  if (!code && !error) {
    const authUrl = new URL('https://discord.com/oauth2/authorize')
    authUrl.searchParams.set('client_id', CLIENT_ID)
    authUrl.searchParams.set('redirect_uri', REDIRECT_URI)
    authUrl.searchParams.set('response_type', 'code')
    authUrl.searchParams.set('scope', 'identify guilds.members.read')
    authUrl.searchParams.set('state', state) // pass through unchanged
    return Response.redirect(authUrl.toString())
  }

  // Fallback if state is unreadable — show a plain error page
  if (!statePayload) {
    return new Response('Invalid state parameter.', { status: 400 })
  }

  const { nonce, origin } = statePayload

  // ── User denied ───────────────────────────────────────────────────────────
  if (error) {
    return callbackRedirect(origin, { error, nonce })
  }

  // ── Step 2: Exchange code for access token ────────────────────────────────
  const tokenResp = await fetch('https://discord.com/api/v10/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      grant_type: 'authorization_code',
      code: code!,
      redirect_uri: REDIRECT_URI,
    }),
  })

  if (!tokenResp.ok) {
    console.error('[discord-oauth] Token exchange failed:', await tokenResp.text())
    return callbackRedirect(origin, { error: 'token_exchange_failed', nonce })
  }

  const { access_token } = await tokenResp.json()

  // ── Step 3: Fetch user identity ───────────────────────────────────────────
  const userResp = await fetch('https://discord.com/api/v10/users/@me', {
    headers: { Authorization: `Bearer ${access_token}` },
  })

  if (!userResp.ok) {
    return callbackRedirect(origin, { error: 'user_fetch_failed', nonce })
  }

  const user = await userResp.json()
  const discordUserId: string = user.id
  const discordUsername: string = user.global_name ?? user.username ?? 'Unknown'

  // ── Step 4: Check guild member roles ─────────────────────────────────────
  let isMaker = false
  const memberResp = await fetch(
    `https://discord.com/api/v10/users/@me/guilds/${GUILD_ID}/member`,
    { headers: { Authorization: `Bearer ${access_token}` } },
  )
  if (memberResp.ok) {
    const member = await memberResp.json()
    isMaker = (member.roles as string[]).includes(MAKER_ROLE_ID)
  }

  // Is this the Discord server owner? (Exempt from the claimer-only completion rule.)
  let isOwner = false
  try {
    const guildResp = await fetch(
      `https://discord.com/api/v10/guilds/${GUILD_ID}`,
      { headers: { Authorization: `Bot ${BOT_TOKEN}` } },
    )
    if (guildResp.ok) {
      const guild = await guildResp.json() as { owner_id?: string }
      isOwner = guild.owner_id === discordUserId
    }
  } catch {
    // Non-fatal — fall back to is_owner=false.
  }

  // ── Step 5: Issue signed token (30-day expiry) ────────────────────────────
  const payload = {
    discord_user_id: discordUserId,
    discord_username: discordUsername,
    is_maker: isMaker,
    is_owner: isOwner,
    exp: Math.floor(Date.now() / 1000) + 86400 * 30,
  }
  const token = await createToken(payload)

  // Redirect popup back to PosterFlow's /discord-callback route.
  // That page writes the token to localStorage, which the main window polls for.
  return callbackRedirect(origin, { token, nonce })
})
