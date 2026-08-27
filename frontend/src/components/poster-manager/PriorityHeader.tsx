import { Save } from 'lucide-react'
import Toolbar from '../Toolbar'
import PriorityScopeSelector, { type PriorityScope } from './PriorityScopeSelector'

// One toolbar for all scopes; only the panels below change. Exported for the Drive Usage view.
export const TITLE = 'Configure Drive Priority'
export const DESCRIPTION =
  'Select which poster styles to use, and set the priority order of your poster and artwork ' +
  '(logo / backdrop / square-art) drives. Higher drives override lower ones when the same ' +
  'asset comes from multiple makers.'

type PriorityHeaderProps = {
  scope: PriorityScope
  onScopeChange: (scope: PriorityScope) => void
  saving: boolean
  hasUnsavedChanges: boolean
  onSave: () => void
}

/**
 * The single Drive Priority toolbar + scope switch row. Both the poster and artwork tabs
 * render this, so the header is identical while only the panels below swap. Save state is
 * passed in because each tab tracks its own.
 */
function PriorityHeader({ scope, onScopeChange, saving, hasUnsavedChanges, onSave }: PriorityHeaderProps) {
  return (
    <>
      <Toolbar title={TITLE} description={DESCRIPTION}>
        <button
          className={`btn-toolbar ${hasUnsavedChanges ? 'btn-unsaved' : ''}`}
          onClick={onSave}
          disabled={!hasUnsavedChanges || saving}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </Toolbar>

      <PriorityScopeSelector value={scope} onChange={onScopeChange} />
    </>
  )
}

export default PriorityHeader
