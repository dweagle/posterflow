import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

// `note` is shown beside the name, at the right and fainter: where the entry is from (a server, an instance).
export type SearchOption = { value: string; label: string; note?: string }

// An entry read out in one piece: "Pixar (Plex)".
export const optionText = (option: { label: string; note?: string }): string => (option.note ? `${option.label} (${option.note})` : option.label)

type Props = {
  // What the box shows while nobody types in it: the picked entry's label, or the typed value.
  text: string
  options: SearchOption[]
  // The user typed: the value is now what they typed.
  onType: (text: string) => void
  // The user picked an entry.
  onPick: (option: SearchOption) => void
  id?: string
  ariaLabel?: string
  placeholder?: string
  title?: string
  // The options already are the matches for what was typed (looked up as the user types), so
  // they are listed as they are instead of being filtered here.
  searched?: boolean
  // The box adds values to a list kept beside it (chips): what is typed becomes a value on
  // Enter or when the box is left, and the box empties again. Without it, typing is the value.
  onCommit?: (text: string) => void
}

const MAX_LIST = 320 // px: the list is never taller than this; it scrolls inside instead
const MIN_LIST = 96
const ROW = 26 // px, one option: an estimate used only to decide where the list opens
const EDGE = 12 // px kept clear of the edge of the scrolling area
const NEAR_END = ROW * 6 // px from the list's end at which its next part is drawn
const PAGE = 100 // entries drawn at first; a longer list draws this many more each time its end is reached

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

// A box to type in with the entries that fit listed under it: a click on the box lists them
// all, typing narrows the list, and anything may still be typed that is not on it. For lists
// too long to scroll through (studios, languages, collections). A long list is drawn a part at
// a time, the next part as its end is scrolled to. The list is drawn in the page like
// ListPicker's: sized to the room there is, above the box when there is more room there.
function SearchPicker({ text, options, onType, onPick, id, ariaLabel, placeholder, title, searched = false, onCommit }: Props) {
  const [draft, setDraft] = useState<string | null>(null) // what is being typed; null = nothing yet
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [limit, setLimit] = useState(PAGE) // how many entries are drawn
  const [place, setPlace] = useState({ above: false, maxHeight: MAX_LIST })
  const root = useRef<HTMLDivElement | null>(null)
  const list = useRef<HTMLUListElement | null>(null)
  const listId = useId()

  const typed = (draft ?? '').trim().toLowerCase()
  // What is typed may be part of the name or of the note: "plex" lists that server's entries.
  const fitting = searched || !typed ? options : options.filter((o) => optionText(o).toLowerCase().includes(typed))
  const shown = fitting.slice(0, limit)
  const more = fitting.length > shown.length

  const show = () => {
    if (!root.current) return
    const box = root.current.getBoundingClientRect()
    const bounds = verticalBounds(root.current)
    const below = bounds.bottom - box.bottom - EDGE
    const above = box.top - bounds.top - EDGE
    const wanted = Math.min(MAX_LIST, Math.max(1, Math.min(options.length, PAGE)) * ROW + 8)
    const goAbove = below < wanted && above > below
    setPlace({ above: goAbove, maxHeight: Math.max(MIN_LIST, Math.min(MAX_LIST, goAbove ? above : below)) })
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    setDraft(null)
    setActive(-1)
    setLimit(PAGE)
  }
  const pick = (option: SearchOption | undefined) => {
    if (!option) return
    onPick(option)
    close()
  }
  // What was typed becomes a value of its own (see onCommit).
  const commit = () => {
    const typedText = (draft ?? '').trim()
    if (onCommit && typedText) onCommit(typedText)
  }

  // Keep the highlighted entry in view as it moves.
  useEffect(() => {
    if (!open || active < 0) return
    const item = list.current?.children[active] as HTMLElement | undefined
    item?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active])

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      if (!open) { show(); return }
      const down = e.key === 'ArrowDown'
      const last = fitting.length - 1
      // Past the last entry drawn, the next part is drawn and the highlight moves on into it.
      if (down && more && active + 1 >= shown.length) setLimit((n) => n + PAGE)
      setActive((i) => (down ? Math.min(last, i + 1) : Math.max(0, i - 1)))
    } else if (e.key === 'Enter') {
      if (open && active >= 0) {
        e.preventDefault()
        pick(shown[active])
      } else if (onCommit) {
        e.preventDefault()
        commit()
        close()
      }
    } else if (e.key === 'Escape') {
      if (open) {
        e.preventDefault()
        setOpen(false)
      }
    }
  }

  return (
    <div
      ref={root}
      className={`search-picker ${open ? 'open' : ''}`}
      onBlur={(e) => {
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
        commit()
        close()
      }}
    >
      <input
        id={id}
        type="text"
        role="combobox"
        aria-label={ariaLabel}
        aria-expanded={open && shown.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        title={title}
        value={draft ?? text}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onFocus={show}
        onClick={() => { if (!open) show() }}
        onChange={(e) => {
          setDraft(e.target.value)
          setActive(-1)
          setLimit(PAGE)
          onType(e.target.value)
          if (!open) show()
        }}
        onKeyDown={onKeyDown}
      />
      {open && shown.length > 0 && (
        <ul
          ref={list}
          id={listId}
          role="listbox"
          aria-label={ariaLabel}
          className={`list-picker-list search-picker-list ${place.above ? 'above' : ''}`}
          style={{ maxHeight: place.maxHeight }}
          onScroll={(e) => {
            const el = e.currentTarget
            if (more && el.scrollTop + el.clientHeight >= el.scrollHeight - NEAR_END) setLimit((n) => n + PAGE)
          }}
        >
          {shown.map((option, index) => (
            <li
              key={option.value}
              id={`${listId}-${index}`}
              role="option"
              aria-label={optionText(option)}
              aria-selected={index === active}
              className={index === active ? 'active' : ''}
              onMouseDown={(e) => e.preventDefault()} // the box keeps the focus (and the keyboard)
              onMouseEnter={() => setActive(index)}
              onClick={() => pick(option)}
            >
              <span className="search-picker-name">{option.label}</span>
              {option.note && <span className="search-picker-note">{option.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default SearchPicker
