/**
 * notify-discord
 *
 * Triggered by a Supabase Database Webhook on INSERT to poster_requests.
 * Creates a Discord forum thread (post) with Claim and Complete buttons.
 *
 * Required Supabase secrets:
 *   DISCORD_BOT_TOKEN   - Bot token from Discord Developer Portal
 *   DISCORD_CHANNEL_ID  - Forum channel ID to post request threads in
 *   DISCORD_GUILD_ID    - Server/guild ID (for building thread URLs)
 *
 * Media-type tag secrets (set to the tag snowflake ID from forum channel settings):
 *   DISCORD_TAG_MOVIE
 *   DISCORD_TAG_SHOW
 *   DISCORD_TAG_SEASON
 *   DISCORD_TAG_COLLECTION
 *
 * Style tag secrets (user-selectable at request time):
 *   DISCORD_TAG_MM2K        - MM2K Style tag ID
 *   DISCORD_TAG_CL2K        - CL2K Style tag ID
 *   DISCORD_TAG_ANIME_MOVIE - Anime Movie tag ID
 *   DISCORD_TAG_ANIME_TV    - Anime TV tag ID
 */

const DISCORD_BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const DISCORD_CHANNEL_ID = Deno.env.get('DISCORD_CHANNEL_ID')!
const DISCORD_GUILD_ID = Deno.env.get('DISCORD_GUILD_ID') ?? null
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

const SB_HEADERS = {
  apikey: SUPABASE_SERVICE_ROLE_KEY,
  Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
  'Content-Type': 'application/json',
}

const TAG_SECRETS: Record<string, string> = {
  movie: 'DISCORD_TAG_MOVIE',
  show: 'DISCORD_TAG_SHOW',
  season: 'DISCORD_TAG_SEASON',
  collection: 'DISCORD_TAG_COLLECTION',
}

// Maps style_tag string values (sent from frontend) to Supabase secret names
const STYLE_TAG_SECRETS: Record<string, string> = {
  'MM2K Style': 'DISCORD_TAG_MM2K',
  'CL2K Style': 'DISCORD_TAG_CL2K',
  'Anime Movie': 'DISCORD_TAG_ANIME_MOVIE',
  'Anime TV': 'DISCORD_TAG_ANIME_TV',
}

const TYPE_COLORS: Record<string, number> = {
  movie: 0x64b5f6,
  show: 0x66bb6a,
  season: 0xffb74d,
  collection: 0xba68c8,
}

function buildTmdbLink(tmdbId: number | null, mediaType: string): string {
  if (!tmdbId) return ''
  if (mediaType === 'movie') return `https://www.themoviedb.org/movie/${tmdbId}`
  if (mediaType === 'collection') return `https://www.themoviedb.org/collection/${tmdbId}`
  if (mediaType === 'person') return `https://www.themoviedb.org/person/${tmdbId}`
  return `https://www.themoviedb.org/tv/${tmdbId}`
}

function buildTvdbLink(tvdbId: number | null): string {
  if (!tvdbId) return ''
  return `https://thetvdb.com/dereferrer/series/${tvdbId}`
}

Deno.serve(async (req) => {
  const body = await req.json()
  const record = body.record

  if (!record) {
    return new Response('No record', { status: 400 })
  }

  const id = record.id

  // Brief pause so that post-INSERT UPDATEs (requested_by_discord_id, ping_discord_id)
  // have time to complete before we read the row.
  await new Promise((resolve) => setTimeout(resolve, 800))

  // Re-fetch the row to get the most up-to-date field values
  const freshResp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${id}&select=*&limit=1`,
    { headers: SB_HEADERS },
  )
  let freshRecord: Record<string, unknown> = record
  if (freshResp.ok) {
    const rows = await freshResp.json() as Record<string, unknown>[]
    if (rows.length > 0) freshRecord = rows[0]
  }

  const { title, year, media_type, tmdb_id, tvdb_id, season_number, notes, style_tags, requested_by, poster_path, ping_discord_username } = freshRecord as {
    title: string
    year: number | null
    media_type: string
    tmdb_id: number | null
    tvdb_id: number | null
    season_number: number | null
    notes: string | null
    style_tags: string[] | null
    requested_by: string | null
    poster_path: string | null
    ping_discord_username: string | null
  }

  const tmdbLink = buildTmdbLink(tmdb_id, media_type)
  const tvdbLink = buildTvdbLink(tvdb_id ?? null)

  const linkParts = [
    tmdbLink ? `[TMDB](${tmdbLink})` : null,
    tvdbLink ? `[TVDB](${tvdbLink})` : null,
  ].filter(Boolean)
  const linksDescription = linkParts.length > 0 ? linkParts.join(' · ') : undefined

  const SEASONS_PREFIX = 'Seasons: '
  let seasonsLabel: string | null = null
  if (season_number != null) {
    seasonsLabel = `Season ${season_number}`
  } else if (media_type === 'season' && typeof notes === 'string' && notes.startsWith(SEASONS_PREFIX)) {
    seasonsLabel = notes.split('\n')[0] // e.g. "Seasons: 1, 2, 3"
  }

  const displayTitle = seasonsLabel != null
    ? (year ? `${title} (${year}) — ${seasonsLabel}` : `${title} — ${seasonsLabel}`)
    : year ? `${title} (${year})` : title

  const embed: Record<string, unknown> = {
    description: linksDescription,
    color: TYPE_COLORS[media_type] ?? 0x64b5f6,
    ...(poster_path ? { thumbnail: { url: poster_path } } : {}),
    fields: [
      { name: 'Type', value: media_type.charAt(0).toUpperCase() + media_type.slice(1), inline: true },
      { name: 'Status', value: '🟡 Pending', inline: true },
      ...(requested_by ? [{ name: 'Requested by', value: requested_by, inline: true }] : []),
      ...(notes ? [{ name: 'Notes', value: notes, inline: false }] : []),
    ],
    footer: { text: `Request ID: ${id}` },
    timestamp: new Date().toISOString(),
  }

  const components = [{
    type: 1,
    components: [
      {
        type: 2,
        style: 1,  // Primary (blurple)
        label: 'Claim Poster',
        custom_id: `claim:${id}`,
        emoji: { name: '🎨' },
      },
      {
        type: 2,
        style: 3,  // Success (green)
        label: 'Mark Complete',
        custom_id: `complete:${id}`,
        emoji: { name: '✅' },
      },
      {
        type: 2,
        style: 4,  // Danger (red)
        label: '✕ Reject',
        custom_id: `reject:${id}`,
      },
    ],
  }]

  // Collect optional tag IDs — media-type tag first, then user-selected style tags
  const appliedTags: string[] = []
  const tagSecret = TAG_SECRETS[media_type]
  if (tagSecret) {
    const tagId = Deno.env.get(tagSecret)
    if (tagId) appliedTags.push(tagId)
  }
  if (Array.isArray(style_tags)) {
    for (const styleTag of style_tags) {
      const secretName = STYLE_TAG_SECRETS[styleTag]
      if (secretName) {
        const tagId = Deno.env.get(secretName)
        if (tagId) appliedTags.push(tagId)
      }
    }
  }

  // Create a forum thread (post) for this request
  const threadPayload: Record<string, unknown> = {
    name: displayTitle,
    message: { embeds: [embed], components },
  }
  if (appliedTags.length > 0) {
    threadPayload.applied_tags = appliedTags
  }

  const discordResp = await fetch(
    `https://discord.com/api/v10/channels/${DISCORD_CHANNEL_ID}/threads`,
    {
      method: 'POST',
      headers: {
        Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(threadPayload),
    }
  )

  if (!discordResp.ok) {
    const err = await discordResp.text()
    console.error('Discord API error:', err)
    return new Response('Discord error', { status: 500 })
  }

  const discordThread = await discordResp.json()

  // If a ping username was specified, resolve it to a user ID via guild member search
  if (ping_discord_username && ping_discord_username.trim().length >= 2) {
    const searchResp = await fetch(
      `https://discord.com/api/v10/guilds/${Deno.env.get('DISCORD_GUILD_ID')}/members/search?query=${encodeURIComponent(ping_discord_username.trim())}&limit=10`,
      { headers: { Authorization: `Bot ${DISCORD_BOT_TOKEN}` } },
    )
    if (searchResp.ok) {
      const members = await searchResp.json() as Array<{ user: { id: string; username: string; global_name?: string | null }; nick?: string | null }>
      const needle = ping_discord_username.trim().toLowerCase()
      const match = members.find((m) =>
        m.user.username.toLowerCase() === needle ||
        (m.user.global_name ?? '').toLowerCase() === needle ||
        (m.nick ?? '').toLowerCase() === needle
      )
      if (match) {
        const resolvedId = match.user.id
        await fetch(`https://discord.com/api/v10/channels/${discordThread.id}/messages`, {
          method: 'POST',
          headers: { Authorization: `Bot ${DISCORD_BOT_TOKEN}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({
            content: `<@${resolvedId}>`,
            allowed_mentions: { parse: [], users: [resolvedId] },
          }),
        })
      } else {
        console.warn(`[notify-discord] No exact member match found for ping username: ${ping_discord_username}`)
      }
    }
  }

  // Store the thread ID and full URL so the community page can link to it
  const threadUrl = DISCORD_GUILD_ID
    ? `https://discord.com/channels/${DISCORD_GUILD_ID}/${discordThread.id}`
    : null
  await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${id}`,
    {
      method: 'PATCH',
      headers: {
        apikey: SUPABASE_SERVICE_ROLE_KEY,
        Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        discord_message_id: discordThread.id,
        ...(threadUrl ? { discord_thread_url: threadUrl } : {}),
      }),
    }
  )

  return new Response('OK', { status: 200 })
})
