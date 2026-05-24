# Changelog

All notable changes to PosterFlow will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.2] - 2026-05-24
### Added
- Maker Tools: TMDB Search poster availability check — each search result now shows a visual indicator when a poster image is available in synced drives (only searches synced drives in the database)

### Fixed
- Plex Upload: show title normalization improved to correctly handle multiple missing seasons and episodes
- IDarr: uploading a file that already exists now archives the existing file to a duplicates directory instead of silently adding numbers to file names
- Flow / Unmatched Assets: collections missing count is now included in Discord job summary notifications

## [0.5.1] - 2026-05-23
### Added
- Community Requests: sidebar badge now shows the count of pending community poster requests
- Community Requests: Discord users will be pinged when their request is fulfilled

### Fixed
- Community Requests: Discord thread embeds now update correctly when request status is changed from within the app

## [0.5.0] - 2026-05-22
### Added
- Community Poster Requests: new Supabase-backed workflow for submitting and fulfilling poster requests via Discord — community members can now submit requests through Unmatched Assets; a Supabase webhook opens a Discord forum thread with Claim/Complete, etc. buttons; makers authenticate via Discord OAuth2, upload finished posters, and update request status in app, with all actions reflected in the Discord thread
- Settings: delete schedule now shows a confirmation modal before removing the schedule

### Fixed
- Maker Tools: TMDB search button label corrected from "TMDB" to "Maker" on the Monitor pages

### Requirements
- `starlette` 0.52.1 → 1.0.1
- `fastapi` 0.129.0 → 0.136.1 (required for starlette ≥ 1.0)
- `cairosvg` 2.7.1 → 2.9.0

## [0.4.4] - 2026-05-18
### Added
- Maker Tools TMDB Search: SVG poster images from TMDB are now automatically converted to PNG before display and download.
### Fixed
- Maker Tools: Photopea **File→Save / Ctrl+S** now correctly returns the save script in the response so the PSD is written to disk on a successful save.  When closing the tab it still prompted/warned that file wasn't saved.
- Setup: updated Google Cloud project setup instructions to match current Google Cloud console UI

## [0.4.3] - 2026-05-17
### Added
- IDarr: file upload now accepts `.psd` files in addition to `.jpg`, `.jpeg`, `.png`, and `.webp`

### Fixed
- Maker Tools: Photopea **File→Save / Ctrl+S** now works when opening an exported PSD — the hash config was missing the `server.url` directive.

## [0.4.2] - 2026-05-17
### Added
- Maker Tools: new **PSD Export** tools included on TMDB Search page — configure a PSD template path and output folder; toggle poster, backdrop, and logo layers per item; confirm overwrites via modal before writing; a default `default_template.psd` based on the community template is bundled for quick use.
- Maker Tools TMDB Search configuration button: new PSD export folder and template path settings fields; users may have to add new mount in docker config for PSD file location if desired.
- Maker Tool TMDB Search: Photopea integration - Users can toggle settings to allow PSDs to automatically open a new Photopea tab in their browser for PSD export/editing. Photoshop cannot be configured due to limitations of PosterFlow running in a Docker container.
- Maker Tools TMDB Search Button: Added buttons in Monitor Series/Discovery that will search items on the TMDB Search tab for quick workflow. Buttons are also available in Unmatched Assets and Poster Style modals.

### Fixed
- WebSocket: improved shutdown handling so in-flight connections are closed cleanly when the server stops
- Exception handling: consistent logging of unexpected exceptions across jobs, scheduler, queue, modules, and services

## [0.4.1] - 2026-05-15
### Added
- TMDB Search: Copy Title button now copies the title with year in parentheses e.g. `Breaking Bad (2008)`

## [0.4.0] - 2026-05-15
### Added
- Maker Tools: new **TMDB Search** tab — search movies, TV shows, and collections by name (with optional year filter); browse TMDB and Apple TV poster images per result including season-level images for TV; copy TMDB/IMDB/TVDB IDs and poster URLs directly from results
- Sidebar: Idarr nav item now accepts drag-and-drop file uploads — drop poster files directly onto the IDarr sidebar link to queue them for IDarr processing without navigating away; also supports a drive picker fallback if not in local page storage; IDarr page refreshes automatically on completion

### Fixed
- Unmatched Assets: titles containing `&` or `&amp;` now normalize correctly so "Title & Title" matches an on-disk folder named "Title and Title"
- Unmatched Assets: fixed a key mismatch (`normalized_folder` vs `normalized_folder_title`) that caused the normalized-folder fallback in `is_match` to always return `None`
- Sidebar: overflow and release notes popover positioning corrected for improved visibility.  Release notes popover was clipped after changes made to improve small screen viewing in last update.

### Changed
- Bumped `python-multipart` 0.0.22 → 0.0.27
- Bumped `Pillow` 12.1.1 → 12.2.0

## [0.3.0] - 2026-05-13
### Added
- Poster Manager: TMDB search results in the Unmatched Assets modal and Poster Style fallback modal now include a copy title button.

### Changed
- UI: app-wide responsive layout refactor — all major pages (Dashboard, GDrives, IDarr, Logs, Maker Tools, Settings, Poster Manager) and the Sidebar now adapt cleanly to narrow screens
- Poster Manager: tabs and inner components (Flow, Renamer, Border, Unmatched, Priority, Settings) refactored with a shared Toolbar component for consistent narrow-screen layout
- Poster Manager: TMDB link row shows the full URL with Open / Copy / Title action buttons alongside it.  Buttons wrap to next line on narrow screens.

## [0.2.14] - 2026-05-12
### Fixed
- Plex Upload: fixed a race condition where a season pack webhook fired before Plex finished scanning the season folder — the show would match in Plex but the season entry didn't exist yet, causing the job to exit silently with no upload; the job now retries until the season appears

## [0.2.13] - 2026-05-10
### Changed
- Plex Upload: file mtime is now stored in upload records — a fast `stat()` check skips the sha256 file read for unchanged posters, eliminating multi-GB page cache accumulation on large upload runs
- Plex Upload: page cache is evicted after each poster is uploaded so memory pressure stays low throughout the job
- Border Replacer / Poster Renamer: page cache is evicted after PIL reads each image file, reducing residual memory after large batches
- Plex Upload / Poster Sync: stale record pruning now queries only the columns needed and deletes in a single batch instead of row-by-row

## [0.2.12] - 2026-05-09
### Added
- IDarr: new **Force Sync** option — when enabled, the drive sync runs after an IDarr job even if no files were renamed, useful for ensuring the drive is always up to date
- IDarr: **Run and Sync** button and IDarr schedule configuration both support the new force sync flag
- Process names for the API and worker threads are now set via `setproctitle`, making them easier to identify in system process lists

### Changed
- Poster Manager: Top page tabs and Workflow tab page refined for better readability on narrow screens (More narrow screen improvements coming across app.)

## [0.2.11] - 2026-05-08
### Fixed
- Plex Upload: Discord notifications (success and error) are no longer sent when the upload step runs as part of the Poster Manager workflow — only the workflow-level notification fires

## [0.2.10] - 2026-05-08
### Changed
- Memory usage is reduced after large jobs (sync, rename, etc.) by releasing freed memory back to the OS when a job finishes
- Rclone output is no longer buffered in full during syncs, reducing peak memory during large transfers
- Log viewer no longer reads the entire log file on connect — only the last 200 KB is loaded

### Fixed
- Plex Upload: internal caches are now cleared at the end of every upload job so stale data doesn't carry over into the next run

## [0.2.9] - 2026-05-07
### Added
- IDarr: **Sync Folder** input now shows an info tooltip explaining that an absolute container-side path is required (e.g. `/config/idarr/sync/cl2k`), preventing silent failures from relative paths

### Fixed
- IDarr: file upload endpoint now returns a clear HTTP 400 error with an actionable message when the sync folder cannot be created or accessed (e.g. bad path, permission denied), instead of crashing with an unhandled exception
- IDarr Runner: TMDB API key is now redacted from error log messages during enrichment failures, preventing accidental key leakage in logs

## [0.2.8] - 2026-05-06
### Added
- Discord Notifications: new **Ping on info** toggle for global and per-feature ping targets — allows pinging on informational notifications separately from success and error
- Discord Notifications: **"What gets sent?"** collapsible disclosure added to each feature row, showing the event types and descriptions for every notification that feature can send
- Discord Notifications: **"How does it work?"** collapsible disclosure on the global webhook section, now includes step-by-step instructions for finding your webhook URL, user IDs, and role IDs
- Discord Notifications: webhook URL and ping target inputs now display side-by-side on wide screens and stack on narrow screens
- Plex Upload: added success Discord notification on full upload completion (movies / shows / seasons / collections uploaded count)
- Plex Upload: added error Discord notification when a full upload job crashes

### Fixed
- IDarr Runner: asset type classification for series now correctly distinguishes between series, seasons, and episodes

## [0.2.7] - 2026-05-05
### Added
- Setup Wizard: added a step for entry of TMDB API key during setup.

### Changed
- Setup Wizard: old steps 4 (Destination) and 5 (Finish) renumbered to 5 and 6 to accommodate the new TMDB step
- Dashboard: poster stats section refactored for improved readability and layout on narrow/small screens
- Dashboard: quick actions and poster coverage cards refactored for better layout on small screens

## [0.2.6] - 2026-05-05
### Added
- Poster Manager Workflow: new **Upload to Plex** step (step 4) — automatically uploads changed posters to Plex after sync, rename, and border replacement complete. Uses hash-based change detection so only new or updated posters are uploaded. Disabled by default.
- Poster Manager Workflow: Plex Upload step now runs before Detect Unmatched Assets (order: Sync → Rename → Border Replacer → Upload to Plex → Detect Unmatched)
- Discord Notifications: new **mention** and **webhook URL** configuration options per notification type, allowing different webhooks and role/user pings for different events
- Settings → Maintenance: database cleanup now shows detailed reasons for each orphaned record, making it easier to understand what will be removed before confirming
- Maker Tools: added description for the **Enable New Releases** discovery option

### Fixed
- IDarr: sidebar pending count now updates correctly after items are resolved
- Sidebar: release notes display and version tracking improvements — changelog is fetched from the backend and the "what's new" badge is cleared after viewing

## [0.2.5] - 2026-05-04
### Changed
- Priority tab: style usage stats now treat the first MM2K or CL2K drive in the priority list as the preferred style, so a custom drive at the top of the list no longer hijacks the preferred/fallback framing
- Priority tab: fallback cards are now limited to MM2K and CL2K only — custom drive fallback entries are intentional overrides and no longer shown as a separate card
- Priority tab: style badge widths in the bar chart are now fixed so all bars start at the same horizontal position regardless of label length (e.g. "CUSTOM" vs "MM2K")

## [0.2.4] - 2026-05-04
### Added
- Poster Manager: style usage statistics — after each rename run, a per-style breakdown of how many posters came from each drive style is stored and surfaced in the Priority tab on the Poster Manager page
- Poster Manager: **Style Usage** modal showing poster counts and fallback item lists per drive style, accessible from the Priority tab
- Poster Manager: download button for the missing posters list on Priority tab

### Fixed
- TMDB search: trailing `(YYYY)` is now stripped from the title when a year is also supplied separately, preventing double-year searches for shows with a year in their name in Sonarr/Radarr (e.g. "INVINCIBLE (2021)" + year 2021)
- Various: added log warnings when TMDB API key is missing in IDarr, Jobs, Maker Tools, and Poster Manager modules instead of silently failing

## [0.2.3] - 2026-05-04
### Fixed
- IDarr: TMDB API key was not being injected into the job config when starting a run via the API or scheduler, causing all IDarr jobs to fail with "TMDB API key is required" after the 0.2.1 consolidation

## [0.2.2] - 2026-05-03
### Fixed
- Poster Renamer: `tmp/` staging directory is now created with `exist_ok=True`, preventing a `FileNotFoundError` when the destination is an externally mounted Docker volume
- Poster Renamer: plain copy fallback (tmp → destination) now runs when border replacer is enabled but fails or is misconfigured, instead of silently leaving files in the staging folder
- Unmatched Assets: stale stats cache is now cleared when the destination directory is missing, so the UI reflects the actual state instead of showing outdated data
- Unmatched Assets: empty stats are now saved to the database when no asset files are found (e.g. folder was deleted), preventing a forever-cached non-empty result
- Frontend: unmatched stats now refresh when an unmatched/workflow job transitions to `failed` (previously only refreshed on `completed`)

## [0.2.1] - 2026-05-03
### Added
- Settings → General: new **API Keys** section for centralised TMDB API key configuration

### Changed
- TMDB API key consolidated from IDarr config and Unmatched Assets into a single global setting; existing keys are auto-migrated on upgrade
- IDarr and Unmatched Assets pages now link to Settings → General → API Keys instead of providing their own key fields

## [0.2.0] - 2026-05-03
### Added
- IDarr: "Auto-Upload to GDrive after Rename" toggle in Quick Add Files card — dependent on Auto-Rename toggle, persisted to database
- IDarr: sidebar badge showing count of pending matches requiring attention
- IDarr: toast notification when an IDarr job completes and pending matches are found
- IDarr: pending matches list auto-refreshes when an IDarr job completes via WebSocket

## [0.1.9] - 2026-05-02
### Added
- Job logs: live tailing via WebSocket with updated UI components
- Drives: `display_name` field added; `drives.json` moved into repository and scripts updated to use display name to prevent long names from overflowing

### Fixed
- Plex webhook settings: default values set to `true` in API and frontend
- Poster carousel: item width and text overflow handling adjusted

## [0.1.8] - 2026-05-01
### Fixed
- fix: fixed year-based filtering for asset matching in Plex upload process

## [0.1.7] - 2026-04-30
### Added
- Plex upload: new per-upload settings for sync after upload, rename after upload, border replacement after upload, and configurable upload delay

### Fixed
- Modal footer: buttons are now center-aligned for consistent UI across all modals

## [0.1.6] - 2026-04-29
### Added
- Drives: `sync_enabled` flag — per-drive toggle to enable/disable Google Drive rclone sync
- Drives: subscribing to a sync-disabled drive now auto-queues an initial local folder scan to index existing files immediately
- Drive modals: "Sync from Google Drive" checkbox now available for all drives (not just custom), with contextual warnings for community drives
- Database migration: `sync_enabled` column added to `drives` table (existing `manual-` drives automatically set to disabled)

### Changed
- `AddCustomDriveModal`: checkbox logic inverted to `syncEnabled` (opt-in) with updated labels and help text
- `DriveEditModal`: improved info text and added a warning when disabling sync on a non-custom community drive

## [0.1.5] - 2026-04-28
### Added
- Drive cards: tooltip for drive ID with one-click copy functionality
- Settings and Setup Wizard: additional URL configuration guidance for media server settings

### Fixed
- Discord notifications: message formatting optimized to respect character limits and improve readability
- MainLayout: GitHub link position adjusted for better alignment without collision
- Dockerfile: removed unnecessary directory creation that caused permissions issues
- Update check: fixed update notification using github release

### Changed
- API endpoints renamed from `/api/poster-manager` to `/api/posterflow` across backend and frontend
- Setup Wizard: styles updated for improved layout and consistency; welcome message revised

## [0.1.4] - 2026-04-27
### Added
- In-app update check now compares against GitHub Releases instead of commit count, eliminating false update notifications from non-release commits
- Version display simplified to `base.branch` format (e.g. `0.1.4.develop`)

### Fixed
- VERSION file now correctly copied into Docker image, resolving incorrect `0.1.0` version display
- Application shutdown in testing mode no longer uses invalid `return` inside `finally` block

### Changed
- Workflow `BUILD_NUMBER` arg removed as commit count is no longer part of the version string

## [0.1.3] - 2026-04-27
### Added
- Bulk add/remove functionality for drive styles in the Priority tab
- IDarr: deduplicate TV shows missing TVDB IDs by TMDB ID after enrichment
- IDarr: log missing TVDB IDs for TV series after enrichment
- Plex upload: deduplication logic for collections and improved asset merging with ID-based search
- Plex upload: initial setup warning to prevent re-uploads on first run
- Plex upload: gdrive_id added to source context for improved asset filtering
- In-app update notification with release notes popover in sidebar
- CHANGELOG for tracking version history

### Fixed
- Unmatched assets view/search modal success message and close button alignment
- Radarr upgrade handling now clears cache when edition is removed
- Radarr webhook instructions updated to include "On File Upgrade" for import events

### Changed
- Updated frontend dependencies (axios, follow-redirects, postcss, proxy-from-env)

## [0.1.2] - 2026-04-16
### Added
- Poster sync: optimized file scanning and database operations
- Poster daily activity endpoint now uses local time for date boundaries
- Webhook deduplication logic for different seasons
- Separated development dependencies into `requirements-dev.txt`

### Fixed
- Hardened backend reliability and security
- Simplified directory creation in Dockerfile

### Changed
- GDrives: updated unsubscribe button icon and adjusted button colors

## [0.1.1] - 2026-04-08
### Added
- Dashboard: poster filtering and lightbox feature
- Unmatched items modal with "all" category and category badges
- TMDB API key support for unmatched items search
- Clipboard copy with toast notification and non-HTTPS fallback
- Plex upload: fast cache summary for optimized retrieval and improved manual settings handling
- Post-job script hooks with enable/disable logging
- Toast notifications for error handling in drive modals

### Fixed
- Database WAL mode exception handling
- Post-job hook log handlers now properly removed after execution
- Setup wizard layout, save states, and token/secret visibility

### Changed
- Docker Compose and entrypoint refactored for clarity and ownership management

## [0.1.0] - 2026-04-04
### Added
- Initial release of PosterFlow
- Google Drive poster sync via rclone
- Poster Manager with unmatched asset detection
- IDarr metadata enrichment and rename normalization
- Plex Upload integration
- Poster Search
- Maker Tools
- Scheduled sync jobs
- Job queue with live progress via WebSocket
- Discord notifications
- Backup and restore
- Setup wizard
- Dark theme UI
