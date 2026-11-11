
import Toolbar from '../Toolbar'
import PriorityScopeSelector, { type PriorityScope } from './PriorityScopeSelector'
import SaveActions from '../../components/SaveActions'

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
  onCancel: () => void
}

/**
 * The single Drive Priority toolbar + scope switch row. Both the poster and artwork tabs
 * render this, so the header is identical while only the panels below swap. Save state is
 * passed in because each tab tracks its own.
 */
function PriorityHeader({ scope, onScopeChange, saving, hasUnsavedChanges, onSave, onCancel }: PriorityHeaderProps) {
  return (
    <>
      <Toolbar title={TITLE} description={DESCRIPTION}>
        <SaveActions dirty={hasUnsavedChanges} saving={saving} disabled={!hasUnsavedChanges || saving} onSave={onSave} onCancel={onCancel} />
      </Toolbar>

      <PriorityScopeSelector value={scope} onChange={onScopeChange} />
    </>
  )
}

export default PriorityHeader
