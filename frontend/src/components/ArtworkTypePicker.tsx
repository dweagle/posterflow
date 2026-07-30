import { ARTWORK_TYPES, ARTWORK_TYPE_LABELS, ARTWORK_TYPE_FOLDERS, type ArtworkType } from '../api/client'
import './ArtworkTypePicker.css'

interface ArtworkTypePickerProps {
  value: ArtworkType[]
  onChange: (types: ArtworkType[]) => void
  /** Indexed counts per type, shown next to each option when known. */
  counts?: Partial<Record<ArtworkType, number>>
}

// Checkbox group for choosing which artwork subfolders a drive syncs. Unchecked types are
// filtered out of the rclone transfer, so the drive never downloads them.
function ArtworkTypePicker({ value, onChange, counts }: ArtworkTypePickerProps) {
  const toggle = (type: ArtworkType) => {
    const selected = new Set(value)
    if (selected.has(type)) selected.delete(type)
    else selected.add(type)
    onChange(ARTWORK_TYPES.filter(t => selected.has(t)))
  }

  return (
    <div className="artwork-type-picker">
      {ARTWORK_TYPES.map(type => (
        <label key={type} className="artwork-type-option">
          <input type="checkbox" checked={value.includes(type)} onChange={() => toggle(type)} />
          <span className="artwork-type-name">{ARTWORK_TYPE_LABELS[type]}</span>
          <code>{ARTWORK_TYPE_FOLDERS[type]}</code>
          {counts?.[type] ? <span className="artwork-type-count">{counts[type]!.toLocaleString()} synced</span> : null}
        </label>
      ))}
      {value.length === 0 && (
        <p className="artwork-type-warning">Select at least one — a drive that syncs nothing should be unsubscribed instead.</p>
      )}
    </div>
  )
}

export default ArtworkTypePicker
