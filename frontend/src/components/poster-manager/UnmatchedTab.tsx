import { AlertCircle, Download, List, RefreshCw, Save, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PlexLibraryConfig, UnmatchedStats } from '../../api/client'
import Toolbar from './Toolbar'

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
  onSaveSettings: () => void
  onDetectUnmatched: () => void
  onDownloadReport: () => void
  onToggleLibrarySelection: (instanceName: string, libraryKey: string) => void
  unmatchedIgnoreRootFoldersText: string
  unmatchedIgnoreCollectionsText: string
  unmatchedIgnoreUnmonitored: boolean
  onSetUnmatchedIgnoreRootFoldersText: (value: string) => void
  onSetUnmatchedIgnoreCollectionsText: (value: string) => void
  onSetUnmatchedIgnoreUnmonitored: (value: boolean) => void
  onOpenModal: (type: 'movies' | 'series' | 'seasons' | 'collections' | 'all') => void
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
  onSaveSettings,
  onDetectUnmatched,
  onDownloadReport,
  onToggleLibrarySelection,
  unmatchedIgnoreRootFoldersText,
  unmatchedIgnoreCollectionsText,
  unmatchedIgnoreUnmonitored,
  onSetUnmatchedIgnoreRootFoldersText,
  onSetUnmatchedIgnoreCollectionsText,
  onSetUnmatchedIgnoreUnmonitored,
  onOpenModal,
}: UnmatchedTabProps) {
  const navigate = useNavigate()

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const hasUnsavedChanges = hasUnsavedLibraryChanges || hasUnsavedUnmatchedSettings

  return (
    <>
      <Toolbar title="Unmatched Assets" description="Media in your library without matching posters in the organized folder">
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
        </div>
        <button
          className={`btn-toolbar ${hasUnsavedChanges ? 'btn-unsaved' : ''}`}
          onClick={onSaveSettings}
          disabled={saving || !hasUnsavedChanges}
          title={hasUnsavedChanges ? 'Save changes' : 'No changes to save'}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        <button className="btn-toolbar btn-primary" onClick={onDetectUnmatched} disabled={detectingUnmatched}>
          <RefreshCw size={16} />
          {detectingUnmatched ? 'Detecting...' : 'Detect Unmatched'}
        </button>
        {unmatchedStats && unmatchedStats.last_run && (
          <>
            <button
              className="btn-toolbar btn-primary"
              onClick={() => onOpenModal('all')}
              title="View and search missing items"
            >
              <Search size={16} />
              View / Search
            </button>
            <button className="btn-toolbar" onClick={onDownloadReport} title="Download complete report of all unmatched items">
              <Download size={16} />
              Download Report
            </button>
          </>
        )}
      </Toolbar>

      {unmatchedStats && unmatchedStats.last_run ? (
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
                    <span>With Posters:</span>
                    <span>{unmatchedStats.summary.movies.total - unmatchedStats.summary.movies.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.movies.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.movies.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.movies && unmatchedStats.unmatched.movies.length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing Posters:</div>
                    {unmatchedStats.unmatched.movies.slice(0, cardPreviewLimit).map((item, idx: number) => (
                      <div key={idx} className="list-item">
                        <span>{item.title}</span>
                        {item.year && <span className="item-year">({item.year})</span>}
                        {item.instance && <span className="item-instance">{item.instance}</span>}
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
                  <h3>Series (Main Posters)</h3>
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
                    <span>With Posters:</span>
                    <span>{unmatchedStats.summary.series.total - unmatchedStats.summary.series.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.series.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.series.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.series && unmatchedStats.unmatched.series.filter((s) => s.missing_main_poster).length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing Main Posters:</div>
                    {unmatchedStats.unmatched.series
                      .filter((s) => s.missing_main_poster)
                      .slice(0, cardPreviewLimit)
                      .map((item, idx: number) => (
                        <div key={idx} className="list-item">
                          <span>{item.title}</span>
                          {item.year && <span className="item-year">({item.year})</span>}
                          {item.instance && <span className="item-instance">{item.instance}</span>}
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
                    <span>With Posters:</span>
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
                            {item.instance && <span className="item-instance">{item.instance}</span>}
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
                    <span>With Posters:</span>
                    <span>{unmatchedStats.summary.collections.total - unmatchedStats.summary.collections.unmatched}</span>
                  </div>
                  <div className="progress-bar">
                    <div className="progress-fill" style={{ width: `${unmatchedStats.summary.collections.percent_complete}%` }} />
                  </div>
                  <div className="progress-label">{formatPercent(unmatchedStats.summary.collections.percent_complete)}% Complete</div>
                </div>
                {unmatchedStats.unmatched.collections && unmatchedStats.unmatched.collections.length > 0 && (
                  <div className="card-list">
                    <div className="list-header">Missing Posters:</div>
                    {unmatchedStats.unmatched.collections.slice(0, cardPreviewLimit).map((item, idx: number) => (
                      <div key={idx} className="list-item">
                        <span>{item.title}</span>
                        {item.instance && <span className="item-instance">{item.instance}</span>}
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
          <p>Click "Detect Unmatched" to check which media items are missing posters</p>
        </div>
      )}

      <div className="renamer-layout-row" style={{ marginTop: '2rem' }}>
        <div className="settings-section renamer-config-card">
          <h2>Detection Settings</h2>
          <p className="section-description">Configure which items to include or exclude from unmatched detection.</p>

          <div className="field-group">
            <textarea
              rows={2}
              className="unmatched-settings-textarea"
              value={unmatchedIgnoreRootFoldersText}
              onChange={(e) => onSetUnmatchedIgnoreRootFoldersText(e.target.value)}
              placeholder="Ignore root folders (example: movies, tv, /mnt/media/movies)"
            />
            <small>Comma-separated root folder names or full root paths.</small>

            <textarea
              rows={2}
              className="unmatched-settings-textarea"
              value={unmatchedIgnoreCollectionsText}
              onChange={(e) => onSetUnmatchedIgnoreCollectionsText(e.target.value)}
              placeholder="Ignore collections (example: Marvel Collection, Disney)"
            />
            <small>Comma-separated collection titles. Matching is case-insensitive.</small>

            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={unmatchedIgnoreUnmonitored}
                onChange={(e) => onSetUnmatchedIgnoreUnmonitored(e.target.checked)}
              />
              <span>
                <strong>Ignore Unmonitored</strong>
                <small>Exclude unmonitored movies, series, and seasons from unmatched detection.</small>
              </span>
            </label>

            <label style={{ marginTop: '1rem', display: 'block', fontWeight: 500 }}>TMDB API Key</label>
            <p style={{ margin: '0.25rem 0 0', fontSize: '0.8rem', color: '#888' }}>
              Configured globally in{' '}
              <a
                href="/settings"
                style={{ color: '#64b5f6' }}
                onClick={(e) => { e.preventDefault(); localStorage.setItem('posterflow.settings.activeTab', 'basic'); navigate('/settings') }}
              >Settings → General → API Keys</a>.
            </p>
          </div>
        </div>

        <div className="settings-section renamer-library-card">
          <h2>Library Selection</h2>
          <p className="section-description">Select which Plex libraries to scan for missing posters.</p>

          <div className="field-group">
            {libraryConfigs.length === 0 ? (
              <div className="empty-state">
                <p style={{ color: '#888', fontSize: '0.9rem', margin: 0 }}>No Plex instances configured. Configure in Settings → Media Servers.</p>
              </div>
            ) : (
              <div className="library-compact-grid">
                {libraryConfigs.map((config) => (
                  <div key={config.instance_name} className="library-instance-group">
                    <div className="instance-header">{config.instance_name}</div>
                    {config.libraries.filter((lib) => lib.enabled).map((library) => {
                      const fullKey = `${config.instance_name}:${library.key}`
                      const isSelected = selectedLibraries.has(fullKey)
                      return (
                        <label key={library.key} className="library-checkbox">
                          <input type="checkbox" checked={isSelected} onChange={() => onToggleLibrarySelection(config.instance_name, library.key)} />
                          <span className="library-checkbox-label">
                            {library.title}
                            <span className="library-badge">{library.type}</span>
                          </span>
                        </label>
                      )
                    })}
                    {config.libraries.filter((lib) => lib.enabled).length === 0 && (
                      <p style={{ color: '#888', fontSize: '0.85rem', fontStyle: 'italic', margin: '0.25rem 0' }}>No libraries enabled</p>
                    )}
                  </div>
                ))}
              </div>
            )}
            <small>Only media from selected libraries will be scanned for missing posters</small>
          </div>
        </div>
      </div>
    </>
  )
}

export default UnmatchedTab