import { useState, type MouseEvent } from 'react'
import { useToast } from './Toast'
import ArtworkTypePicker from './ArtworkTypePicker'
import { type ArtworkType } from '../api/client'
import './DriveEditModal.css'

interface DriveEditModalProps {
  drive: {
    id: number
    name: string
    drive_id: string
    is_custom: boolean
    subscribed: boolean
    sync_enabled: boolean
    custom_path: string | null
    /** Artwork drives only — present means the type picker is shown. */
    synced_types?: ArtworkType[]
  }
  /** Indexed counts per artwork type, so the picker can show what's on disk. */
  artworkTypeCounts?: Partial<Record<ArtworkType, number>>
  onClose: () => void
  onSave: (driveId: number, updates: { custom_path: string | null; sync_enabled: boolean; drive_id?: string; synced_types?: ArtworkType[] }) => void
}

function DriveEditModal({ drive, artworkTypeCounts, onClose, onSave }: DriveEditModalProps) {
  const initiallyManual = drive.is_custom && drive.drive_id.startsWith('manual-')
  const [customPath, setCustomPath] = useState(drive.custom_path || '')
  const [driveId, setDriveId] = useState(initiallyManual ? '' : (drive.drive_id || ''))
  const [syncEnabled, setSyncEnabled] = useState(drive.sync_enabled)
  const [syncedTypes, setSyncedTypes] = useState<ArtworkType[]>(drive.synced_types || [])
  const isArtwork = drive.synced_types !== undefined
  const { showToast } = useToast()

  const handleSave = () => {
    if (drive.is_custom && syncEnabled && !driveId.trim()) {
      showToast('Google Drive ID is required when GDrive sync is enabled', 'error')
      return
    }
    if (isArtwork && syncedTypes.length === 0) {
      showToast('Select at least one artwork type', 'error')
      return
    }

    onSave(drive.id, {
      custom_path: customPath || null,
      sync_enabled: syncEnabled,
      ...(isArtwork ? { synced_types: syncedTypes } : {}),
      ...(drive.is_custom
        ? {
            // For custom drives: pass drive_id so the backend can keep/generate the manual- ID
            drive_id: syncEnabled ? driveId.trim() : (initiallyManual ? drive.drive_id : ''),
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
              When enabled, rclone downloads posters from Google Drive to the local folder on each sync job.
            </small>
            {drive.is_custom && syncEnabled && (
              <small style={{ color: '#aaa' }}>
                Tip: disable this if you want a local override folder — drop posters in manually instead of syncing from Google Drive.
              </small>
            )}
            {!drive.is_custom && !syncEnabled && (
              <small style={{ color: '#f44336' }}>
                ⚠ Not recommended for community drives. Only disable sync if this is your own personal Google Drive
                and you want to manage its local folder directly (e.g. you are a poster maker publishing your own content).
              </small>
            )}
            {!syncEnabled && (
              <small style={{ color: '#ffb74d' }}>
                GDrive sync is off — posters you place manually in this drive's local folder will be tracked,
                but only after a sync job runs. Use the sync button on the drive card, or schedule regular syncs,
                to keep the index up to date.
              </small>
            )}
          </div>

          <div className="form-group">
            <label className="field-label">Drive Name</label>
            <input type="text" value={drive.name} disabled />
          </div>

          {isArtwork && (
            <div className="form-group">
              <label className="field-label">Artwork to sync</label>
              <ArtworkTypePicker value={syncedTypes} onChange={setSyncedTypes} counts={artworkTypeCounts} />
              <small>Only the folders you pick are downloaded on the next sync.</small>
              <small style={{ color: '#ffb74d' }}>
                Unchecking a type deletes its already-synced files from this drive's local folder.
              </small>
            </div>
          )}

          {drive.is_custom && syncEnabled && (
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
              placeholder="e.g., /media/posters or subfolders/name"
            />
            {syncEnabled ? (
              <>
                <small>Leave empty to sync into the default location: <code>{'<storage>/<style>/<drive name>'}</code>.</small>
                <small><strong>Absolute path</strong> (starts with <code>/</code>): syncs directly into that folder — e.g. <code>/media/posters/movies</code>.</small>
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