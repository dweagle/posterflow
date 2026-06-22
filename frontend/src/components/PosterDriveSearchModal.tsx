import { useEffect } from 'react'
import { X } from 'lucide-react'
import DriveSearchPanel from './DriveSearchPanel'
import './PosterDriveSearchModal.css'

type PosterDriveSearchModalProps = {
  /** Pre-fill the search box (usually the title being worked on). */
  initialQuery?: string
  onClose: () => void
}

/**
 * In-place drive poster search so makers can check the synced drives without
 * leaving the Requests card or Maker Tools search. Wraps the shared
 * DriveSearchPanel in a modal.
 */
export default function PosterDriveSearchModal({ initialQuery = '', onClose }: PosterDriveSearchModalProps) {
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  return (
    <div className="drive-search-modal-overlay" onMouseDown={onClose}>
      <div className="drive-search-modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="drive-search-modal-header">
          <h2>Search Drive Posters</h2>
          <button type="button" className="drive-search-modal-close" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <div className="drive-search-modal-body">
          <DriveSearchPanel initialQuery={initialQuery} autoFocus />
        </div>
      </div>
    </div>
  )
}
