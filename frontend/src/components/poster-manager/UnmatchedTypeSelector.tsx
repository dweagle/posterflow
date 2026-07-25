import { SlidersHorizontal } from 'lucide-react'
import { ArtworkType } from '../../api/client'

/** Which asset type the Unmatched tab is showing. */
export type UnmatchedScope = 'posters' | ArtworkType

export const UNMATCHED_SCOPES: UnmatchedScope[] = ['posters', 'logo', 'background', 'squareart']

const LABELS: Record<UnmatchedScope, string> = {
  posters: 'Posters',
  logo: 'Logos',
  background: 'Backdrops',
  squareart: 'Square Art',
}

type Props = {
  value: UnmatchedScope
  onChange: (scope: UnmatchedScope) => void
  /** True when the shared Detection Settings view is showing instead of the stats. */
  settingsActive: boolean
  onShowSettings: () => void
}

/**
 * Row under the Unmatched toolbar: one equal-width button per asset type, then the
 * shared Detection Settings view (shared across every type, so it gets its own button
 * rather than hanging below each type's stats).
 */
function UnmatchedTypeSelector({ value, onChange, settingsActive, onShowSettings }: Props) {
  return (
    <div className="artwork-type-selector pf-subtabs">
      {UNMATCHED_SCOPES.map((scope) => (
        <button
          key={scope}
          className={!settingsActive && value === scope ? 'active' : ''}
          onClick={() => onChange(scope)}
        >
          {LABELS[scope]}
        </button>
      ))}
      <button
        className={`type-selector-settings ${settingsActive ? 'active' : ''}`}
        onClick={onShowSettings}
      >
        <SlidersHorizontal size={14} />
        Detection Settings
      </button>
    </div>
  )
}

export default UnmatchedTypeSelector
