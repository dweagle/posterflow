/** Which drive set the Drive Priority tab is configuring. */
export type PriorityScope = 'posters' | 'artwork'

const SCOPES: { value: PriorityScope; label: string }[] = [
  { value: 'posters', label: 'Poster Drives' },
  { value: 'artwork', label: 'Artwork Drives' },
]

type Props = {
  value: PriorityScope
  onChange: (scope: PriorityScope) => void
}

/**
 * Row under the Drive Priority toolbar. Poster and artwork drives are separate community
 * drive types, so each keeps its own priority list; this only switches which one shows.
 * Styled with the Unmatched asset-type row so both switch rows read as the same control.
 */
function PriorityScopeSelector({ value, onChange }: Props) {
  return (
    <div className="priority-scope-toggle pf-subtabs">
      {SCOPES.map((scope) => (
        <button
          key={scope.value}
          className={value === scope.value ? 'active' : ''}
          onClick={() => onChange(scope.value)}
        >
          {scope.label}
        </button>
      ))}
    </div>
  )
}

export default PriorityScopeSelector
