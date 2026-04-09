# PosterFlow

A self-hosted poster management system that syncs and manages movie and TV show posters across Google Drive using rclone. Includes a FastAPI backend and React/TypeScript frontend, packaged as a single Docker container.

PosterFlow is heavily inspired by [DAPS by Drazzilb](https://github.com/Drazzilb08/daps)

## Features

- **Drive Syncing** — Subscribe to community preset drives (MM2K, CL2K, etc.) or add your own custom Google Drive sources
- **Poster Renamer** — Automatically renames downloaded posters to match your Plex/Radarr/Sonarr library
- **Border Replacer** — Replaces poster borders in bulk
- **Unmatched Assets** — Detects and reports assets in your library that are missing posters. TMDB links for missing items
- **Plex Upload** — Upload posters directly to Plex libraries
- **IDarr** — Metadata enrichment pipeline for poster-maker assets (TMDB/TVDB/IMDB ID assignment and rename normalization)
- **Maker Tools** — Poster-makers can monitor upcoming movie and TV releases to track missing posters
- **Scheduler** — Automate any job on a recurring schedule
- **Live Job Status** — WebSocket-powered real-time job progress and log streaming
- **Discord Notifications** — Optional notifications on job completion

## Quick Start

The setup wizard runs on first launch to configure your media server connections and Google Drive credentials.

## Access

| URL | Description |
|-----|-------------|
| `http://localhost:8357` | Main application |

## Docker Compose

```yaml
services:
  posterflow:
    image: dweagle/posterflow:develop
    container_name: posterflow
    ports:
      - 8357:8000
    volumes:
    # These are default locations for app files, downloading from gdrives and renaming.  If
    # you are using kometa assets folder, or want your posters stored in a different location,
    # mount those locations.
      - path/to/config:/config   # Database, rclone config, logs, and idarr working dir
      - path/to/kometa/assets:/assets # in poster manager settings, set destination directory to this location
      - path/to/idarr/posters:/idarr # for poster makers only -location of posters you want to sync up to gdrive
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
```
## Volumes

| Path | Purpose |
|------|---------|
| `/config` | SQLite database, rclone config, drives cache, logs, and idarr working dir |
| `/posters` | Downloaded GDrive poster files (`gdrive/` for drive syncs, `assets/` for renamed posters) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PUID` | `1000` | User ID for file ownership |
| `PGID` | `1000` | Group ID for file ownership |
| `TZ` | `UTC` | Timezone |
| `DEBUG` | `false` | Enable debug logging on startup (can be toggled in-app) |
| `LOG_LEVEL` | `INFO` | File log verbosity when debug mode is off |
| `CORS_ORIGINS` | *(localhost defaults)* | Comma-separated allowed browser origins |

> Debug mode state is persisted in the database and restored on restart. You can toggle it from Settings without restarting the container.