import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { ChevronDown } from 'lucide-react'

type PickerOption = { value: string; label: string }

type Props = {
  value: string
  options: PickerOption[]
  onChange: (value: string) => void
  id?: string
  ariaLabel?: string
  title?: string
  disabled?: boolean
}

const MAX_LIST = 320 // px: the list is never taller than this; it scrolls inside instead
const MIN_LIST = 96
const ROW = 26 // px, one option: an estimate used only to decide where the list opens
const EDGE = 12 // px kept clear of the edge of the scrolling area

// Where the list may show without being cut: the viewport, narrowed by every ancestor that
// clips or scrolls vertically (the page's content area scrolls).
const verticalBounds = (el: HTMLElement) => {
  let top = 0
  let bottom = window.innerHeight
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    if (getComputedStyle(a).overflowY !== 'visible') {
      const r = a.getBoundingClientRect()
      top = Math.max(top, r.top)
      bottom = Math.min(bottom, r.bottom)
    }
  }
  return { top, bottom }
}

// A dropdown for long lists. A browser's own menu for a long <select> needs more room than
// a small screen has and may simply shut. This one draws its list in the page: it is sized
// to the room there is, opens upward when there is more room above, and scrolls inside.
function ListPicker({ value, options, onChange, id, ariaLabel, title, disabled = false }: Props) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1) // the highlighted option
  const [place, setPlace] = useState({ above: false, maxHeight: MAX_LIST })
  const root = useRef<HTMLDivElement | null>(null)
  const list = useRef<HTMLUListElement | null>(null)
  const typed = useRef({ text: '', at: 0 })
  const listId = useId()
  const selected = options.findIndex((o) => o.value === value)

  const show = () => {
    if (disabled || options.length === 0 || !root.current) return
    const box = root.current.getBoundingClientRect()
    const bounds = verticalBounds(root.current)
    const below = bounds.bottom - box.bottom - EDGE
    const above = box.top - bounds.top - EDGE
    const wanted = Math.min(MAX_LIST, options.length * ROW + 8)
    const goAbove = below < wanted && above > below
    setPlace({ above: goAbove, maxHeight: Math.max(MIN_LIST, Math.min(MAX_LIST, goAbove ? above : below)) })
    setActive(selected >= 0 ? selected : 0)
    setOpen(true)
  }

  const choose = (index: number) => {
    const option = options[index]
    setOpen(false)
    if (option && option.value !== value) onChange(option.value)
  }

  // Keep the highlighted option in view as it moves.
  useEffect(() => {
    if (!open || active < 0) return
    const item = list.current?.children[active] as HTMLElement | undefined
    item?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active])

  // A press anywhere else closes the list.
  useEffect(() => {
    if (!open) return
    const outside = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', outside)
    return () => document.removeEventListener('mousedown', outside)
  }, [open])

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (disabled) return
    if (!open) {
      if (['ArrowDown', 'ArrowUp', 'Enter', ' '].includes(e.key)) {
        e.preventDefault()
        show()
      }
      return
    }
    const last = options.length - 1
    if (e.key === 'Escape' || e.key === 'Tab') {
      if (e.key === 'Escape') e.preventDefault()
      setOpen(false)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((i) => Math.min(last, i + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(0, i - 1))
    } else if (e.key === 'Home') {
      e.preventDefault()
      setActive(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      setActive(last)
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      choose(active)
    } else if (e.key.length === 1) {
      // Typing jumps to the next option that starts with what was typed.
      const now = Date.now()
      typed.current = { text: (now - typed.current.at < 700 ? typed.current.text : '') + e.key.toLowerCase(), at: now }
      const from = typed.current.text.length === 1 ? active + 1 : active
      const order = options.map((_, i) => (from + i) % options.length)
      const hit = order.find((i) => options[i].label.toLowerCase().startsWith(typed.current.text))
      if (hit !== undefined) setActive(hit)
    }
  }

  return (
    <div
      ref={root}
      className={`list-picker ${open ? 'open' : ''}`}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <button
        type="button"
        id={id}
        className="list-picker-button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        aria-label={ariaLabel}
        title={title}
        disabled={disabled}
        data-value={value}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={onKeyDown}
      >
        <span className="list-picker-label">{selected >= 0 ? options[selected].label : ''}</span>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <ul ref={list} id={listId} role="listbox" aria-label={ariaLabel} className={`list-picker-list ${place.above ? 'above' : ''}`} style={{ maxHeight: place.maxHeight }}>
          {options.map((option, index) => (
            <li
              key={option.value}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={option.value === value}
              className={`${index === active ? 'active' : ''} ${option.value === value ? 'selected' : ''}`}
              onMouseDown={(e) => e.preventDefault()} // the button keeps the focus (and the keyboard)
              onMouseEnter={() => setActive(index)}
              onClick={() => choose(index)}
            >
              {option.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default ListPicker
