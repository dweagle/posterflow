import { PlexLibrary } from '../../api/client'
import Modal from '../../components/Modal'

type PlexLibraryModalProps = {
  isOpen: boolean
  instanceName?: string
  loadingLibraries: boolean
  libraries: PlexLibrary[]
  onClose: () => void
  onToggleLibrary: (libraryKey: string) => void
  onSave: () => void
}

function PlexLibraryModal({
  isOpen,
  instanceName,
  loadingLibraries,
  libraries,
  onClose,
  onToggleLibrary,
  onSave,
}: PlexLibraryModalProps) {
  if (!isOpen) {
    return null
  }

  return (
    <Modal onClose={onClose} closeOnOverlay={false}>
      <Modal.Header title={<>Manage Plex Libraries - {instanceName}</>} />
      <Modal.Body>
        {loadingLibraries ? (
          <div className="modal-empty">
            <p>Loading libraries...</p>
          </div>
        ) : (
     <>
            <p className="modal-text text-muted">
              Select which libraries to include in poster operations and unmatched asset detection.
            </p>
            <div className="library-list">
              {libraries.map((library) => (
                <div key={library.key} className="library-item">
                  <label className="library-label">
                    <input
                      type="checkbox"
                      checked={library.enabled}
                      onChange={() => onToggleLibrary(library.key)}
                    />
                    <div className="library-info">
                      <span className="library-name">{library.title}</span>
                      <span className="library-type">{library.type}</span>
                    </div>
                  </label>
                </div>
              ))}
            </div>
          </>
        )}
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="btn-primary" onClick={onSave} disabled={loadingLibraries}>
          Save Library Configuration
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default PlexLibraryModal
