import { type MouseEvent } from 'react'

type UnsavedChangesModalProps = {
  isOpen: boolean
  onCancel: () => void
  onDiscard: () => void
}

function UnsavedChangesModal({ isOpen, onCancel, onDiscard }: UnsavedChangesModalProps) {
  if (!isOpen) {
    return null
  }

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onCancel()
    }
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal confirmation-modal">
        <div className="modal-header">
          <h2>Unsaved Changes</h2>
          <button className="modal-close" onClick={onCancel}>×</button>
        </div>
        <div className="modal-body">
          <p>You have unsaved changes on this tab. Are you sure you want to leave? Your changes will be lost if you don't save them.</p>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn-primary unsaved-discard-btn" onClick={onDiscard}>
            Discard Changes
          </button>
        </div>
      </div>
    </div>
  )
}

export default UnsavedChangesModal
