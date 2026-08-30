# PosterFlow

A self-hosted poster management system that syncs and manages movie and TV show posters across Google Drive using rclone. Includes a FastAPI backend and React/TypeScript frontend, packaged as a single Docker container.

PosterFlow is heavily inspired by [DAPS by Drazzilb](https://github.com/Drazzilb08/daps)

See the Wiki for more information [Wiki](https://github.com/dweagle/posterflow/wiki)

Community drive recommendations — poster drives, artwork drives and the priority baseline: [Google Drives](https://dweagle.github.io/posterflow/gdrives/) ([source](docs/))

## Dashboard
![dashboard](https://github.com/user-attachments/assets/91a834b3-0652-440d-bc11-6535eb9c627c)
*The dashboard is the top landing spot when accessing the app. The sidebar exposes every top-level surface.*

## Features

- **Drive Syncing** — Subscribe to community preset drives (MM2K, CL2K, etc.) or add your own custom Google Drive sources
- **Poster Renamer** — Automatically renames downloaded posters to match your Plex/Jellyfin/Radarr/Sonarr library
- **Border Replacer** — Replaces poster borders in bulk
- **Unmatched Assets** — Detects and reports assets in your library that are missing posters. TMDB links for missing items
- **Asset Upload** — Upload posters and artwork directly to your Plex and Jellyfin libraries
- **IDarr** — Metadata enrichment pipeline for poster-maker assets (TMDB/TVDB/IMDB ID assignment and rename normalization)
- **Community Requests** — Shared request board where users submit missing posters for makers to claim and fulfill. Discord integration auto-fills your username and unlocks an upload button for verified poster makers
- **Maker Tools** — Poster-makers can monitor upcoming movie and TV releases to track missing posters
- **Scheduler** — Automate any job on a recurring schedule
- **Live Job Status** — WebSocket-powered real-time job progress and log streaming
- **After Job Scripts** — Autorun a custom script after a completed job
- **Discord Notifications** — Optional notifications on job completion

## Quick Start

The setup wizard runs on first launch to configure your media server connections and Google Drive credentials.

## Access

| URL | Description |
|-----|-------------|
| `http://localhost:8357` | Main application |

### Install
Docker is the recommended and most-tested route; an Unraid template is available in CA.
Prefer no Docker? PosterFlow also runs natively -
see [docs/native-install.md](docs/native-install.md).

## Docker Compose

Currently only a develop tag exists
| Tag | Source branch | Updated |
|---|---|---|
| `develop` | `develop` | When a maintainer runs the workflow against `develop`. |

## docker-compose.yml

The canonical compose file. Every key is explained inline.

```yaml
services:
  posterflow:
    image: dweagle/posterflow:develop
    container_name: posterflow
    ports:
      - "8357:8000"
    volumes:
      # The only required mount. Holds posterflow.db, rclone.conf, drives_cache.json,
      # the default poster cache (/config/posters/gdrive/), /config/logs/,
      # /config/scripts/ and /config/idarr/. Back this directory up.
      - ./config:/config

      # Optional. Mount your Kometa assets directory here (or anywhere) and point
      # the renamer's "Destination" setting at it. Without this mount, PosterFlow
      # writes its organized output under /config/posters/assets/ and you have
      # to copy it out yourself.
      - /path/to/kometa/assets:/assets

      # Optional, poster-makers only. Mount the directory where you stage posters
      # you intend to upload to your personal Google Drive via IDarr.
      - /path/to/idarr/staging:/idarr
      
    environment:
      - PUID=1000        # Host UID that owns the mount points above.
      - PGID=1000        # Host GID. Both default to 1000 if unset.
      - TZ=America/New_York   # Host timezone. Drives scheduler local-time interpretation.
      - DEBUG=false      # Optional. true forces file logging to DEBUG on startup.
      - LOG_LEVEL=INFO   # Optional. File log level when DEBUG=false.
      - ALLOWED_FRAME_ORIGINS=  # Optional. Comma-separated origins allowed to embed the app in an iframe (e.g. http://organizr.local:8080).
    restart: unless-stopped
```

### Equivalent `docker run`

```bash
docker run -d \
  --name posterflow \
  -p 8357:8000 \
  -v /srv/posterflow/config:/config \
  -v /srv/kometa/assets:/assets \
  -e PUID=1000 -e PGID=1000 \
  -e TZ=America/New_York \
  --restart unless-stopped \
  dweagle/posterflow:develop
```

## Volumes

| Container path | Required? | Created by | Purpose |
|---|---|---|---|
| `/config` | Yes | The entrypoint `chown`s this and the app `mkdir`s `/config/logs`, `/config/idarr` and `/config/scripts` on every boot (see `backend/core/config.py`). | SQLite DB at `/config/posterflow.db`, WAL/SHM sidecars next to it, `rclone.conf`, `drives_cache.json`, the default GDrive cache at `/config/posters/gdrive/`, logs at `/config/logs/posterflow.log` (plus per-job log dirs), after-job scripts at `/config/scripts/`, IDarr working dir at `/config/idarr/`. |
| `/assets` (or anywhere) | No | You. | Destination for organized, renamed, bordered posters. Point Kometa's `asset_directory` at the same host path. |
| `/idarr` (or anywhere) | No | You. | Poster-maker staging area for IDarr. Used only if you operate the IDarr workflow. |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `1000` | User ID for file ownership |
| `PGID` | `1000` | Group ID for file ownership |
| `TZ` | `UTC` | Timezone |
| `DEBUG` | `false` | Enable debug logging on startup (can be toggled in-app) |
| `LOG_LEVEL` | `INFO` | File log verbosity when debug mode is off |
| `ALLOWED_FRAME_ORIGINS` | *(empty)* | Comma-separated origins allowed to embed the app in an iframe (e.g. an Organizr/Homarr dashboard). Format `http(s)://host[:port]` — no paths or wildcards; invalid entries are ignored with a startup warning. Empty keeps embedding blocked for all other sites. |

> Debug mode state is persisted in the database and restored on restart. You can toggle it from Settings without restarting the container.
