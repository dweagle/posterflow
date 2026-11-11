import Modal from '../Modal'

type RestartRequiredModalProps = {
  isOpen: boolean
  onClose: () => void
}

function RestartRequiredModal({ isOpen, onClose }: RestartRequiredModalProps) {
  if (!isOpen) {
    return null
  }

  return (
    <Modal>
      <Modal.Header title="⚠️ Container Restart Required" />
      <Modal.Body>
        <p className="modal-text">
          <strong>Backup restored successfully!</strong>
        </p>
        <p className="modal-text">
          Your database and configuration have been restored from the backup.
          The old files have been saved to <code>/config/safety_backups/</code> with a timestamp suffix.
        </p>
        <p className="modal-text">
          <strong>You must restart the Docker container</strong> for the changes to take effect.
        </p>
        <div className="backup-warning">
          💡 Run: <code>docker compose restart posterflow</code>
        </div>
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-primary btn-block" onClick={onClose}>
          I Understand
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default RestartRequiredModal
