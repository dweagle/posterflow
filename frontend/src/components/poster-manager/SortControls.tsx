import { ArrowDown, ArrowUp } from 'lucide-react'
import { GroupFilter, SortDir, SortField, SortPrefs } from './itemSort'

type SortControlsProps = {
  prefs: SortPrefs
  onChange: (patch: Partial<SortPrefs>) => void
  showGroup?: boolean
  showSeasons?: boolean
  showDates?: boolean
}

const GROUP_OPTIONS: { value: GroupFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'movie', label: 'Movies' },
  { value: 'show', label: 'Shows' },
  { value: 'collection', label: 'Collections' },
]

// Natural direction applied whenever a field is picked (dates read newest-first);
// the arrow still toggles it afterward.
const DEFAULT_DIR: Record<SortField, SortDir> = {
  title: 'asc',
  year: 'asc',
  seasons: 'asc',
  added: 'desc',
  released: 'desc',
}

export default function SortControls({ prefs, onChange, showGroup = true, showSeasons = false, showDates = false }: SortControlsProps) {
  // A persisted field whose option isn't offered in this view falls back to title.
  const fieldVisible = (f: SortField) =>
    f === 'title' || f === 'year' || (f === 'seasons' && showSeasons) || ((f === 'added' || f === 'released') && showDates)

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
          value={fieldVisible(prefs.field) ? prefs.field : 'title'}
          onChange={(e) => {
            const field = e.target.value as SortField
            onChange({ field, dir: DEFAULT_DIR[field] })
          }}
        >
          <option value="title">Title</option>
          <option value="year">Year</option>
          {showDates && <option value="added">Date added</option>}
          {showDates && <option value="released">Release date</option>}
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
