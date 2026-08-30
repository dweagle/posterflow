import { Edit2, Eye, EyeOff, List, Lock, Save, Trash2, Zap } from 'lucide-react'
import { MEDIA_SERVER_COPY, instanceServerType, isJellyfinInstance } from '../../utils/mediaServer'

type ServerInstance = {
  name: string
  url: string
  api_key: string
  type?: 'plex' | 'jellyfin' // media server instances only; absent = plex
}

type MediaSettings = {
  plex_instances: ServerInstance[]
  sonarr_instances: ServerInstance[]
  radarr_instances: ServerInstance[]
  media_server_media_source?: string
}

type SettingsMediaSectionProps = {
  mediaSettings: MediaSettings
  mediaServerMediaSourceEnabled: boolean
  mediaServerMediaSourceAuto: boolean
  onToggleMediaServerMediaSource: () => void
  editingPlex: Set<number>
  editingSonarr: Set<number>
  editingRadarr: Set<number>
  testingPlex: Set<number>
  testingSonarr: Set<number>
  testingRadarr: Set<number>
  showPlexTokens: Record<number, boolean>
  showSonarrKeys: Record<number, boolean>
  showRadarrKeys: Record<number, boolean>
  saving: boolean
  dirtyPlexInstances: Set<number>
  dirtySonarrInstances: Set<number>
  dirtyRadarrInstances: Set<number>
  onAddPlexInstance: () => void
  onAddSonarrInstance: () => void
  onAddRadarrInstance: () => void
  onToggleEditPlex: (index: number) => void
  onToggleEditSonarr: (index: number) => void
  onToggleEditRadarr: (index: number) => void
  onTestPlexConnection: (index: number) => void
  onTestSonarrConnection: (index: number) => void
  onTestRadarrConnection: (index: number) => void
  onOpenLibraryModal: (index: number) => void
  onSaveMediaSettings: () => void
  onConfirmRemovePlexInstance: (index: number) => void
  onConfirmRemoveSonarrInstance: (index: number) => void
  onConfirmRemoveRadarrInstance: (index: number) => void
  onUpdatePlexInstance: (index: number, field: keyof ServerInstance, value: string) => void
  onUpdateSonarrInstance: (index: number, field: keyof ServerInstance, value: string) => void
  onUpdateRadarrInstance: (index: number, field: keyof ServerInstance, value: string) => void
  onTogglePlexTokenVisibility: (index: number) => void | Promise<void>
  onToggleSonarrKeyVisibility: (index: number) => void | Promise<void>
  onToggleRadarrKeyVisibility: (index: number) => void | Promise<void>
}

function SettingsMediaSection({
  mediaSettings,
  mediaServerMediaSourceEnabled,
  mediaServerMediaSourceAuto,
  onToggleMediaServerMediaSource,
  editingPlex,
  editingSonarr,
  editingRadarr,
  testingPlex,
  testingSonarr,
  testingRadarr,
  showPlexTokens,
  showSonarrKeys,
  showRadarrKeys,
  saving,
  dirtyPlexInstances,
  dirtySonarrInstances,
  dirtyRadarrInstances,
  onAddPlexInstance,
  onAddSonarrInstance,
  onAddRadarrInstance,
  onToggleEditPlex,
  onToggleEditSonarr,
  onToggleEditRadarr,
  onTestPlexConnection,
  onTestSonarrConnection,
  onTestRadarrConnection,
  onOpenLibraryModal,
  onSaveMediaSettings,
  onConfirmRemovePlexInstance,
  onConfirmRemoveSonarrInstance,
  onConfirmRemoveRadarrInstance,
  onUpdatePlexInstance,
  onUpdateSonarrInstance,
  onUpdateRadarrInstance,
  onTogglePlexTokenVisibility,
  onToggleSonarrKeyVisibility,
  onToggleRadarrKeyVisibility,
}: SettingsMediaSectionProps) {
  const HIDDEN_MASK_DISPLAY = '••••••••••••'

  return (
    <div className="settings-section">
      <div className="media-servers-container">
        <div className="server-section">
          <div className="server-section-header">
            <h3>Media Servers</h3>
            <button type="button" className="btn-add-instance" onClick={onAddPlexInstance}>
              + Add Instance
            </button>
          </div>
          <div className="server-cards-grid">
            {mediaSettings.plex_instances.map((instance, index) => {
              const isEditing = editingPlex.has(index)
              const hasSettings = !!(instance.url || instance.api_key)
              const hasUnsaved = dirtyPlexInstances.has(index)
              const isJellyfin = isJellyfinInstance(instance)
              const copy = MEDIA_SERVER_COPY[instanceServerType(instance)]
              return (
                <div key={index} className={`server-card plex-card ${!isEditing && hasSettings ? 'locked' : ''}`}>
                  <div className="card-header">
                    <span className="card-title">{instance.name || `Plex ${index + 1}`}</span>
                    <div className="card-actions">
                      {hasSettings && (
                        <button type="button" className="btn-edit-instance" onClick={() => onToggleEditPlex(index)} title={isEditing ? 'Lock instance' : 'Edit instance'}>
                          {isEditing ? <Lock size={16} /> : <Edit2 size={16} />}
                        </button>
                      )}
                      {hasSettings && (
                        <button type="button" className="btn-test-instance" onClick={() => onTestPlexConnection(index)} disabled={testingPlex.has(index)} title="Test connection">
                          <Zap size={16} />
                        </button>
                      )}
                      {hasSettings && (
                        <button type="button" className="btn-test-instance" onClick={() => onOpenLibraryModal(index)} title="Manage libraries">
                          <List size={16} />
                        </button>
                      )}
                      <button
                        type="button"
                        className={`btn-save-instance ${hasUnsaved ? 'btn-unsaved' : ''}`}
                        onClick={onSaveMediaSettings}
                        disabled={saving || !hasUnsaved}
                        title={hasUnsaved ? 'Save all settings' : 'No changes to save for this instance'}
                      >
                        <Save size={16} />
                      </button>
                      {mediaSettings.plex_instances.length > 1 && (
                        <button type="button" className="btn-remove-instance" onClick={() => onConfirmRemovePlexInstance(index)} title="Remove instance">
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  </div>
                  <div className="card-body">
                    <div className="form-group">
                      <label>Server Type</label>
                      <select
                        value={isJellyfin ? 'jellyfin' : 'plex'}
                        onChange={(e) => onUpdatePlexInstance(index, 'type', e.target.value)}
                        disabled={!isEditing && hasSettings}
                      >
                        <option value="plex">Plex</option>
                        <option value="jellyfin">Jellyfin</option>
                      </select>
                    </div>
                    <div className="form-group">
                      <label>Name</label>
                      <input
                        type="text"
                        value={instance.name}
                        onChange={(e) => onUpdatePlexInstance(index, 'name', e.target.value)}
                        placeholder={copy.namePlaceholder}
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>{copy.urlLabel}</label>
                      <input
                        type="text"
                        value={instance.url}
                        onChange={(e) => onUpdatePlexInstance(index, 'url', e.target.value)}
                        placeholder={copy.urlPlaceholder}
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>{copy.keyLabel}</label>
                      <div className="input-with-toggle">
                        <input
                          type={showPlexTokens[index] ? 'text' : 'password'}
                          value={
                            !showPlexTokens[index] && !isEditing && hasSettings && instance.api_key
                              ? HIDDEN_MASK_DISPLAY
                              : instance.api_key
                          }
                          onChange={(e) => onUpdatePlexInstance(index, 'api_key', e.target.value)}
                          placeholder={isJellyfin ? 'Jellyfin API Key' : 'X-Plex-Token'}
                          readOnly={!isEditing && hasSettings}
                        />
                        <button type="button" className="toggle-visibility" onClick={() => onTogglePlexTokenVisibility(index)} title={showPlexTokens[index] ? 'Hide' : 'Show'}>
                          {showPlexTokens[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                      </div>
                      {isJellyfin ? (
                        <small>Create one in Jellyfin under Dashboard → API Keys</small>
                      ) : (
                        <small>Find your token: <a href="https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/" target="_blank" rel="noopener noreferrer">Plex Support Article</a></small>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
          <div className="media-source-toggle-row">
            <label className="toggle-switch" title={mediaServerMediaSourceEnabled ? 'Enabled' : 'Disabled'}>
              <input
                type="checkbox"
                checked={mediaServerMediaSourceEnabled}
                onChange={onToggleMediaServerMediaSource}
              />
              <span className="toggle-slider"></span>
            </label>
            <div>
              <strong>Source movies &amp; shows from media server libraries</strong>
              {mediaServerMediaSourceAuto && (
                <span className="media-source-auto-hint"> (auto — {mediaServerMediaSourceEnabled ? 'on because no Radarr/Sonarr is configured' : 'off while Radarr/Sonarr are configured'})</span>
              )}
              <small>
                Lets the renamer, unmatched scan, and cleanup match against your Plex/Jellyfin libraries directly.
                Works without any Radarr/Sonarr, or alongside them for libraries the arrs don't manage.
              </small>
            </div>
          </div>
        </div>

        <div className="server-section">
          <div className="server-section-header">
            <h3>Sonarr Instances</h3>
            <button type="button" className="btn-add-instance" onClick={onAddSonarrInstance}>
              + Add Instance
            </button>
          </div>
          {mediaSettings.sonarr_instances.length === 0 && (
            <p className="empty-instances-note">
              No Sonarr instances — shows are sourced from your media server libraries when the toggle above is on.
            </p>
          )}
          <div className="server-cards-grid">
            {mediaSettings.sonarr_instances.map((instance, index) => {
              const isEditing = editingSonarr.has(index)
              const hasSettings = !!(instance.url || instance.api_key)
              const hasUnsaved = dirtySonarrInstances.has(index)
              return (
                <div key={index} className={`server-card ${!isEditing && hasSettings ? 'locked' : ''}`}>
                  <div className="card-header">
                    <span className="card-title">{instance.name || `Sonarr ${index + 1}`}</span>
                    <div className="card-actions">
                      {hasSettings && (
                        <button type="button" className="btn-edit-instance" onClick={() => onToggleEditSonarr(index)} title={isEditing ? 'Lock instance' : 'Edit instance'}>
                          {isEditing ? <Lock size={16} /> : <Edit2 size={16} />}
                        </button>
                      )}
                      {hasSettings && (
                        <button type="button" className="btn-test-instance" onClick={() => onTestSonarrConnection(index)} disabled={testingSonarr.has(index)} title="Test connection">
                          <Zap size={16} />
                        </button>
                      )}
                      <button
                        type="button"
                        className={`btn-save-instance ${hasUnsaved ? 'btn-unsaved' : ''}`}
                        onClick={onSaveMediaSettings}
                        disabled={saving || !hasUnsaved}
                        title={hasUnsaved ? 'Save all settings' : 'No changes to save for this instance'}
                      >
                        <Save size={16} />
                      </button>
                      <button type="button" className="btn-remove-instance" onClick={() => onConfirmRemoveSonarrInstance(index)} title="Remove instance">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                  <div className="card-body">
                    <div className="form-group">
                      <label>Name</label>
                      <input
                        type="text"
                        value={instance.name}
                        onChange={(e) => onUpdateSonarrInstance(index, 'name', e.target.value)}
                        placeholder="e.g., Sonarr 4K, Sonarr HD"
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>Sonarr URL</label>
                      <input
                        type="text"
                        value={instance.url}
                        onChange={(e) => onUpdateSonarrInstance(index, 'url', e.target.value)}
                        placeholder="http://localhost:8989"
                        readOnly={!isEditing && hasSettings}
                      />
                      <small>If Sonarr has a Base URL set (Settings → General), include it here. e.g., <code>http://localhost:8989/sonarr</code></small>
                    </div>
                    <div className="form-group">
                      <label>API Key</label>
                      <div className="input-with-toggle">
                        <input
                          type={showSonarrKeys[index] ? 'text' : 'password'}
                          value={
                            !showSonarrKeys[index] && !isEditing && hasSettings && instance.api_key
                              ? HIDDEN_MASK_DISPLAY
                              : instance.api_key
                          }
                          onChange={(e) => onUpdateSonarrInstance(index, 'api_key', e.target.value)}
                          placeholder="Sonarr API Key"
                          readOnly={!isEditing && hasSettings}
                        />
                        <button type="button" className="toggle-visibility" onClick={() => onToggleSonarrKeyVisibility(index)} title={showSonarrKeys[index] ? 'Hide' : 'Show'}>
                          {showSonarrKeys[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                      </div>
                      <small>Find in Sonarr: Settings → General → Security → API Key</small>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="server-section">
          <div className="server-section-header">
            <h3>Radarr Instances</h3>
            <button type="button" className="btn-add-instance" onClick={onAddRadarrInstance}>
              + Add Instance
            </button>
          </div>
          {mediaSettings.radarr_instances.length === 0 && (
            <p className="empty-instances-note">
              No Radarr instances — movies are sourced from your media server libraries when the toggle above is on.
            </p>
          )}
          <div className="server-cards-grid">
            {mediaSettings.radarr_instances.map((instance, index) => {
              const isEditing = editingRadarr.has(index)
              const hasSettings = !!(instance.url || instance.api_key)
              const hasUnsaved = dirtyRadarrInstances.has(index)
              return (
                <div key={index} className={`server-card ${!isEditing && hasSettings ? 'locked' : ''}`}>
                  <div className="card-header">
                    <span className="card-title">{instance.name || `Radarr ${index + 1}`}</span>
                    <div className="card-actions">
                      {hasSettings && (
                        <button type="button" className="btn-edit-instance" onClick={() => onToggleEditRadarr(index)} title={isEditing ? 'Lock instance' : 'Edit instance'}>
                          {isEditing ? <Lock size={16} /> : <Edit2 size={16} />}
                        </button>
                      )}
                      {hasSettings && (
                        <button type="button" className="btn-test-instance" onClick={() => onTestRadarrConnection(index)} disabled={testingRadarr.has(index)} title="Test connection">
                          <Zap size={16} />
                        </button>
                      )}
                      <button
                        type="button"
                        className={`btn-save-instance ${hasUnsaved ? 'btn-unsaved' : ''}`}
                        onClick={onSaveMediaSettings}
                        disabled={saving || !hasUnsaved}
                        title={hasUnsaved ? 'Save all settings' : 'No changes to save for this instance'}
                      >
                        <Save size={16} />
                      </button>
                      <button type="button" className="btn-remove-instance" onClick={() => onConfirmRemoveRadarrInstance(index)} title="Remove instance">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                  <div className="card-body">
                    <div className="form-group">
                      <label>Name</label>
                      <input
                        type="text"
                        value={instance.name}
                        onChange={(e) => onUpdateRadarrInstance(index, 'name', e.target.value)}
                        placeholder="e.g., Radarr 4K, Radarr HD"
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>Radarr URL</label>
                      <input
                        type="text"
                        value={instance.url}
                        onChange={(e) => onUpdateRadarrInstance(index, 'url', e.target.value)}
                        placeholder="http://localhost:7878"
                        readOnly={!isEditing && hasSettings}
                      />
                      <small>If Radarr has a Base URL set (Settings → General), include it here. e.g., <code>http://localhost:7878/radarr</code></small>
                    </div>
                    <div className="form-group">
                      <label>API Key</label>
                      <div className="input-with-toggle">
                        <input
                          type={showRadarrKeys[index] ? 'text' : 'password'}
                          value={
                            !showRadarrKeys[index] && !isEditing && hasSettings && instance.api_key
                              ? HIDDEN_MASK_DISPLAY
                              : instance.api_key
                          }
                          onChange={(e) => onUpdateRadarrInstance(index, 'api_key', e.target.value)}
                          placeholder="Radarr API Key"
                          readOnly={!isEditing && hasSettings}
                        />
                        <button type="button" className="toggle-visibility" onClick={() => onToggleRadarrKeyVisibility(index)} title={showRadarrKeys[index] ? 'Hide' : 'Show'}>
                          {showRadarrKeys[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                      </div>
                      <small>Find in Radarr: Settings → General → Security → API Key</small>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

export default SettingsMediaSection
