# Changelog

All notable changes to PosterFlow will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
