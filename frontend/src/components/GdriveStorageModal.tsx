import { useState, useEffect, type MouseEvent } from 'react'
import { getGdriveStoragePath, saveGdriveStoragePath } from '../api/settings'
import { useToast } from './Toast'
import './GdriveStorageModal.css'

const DEFAULT_PATH = '/config/posters/gdrive'

interface GdriveStorageModalProps {
  onClose: () => void
}

function GdriveStorageModal({ onClose }: GdriveStorageModalProps) {
  const [path, setPath] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const { showToast } = useToast()

  useEffect(() => {
    getGdriveStoragePath()
      .then((data) => setPath(data.path))
      .catch(() => setPath(''))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const result = await saveGdriveStoragePath(path)
      showToast(`Storage path saved: ${result.path}`, 'success')
      onClose()
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to save storage path'
      showToast(msg, 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleOverlayClick = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal gdrive-storage-modal">
        <div className="modal-header">
          <h2>GDrive Storage Location</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          {loading ? (
            <p className="gdrive-storage-loading">Loading...</p>
          ) : (
            <>
              <p className="gdrive-storage-description">
                Set the base folder where synced poster files will be stored on disk.
                Leave blank to use the default location inside the <code>/config</code> volume.
              </p>

              <div className="form-group">
                <label htmlFor="gdrive-storage-path">Storage Path</label>
                <input
                  id="gdrive-storage-path"
                  type="text"
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder={DEFAULT_PATH}
                />
                <small>
                  Default: <code>{DEFAULT_PATH}</code>
                  {' '}— lives inside your <code>/config</code> volume mount.
                  Set a custom absolute path (e.g. <code>/posters/gdrive</code>) if you
                  mount a separate <code>/posters</code> volume.
                </small>
              </div>

              <div className="gdrive-storage-note">
                <strong>Note:</strong> The new path takes effect immediately. Existing poster
                files at the old location are <em>not</em> moved automatically.
              </div>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={handleSave} disabled={saving || loading}>
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default GdriveStorageModal
