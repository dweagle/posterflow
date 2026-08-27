/**
 * close-threads
 *
 * Intended to be called on a schedule (e.g. every hour via pg_cron).
 * Finds all poster_requests whose close_at timestamp has passed,
 * locks + archives the Discord forum thread, then clears close_at
 * so the record isn't processed again.
 *
 * Also re-fires notify-discord for recent requests that never got a
 * forum thread — the INSERT webhook goes through pg_net, which does
 * not retry, so a transient edge-runtime 503 drops the post.
 *
 * Required Supabase secrets:
 *   DISCORD_BOT_TOKEN         - Bot token
 *   SUPABASE_URL              - Auto-provided by Supabase
 *   SUPABASE_SERVICE_ROLE_KEY - Auto-provided by Supabase
 */

const DISCORD_BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

const SUPABASE_HEADERS = {
  apikey: SUPABASE_SERVICE_ROLE_KEY,
  Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
  'Content-Type': 'application/json',
}

Deno.serve(async (_req) => {
  // Fetch requests whose scheduled close time has passed and have a Discord thread
  const now = new Date().toISOString()
  const fetchResp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?close_at=lte.${encodeURIComponent(now)}&discord_message_id=not.is.null&select=id,discord_message_id`,
    { headers: SUPABASE_HEADERS },
  )

  if (!fetchResp.ok) {
    const err = await fetchResp.text()
    console.error('[close-threads] Failed to fetch pending closures:', err)
    return new Response('Supabase fetch error', { status: 500 })
  }

  const requests: { id: string; discord_message_id: string }[] = await fetchResp.json()
  console.log(`[close-threads] Found ${requests.length} thread(s) to close`)

  let closed = 0
  for (const request of requests) {
    // Lock + archive the Discord forum thread
    const lockResp = await fetch(
      `https://discord.com/api/v10/channels/${request.discord_message_id}`,
      {
        method: 'PATCH',
        headers: {
          Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ locked: true, archived: true }),
      },
    )

    if (!lockResp.ok) {
      const err = await lockResp.text()
      console.error(`[close-threads] Failed to lock thread ${request.discord_message_id}:`, err)
      // Continue — try the rest even if one fails
      continue
    }

    // Clear close_at so this record isn't processed on the next run
    const clearResp = await fetch(
      `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${request.id}`,
      {
        method: 'PATCH',
        headers: { ...SUPABASE_HEADERS, Prefer: 'return=minimal' },
        body: JSON.stringify({ close_at: null }),
      },
    )

    if (!clearResp.ok) {
      console.error(`[close-threads] Failed to clear close_at for ${request.id}:`, await clearResp.text())
    } else {
      closed++
      console.log(`[close-threads] Closed thread for request ${request.id}`)
    }
  }

  // Re-fire dropped INSERT webhooks. Bounded to the last 24h so stale
  // threadless rows stay untouched, and 2+ minutes old so an in-flight
  // submission isn't double-posted.
  const sweepFrom = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString()
  const sweepTo = new Date(Date.now() - 2 * 60 * 1000).toISOString()
  let notified = 0
  const missedResp = await fetch(
    `${SUPABASE_URL}/rest/v1/poster_requests?discord_message_id=is.null&created_at=gte.${encodeURIComponent(sweepFrom)}&created_at=lte.${encodeURIComponent(sweepTo)}&select=id,title&limit=20`,
    { headers: SUPABASE_HEADERS },
  )
  if (!missedResp.ok) {
    console.error('[close-threads] Failed to fetch threadless requests:', await missedResp.text())
  } else {
    const missed: { id: string; title: string }[] = await missedResp.json()
    if (missed.length > 0) {
      console.log(`[close-threads] Found ${missed.length} request(s) missing a Discord thread`)
    }
    for (const request of missed) {
      const notifyResp = await fetch(`${SUPABASE_URL}/functions/v1/notify-discord`, {
        method: 'POST',
        headers: SUPABASE_HEADERS,
        body: JSON.stringify({ record: { id: request.id } }),
      })
      if (notifyResp.ok) {
        notified++
        console.log(`[close-threads] Re-fired notify-discord for "${request.title}" (${request.id})`)
      } else {
        console.error(
          `[close-threads] notify-discord re-fire failed for "${request.title}" (${request.id}):`,
          await notifyResp.text(),
        )
      }
    }
  }

  return new Response(
    JSON.stringify({ processed: requests.length, closed, notified }),
    { status: 200, headers: { 'Content-Type': 'application/json' } },
  )
})
