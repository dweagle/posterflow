import { AlertCircle, List } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PlexLibraryConfig, UnmatchedStats } from '../../api/client'
import { useCommunityClaimStatus } from '../../hooks/useCommunityClaimStatus'
import CommunityStatusBadge from './CommunityStatusBadge'
import ArrMissingBadge from './ArrMissingBadge'
import LibrarySelectGrid from './LibrarySelectGrid'
import UnmatchedHeader from './UnmatchedHeader'
import UnmatchedIgnoreList from './UnmatchedIgnoreList'
import { UnmatchedScope } from './UnmatchedTypeSelector'

type PreviewItem = { title: string; year?: number | null; tmdb_id?: number | null; tvdb_id?: number | null; available?: boolean | null }

type UnmatchedTabProps = {
  unmatchedStats: UnmatchedStats | null
  hasUnsavedLibraryChanges: boolean
  hasUnsavedUnmatchedSettings: boolean
  saving: boolean
  detectingUnmatched: boolean
  libraryConfigs: PlexLibraryConfig[]
  selectedLibraries: Set<string>
  cardPreviewLimit: number
  formatPercent: (percent: number) => string
  scope: UnmatchedScope
  onScopeChange: (scope: UnmatchedScope) => void
  /** True when the shared Detection Settings view replaces the stats. */
  settingsActive: boolean
  onShowSettings: () => void
  /** Asset noun used in card labels ("Posters", "Logos", …). */
  typeNoun?: string
  onSaveSettings: () => void
  onDetectUnmatched: () => void
  onDownloadReport: () => void
  onToggleLibrarySelection: (instanceName: string, libraryKey: string) => void
  unmatchedIgnoreRootFoldersText: string
  unmatchedIgnoreUnmonitored: boolean
  onSetUnmatchedIgnoreRootFoldersText: (value: string) => void
  onSetUnmatchedIgnoreUnmonitored: (value: boolean) => void
  onOpenModal: (type: 'movies' | 'series' | 'seasons' | 'collections' | 'all') => void
  /** Called when the per-item ignore list changes so the page can refresh stats. */
  onIgnoreListChanged?: () => void
}

function UnmatchedTab({
  unmatchedStats,
  hasUnsavedLibraryChanges,
  hasUnsavedUnmatchedSettings,
  saving,
  detectingUnmatched,
  libraryConfigs,
  selectedLibraries,
  cardPreviewLimit,
  formatPercent,
  scope,
  onScopeChange,
  settingsActive,
  onShowSettings,
  typeNoun = 'Posters',
  onSaveSettings,
  onDetectUnmatched,
  onDownloadReport,
  onToggleLibrarySelection,
  unmatchedIgnoreRootFoldersText,
  unmatchedIgnoreUnmonitored,
  onSetUnmatchedIgnoreRootFoldersText,
  onSetUnmatchedIgnoreUnmonitored,
  onOpenModal,
  onIgnoreListChanged,
}: UnmatchedTabProps) {
  const navigate = useNavigate()
  const { getStatus: getClaimStatus } = useCommunityClaimStatus()

  // Per-style "already claimed/made" checks for a preview row (color = style,
  // tooltip spells it out).
  const claimBadge = (item: PreviewItem, mediaType: string) => (
    <CommunityStatusBadge status={getClaimStatus({ tmdb_id: item.tmdb_id, tvdb_id: item.tvdb_id, media_type: mediaType, title: item.title, year: item.year })} />
  )
  // Red "M" indicator when the item is tracked in *arr but not downloaded.
  const arrBadge = (item: PreviewItem) => <ArrMissingBadge available={item.available} />

  const hasUnsavedChanges = hasUnsavedLibraryChanges || hasUnsavedUnmatchedSettings

  // Artwork has no season dimension (its seasons stats are zeroed), so the seasons card
  // drops out on its own and the series card reads plainly as "Series".
  const hasSeasons = Boolean(unmatchedStats?.summary?.seasons && unmatchedStats.summary.seasons.total > 0)
  const seriesHeading = hasSeasons ? `Series (Main ${typeNoun})` : 'Series'

  return (
    <>
      <UnmatchedHeader
        scope={scope}
        onScopeChange={onScopeChange}
        settingsActive={settingsActive}
        onShowSettings={onShowSettings}
        saving={saving}
        hasUnsavedChanges={hasUnsavedChanges}
        detecting={detectingUnmatched}
        hasResults={Boolean(unmatchedStats && unmatchedStats.last_run)}
        onSaveSettings={onSaveSettings}
        onDetect={onDetectUnmatched}
        onViewSearch={() => onOpenModal('all')}
        onDownloadReport={onDownloadReport}
      />

      {!settingsActive && (unmatchedStats && unmatchedStats.last_run ? (
        <div className="unmatched-tab-content">
          <div className="unmatched-grid">
            {unmatchedStats.summary.movies && unmatchedStats.summary.movies.total > 0 && (
              <div className="unmatched-card">
                <div className="card-header">
                  <h3>Movies</h3>
                  <span className={`badge ${unmatchedStats.summary.movies.unmatched > 0 ? 'badge-warning' : 'badge-success'}`}>
                    {unmatchedStats.summary.movies.unmatched} missing
                  </span>
                </div>
                <div className="card-stats">
                  <div className="stat-row">
                    <span>Total:</span>
                    <span>{unmatchedStats.summary.movies.total}</span>
                  </div>
                  <div className="stat-row">
                    <span>With {typeNoun}:</span>
                    <span>{unmatchedStats.summary.movies.total - unmatchedStats.summary.movies.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.movies.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.movies.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.movies && unmatchedStats.unmatched.movies.length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing {typeNoun}:</div>
                    {unmatchedStats.unmatched.movies.slice(0, cardPreviewLimit).map((item, idx: number) => (
                      <div key={idx} className="list-item">
                        <span>{item.title}</span>
                        {item.year && <span className="item-year">({item.year})</span>}
                        <span className="list-item-badges">
                          {claimBadge(item, 'movie')}
                          {arrBadge(item)}
                          {item.instance && <span className="item-instance">{item.instance}</span>}
                        </span>
                      </div>
                    ))}
                    {unmatchedStats.unmatched.movies.length > cardPreviewLimit && (
                      <div className="list-more">+ {unmatchedStats.unmatched.movies.length - cardPreviewLimit} more...</div>
                    )}
                  </div>
                )}
                {unmatchedStats.unmatched.movies && unmatchedStats.unmatched.movies.length > 0 && (
                  <button className="card-view-all-btn" onClick={() => onOpenModal('movies')}>
                    <List size={14} />
                    View All {unmatchedStats.unmatched.movies.length} Missing
                  </button>
                )}
              </div>
            )}

            {unmatchedStats.summary.series && unmatchedStats.summary.series.total > 0 && (
              <div className="unmatched-card">
                <div className="card-header">
                  <h3>{seriesHeading}</h3>
                  <span className={`badge ${unmatchedStats.summary.series.unmatched > 0 ? 'badge-warning' : 'badge-success'}`}>
                    {unmatchedStats.summary.series.unmatched} missing
                  </span>
                </div>
                <div className="card-stats">
                  <div className="stat-row">
                    <span>Total:</span>
                    <span>{unmatchedStats.summary.series.total}</span>
                  </div>
                  <div className="stat-row">
                    <span>With {typeNoun}:</span>
                    <span>{unmatchedStats.summary.series.total - unmatchedStats.summary.series.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.series.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.series.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.series && unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing Main {typeNoun}:</div>
                    {unmatchedStats.unmatched.series
                      .filter((s) => s.missing_main_poster)
                      .slice(0, cardPreviewLimit)
                      .map((item, idx: number) => (
                        <div key={idx} className="list-item">
                          <span>{item.title}</span>
                          {item.year && <span className="item-year">({item.year})</span>}
                          <span className="list-item-badges">
                            {claimBadge(item, 'show')}
                            {arrBadge(item)}
                            {item.instance && <span className="item-instance">{item.instance}</span>}
                          </span>
                        </div>
                      ))}
                    {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length > cardPreviewLimit && (
                      <div className="list-more">+ {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length - cardPreviewLimit} more...</div>
                    )}
                  </div>
                )}
                {unmatchedStats.unmatched.series && unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length > 0 && (
                  <button className="card-view-all-btn" onClick={() => onOpenModal('series')}>
                    <List size={14} />
                    View All {unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length} Missing
                  </button>
                )}
              </div>
            )}

            {unmatchedStats.summary.seasons && unmatchedStats.summary.seasons.total > 0 && (
              <div className="unmatched-card">
                <div className="card-header">
                  <h3>Season Posters</h3>
                  <span className={`badge ${unmatchedStats.summary.seasons.unmatched > 0 ? 'badge-warning' : 'badge-success'}`}>
                    {unmatchedStats.summary.seasons.unmatched} missing
                  </span>
                </div>
                <div className="card-stats">
                  <div className="stat-row">
                    <span>Total:</span>
                    <span>{unmatchedStats.summary.seasons.total}</span>
                  </div>
                  <div className="stat-row">
                    <span>With {typeNoun}:</span>
                    <span>{unmatchedStats.summary.seasons.total - unmatchedStats.summary.seasons.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.seasons.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.seasons.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.series && unmatchedStats.unmatched.series.filter((s) => s.missing_seasons?.length > 0).length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Series with Missing Seasons:</div>
                    {unmatchedStats.unmatched.series
                      .filter((s) => s.missing_seasons?.length > 0)
                      .slice(0, cardPreviewLimit)
                      .map((item, idx: number) => (
                        <div key={idx} className="list-item-group">
                          <div className="list-item-title">
                            <div className="list-item-title-main">
                              <span>{item.title}</span>
                              {item.year && <span className="item-year">({item.year})</span>}
                            </div>
                            <span className="list-item-badges">
                              {claimBadge(item, 'show')}
                              {arrBadge(item)}
                              {item.instance && <span className="item-instance">{item.instance}</span>}
                            </span>
                          </div>
                          <div className="missing-seasons-list">Missing: {item.missing_seasons.map((s: number) => `S${s}`).join(', ')}</div>
                        </div>
                      ))}
                    {unmatchedStats.unmatched.series.filter((s) => s.missing_seasons?.length > 0).length > cardPreviewLimit && (
                      <div className="list-more">+ {unmatchedStats.unmatched.series.filter((s) => s.missing_seasons?.length > 0).length - cardPreviewLimit} more...</div>
                    )}
                  </div>
                )}
                {unmatchedStats.unmatched.series && unmatchedStats.unmatched.series.filter((s) => s.missing_seasons?.length > 0).length > 0 && (
                  <button className="card-view-all-btn" onClick={() => onOpenModal('seasons')}>
                    <List size={14} />
                    View All {unmatchedStats.unmatched.series.filter((s) => s.missing_seasons?.length > 0).length} Missing
                  </button>
                )}
              </div>
            )}

            {unmatchedStats.summary.collections && unmatchedStats.summary.collections.total > 0 && (
              <div className="unmatched-card">
                <div className="card-header">
                  <h3>Collections</h3>
                  <span className={`badge ${unmatchedStats.summary.collections.unmatched > 0 ? 'badge-warning' : 'badge-success'}`}>
                    {unmatchedStats.summary.collections.unmatched} missing
                  </span>
                </div>
                <div className="card-stats">
                  <div className="stat-row">
                    <span>Total:</span>
                    <span>{unmatchedStats.summary.collections.total}</span>
                  </div>
                  <div className="stat-row">
                    <span>With {typeNoun}:</span>
                    <span>{unmatchedStats.summary.collections.total - unmatchedStats.summary.collections.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.collections.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.collections.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.collections && unmatchedStats.unmatched.collections.length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing {typeNoun}:</div>
                    {unmatchedStats.unmatched.collections.slice(0, cardPreviewLimit).map((item, idx: number) => (
                      <div key={idx} className="list-item">
                        <span>{item.title}</span>
                        <span className="list-item-badges">
                          {claimBadge(item, 'collection')}
                          {arrBadge(item)}
                          {item.instance && <span className="item-instance">{item.instance}</span>}
                        </span>
                      </div>
                    ))}
                    {unmatchedStats.unmatched.collections.length > cardPreviewLimit && (
                      <div className="list-more">+ {unmatchedStats.unmatched.collections.length - cardPreviewLimit} more...</div>
                    )}
                  </div>
                )}
                {unmatchedStats.unmatched.collections && unmatchedStats.unmatched.collections.length > 0 && (
                  <button className="card-view-all-btn" onClick={() => onOpenModal('collections')}>
                    <List size={14} />
                    View All {unmatchedStats.unmatched.collections.length} Missing
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="empty-state">
          <AlertCircle size={48} />
          <h3>No Unmatched Detection Results</h3>
          <p>Click "Detect Unmatched" to check which media items are missing {typeNoun.toLowerCase()}</p>
        </div>
      ))}

      {settingsActive && (
      <div className="renamer-layout-row">
        <div className="settings-section renamer-config-card">
          <h2>Detection Settings</h2>
          <p className="section-description">Configure which items to include or exclude from unmatched detection. Shared across all asset types.</p>

          <div className="field-group">
            <label style={{ display: 'block', fontWeight: 500 }}>TMDB API Key</label>
            <p style={{ margin: '0.25rem 0 0', fontSize: '0.8rem', color: '#888' }}>
              Configured globally in{' '}
              <a
                href="/settings"
                style={{ color: '#64b5f6' }}
                onClick={(e) => { e.preventDefault(); localStorage.setItem('posterflow.settings.activeTab', 'basic'); navigate('/settings') }}
              >Settings → General → API Keys</a>.
            </p>

            <UnmatchedIgnoreList onChanged={onIgnoreListChanged} />

            <label style={{ marginTop: '1rem', display: 'block', fontWeight: 500 }}>Ignore Root Folders</label>
            <small>
              Excludes every movie and series stored under a matching Radarr/Sonarr root folder — use it to
              skip whole locations, like a 4K or kids library. Enter folder names (movies, tv) or full paths
              (/mnt/media/movies), comma-separated; matching is case-insensitive. Collections aren't affected.
            </small>
            <textarea
              rows={2}
              className="unmatched-settings-textarea"
              value={unmatchedIgnoreRootFoldersText}
              onChange={(e) => onSetUnmatchedIgnoreRootFoldersText(e.target.value)}
              placeholder="Ignore root folders (example: movies, tv, /mnt/media/movies)"
            />


            <label className="checkbox-label checkbox-label--inline">
              <input
                type="checkbox"
                checked={unmatchedIgnoreUnmonitored}
                onChange={(e) => onSetUnmatchedIgnoreUnmonitored(e.target.checked)}
              />
              <span>
                <strong>Ignore Unmonitored</strong>
                <small>Exclude unmonitored movies, series, and seasons from detection.</small>
              </span>
            </label>
          </div>
        </div>

        <div className="settings-section renamer-library-card">
          <h2>Library Selection</h2>
          <p className="section-description">Select which Plex libraries to scan. Shared across all asset types.</p>

          <div className="field-group">
            {libraryConfigs.length === 0 ? (
              <div className="empty-state">
                <p style={{ color: '#888', fontSize: '0.9rem', margin: 0 }}>No Plex instances configured. Configure in Settings → Media Servers.</p>
              </div>
            ) : (
              <LibrarySelectGrid
                libraryConfigs={libraryConfigs}
                selected={selectedLibraries}
                onToggle={(instance, key) => onToggleLibrarySelection(instance, key)}
              />
            )}
            <small>Only media from selected libraries will be scanned</small>
          </div>
        </div>
      </div>
      )}
    </>
  )
}

export default UnmatchedTab