import { POSTER_STYLES } from '../community/posterStyles'

type Props = {
  value: string | null
  onChange: (value: string) => void
  disabled?: boolean
}

// Required CL2K/MM2K segmented control shown next to the publish actions (the
// Unmatched footer's "Add All to List" and the Create List picker). The chosen
// style is stamped on every item so requests made from them come pre-filled.
export default function PublishStyleToggle({ value, onChange, disabled }: Props) {
  return (
    <div className="publish-style-toggle">
      <span className="publish-style-toggle-label">
        Style <span className="request-required" title="Required">*</span>
      </span>
      <div className="publish-style-toggle-options" role="group" aria-label="Poster style (required)">
        {POSTER_STYLES.map((s) => (
          <button
            key={s.value}
            type="button"
            className={`publish-style-toggle-btn${value === s.value ? ' selected' : ''}`}
            onClick={() => onChange(s.value)}
            disabled={disabled}
            aria-pressed={value === s.value}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  )
}
