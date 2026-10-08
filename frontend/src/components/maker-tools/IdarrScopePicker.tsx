import { HardDrive } from 'lucide-react'
import { useIdarrSyncTarget } from '../../hooks/useIdarrSyncTarget'

/** "IDarr drive" picker for the poster maker pages: the shared sidebar scope, poster drives only. */
export default function IdarrScopePicker() {
  const { posterOptions: options, selectedPosterValue: selectedValue, setSelectedValue } = useIdarrSyncTarget()
  if (options.length === 0) return null
  return (
    <div className="artwork-scope-control">
      <HardDrive size={15} />
      <span className="artwork-scope-label">IDarr drive:</span>
      <select
        value={selectedValue}
        onChange={(e) => setSelectedValue(e.target.value)}
        title="Where posters dropped on these cards are added — the same scope as the sidebar picker"
      >
        {options.map((o) => <option key={o.index} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  )
}
