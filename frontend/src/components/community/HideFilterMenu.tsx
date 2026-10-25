import { useEffect, useRef, useState } from 'react'
import { ChevronDown, EyeOff } from 'lucide-react'
import { HIDE_FILTER_OPTIONS, type HideFilterKey, type HideFilters } from './hideFilters'

type Props = {
  value: HideFilters
  onChange: (next: HideFilters) => void
  /** Options left out of the menu, e.g. MM2K when the whole app hides that style. */
  omit?: HideFilterKey[]
}

/** "Hide" dropdown in the community toolbars: a checklist of what to leave out of the list. */
export default function HideFilterMenu({ value, onChange, omit = [] }: Props) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const options = HIDE_FILTER_OPTIONS.filter((o) => !omit.includes(o.key))
  const activeCount = options.filter((o) => value[o.key]).length

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  if (options.length === 0) return null

  return (
    <div className="community-hide-menu" ref={rootRef}>
      <button
        type="button"
        className={`community-tab-btn community-hide-menu-btn${activeCount > 0 ? ' active' : ''}`}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title="Leave kinds of titles out of the list. Stays set when you navigate away and back"
      >
        <EyeOff size={14} />
        Hide
        {activeCount > 0 && <span className="community-hide-menu-count">{activeCount}</span>}
        <ChevronDown size={14} className="community-hide-menu-chevron" />
      </button>
      {open && (
        <div className="community-hide-menu-pop" role="group" aria-label="Hide from the list">
          {options.map((o) => (
            <label key={o.key} className="community-hide-menu-item" title={o.hint}>
              <input
                type="checkbox"
                checked={value[o.key]}
                onChange={(e) => onChange({ ...value, [o.key]: e.target.checked })}
              />
              <span>{o.label}</span>
            </label>
          ))}
          <div className="community-hide-menu-note">Titles without a TMDB id stay visible.</div>
        </div>
      )}
    </div>
  )
}
