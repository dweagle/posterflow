import { Eye, Play, Save, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PlexLibraryConfig } from '../../api/client'
import { ManualMediaEntry } from '../../api/posterManager'
import LibrarySelectGrid from './LibrarySelectGrid'
import Toolbar from '../Toolbar'

type RenamerTabProps = {
  hasUnsavedLibraryChanges: boolean
  hasUnsavedBorderChanges: boolean
  saving: boolean
  renaming: boolean
  autoRunBorder: boolean
  autoRunCleanup: boolean
  cleanupDeleteUnknown: boolean
  settingsLoaded: boolean
  libraryConfigs: PlexLibraryConfig[]
  selectedLibraries: Set<string>
  // Asset types to include in the run
  include: string[]
  includeLoaded: boolean
  hasUnsavedInclude: boolean
  onToggleInclude: (assetType: string, checked: boolean) => void
  // Manual media
  manualEntries: ManualMediaEntry[]
  formTitle: string
  formYear: string
  formType: 'movie' | 'series'
  formSeasonCount: string
  formSpecials: boolean
  formTmdbId: string
  formTvdbId: string
  formImdbId: string
  adding: boolean
  onSaveSettings: () => void
  onRunRename: (dryRun: boolean) => void
  onSetAutoRunBorder: (enabled: boolean) => void
  onSetAutoRunCleanup: (enabled: boolean) => void
  onSetCleanupDeleteUnknown: (enabled: boolean) => void
  onToggleLibrarySelection: (instanceName: string, libraryKey: string) => void
  onFormTitleChange: (v: string) => void
  onFormYearChange: (v: string) => void
  onFormTypeChange: (type: 'movie' | 'series') => void
  onFormSeasonCountChange: (v: string) => void
  onFormSpecialsChange: (v: boolean) => void
  onFormTmdbIdChange: (v: string) => void
  onFormTvdbIdChange: (v: string) => void
  onFormImdbIdChange: (v: string) => void
  onAddManualEntry: () => void
  onDeleteEntry: (id: number) => void
}

function RenamerTab({
  hasUnsavedLibraryChanges,
  hasUnsavedBorderChanges,
  saving,
  renaming,
  autoRunBorder,
  autoRunCleanup,
  cleanupDeleteUnknown,
  settingsLoaded,
  libraryConfigs,
  selectedLibraries,
  include,
  includeLoaded,
  hasUnsavedInclude,
  onToggleInclude,
  manualEntries,
  formTitle,
  formYear,
  formType,
  formSeasonCount,
  formSpecials,
  formTmdbId,
  formTvdbId,
  formImdbId,
  adding,
  onSaveSettings,
  onRunRename,
  onSetAutoRunBorder,
  onSetAutoRunCleanup,
  onSetCleanupDeleteUnknown,
  onToggleLibrarySelection,
  onFormTitleChange,
  onFormYearChange,
  onFormTypeChange,
  onFormSeasonCountChange,
  onFormSpecialsChange,
  onFormTmdbIdChange,
  onFormTvdbIdChange,
  onFormImdbIdChange,
  onAddManualEntry,
  onDeleteEntry,
}: RenamerTabProps) {
  const navigate = useNavigate()

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  return (
    <>
      <Toolbar title="Asset Renamer" description="Rename and organize posters and artwork into your item folders">
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
        </div>
        <button
          className={`btn-toolbar ${(hasUnsavedLibraryChanges || hasUnsavedBorderChanges || hasUnsavedInclude) ? 'btn-unsaved' : ''}`}
          onClick={onSaveSettings}
          disabled={(!hasUnsavedLibraryChanges && !hasUnsavedBorderChanges && !hasUnsavedInclude) || saving || libraryConfigs.length === 0}
          title={libraryConfigs.length === 0 ? 'Configure libraries in Settings first' : ((hasUnsavedLibraryChanges || hasUnsavedBorderChanges || hasUnsavedInclude) ? 'Save changes' : 'No changes to save')}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        <button className="btn-toolbar" onClick={() => onRunRename(true)} disabled={renaming} title="Dry run asset renamer">
          <Eye size={16} />
          Dry Run
        </button>
        <button className="btn-toolbar btn-primary" onClick={() => onRunRename(false)} disabled={renaming}>
          <Play size={16} />
          {renaming ? 'Starting...' : 'Rename Assets'}
        </button>
      </Toolbar>

      <div className="renamer-layout-row">
        <div className="settings-section renamer-config-card">
          <h2>Rename Configuration</h2>
          <p className="section-description">Choose what to include, then set options for standalone rename runs.</p>

          <div className="config-subgroup-heading">Assets</div>
          <div className="field-group">
            <label>Include</label>
            {/* Render the toggles only once the saved state is loaded, so they mount in
                their saved position instead of animating from the default on page load. */}
            <div className="asset-include-toggles" aria-busy={!includeLoaded}>
              {includeLoaded && ([['posters', 'Posters'], ['logo', 'Logos'], ['background', 'Backgrounds'], ['squareart', 'Square Art']] as const).map(([val, label]) => (
                <div key={val} className="toggle-field asset-include-toggle">
                  <label className="toggle-switch">
                    <input type="checkbox" checked={include.includes(val)} onChange={(e) => onToggleInclude(val, e.target.checked)} />
                    <span className="toggle-slider"></span>
                  </label>
                  <span className="toggle-label">{label}</span>
                </div>
              ))}
            </div>
            <small>Pick which asset types this run processes. Posters come from your poster drives; logos, backgrounds and square art come from your synced artwork drives. <strong style={{ color: '#f59e0b' }}>Unmatched detection checks only the selected asset types.</strong> Click <strong>Save Settings</strong> to apply.</small>
            {includeLoaded && include.some((t) => t !== 'posters') && (
              <small className="kometa-asset-note"><strong>Running Kometa against this folder?</strong> Keep <code>dimensional_asset_rename: false</code> in Kometa's settings. When enabled, Kometa treats wide images as backdrops and renames placed <code>logo.png</code> files to <code>background.png</code>.</small>
            )}
          </div>

          <div className="config-subgroup-heading config-subgroup-divider">
            Standalone Run Options
            <span className="config-subgroup-hint">Only affect standalone runs on this page — not the Workflow</span>
          </div>

          <div className="field-group">
            <label>Auto-Run Border Replacer</label>
            <div className="toggle-field" aria-busy={!settingsLoaded}>
              {settingsLoaded && (
                <>
                  <label className="toggle-switch">
                    <input type="checkbox" checked={autoRunBorder} onChange={(e) => onSetAutoRunBorder(e.target.checked)} />
                    <span className="toggle-slider"></span>
                  </label>
                  <span className="toggle-label">{autoRunBorder ? 'Enabled (Runs After Rename)' : 'Disabled'}</span>
                </>
              )}
            </div>
            <small>Runs Border Replacer automatically after a standalone rename (uses the incremental/full mode set on the Border Replacer page).</small>
            <small className="standalone-warning">⚠️ Plex Upload also uses this setting — enable it if you want borders applied during Plex Upload runs.</small>
          </div>

          <div className="field-group">
            <label>Auto-Run Asset Cleanup</label>
            <div className="toggle-field" aria-busy={!settingsLoaded}>
              {settingsLoaded && (
                <>
                  <label className="toggle-switch">
                    <input type="checkbox" checked={autoRunCleanup} onChange={(e) => onSetAutoRunCleanup(e.target.checked)} />
                    <span className="toggle-slider"></span>
                  </label>
                  <span className="toggle-label">{autoRunCleanup ? 'Enabled (Runs After Rename/Border)' : 'Disabled'}</span>
                </>
              )}
            </div>
            <small>Removes asset folders for media no longer in Radarr/Sonarr/Plex, plus stale duplicate folders left after a rename. Runs after rename (and border, if on).</small>
            {autoRunCleanup && (
              <label className="checkbox-option" style={{ marginTop: '0.4rem' }}>
                <input type="checkbox" checked={cleanupDeleteUnknown} onChange={(e) => onSetCleanupDeleteUnknown(e.target.checked)} />
                <span>Also remove unknown/stray folders (no match, no ID — riskier)</span>
              </label>
            )}
          </div>
        </div>

        <div className="settings-section renamer-library-card">
          <h2>Library Selection</h2>
          <p className="section-description">Select which Plex libraries to process during rename.</p>

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
            <small>Only media from selected libraries will be processed during rename</small>
          </div>
        </div>
      </div>

      {/* Manual Media Card */}
      <div className="settings-section manual-media-section">
        <h2>Manual Media</h2>
        <p className="section-description">
          Add movies or shows that are in Plex but not managed by Radarr/Sonarr. These will be included in poster renaming, border replacement, unmatched detection, and Plex upload.
        </p>

        <details className="manual-media-help">
          <summary className="manual-media-help-summary">Tips for matching posters correctly</summary>
          <div className="manual-media-help-body">
            <p>PosterFlow searches your subscribed poster drives for a folder that matches each entry you add below. Here's how to make sure it finds the right one:</p>

            <div className="manual-media-help-section">
              <span className="manual-media-help-label">Title</span>
              <span className="manual-media-help-example">Enter the title <em>exactly as it appears on the poster drive</em> — this is usually the same as the show/movie's common name. (e.g. <em>The Thing)</em></span>
            </div>

            <div className="manual-media-help-section">
              <span className="manual-media-help-label">Year</span>
              <span className="manual-media-help-example">Add the Year. Prevents mismatches between remakes with the same name (e.g. <em>The Thing (1982)</em> vs <em>The Thing (2011)</em>). Also ensures your output folder matches Plex's expected <em>Movie Title (Year)</em> format.</span>
            </div>

            <div className="manual-media-help-section">
              <span className="manual-media-help-label">IDs — most reliable</span>
              <span className="manual-media-help-example">
                If you enter a TMDB ID (movies) or TVDB ID (TV shows), PosterFlow will match by ID alone — title spelling becomes irrelevant. Find IDs at <em>themoviedb.org</em> or <em>thetvdb.com</em>.
              </span>
            </div>

            <p className="manual-media-help-note">
              The output folder PosterFlow creates will use the title and year you enter here, so make sure they match what Plex expects.
            </p>
          </div>
        </details>

        <div className="field-group">
          <label>Add Entry</label>
          {/* Primary row: type, title, year, seasons, add */}
          <div className="manual-media-search-row">
            <select
              className="manual-media-type-select"
              value={formType}
              onChange={(e) => onFormTypeChange(e.target.value as 'movie' | 'series')}
            >
              <option value="movie">Movie</option>
              <option value="series">TV Show</option>
            </select>
            <input
              type="text"
              placeholder="Title"
              value={formTitle}
              onChange={(e) => onFormTitleChange(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onAddManualEntry()}
              style={{ flex: 1 }}
            />
            <input
              type="text"
              placeholder="Year"
              value={formYear}
              onChange={(e) => onFormYearChange(e.target.value)}
              className="manual-media-year-input"
            />
            {formType === 'series' && (
              <>
                <input
                  type="text"
                  placeholder="# Seasons"
                  value={formSeasonCount}
                  onChange={(e) => onFormSeasonCountChange(e.target.value)}
                  className="manual-media-seasons-input"
                  title="Number of regular seasons (e.g. 3 → S01, S02, S03)"
                />
                <label className="manual-media-specials-label" title="Include Season 00 (Specials)">
                  <input
                    type="checkbox"
                    checked={formSpecials}
                    onChange={(e) => onFormSpecialsChange(e.target.checked)}
                  />
                  S00
                </label>
              </>
            )}
            <button
              className="btn-secondary manual-media-add-btn"
              disabled={adding || !formTitle.trim()}
              onClick={onAddManualEntry}
            >
              Add
            </button>
          </div>
          {/* Optional IDs row */}
          <div className="manual-media-ids-warning">
            <span>⚠ Only enter an ID if it uniquely identifies this item. An ID shared with another item in your library will cause matching conflicts.</span>
          </div>
          <div className="manual-media-ids-row">
            <div className="manual-media-id-field">
              <span className="manual-media-id-label">TMDB ID</span>
              <input
                type="text"
                placeholder="optional, e.g. 123456"
                value={formTmdbId}
                onChange={(e) => onFormTmdbIdChange(e.target.value)}
              />
            </div>
            <div className="manual-media-id-field">
              <span className="manual-media-id-label">TVDB ID</span>
              <input
                type="text"
                placeholder="optional, e.g. 81189"
                value={formTvdbId}
                onChange={(e) => onFormTvdbIdChange(e.target.value)}
              />
            </div>
            <div className="manual-media-id-field">
              <span className="manual-media-id-label">IMDB ID</span>
              <input
                type="text"
                placeholder="optional, e.g. tt1234567"
                value={formImdbId}
                onChange={(e) => onFormImdbIdChange(e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="field-group" style={{ marginTop: '0.75rem' }}>
          <label>Entries ({manualEntries.length})</label>
          {manualEntries.length === 0 ? (
            <div className="empty-state">
              <p style={{ color: '#888', fontSize: '0.9rem', margin: 0 }}>No entries yet. Use the form above to add items.</p>
            </div>
          ) : (
            <div className="manual-media-table">
              {manualEntries.map((entry) => (
                <div key={entry.id} className="manual-media-entry-row">
                  <div className="manual-media-entry-info">
                    <span className="manual-media-entry-title">{entry.title}</span>
                    {entry.year && <span className="manual-media-entry-year">{entry.year}</span>}
                    <span className={`library-badge ${entry.media_type === 'movie' ? 'badge-movie' : 'badge-series'}`}>
                      {entry.media_type === 'movie' ? 'Movie' : 'TV Show'}
                    </span>
                    {entry.media_type === 'series' && entry.seasons.length > 0 && (
                      <span className="manual-media-entry-meta">{entry.seasons.length} season{entry.seasons.length !== 1 ? 's' : ''}</span>
                    )}
                    {entry.tmdb_id && <span className="manual-media-entry-meta">TMDB: {entry.tmdb_id}</span>}
                    {entry.tvdb_id && <span className="manual-media-entry-meta">TVDB: {entry.tvdb_id}</span>}
                    {entry.imdb_id && <span className="manual-media-entry-meta">{entry.imdb_id}</span>}
                  </div>
                  <button className="btn-icon-danger" title="Remove entry" onClick={() => onDeleteEntry(entry.id)}>
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}

export default RenamerTab
