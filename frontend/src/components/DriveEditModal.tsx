import { useState, type MouseEvent } from 'react'
import { useToast } from './Toast'
import './DriveEditModal.css'

interface DriveEditModalProps {
  drive: {
    id: number
    name: string
    drive_id: string
    is_custom: boolean
    subscribed: boolean
    custom_path: string | null
  }
  onClose: () => void
  onSave: (driveId: number, updates: { custom_path: string | null; subscribed?: boolean; drive_id?: string }) => void
}

function DriveEditModal({ drive, onClose, onSave }: DriveEditModalProps) {
  const initiallyExcludedFromSync = drive.is_custom && drive.drive_id.startsWith('manual-')
  const [customPath, setCustomPath] = useState(drive.custom_path || '')
  const [driveId, setDriveId] = useState(initiallyExcludedFromSync ? '' : (drive.drive_id || ''))
  const [excludeFromSync, setExcludeFromSync] = useState(initiallyExcludedFromSync)
  const { showToast } = useToast()

  const handleSave = () => {
    if (drive.is_custom && !excludeFromSync && !driveId.trim()) {
      showToast('Google Drive ID is required when sync is enabled', 'error')
      return
    }

    onSave(drive.id, {
      custom_path: customPath || null,
      ...(drive.is_custom
        ? {
            drive_id: excludeFromSync
              ? (initiallyExcludedFromSync ? drive.drive_id : '')
              : driveId.trim(),
          }
        : {}),
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
          <h2>Edit Drive Settings</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {drive.is_custom && (
            <div className="form-group checkbox-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={excludeFromSync}
                  onChange={(e) => setExcludeFromSync(e.target.checked)}
                />
                Exclude this drive from sync jobs
              </label>
              <small>Enable this if this custom drive is for manual posters only.</small>
            </div>
          )}

          <div className="form-group">
            <label>{excludeFromSync ? 'Folder Name' : 'Drive Name'}</label>
            <input type="text" value={drive.name} disabled />
          </div>

          {drive.is_custom && !excludeFromSync && (
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
              placeholder={excludeFromSync ? 'e.g., /media/posters or subfolders/name' : 'e.g., /media/posters or subfolders/name'}
            />
            {excludeFromSync ? (
              <>
                <small>Leave empty to use the default location inside your configured GDrive storage folder.</small>
                <small><strong>Absolute path</strong> (starts with <code>/</code>): files go exactly there — e.g. <code>/media/posters/manual</code>.</small>
                <small><strong>Relative path</strong> (no leading <code>/</code>): appended to your GDrive storage base — e.g. <code>manual/overrides</code> → <code>/config/posters/gdrive/manual/overrides</code>.</small>
              </>
            ) : (
              <>
                <small>Leave empty to sync into the default location: <code>{'<storage>/<style>/<drive name>'}</code>.</small>
                <small><strong>Absolute path</strong> (starts with <code>/</code>): syncs directly into that folder, no subfolders added — e.g. <code>/media/posters/movies</code>.</small>
                <small><strong>Relative path</strong> (no leading <code>/</code>): appended to your GDrive storage base — e.g. <code>overrides/movies</code> → <code>/config/posters/gdrive/overrides/movies</code>.</small>
              </>
            )}
          </div>
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSave}>Save Changes</button>
        </div>
      </div>
    </div>
  )
}

export default DriveEditModal