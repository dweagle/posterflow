import { Eye, Play, Save } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { PlexLibraryConfig } from '../../api/client'

type RenamerTabProps = {
  hasUnsavedLibraryChanges: boolean
  hasUnsavedBorderChanges: boolean
  saving: boolean
  renaming: boolean
  autoRunBorder: boolean
  libraryConfigs: PlexLibraryConfig[]
  selectedLibraries: Set<string>
  onSaveSettings: () => void
  onRunRename: (dryRun: boolean) => void
  onSetAutoRunBorder: (enabled: boolean) => void
  onToggleLibrarySelection: (instanceName: string, libraryKey: string) => void
}

function RenamerTab({
  hasUnsavedLibraryChanges,
  hasUnsavedBorderChanges,
  saving,
  renaming,
  autoRunBorder,
  libraryConfigs,
  selectedLibraries,
  onSaveSettings,
  onRunRename,
  onSetAutoRunBorder,
  onToggleLibrarySelection,
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
      <div className="toolbar">
        <div>
          <h2>Poster Renamer</h2>
          <p>Organize and rename poster files from multiple sources</p>
        </div>
        <div className="action-buttons">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
          <button
            className={`btn-toolbar ${(hasUnsavedLibraryChanges || hasUnsavedBorderChanges) ? 'btn-unsaved' : ''}`}
            onClick={onSaveSettings}
            disabled={(!hasUnsavedLibraryChanges && !hasUnsavedBorderChanges) || saving || libraryConfigs.length === 0}
            title={libraryConfigs.length === 0 ? 'Configure libraries in Settings first' : ((hasUnsavedLibraryChanges || hasUnsavedBorderChanges) ? 'Save changes' : 'No changes to save')}
          >
            <Save size={16} />
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
          <button className="btn-toolbar" onClick={() => onRunRename(true)} disabled={renaming} title="Dry run poster renamer">
            <Eye size={16} />
            Dry Run
          </button>
          <button className="btn-toolbar btn-primary" onClick={() => onRunRename(false)} disabled={renaming}>
            <Play size={16} />
            {renaming ? 'Starting...' : 'Rename Posters'}
          </button>
        </div>
      </div>

      <div className="settings-section">
        <h2>Rename Configuration</h2>
        <p className="section-description">Select which Plex libraries to process and configure border replacer options.</p>

        <div className="settings-grid library-layout">
          <div className="field-group">
            <label>Auto-Run Border Replacer</label>
            <div className="toggle-field">
              <label className="toggle-switch">
                <input type="checkbox" checked={autoRunBorder} onChange={(e) => onSetAutoRunBorder(e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">{autoRunBorder ? 'Enabled (Runs After Rename Standalone Run)' : 'Disabled'}</span>
            </div>
            <small>When enabled, border replacer runs automatically after renaming posters (uses Poster Renamer incremental/full mode setting on Border Replacer page)</small>
            <small className="standalone-warning">⚠️ Standalone runs only. To disable Border Replacer in the Workflow, toggle it off/on on the Workflow page.</small>
          </div>

          <div className="field-group">
            <label>Library Selection</label>
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
            <small>Only media from selected libraries will be processed during rename</small>
          </div>
        </div>
      </div>
    </>
  )
}

export default RenamerTab
