import { Edit2, Eye, EyeOff, List, Lock, Save, Trash2, Zap } from 'lucide-react'

type ServerInstance = {
  name: string
  url: string
  api_key: string
}

type MediaSettings = {
  plex_instances: ServerInstance[]
  sonarr_instances: ServerInstance[]
  radarr_instances: ServerInstance[]
}

type SettingsMediaSectionProps = {
  mediaSettings: MediaSettings
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
            <h3>Plex Media Servers</h3>
            <button type="button" className="btn-add-instance" onClick={onAddPlexInstance}>
              + Add Instance
            </button>
          </div>
          <div className="server-cards-grid">
            {mediaSettings.plex_instances.map((instance, index) => {
              const isEditing = editingPlex.has(index)
              const hasSettings = !!(instance.url || instance.api_key)
              const hasUnsaved = dirtyPlexInstances.has(index)
              return (
                <div key={index} className={`server-card ${!isEditing && hasSettings ? 'locked' : ''}`}>
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
                      <label>Name</label>
                      <input
                        type="text"
                        value={instance.name}
                        onChange={(e) => onUpdatePlexInstance(index, 'name', e.target.value)}
                        placeholder="e.g., Plex Main, Plex 4K"
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>Plex URL</label>
                      <input
                        type="text"
                        value={instance.url}
                        onChange={(e) => onUpdatePlexInstance(index, 'url', e.target.value)}
                        placeholder="http://localhost:32400"
                        readOnly={!isEditing && hasSettings}
                      />
                    </div>
                    <div className="form-group">
                      <label>Plex Token</label>
                      <div className="input-with-toggle">
                        <input
                          type={showPlexTokens[index] ? 'text' : 'password'}
                          value={
                            !showPlexTokens[index] && !isEditing && hasSettings && instance.api_key
                              ? HIDDEN_MASK_DISPLAY
                              : instance.api_key
                          }
                          onChange={(e) => onUpdatePlexInstance(index, 'api_key', e.target.value)}
                          placeholder="X-Plex-Token"
                          readOnly={!isEditing && hasSettings}
                        />
                        <button type="button" className="toggle-visibility" onClick={() => onTogglePlexTokenVisibility(index)} title={showPlexTokens[index] ? 'Hide' : 'Show'}>
                          {showPlexTokens[index] ? <EyeOff size={18} /> : <Eye size={18} />}
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="server-section">
          <div className="server-section-header">
            <h3>Sonarr Instances</h3>
            <button type="button" className="btn-add-instance" onClick={onAddSonarrInstance}>
              + Add Instance
            </button>
          </div>
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
                      {mediaSettings.sonarr_instances.length > 1 && (
                        <button type="button" className="btn-remove-instance" onClick={() => onConfirmRemoveSonarrInstance(index)} title="Remove instance">
                          <Trash2 size={16} />
                        </button>
                      )}
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
                      {mediaSettings.radarr_instances.length > 1 && (
                        <button type="button" className="btn-remove-instance" onClick={() => onConfirmRemoveRadarrInstance(index)} title="Remove instance">
                          <Trash2 size={16} />
                        </button>
                      )}
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
