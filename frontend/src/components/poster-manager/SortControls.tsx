import { ArrowDown, ArrowUp } from 'lucide-react'
import { GroupFilter, SortField, SortPrefs } from './itemSort'

type SortControlsProps = {
  prefs: SortPrefs
  onChange: (patch: Partial<SortPrefs>) => void
  showGroup?: boolean
  showSeasons?: boolean
}

const GROUP_OPTIONS: { value: GroupFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'movie', label: 'Movies' },
  { value: 'show', label: 'Shows' },
  { value: 'collection', label: 'Collections' },
]

export default function SortControls({ prefs, onChange, showGroup = true, showSeasons = false }: SortControlsProps) {
  return (
    <div className="sort-controls">
      {showGroup && (
        <div className="sort-group-pills" role="group" aria-label="Filter by type">
          {GROUP_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              className={`sort-pill${prefs.group === opt.value ? ' active' : ''}`}
              onClick={() => onChange({ group: opt.value })}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}

      <div className="sort-field-wrap">
        <label className="sort-label">Sort</label>
        <select
          className="sort-select"
          value={!showSeasons && prefs.field === 'seasons' ? 'title' : prefs.field}
          onChange={(e) => onChange({ field: e.target.value as SortField })}
        >
          <option value="title">Title</option>
          <option value="year">Year</option>
          {showSeasons && <option value="seasons">Missing seasons</option>}
        </select>
        <button
          type="button"
          className="sort-dir-btn"
          onClick={() => onChange({ dir: prefs.dir === 'asc' ? 'desc' : 'asc' })}
          title={prefs.dir === 'asc' ? 'Ascending' : 'Descending'}
        >
          {prefs.dir === 'asc' ? <ArrowUp size={15} /> : <ArrowDown size={15} />}
        </button>
      </div>
    </div>
  )
}
