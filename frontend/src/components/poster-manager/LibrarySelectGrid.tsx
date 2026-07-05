import { PlexLibraryConfig } from '../../api/client'

type LibrarySelectGridProps = {
  libraryConfigs: PlexLibraryConfig[]
  // Full "instance:key" keys of the currently-selected libraries.
  selected: Set<string>
  // Called with the instance name, library key, and the combined "instance:key".
  onToggle: (instanceName: string, libraryKey: string, fullKey: string) => void
}

// Compact grid of enabled Plex libraries grouped by instance, each a checkbox.
// Shared by the Renamer, Unmatched, and Border-rules tabs so the markup stays in one place.
function LibrarySelectGrid({ libraryConfigs, selected, onToggle }: LibrarySelectGridProps) {
  return (
    <div className="library-compact-grid">
      {libraryConfigs.map((config) => {
        const enabled = config.libraries.filter((lib) => lib.enabled)
        return (
          <div key={config.instance_name} className="library-instance-group">
            <div className="instance-header">{config.instance_name}</div>
            {enabled.map((library) => {
              const fullKey = `${config.instance_name}:${library.key}`
              return (
                <label key={library.key} className="library-checkbox">
                  <input
                    type="checkbox"
                    checked={selected.has(fullKey)}
                    onChange={() => onToggle(config.instance_name, library.key, fullKey)}
                  />
                  <span className="library-checkbox-label">
                    {library.title}
                    <span className="library-badge">{library.type}</span>
                  </span>
                </label>
              )
            })}
            {enabled.length === 0 && (
              <p style={{ color: '#888', fontSize: '0.85rem', fontStyle: 'italic', margin: '0.25rem 0' }}>
                No libraries enabled
              </p>
            )}
          </div>
        )
      })}
    </div>
  )
}

export default LibrarySelectGrid
