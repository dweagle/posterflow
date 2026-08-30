import { useState, type MouseEvent } from 'react'
import { ARTWORK_TYPES, type ArtworkType } from '../api/client'
import ArtworkTypePicker from './ArtworkTypePicker'
import './DriveEditModal.css'

interface ArtworkSubscribeModalProps {
  driveName: string
  /** Only asked when the user hasn't set a "don't ask again" preference. */
  askPriority: boolean
  onCancel: () => void
  onConfirm: (types: ArtworkType[], addToPriority: boolean, dontAskPriorityAgain: boolean) => void
}

// Subscribe dialog for artwork drives: pick which artwork types to pull before the first
// (potentially large) download, and answer the Drive Priority question in the same step.
function ArtworkSubscribeModal({ driveName, askPriority, onCancel, onConfirm }: ArtworkSubscribeModalProps) {
  const [types, setTypes] = useState<ArtworkType[]>([...ARTWORK_TYPES])
  const [addToPriority, setAddToPriority] = useState(true)
  const [dontAsk, setDontAsk] = useState(false)

  const handleOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onCancel()
  }

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>Subscribe to {driveName}</h2>
          <button className="modal-close" onClick={onCancel}>×</button>
        </div>

        <div className="modal-body">
          <div className="form-group">
            <label className="field-label">Artwork to sync</label>
            <ArtworkTypePicker value={types} onChange={setTypes} />
            <small>Only the folders you pick are downloaded. You can change this later in the drive's settings.</small>
          </div>

          {askPriority && (
            <div className="form-group checkbox-group">
              <label className="checkbox-label">
                <input type="checkbox" checked={addToPriority} onChange={(e) => setAddToPriority(e.target.checked)} />
                Add to Drive Priority
              </label>
              <small>
                Adds this drive to your matching order in Asset Manager → Drive Priority (Artwork). Without it the
                drive syncs but isn't used for matching.
              </small>
              <label className="checkbox-label">
                <input type="checkbox" checked={dontAsk} onChange={(e) => setDontAsk(e.target.checked)} />
                Don't ask again for artwork drives
              </label>
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel}>Cancel</button>
          <button
            className="btn-primary"
            disabled={types.length === 0}
            onClick={() => onConfirm(types, addToPriority, dontAsk)}
          >
            Subscribe
          </button>
        </div>
      </div>
    </div>
  )
}

export default ArtworkSubscribeModal
