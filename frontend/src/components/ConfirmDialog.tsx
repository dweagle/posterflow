import { AlertTriangle } from 'lucide-react'
import { type MouseEvent } from 'react'
import './ConfirmDialog.css'

interface ConfirmDialogProps {
  isOpen: boolean
  title: string
  message: string
  confirmText?: string
  cancelText?: string
  onConfirm: () => void
  onCancel: () => void
  variant?: 'danger' | 'warning' | 'info'
  children?: React.ReactNode
}

function ConfirmDialog({
  isOpen,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  onConfirm,
  onCancel,
  variant = 'warning',
  children
}: ConfirmDialogProps) {
  if (!isOpen) return null

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) {
      onCancel()
    }
  }

  return (
    <div className="modal-overlay confirm-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal confirmation-modal confirm-dialog">
        <div className={`modal-header confirm-header confirm-header-${variant}`}>
          <AlertTriangle size={24} />
          <h3>{title}</h3>
          <button className="modal-close" onClick={onCancel}>×</button>
        </div>
        <div className="modal-body confirm-body">
          <p>{message}</p>
          {children && <div className="confirm-extra">{children}</div>}
        </div>
        <div className="modal-footer confirm-footer">
          <button className="btn-secondary btn-cancel" onClick={onCancel}>
            {cancelText}
          </button>
          <button className={`btn-primary btn-confirm btn-confirm-${variant}`} onClick={onConfirm}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ConfirmDialog
