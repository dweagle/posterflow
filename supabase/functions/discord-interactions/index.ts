/**
 * discord-interactions
 *
 * Receives Discord button interaction callbacks (Claim / Complete).
 * Verifies Discord's Ed25519 signature, updates Supabase, and edits
 * the original Discord message to reflect the new status.
 *
 * This URL must be registered as the Interactions Endpoint URL
 * in your Discord Application settings.
 *
 * Required Supabase secrets:
 *   DISCORD_PUBLIC_KEY          - From Discord Developer Portal (General Information)
 *   DISCORD_BOT_TOKEN           - Bot token
 *   DISCORD_CHANNEL_ID          - Channel ID (used for context)
 *   DISCORD_MAKER_ROLE_ID       - Role ID allowed to claim/complete requests
 *   SUPABASE_URL                - Auto-provided by Supabase
 *   SUPABASE_SERVICE_ROLE_KEY   - Auto-provided by Supabase
 */

import nacl from 'https://esm.sh/tweetnacl@1.0.3'

// Cache the imported public key across warm invocations
let _cachedPublicKey: CryptoKey | null = null
async function getPublicKey(): Promise<CryptoKey> {
  if (_cachedPublicKey) return _cachedPublicKey
  _cachedPublicKey = await crypto.subtle.importKey(
    'raw',
    hexToBytes(DISCORD_PUBLIC_KEY),
    { name: 'Ed25519', namedCurve: 'Ed25519' },
    false,
    ['verify'],
  )
  return _cachedPublicKey
}

const DISCORD_PUBLIC_KEY = Deno.env.get('DISCORD_PUBLIC_KEY')!
const DISCORD_BOT_TOKEN = Deno.env.get('DISCORD_BOT_TOKEN')!
const DISCORD_MAKER_ROLE_ID = Deno.env.get('DISCORD_MAKER_ROLE_ID') ?? null
const SUPABASE_URL = Deno.env.get('SUPABASE_URL')!
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!

function hexToBytes(hex: string): Uint8Array {
  const pairs = hex.match(/.{1,2}/g) ?? []
  return new Uint8Array(pairs.map((b) => parseInt(b, 16)))
}

async function verifyDiscordSignature(
  signature: string,
  timestamp: string,
  body: string,
): Promise<boolean> {
  try {
    const encoder = new TextEncoder()
    const message = encoder.encode(timestamp + body)
    const key = await getPublicKey()
    return await crypto.subtle.verify('Ed25519', key, hexToBytes(signature), message)
  } catch {
    return false
  }
}

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

// The community poster style (CL2K/MM2K) in a request's tags / list item's tag.
// Mirrors the poster_request_style() SQL helper — CL2K wins if both appear.
function styleOf(tags: unknown): string {
  const t = Array.isArray(tags) ? tags : typeof tags === 'string' ? [tags] : []
  if (t.includes('CL2K Style') || t.includes('CL2K')) return 'CL2K'
  if (t.includes('MM2K Style') || t.includes('MM2K')) return 'MM2K'
  return ''
}

interface MirrorRow {
  tmdb_id: number | null
  media_type: string
  season_number: number | null
  title: string
  style_tags: string[] | null
}

// Cross-sync: a fulfilled request means the poster now exists in that style, so
// mark every matching community-list item (any owner) fulfilled — the same
// style-aware mirror update-request-status runs for UI completions. Only a
// SAME-style entry is mirrored (an entry with no recognized style counts as any
// style). Best-effort; never blocks the Discord flow.
async function fulfillMatchingListItems(
  row: MirrorRow,
  makerName: string,
  makerId: string | null,
): Promise<void> {
  const sbHeaders = {
    apikey: SUPABASE_SERVICE_ROLE_KEY,
    Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
    'Content-Type': 'application/json',
  }
  try {
    const params = new URLSearchParams()
    params.set('select', 'id,style_tag')
    params.set('media_type', `eq.${row.media_type}`)
    params.set('status', 'in.(open,in_progress)')
    if (row.tmdb_id != null) {
      params.set('tmdb_id', `eq.${row.tmdb_id}`)
      if (row.media_type === 'season' && row.season_number != null) {
        params.set('season_number', `eq.${row.season_number}`)
      } else {
        params.set('season_number', 'is.null')
      }
    } else {
      params.set('tmdb_id', 'is.null')
      params.set('title', `ilike.${row.title}`)
    }
    const listResp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?${params}`, {
      headers: sbHeaders,
    })
    if (!listResp.ok) {
      console.error('[discord-interactions] list mirror lookup failed:', await listResp.text())
      return
    }
    const reqStyle = styleOf(row.style_tags)
    const ids = ((await listResp.json()) as { id: string; style_tag: string | null }[])
      .filter((r) => {
        const s = styleOf(r.style_tag)
        return s === reqStyle || s === ''
      })
      .map((r) => r.id)
    if (ids.length === 0) return
    const now = new Date().toISOString()
    const resp = await fetch(`${SUPABASE_URL}/rest/v1/poster_list_items?id=in.(${ids.join(',')})`, {
      method: 'PATCH',
      headers: { ...sbHeaders, Prefer: 'return=minimal' },
      body: JSON.stringify({
        status: 'fulfilled',
        fulfilled_by: makerName,
        fulfilled_by_discord_id: makerId,
        fulfilled_at: now,
        updated_at: now,
      }),
    })
    if (!resp.ok) console.error('[discord-interactions] list mirror failed:', await resp.text())
  } catch (e) {
    console.error('[discord-interactions] list mirror error:', e)
  }
}

// Background task: persists the status change to Supabase and locks the thread
// on complete. Runs fire-and-forget after we've already returned type 7 to Discord.
async function persistButtonClick(
  interaction: Record<string, unknown>,
  action: string,
  requestId: string,
  clicker: string,
  clickerUserId: string | null,
) {
  const newStatus = action === 'claim' ? 'in_progress' : action === 'reject' ? 'rejected' : 'fulfilled'
  const updateData: Record<string, unknown> = { status: newStatus }

  if (action === 'claim') {
    updateData.claimed_by = clicker
    updateData.claimed_by_discord_id = clickerUserId
  }
  if (action === 'complete') {
    updateData.fulfilled_at = new Date().toISOString()
    updateData.fulfilled_by = clicker
  }
  if (action === 'reject') {
    updateData.fulfilled_at = new Date().toISOString()
  }
  // Schedule thread closure 24 hours from now instead of locking immediately
  if (action === 'complete' || action === 'reject') {
    updateData.close_at = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString()
  }

  // Run status update and requester-ID fetch in parallel to minimise background task duration
  const fetchRequesterPromise = (action === 'complete' || action === 'reject')
    ? fetch(
        `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}&select=requested_by_discord_id,tmdb_id,media_type,season_number,title,style_tags&limit=1`,
        {
          headers: {
            apikey: SUPABASE_SERVICE_ROLE_KEY,
            Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
          },
        },
      )
    : Promise.resolve(null)

  const [updateResp, reqResp] = await Promise.all([
    fetch(
      `${SUPABASE_URL}/rest/v1/poster_requests?id=eq.${requestId}`,
      {
        method: 'PATCH',
        headers: {
          apikey: SUPABASE_SERVICE_ROLE_KEY,
          Authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
          'Content-Type': 'application/json',
          Prefer: 'return=minimal',
        },
        body: JSON.stringify(updateData),
      },
    ),
    fetchRequesterPromise,
  ])

  if (!updateResp.ok) {
    console.error('Supabase update failed:', await updateResp.text())
  }

  let requesterDiscordId: string | null = null
  let requestRow: (MirrorRow & { requested_by_discord_id: string | null }) | null = null
  if (reqResp?.ok) {
    const rows = await reqResp.json() as (MirrorRow & { requested_by_discord_id: string | null })[]
    requestRow = rows?.[0] ?? null
    requesterDiscordId = requestRow?.requested_by_discord_id ?? null
  }

  // Mirror the completion into the community lists (same-style entries only) —
  // parity with the PosterFlow-UI complete path in update-request-status.
  if (action === 'complete' && updateResp.ok && requestRow) {
    await fulfillMatchingListItems(requestRow, clicker, clickerUserId)
  }

  // Post a farewell message and schedule thread closure in 24 hours
  if (action === 'complete' || action === 'reject') {
    const channelId = interaction.channel_id as string
    const requesterPing = requesterDiscordId ? ` <@${requesterDiscordId}>` : ''

    const closeMessage = action === 'complete'
      ? `✅ **Fulfilled by ${clicker}!** This thread will automatically close in **24 hours**. If you have any issues, please ask a moderator to reopen it.${requesterPing}`
      : `❌ **Rejected by ${clicker}.** This thread will automatically close in **24 hours**. If you have any issues, please ask a moderator to reopen it.${requesterPing}`
    const msgBody: Record<string, unknown> = { content: closeMessage }
    if (requesterDiscordId) {
      msgBody.allowed_mentions = { parse: [], users: [requesterDiscordId] }
    }
    await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
      method: 'POST',
      headers: {
        Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(msgBody),
    })
  }

  if (action === 'claim') {
    const channelId = interaction.channel_id as string
    await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
      method: 'POST',
      headers: {
        Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        content: `🎨 **Claimed by ${clicker}** — working on it!`,
      }),
    })
  }
}

Deno.serve(async (req) => {
  console.log('[discord-interactions] Request received:', req.method, req.url)
  try {
  const body = await req.text()
  const signature = req.headers.get('x-signature-ed25519') ?? ''
  const timestamp = req.headers.get('x-signature-timestamp') ?? ''
  console.log('[discord-interactions] sig present:', !!signature, 'ts present:', !!timestamp)

  // Discord requires signature verification — reject anything invalid
  if (!await verifyDiscordSignature(signature, timestamp, body)) {
    console.error('[discord-interactions] Signature verification failed')
    return new Response('Invalid signature', { status: 401 })
  }

  const interaction = JSON.parse(body)
  console.log('[discord-interactions] type:', interaction.type, 'custom_id:', (interaction.data as Record<string,unknown>)?.custom_id)

  // Type 1 = PING (Discord endpoint verification)
  if (interaction.type === 1) {
    return jsonResponse({ type: 1 })
  }

  // Type 3 = MESSAGE_COMPONENT (button click)
  if (interaction.type === 3) {
    // Extract clicker info synchronously from the interaction payload
    const member = interaction.member as Record<string, unknown> | undefined
    const user = interaction.user as Record<string, unknown> | undefined
    const memberUser = member?.user as Record<string, unknown> | undefined
    const clickerUserId =
      (memberUser?.id as string) ??
      (user?.id as string) ??
      null
    const clicker =
      (memberUser?.global_name as string) ??
      (memberUser?.username as string) ??
      (user?.global_name as string) ??
      (user?.username as string) ??
      'a maker'

    // Parse custom_id early so we can route before the role check
    const data = interaction.data as Record<string, string>
    const customId = data?.custom_id ?? ''

    // Handle close_thread button — open to thread owner or any admin, not role-gated
    if (customId === 'close_thread') {
      const channel = interaction.channel as Record<string, unknown> | undefined
      const threadOwnerId = channel?.owner_id as string | undefined
      const perms = BigInt((member?.permissions as string) ?? '0')
      const isAdmin = (perms & BigInt(0x8)) === BigInt(0x8)
      const isThreadOwner = threadOwnerId != null && clickerUserId === threadOwnerId

      if (!isAdmin && !isThreadOwner) {
        return jsonResponse({
          type: 4,
          data: {
            content: 'Only the person who opened this thread or a server administrator can close it.',
            flags: 64,
          },
        })
      }

      const channelId = interaction.channel_id as string
      const bgTask = (async () => {
        await new Promise((resolve) => setTimeout(resolve, 1000))
        const lockResp = await fetch(`https://discord.com/api/v10/channels/${channelId}`, {
          method: 'PATCH',
          headers: {
            Authorization: `Bot ${DISCORD_BOT_TOKEN}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ locked: true, archived: true }),
        })
        if (!lockResp.ok) {
          console.error('[discord-interactions] close_thread lock failed:', await lockResp.text())
        }
      })().catch(console.error)

      try {
        // @ts-ignore - EdgeRuntime is a Supabase-specific global
        EdgeRuntime.waitUntil(bgTask)
      } catch {
        // EdgeRuntime may not be available in all environments; ignore
      }

      return jsonResponse({
        type: 4,
        data: { content: '✅ This thread has been closed and locked.' },
      })
    }

    // Role check — synchronous, role list is in the interaction payload
    if (DISCORD_MAKER_ROLE_ID) {
      const memberRoles = (member?.roles as string[] | undefined) ?? []
      if (!memberRoles.includes(DISCORD_MAKER_ROLE_ID)) {
        return jsonResponse({
          type: 4,
          data: {
            content: '⚠️ Only members with the Poster Maker role can claim or complete requests.',
            flags: 64,
          },
        })
      }
    }
    const parts = customId.split(':')
    const action = parts[0] ?? ''
    const requestId = parts[1] ?? ''
    const encodedClaimerId = parts[2] ?? ''

    if (!['claim', 'complete', 'reject'].includes(action) || !requestId) {
      return jsonResponse({ type: 1 })
    }

    // Claimer check for 'complete' — synchronous, claimer ID encoded in custom_id
    if (action === 'complete' && encodedClaimerId && encodedClaimerId !== clickerUserId) {
      return jsonResponse({
        type: 4,
        data: {
          content: '⚠️ Only the maker who claimed this request can mark it as complete.',
          flags: 64,
        },
      })
    }

    // Build the updated embed synchronously from the interaction payload.
    // Using type 7 (UPDATE_MESSAGE) updates the Discord message immediately in
    // this response — no deferred gap, no "Interaction Failed" flash.
    const message = interaction.message as Record<string, unknown> | undefined
    const embeds = message?.embeds as Record<string, unknown>[] | undefined
    const originalEmbed = embeds?.[0] ?? {}

    const statusLabel =
      action === 'claim' ? `🎨 Claimed by ${clicker}` :
      action === 'reject' ? `❌ Rejected by ${clicker}` :
      `✅ Completed by ${clicker}`
    const newColor =
      action === 'claim' ? 0xffb74d :
      action === 'reject' ? 0x9e9e9e :
      0x4caf50

    const updatedEmbed = {
      ...originalEmbed,
      color: newColor,
      fields: ((originalEmbed.fields ?? []) as { name: string; value: string; inline?: boolean }[]).map((f) =>
        f.name === 'Status' ? { ...f, value: statusLabel } : f
      ),
    }

    // After complete: remove all buttons.
    // After reject: reset to fresh all-enabled state so a mod can unlock and
    //   have a maker re-claim or reject again. Thread is still locked by persistButtonClick.
    // After claim: disable Claim, encode claimer ID in Complete custom_id.
    const freshButtons = [
      {
        type: 2,
        style: 1,
        label: 'Claim Poster',
        custom_id: `claim:${requestId}`,
        emoji: { name: '🎨' },
      },
      {
        type: 2,
        style: 3,
        label: 'Mark Complete',
        custom_id: `complete:${requestId}`,
        emoji: { name: '✅' },
      },
      {
        type: 2,
        style: 4,
        label: '✕ Reject',
        custom_id: `reject:${requestId}`,
      },
    ]
    const updatedComponents = action === 'complete'
      ? []
      : action === 'reject'
      ? [{ type: 1, components: freshButtons }]
      : [{
          type: 1,
          components: [
            {
              type: 2,
              style: 2,
              label: 'Claim Poster',
              custom_id: `claim:${requestId}`,
              emoji: { name: '🎨' },
              disabled: true,
            },
            {
              type: 2,
              style: 3,
              label: 'Mark Complete',
              custom_id: `complete:${requestId}:${clickerUserId ?? ''}`,
              emoji: { name: '✅' },
            },
            {
              type: 2,
              style: 4,
              label: '✕ Reject',
              custom_id: `reject:${requestId}`,
            },
          ],
        }]

    // Fire-and-forget: persist to Supabase and lock thread (if complete).
    // EdgeRuntime.waitUntil keeps the task alive after the response is sent.
    const bgTask = persistButtonClick(interaction, action, requestId, clicker, clickerUserId).catch(console.error)
    try {
      // @ts-ignore - EdgeRuntime is a Supabase-specific global
      EdgeRuntime.waitUntil(bgTask)
    } catch {
      // EdgeRuntime may not be available in all environments; ignore
    }

    // Type 7 = UPDATE_MESSAGE — immediately replaces the message in this response.
    // No deferred gap, no "Interaction Failed" flash.
    return jsonResponse({
      type: 7,
      data: {
        embeds: [updatedEmbed],
        components: updatedComponents,
      },
    })
  }

  // Type 2 = APPLICATION_COMMAND (slash command)
  if (interaction.type === 2) {
    const commandName = (interaction.data as Record<string, unknown>)?.name as string | undefined

    if (commandName === 'archive') {
      const channelId = interaction.channel_id as string | undefined
      if (!channelId) {
        return jsonResponse({
          type: 4,
          data: { content: '⚠️ Could not determine the current channel.', flags: 64 },
        })
      }

      const member = interaction.member as Record<string, unknown> | undefined
      const user = interaction.user as Record<string, unknown> | undefined
      const memberUser = member?.user as Record<string, unknown> | undefined
      const username =
        (memberUser?.global_name as string) ??
        (memberUser?.username as string) ??
        (user?.global_name as string) ??
        (user?.username as string) ??
        'Someone'

      // Fire-and-forget: short delay then lock + archive so Discord has time
      // to render the response message before the thread becomes archived
      const bgTask = (async () => {
        await new Promise((resolve) => setTimeout(resolve, 1500))
        const lockResp = await fetch(
          `https://discord.com/api/v10/channels/${channelId}`,
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
          console.error(`[discord-interactions] /archive failed for channel ${channelId}:`, await lockResp.text())
        }
      })().catch(console.error)

      try {
        // @ts-ignore - EdgeRuntime is a Supabase-specific global
        EdgeRuntime.waitUntil(bgTask)
      } catch {
        // EdgeRuntime may not be available in all environments; ignore
      }

      // Respond instantly with a public message — lock happens in background
      return jsonResponse({
        type: 4,
        data: { content: `🔒 This thread has been locked and archived by ${username}.` },
      })
    }
  }

  return jsonResponse({ type: 1 })
  } catch (err) {
    console.error('[discord-interactions] Unhandled error:', err)
    // Return a valid type 4 ephemeral message so Discord doesn't show "Interaction Failed"
    return jsonResponse({
      type: 4,
      data: { content: 'An error occurred. Please try again.', flags: 64 },
    })
  }
})
