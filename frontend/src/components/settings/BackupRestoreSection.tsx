import { ChangeEvent, useEffect, useRef, useState } from 'react'
import { getBackupStorage, saveBackupStorage, saveBackupToLocation, getApiErrorMessage } from '../../api/client'

type ToastType = 'success' | 'error' | 'info'

type BackupRestoreSectionProps = {
  backupLoading: boolean
  restoreLoading: boolean
  onDownloadBackup: () => Promise<void>
  onRestoreBackup: (file: File) => Promise<void>
  showToast: (message: string, type?: ToastType) => void
}

function BackupRestoreSection({
  backupLoading,
  restoreLoading,
  onDownloadBackup,
  onRestoreBackup,
  showToast,
}: BackupRestoreSectionProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [backupPath, setBackupPath] = useState('')
  const [retention, setRetention] = useState('7')
  const [defaultPath, setDefaultPath] = useState('/config/backups')
  const [isDocker, setIsDocker] = useState(true)
  const [storageSaving, setStorageSaving] = useState(false)
  const [savingToLocation, setSavingToLocation] = useState(false)

  useEffect(() => {
    let mounted = true

    const loadBackupStorage = async () => {
      try {
        const storage = await getBackupStorage()
        if (mounted) {
          setBackupPath(storage.path)
          setRetention(String(storage.retention))
          setDefaultPath(storage.default_path)
          if (storage.is_docker !== undefined) setIsDocker(storage.is_docker)
        }
      } catch (error) {
        console.error('Error loading backup storage settings:', error)
      }
    }

    loadBackupStorage()

    return () => {
      mounted = false
    }
  }, [])

  const handleSaveBackupStorage = async () => {
    const retentionValue = parseInt(retention, 10)
    if (isNaN(retentionValue) || retentionValue < 0 || retentionValue > 365) {
      showToast('Backups to keep must be between 0 and 365', 'error')
      return
    }

    setStorageSaving(true)
    try {
      const saved = await saveBackupStorage(backupPath.trim(), retentionValue)
      setBackupPath(saved.path)
      setRetention(String(saved.retention))
      showToast('Backup settings saved', 'success')
    } catch (error) {
      console.error('Error saving backup storage settings:', error)
      showToast(getApiErrorMessage(error, 'Failed to save backup settings'), 'error')
    } finally {
      setStorageSaving(false)
    }
  }

  const handleSaveToLocation = async () => {
    setSavingToLocation(true)
    try {
      const result = await saveBackupToLocation()
      showToast(result.message, 'success')
    } catch (error) {
      console.error('Error saving backup to location:', error)
      showToast(getApiErrorMessage(error, 'Failed to save backup'), 'error')
    } finally {
      setSavingToLocation(false)
    }
  }

  const handleFileChange = async (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    await onRestoreBackup(file)
    e.target.value = ''
  }

  return (
    <div className="settings-section">
      <div className="settings-section-header">
        <h2>Backup & Restore</h2>
        <p className="setting-description">
          Export or import your complete PosterFlow configuration including database, schedules, and settings.
        </p>
      </div>

      <div className="backup-section">
        <div className="backup-item">
          <div className="backup-columns">
            <div className="backup-column">
              <h3>Create Backup</h3>
              <p>Back up your complete PosterFlow configuration. This includes:</p>
              <ul className="backup-info-list">
                <li>✓ All settings and configurations</li>
                <li>✓ Drive subscriptions and schedules</li>
                <li>✓ Google Drive authentication (rclone.conf)</li>
                <li>✓ Job history and poster metadata</li>
              </ul>
              <div className="backup-actions">
                <button className="btn-primary" onClick={onDownloadBackup} disabled={backupLoading}>
                  {backupLoading ? 'Creating Backup...' : 'Download Backup'}
                </button>
                <button className="btn-secondary" onClick={handleSaveToLocation} disabled={savingToLocation}>
                  {savingToLocation ? 'Saving...' : 'Save to Backup Location'}
                </button>
              </div>
            </div>

            <div className="backup-column">
              <h3>Backup Location</h3>
              <p>
                Backups saved manually or on a schedule go to this folder. To run backups
                automatically, add a schedule with the task <strong>Backup</strong> in the Scheduling tab.
              </p>
              <div className="form-group">
                <label htmlFor="backup-location">Backup Location</label>
                <input
                  id="backup-location"
                  type="text"
                  value={backupPath}
                  onChange={(e) => setBackupPath(e.target.value)}
                  placeholder={defaultPath}
                />
                <p className="field-hint">
                  {isDocker ? 'Container path' : 'Absolute path'} where backups are saved. Leave empty to use the default.
                </p>
              </div>
              <div className="form-group">
                <label htmlFor="backup-retention">Backups to Keep</label>
                <input
                  id="backup-retention"
                  type="number"
                  value={retention}
                  onChange={(e) => setRetention(e.target.value)}
                />
                <p className="field-hint">
                  Oldest backups in the folder are deleted beyond this count. 0 keeps everything.
                </p>
              </div>
              <button className="btn-primary" onClick={handleSaveBackupStorage} disabled={storageSaving}>
                {storageSaving ? 'Saving...' : 'Save Backup Settings'}
              </button>
            </div>
          </div>
        </div>

        <div className="backup-item">
          <h3>Restore from Backup</h3>
          <p>Upload a previously created backup file to restore your configuration.</p>
          <div className="backup-warning">
            ⚠️ <strong>Warning:</strong> This will replace your current configuration.
            Current settings will be backed up automatically before restore.
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
          <button
            className="btn-secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={restoreLoading}
          >
            {restoreLoading ? 'Restoring...' : 'Upload & Restore Backup'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default BackupRestoreSection
