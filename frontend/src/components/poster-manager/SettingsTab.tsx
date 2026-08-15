import { Save } from 'lucide-react'
import { DEFAULT_POSTER_DESTINATION, PosterConfig } from '../../api/client'
import Toolbar from '../Toolbar'

type SettingsTabProps = {
  config: PosterConfig | null
  hasUnsavedChanges: boolean
  saving: boolean
  onSaveConfig: () => void
  onConfigChange: (updatedConfig: PosterConfig) => void
}

// Posters and artwork are renamed into the same destination, so the example shows both.
// A blank destination previews the default, which is what saving blank will store.
function layoutExample(destination: string, assetFolders: boolean, defaultDestination: string): string[] {
  const dest = (destination.trim() || defaultDestination).replace(/\/+$/, '')
  return assetFolders
    ? [`${dest}/Title (2023)/poster.jpg`, `${dest}/Title (2023)/logo.png`]
    : [`${dest}/Title (2023).jpg`, `${dest}/Title (2023) - logo.png`]
}

function SettingsTab({
  config,
  hasUnsavedChanges,
  saving,
  onSaveConfig,
  onConfigChange,
}: SettingsTabProps) {
  const defaultDestination = config?.default_destination || DEFAULT_POSTER_DESTINATION
  return (
    <>
      <Toolbar title="Asset Manager Settings" description="Configure how PosterFlow organizes and renames your poster and artwork files">
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
              <label>Destination Directory</label>
              <input
                type="text"
                value={config.destination}
                onChange={(e) => onConfigChange({ ...config, destination: e.target.value })}
                placeholder="ex. /kometa/config/assets"
              />
              <small>
                The single folder PosterFlow renames into. Every asset type lands here —
                posters, logos, backdrops and square art — grouped per movie, show or
                collection. If you use Kometa, point this at its <code>asset_directory</code> so
                it picks the files up. Leave blank to use the default:{' '}
                <code>{defaultDestination}</code>
              </small>
              <div className="path-example">
                <span className="path-example-label">Files land like this:</span>
                {layoutExample(config.destination, config.asset_folders, defaultDestination).map((path) => (
                  <code key={path}>{path}</code>
                ))}
              </div>
              <small className="standalone-warning">
                {config.is_docker === false ? (
                  <>
                    ⚠️ Use an absolute path PosterFlow can write to. If Kometa runs in Docker,
                    mount this same folder into its container so both see it.
                  </>
                ) : (
                  <>
                    ⚠️ Use the path as PosterFlow's container sees it, not the host path. It must be
                    a mounted volume, and Kometa must see the same folder for its own path.
                  </>
                )}
              </small>
            </div>

            <div className="field-group">
              <label>File Operation</label>
              <select value={config.action_type || 'copy'} onChange={(e) => onConfigChange({ ...config, action_type: e.target.value })}>
                <option value="copy">Copy (Safe, creates duplicates)</option>
                <option value="move">Move (Relocates files)</option>
                <option value="hardlink">Hard Link (Same files, different paths)</option>
                <option value="symlink">Symbolic Link (References originals)</option>
              </select>
              <small>How files reach the destination (affects storage usage)</small>
            </div>

            <div className="field-group checkbox-row">
              <label className="checkbox-label">
                <input type="checkbox" checked={config.asset_folders} onChange={(e) => onConfigChange({ ...config, asset_folders: e.target.checked })} />
                <span>
                  <strong>Use Asset Folders</strong>
                  <small>Give each item its own folder (Kometa/Plex style) instead of one flat directory</small>
                </span>
              </label>
            </div>
          </div>
          <p className="settings-note">
            These settings apply to posters and artwork alike. Sources are loaded automatically
            from the drives you subscribe to.
          </p>
        </div>
      )}
    </>
  )
}

export default SettingsTab
