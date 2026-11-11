import { useState } from 'react'
import { useToast } from './Toast'
import Modal from '../components/Modal'

interface AddCustomDriveModalProps {
  onClose: () => void
  onAdd: (drive: {
    name: string
    drive_id: string
    custom_path: string | null
    subscribed: boolean
    sync_enabled: boolean
  }) => void
}

function AddCustomDriveModal({ onClose, onAdd }: AddCustomDriveModalProps) {
  const [name, setName] = useState('')
  const [driveId, setDriveId] = useState('')
  const [customPath, setCustomPath] = useState('')
  const [syncEnabled, setSyncEnabled] = useState(true)
  const { showToast } = useToast()

  const handleAdd = () => {
    if (!name) {
      showToast('Drive name is required', 'error')
      return
    }

    if (syncEnabled && !driveId.trim()) {
      showToast('Google Drive ID is required when GDrive sync is enabled', 'error')
      return
    }

    onAdd({
      name,
      drive_id: syncEnabled ? driveId.trim() : '',
      custom_path: customPath || null,
      subscribed: true,
      sync_enabled: syncEnabled,
    })
    onClose()
  }

  return (
    <Modal onClose={onClose}>
      <Modal.Header title="Add Custom Drive" />
      <Modal.Body>
        <div className="form-group checkbox-group">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={syncEnabled}
              onChange={(e) => setSyncEnabled(e.target.checked)}
            />
            Sync from Google Drive
          </label>
          <small>
            When enabled, rclone downloads posters from Google Drive into this folder.
            Disable to create a local-only folder where you manually place posters.
          </small>
        </div>

        <div className="form-group">
          <label className="field-label">{syncEnabled ? 'Drive Name *' : 'Folder Name *'}</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={syncEnabled ? 'My Custom Posters' : 'My Override Folder'}
          />
        </div>

        {syncEnabled && (
          <div className="form-group">
            <label className="field-label">Google Drive ID *</label>
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
          <label className="field-label">{syncEnabled ? 'Custom Sync Path (optional)' : 'Custom Folder Path (optional)'}</label>
          <input
            type="text"
            value={customPath}
            onChange={(e) => setCustomPath(e.target.value)}
            placeholder="e.g., Movies/Overrides"
          />
          {syncEnabled ? (
      <>
              <small>Leave empty to sync into the default location: <code>{'<storage>/<style>/<drive name>'}</code>.</small>
              <small><strong>Absolute path</strong> (starts with <code>/</code>): syncs directly into that folder, no subfolders added — e.g. <code>/media/posters/movies</code>.</small>
              <small><strong>Relative path</strong> (no leading <code>/</code>): appended to your GDrive storage base — e.g. <code>overrides/movies</code> → <code>/config/posters/gdrive/overrides/movies</code>.</small>
            </>
          ) : (
      <>
              <small>Leave empty to use the default location inside your configured GDrive storage folder.</small>
              <small><strong>Absolute path</strong> (starts with <code>/</code>): files go exactly there — e.g. <code>/media/posters/manual</code>.</small>
              <small><strong>Relative path</strong> (no leading <code>/</code>): appended to your GDrive storage base — e.g. <code>manual/overrides</code> → <code>/config/posters/gdrive/manual/overrides</code>.</small>
            </>
          )}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={handleAdd}>Add Drive</button>
      </Modal.Footer>
    </Modal>
  )
}

export default AddCustomDriveModal