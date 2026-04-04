import { useState, type MouseEvent } from 'react'
import './DriveEditModal.css'

interface AddCustomDriveModalProps {
  onClose: () => void
  onAdd: (drive: {
    name: string
    drive_id: string
    custom_path: string | null
    subscribed: boolean
  }) => void
}

function AddCustomDriveModal({ onClose, onAdd }: AddCustomDriveModalProps) {
  const [name, setName] = useState('')
  const [driveId, setDriveId] = useState('')
  const [customPath, setCustomPath] = useState('')
  const [excludeFromSync, setExcludeFromSync] = useState(false)

  const handleAdd = () => {
    if (!name) {
      alert('Drive name is required')
      return
    }

    if (!excludeFromSync && !driveId.trim()) {
      alert('Google Drive ID is required when sync is enabled')
      return
    }

    onAdd({
      name,
      drive_id: excludeFromSync ? '' : driveId.trim(),
      custom_path: customPath || null,
      subscribed: true
    })
    onClose()
  }

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onClose()
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>Add Custom Drive</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="form-group checkbox-group">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={excludeFromSync}
                onChange={(e) => setExcludeFromSync(e.target.checked)}
              />
              Add as custom local folder only (no Google Drive sync)
            </label>
            <small>
              When enabled, this creates a custom folder for manual posters and does not sync from Google Drive.
            </small>
          </div>

          <div className="form-group">
            <label>{excludeFromSync ? 'Folder Name *' : 'Drive Name *'}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={excludeFromSync ? 'My Override Folder' : 'My Custom Posters'}
            />
          </div>

          {!excludeFromSync && (
            <div className="form-group">
              <label>Google Drive ID *</label>
              <input
                type="text"
                value={driveId}
                onChange={(e) => setDriveId(e.target.value)}
                placeholder="1ABC...xyz"
              />
              <small>Required for synced drives (found in the Google Drive share link).</small>
            </div>
          )}

          <div className="form-group">
            <label>{excludeFromSync ? 'Custom Folder Path (optional)' : 'Custom Sync Path (optional)'}</label>
            <input
              type="text"
              value={customPath}
              onChange={(e) => setCustomPath(e.target.value)}
              placeholder="e.g., Movies/Overrides"
            />
            <small>
              {excludeFromSync
                ? 'Leave empty to use app default folder location. Set a custom path to use a specific subfolder.'
                : 'Leave empty to sync to app default folder. Set to custom path to specific subfolders.'}
            </small>
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleAdd}>Add Drive</button>
        </div>
      </div>
    </div>
  )
}

export default AddCustomDriveModal