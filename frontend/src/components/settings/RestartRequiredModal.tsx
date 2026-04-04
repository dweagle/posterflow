type RestartRequiredModalProps = {
  isOpen: boolean
  onClose: () => void
}

function RestartRequiredModal({ isOpen, onClose }: RestartRequiredModalProps) {
  if (!isOpen) {
    return null
  }

  return (
    <div className="modal-overlay">
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>⚠️ Container Restart Required</h2>
        </div>
        <div className="modal-body">
          <p style={{ marginBottom: '1rem', fontSize: '1.1em' }}>
            <strong>Backup restored successfully!</strong>
          </p>
          <p style={{ marginBottom: '1rem' }}>
            Your database and configuration have been restored from the backup.
            The old files have been saved to <code>/config/safety_backups/</code> with a timestamp suffix.
          </p>
          <p style={{ marginBottom: '1rem' }}>
            <strong>You must restart the Docker container</strong> for the changes to take effect.
          </p>
          <div className="backup-warning" style={{ marginTop: '1rem' }}>
            💡 Run: <code style={{ background: '#2a2a2a', padding: '0.25rem 0.5rem', borderRadius: '4px' }}>docker compose restart posterflow</code>
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-primary" onClick={onClose} style={{ width: '100%' }}>
            I Understand
          </button>
        </div>
      </div>
    </div>
  )
}

export default RestartRequiredModal
