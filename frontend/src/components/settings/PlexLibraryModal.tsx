import { PlexLibrary } from '../../api/client'

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
    <div className="modal-overlay">
      <div className="modal-content schedule-modal">
        <div className="modal-header">
          <h2>Manage Plex Libraries - {instanceName}</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {loadingLibraries ? (
            <div style={{ textAlign: 'center', padding: '2rem' }}>
              <p>Loading libraries...</p>
            </div>
          ) : (
            <>
              <p style={{ marginBottom: '1rem', color: '#aaa' }}>
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
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={onSave} disabled={loadingLibraries}>
            Save Library Configuration
          </button>
        </div>
      </div>
    </div>
  )
}

export default PlexLibraryModal
