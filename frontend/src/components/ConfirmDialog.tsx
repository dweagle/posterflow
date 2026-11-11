import { AlertTriangle } from 'lucide-react'
import Modal from './Modal'
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

const CONFIRM_CLASS = { danger: 'btn-danger', warning: 'btn-primary', info: 'btn-primary' }

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

  return (
    <Modal onClose={onCancel} size="sm" className="confirm-dialog">
      <Modal.Header className={`confirm-header confirm-header-${variant}`} title={<><AlertTriangle size={22} />{title}</>} />
      <Modal.Body className="confirm-body">
        <p>{message}</p>
        {children && <div className="confirm-extra">{children}</div>}
      </Modal.Body>
      <Modal.Footer className="confirm-footer">
        <button className="btn-secondary" onClick={onCancel}>
          {cancelText}
        </button>
        <button className={CONFIRM_CLASS[variant]} onClick={onConfirm}>
          {confirmText}
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default ConfirmDialog
