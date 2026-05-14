import { Save } from 'lucide-react'
import { PosterConfig } from '../../api/client'
import Toolbar from './Toolbar'

type SettingsTabProps = {
  config: PosterConfig | null
  hasUnsavedChanges: boolean
  saving: boolean
  onSaveConfig: () => void
  onConfigChange: (updatedConfig: PosterConfig) => void
}

function SettingsTab({
  config,
  hasUnsavedChanges,
  saving,
  onSaveConfig,
  onConfigChange,
}: SettingsTabProps) {
  return (
    <>
      <Toolbar title="Poster Manager Settings" description="Configure how PosterFlow organizes and renames your poster files">
        <button
          className={`btn-toolbar ${hasUnsavedChanges ? 'btn-unsaved' : ''}`}
          onClick={onSaveConfig}
          disabled={!hasUnsavedChanges || saving}
          title={hasUnsavedChanges ? 'Save changes' : 'No changes to save'}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </Toolbar>

      {config && (
        <div className="settings-section">
          <div className="settings-grid">
            <div className="field-group">
              <label>Destination Directory (possibly kometa's asset directory)</label>
              <input
                type="text"
                value={config.destination}
                onChange={(e) => onConfigChange({ ...config, destination: e.target.value })}
                placeholder="ex. /kometa/config/assets"
              />
              <small className="standalone-warning">⚠️ Where organized and renamed posters will be saved. Must be a mounted volume in your Docker container</small>
            </div>

            <div className="field-group">
              <label>File Operation</label>
              <select value={config.action_type || 'copy'} onChange={(e) => onConfigChange({ ...config, action_type: e.target.value })}>
                <option value="copy">Copy (Safe, creates duplicates)</option>
                <option value="move">Move (Relocates files)</option>
                <option value="hardlink">Hard Link (Same files, different paths)</option>
                <option value="symlink">Symbolic Link (References originals)</option>
              </select>
              <small>How to organize poster files (affects storage usage)</small>
            </div>

            <div className="field-group checkbox-row">
              <label className="checkbox-label">
                <input type="checkbox" checked={config.asset_folders} onChange={(e) => onConfigChange({ ...config, asset_folders: e.target.checked })} />
                <span>
                  <strong>Use Asset Folders</strong>
                  <small>Organize in folders like "Movie Name (2023)" (Kometa/Plex style)</small>
                </span>
              </label>
            </div>
          </div>
          <p className="settings-note">Poster sources are automatically loaded from your subscribed Google Drives.</p>
        </div>
      )}
    </>
  )
}

export default SettingsTab
