import Modal from '../../components/Modal'

type UnsavedChangesModalProps = {
  isOpen: boolean
  onCancel: () => void
  onDiscard: () => void
}

function UnsavedChangesModal({ isOpen, onCancel, onDiscard }: UnsavedChangesModalProps) {
  if (!isOpen) {
    return null
  }

  return (
    <Modal onClose={onCancel} size="sm">
      <Modal.Header title="Unsaved Changes" />
      <Modal.Body>
        <p>You have unsaved changes on this tab. Are you sure you want to leave? Your changes will be lost if you don't save them.</p>
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn-danger" onClick={onDiscard}>
          Discard Changes
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default UnsavedChangesModal
