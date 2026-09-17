import { HardDrive } from 'lucide-react'
import { useIdarrSyncTarget } from '../../hooks/useIdarrSyncTarget'

/** "IDarr drive" picker for the poster maker pages: the same shared scope as the sidebar picker. */
export default function IdarrScopePicker() {
  const { options, selectedValue, setSelectedValue } = useIdarrSyncTarget()
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
