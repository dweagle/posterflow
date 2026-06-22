# Supabase edge functions (`supabase/functions/`)

The community backend's serverless functions (Deno), running in the shared
Supabase project (`qwudwkxfqowjtisdlplv`). They sit in front of the tables in
[`../sql/`](../sql) — all writes go through these (service role), and they verify
a signed Discord token before doing anything privileged.

All functions set `verify_jwt = false` in [`../config.toml`](../config.toml): they
do their **own** auth (signed Discord token, Discord interaction signature, or DB
webhook), not Supabase's built‑in JWT gate.

## Functions

| Function | Purpose | Invoked by | Extra secrets (beyond the auto ones) |
|----------|---------|------------|--------------------------------------|
| `discord-oauth` | OAuth popup: exchange Discord code, confirm guild membership + Maker role, **mint the signed app token** | Browser (popup) | `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MAKER_ROLE_ID`, `DISCORD_JWT_SECRET` |
| `submit-request` | Insert a poster request (RPC) + per‑user/IP rate limits | FastAPI proxy (`/api/community/requests`) | `DISCORD_JWT_SECRET` |
| `submit-list-items` | Bulk‑publish items to the Lists tab | FastAPI proxy (`/api/community/lists`) | `DISCORD_JWT_SECRET` |
| `update-request-status` | claim / complete / reject / close / **remove** / **release** + Discord thread updates | Browser (direct) | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MAKER_ROLE_ID`, `DISCORD_JWT_SECRET` |
| `update-list-item` | claim / complete / release / reject / remove / **remove_mine** for list items | Browser (direct) | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MAKER_ROLE_ID`, `DISCORD_JWT_SECRET` |
| `post-poster` | Maker uploads finished poster file(s) to a request's thread | Browser (direct) | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_MAKER_ROLE_ID`, `DISCORD_JWT_SECRET` |
| `notify-discord` | Create the forum thread when a request is inserted | **DB webhook** on `poster_requests` INSERT | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID` |
| `discord-interactions` | Handle Discord button clicks (claim/complete/reject from Discord) | Discord (interaction webhook) | `DISCORD_PUBLIC_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_MAKER_ROLE_ID` |
| `close-threads` | Archive threads whose `close_at` has passed | Scheduled (cron) | `DISCORD_BOT_TOKEN` |

Every function also uses `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, which
Supabase **injects automatically** — do not set those by hand.

## Secrets reference

Set these once with `supabase secrets set` (they're shared by all functions in the
project). Auto‑injected ones are marked.

| Secret | What it is / where to get it |
|--------|------------------------------|
| `DISCORD_JWT_SECRET` | Random HMAC‑SHA256 key used to **sign** the app's session token (`discord-oauth`) and **verify** it everywhere else. Must be identical across functions; rotating it logs everyone out. |
| `DISCORD_BOT_TOKEN` | Bot token (Developer Portal → Bot). Posts/edits messages, checks roles, locks/archives threads, looks up the guild owner. |
| `DISCORD_PUBLIC_KEY` | App public key (Developer Portal → General Information). Verifies incoming Discord **interaction** signatures (`discord-interactions`). |
| `DISCORD_CLIENT_ID` | OAuth2 client id (Developer Portal → OAuth2). |
| `DISCORD_CLIENT_SECRET` | OAuth2 client secret (Developer Portal → OAuth2). |
| `DISCORD_GUILD_ID` | The PosterFlow Discord server (guild) id. Used for role checks + owner lookup. |
| `DISCORD_MAKER_ROLE_ID` | The "Poster Maker" role id in that guild. |
| `DISCORD_CHANNEL_ID` | The forum channel id where request threads are created. |
| `SUPABASE_URL` | **Auto‑injected.** |
| `SUPABASE_SERVICE_ROLE_KEY` | **Auto‑injected.** Bypasses RLS — never expose client‑side. |

```bash
# Set / update a secret (example)
supabase secrets set DISCORD_JWT_SECRET='…' DISCORD_BOT_TOKEN='…'
# Verify what's set
supabase secrets list
```

### Bot requirements
The bot must be **in the guild** and have permissions to: View Channels, Send
Messages / Create Posts (forum), Manage Messages (edit the starter embed +
buttons), and **Manage Threads** (lock/archive on close/remove). Role checks use
the REST guild‑member lookup, so the bot needs guild membership (no gateway intent
needed for the REST calls).

### notify-discord webhook secret (separate)
`notify-discord` is fired by a **Database Webhook** (Database → Webhooks, the
`Posterflow` trigger on `poster_requests`). Its `Authorization: Bearer …` header
is configured **on the trigger**, not in `supabase secrets` — keep that value out
of git (it's redacted in [`../sql/poster_requests.sql`](../sql/poster_requests.sql)).

## Deploy

```bash
# Deploy one
supabase functions deploy update-request-status
# Deploy several
supabase functions deploy submit-list-items update-list-item update-request-status
```

`config.toml` carries the `verify_jwt = false` setting per function, so the CLI
applies it on deploy. The `deno.json` in this folder pins the Deno compiler libs.
