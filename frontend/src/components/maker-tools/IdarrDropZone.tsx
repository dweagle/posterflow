import { Check, Loader2, Upload } from 'lucide-react'
import type { IdarrDropState } from '../../hooks/useIdarrDrop'

type Props = {
  active: boolean
  state: IdarrDropState
  targetLabel: string
  /** What the box asks for: "poster(s)" on maker cards, "artwork" on artwork cards. */
  what?: string
  /** Extra sentence for the hover tooltip (e.g. how artwork files are sorted). */
  hint?: string
}

/** Visual drop target inside a card; the whole card accepts the drop (see useIdarrDrop). */
export default function IdarrDropZone({ active, state, targetLabel, what = 'poster(s)', hint }: Props) {
  const title = `Drop ${what} here to add to IDarr (${targetLabel}).${hint ? ` ${hint}` : ''}`
  return (
    <div
      className={`idarr-drop-zone${active ? ' drop-active' : ''}${state !== 'idle' ? ` idarr-drop-zone--${state}` : ''}`}
      title={title}
    >
      {state === 'adding' ? <Loader2 size={22} className="spin-icon" /> : state === 'done' ? <Check size={22} /> : <Upload size={22} />}
      <span>{state === 'adding' ? 'Adding…' : state === 'done' ? 'Added!' : `Drop ${what} to add to IDarr`}</span>
    </div>
  )
}
