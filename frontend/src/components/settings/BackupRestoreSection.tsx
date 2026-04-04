import { ChangeEvent, useRef } from 'react'

type BackupRestoreSectionProps = {
  backupLoading: boolean
  restoreLoading: boolean
  onDownloadBackup: () => Promise<void>
  onRestoreBackup: (file: File) => Promise<void>
}

function BackupRestoreSection({
  backupLoading,
  restoreLoading,
  onDownloadBackup,
  onRestoreBackup,
}: BackupRestoreSectionProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)

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
          <h3>Create Backup</h3>
          <p>Download a complete backup of your PosterFlow configuration. This includes:</p>
          <ul className="backup-info-list">
            <li>✓ All settings and configurations</li>
            <li>✓ Drive subscriptions and schedules</li>
            <li>✓ Google Drive authentication (rclone.conf)</li>
            <li>✓ Job history and poster metadata</li>
          </ul>
          <button className="btn-primary" onClick={onDownloadBackup} disabled={backupLoading}>
            {backupLoading ? 'Creating Backup...' : 'Download Backup'}
          </button>
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
