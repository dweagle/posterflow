import { useState, useEffect } from 'react'
import { getGdriveStoragePath, saveGdriveStoragePath, getArtworkGdriveStoragePath, saveArtworkGdriveStoragePath } from '../api/settings'
import { useToast } from './Toast'
import './GdriveStorageModal.css'
import Modal from '../components/Modal'

interface GdriveStorageModalProps {
  onClose: () => void
  variant?: 'posters' | 'artwork'
}

function GdriveStorageModal({ onClose, variant = 'posters' }: GdriveStorageModalProps) {
  const isArtwork = variant === 'artwork'
  const DEFAULT_PATH = isArtwork ? '/config/artwork/gdrive' : '/config/posters/gdrive'
  const getStoragePath = isArtwork ? getArtworkGdriveStoragePath : getGdriveStoragePath
  const saveStoragePath = isArtwork ? saveArtworkGdriveStoragePath : saveGdriveStoragePath
  const assetNoun = isArtwork ? 'artwork' : 'poster'

  const [path, setPath] = useState('')
  const [defaultPath, setDefaultPath] = useState(DEFAULT_PATH)
  const [isDocker, setIsDocker] = useState(true)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const { showToast } = useToast()

  useEffect(() => {
    getStoragePath()
      .then((data) => {
        setPath(data.path)
        if (data.default_path) setDefaultPath(data.default_path)
        if (data.is_docker !== undefined) setIsDocker(data.is_docker)
      })
      .catch(() => setPath(''))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      const result = await saveStoragePath(path)
      showToast(`Storage path saved: ${result.path}`, 'success')
      onClose()
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Failed to save storage path'
      showToast(msg, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} className="gdrive-storage-modal">
      <Modal.Header title={isArtwork ? 'Artwork Storage Location' : 'GDrive Storage Location'} />
      <Modal.Body>
        {loading ? (
          <p className="gdrive-storage-loading">Loading...</p>
        ) : (
     <>
            <p className="gdrive-storage-description">
              Set the base folder where synced {assetNoun} files will be stored on disk.
              Leave blank to use the default location inside {isDocker
                ? <>the <code>/config</code> volume</>
                : "PosterFlow's config directory"}.
            </p>

            <div className="form-group">
              <label className="field-label" htmlFor="gdrive-storage-path">Storage Path</label>
              <input
                id="gdrive-storage-path"
                type="text"
                value={path}
                onChange={(e) => setPath(e.target.value)}
                placeholder={defaultPath}
              />
              <small>
                Default: <code>{defaultPath}</code>
                {isDocker ? (
         <>
                    {' '}— lives inside your <code>/config</code> volume mount.
                    Set a custom absolute path if you mount a separate volume.
                  </>
                ) : (
                  <> — set a custom absolute path to store {assetNoun} files elsewhere.</>
                )}
              </small>
            </div>

            <div className="gdrive-storage-note">
              <strong>Note:</strong> The new path takes effect immediately. Existing {assetNoun}
              {' '}files at the old location are <em>not</em> moved automatically.
            </div>
          </>
        )}
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={handleSave} disabled={saving || loading}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default GdriveStorageModal
