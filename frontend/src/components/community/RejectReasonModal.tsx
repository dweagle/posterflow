import { useState } from 'react'
import Modal from '../../components/Modal'

const REJECT_REASON_MAX = 200

type Props = {
  title: string
  what: 'request' | 'item'
  onCancel: () => void
  onConfirm: (reason: string) => void
}

// Asks the maker why before a request or list item is rejected. Reject stays off until a reason is typed.
export default function RejectReasonModal({ title, what, onCancel, onConfirm }: Props) {
  const [reason, setReason] = useState('')
  // One line, as the server saves it.
  const trimmed = reason.replace(/\s+/g, ' ').trim()

  return (
    <Modal onClose={onCancel} size="sm">
      <Modal.Header title={`Reject ${what}`} />
      <Modal.Body>
        <p className="close-confirm-hint">
          Say why <strong>{title}</strong> is being rejected.{' '}
          {what === 'request'
            ? 'The requester sees the reason on the request and in its Discord thread.'
            : 'Makers see the reason on the rejected item.'}
        </p>
        <textarea
          className="request-notes-textarea"
          aria-label="Reason for rejecting"
          placeholder="Reason"
          rows={3}
          maxLength={REJECT_REASON_MAX}
          autoFocus
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <div className="creq-field-hint text-dim">{reason.length}/{REJECT_REASON_MAX}</div>
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onCancel}>Cancel</button>
        <button className="btn-danger" disabled={!trimmed} onClick={() => onConfirm(trimmed)}>Reject</button>
      </Modal.Footer>
    </Modal>
  )
}
