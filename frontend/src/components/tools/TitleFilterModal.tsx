import { useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import Modal from '../Modal'
import { getApiErrorMessage } from '../../api/client'
import { getArrTitles, type ArrTitle } from '../../api/tools'
import './RenameTagModal.css'

const SHOWN = 100 // rows listed at first, and how many more each Show more adds

interface Props {
  toolId: string
  instance: string
  value: string // the title filter as it stands
  noun: string // one item: 'movie' or 'series'
  nouns: string
  onClose: () => void
  onApply: (value: string) => void
}

// The title filter of an arr tool, tried against the instance's own titles, read from the arr each
// time. The list is what the text matches by the rule a run uses: the title contains it, capitals
// aside. Clicking a title makes it the text. Apply fills the field; nothing is saved until the profile is.
function TitleFilterModal({ toolId, instance, value, noun, nouns, onClose, onApply }: Props) {
  const [items, setItems] = useState<ArrTitle[] | null>(null)
  const [error, setError] = useState('')
  const [text, setText] = useState(value)
  const [limit, setLimit] = useState(SHOWN) // rows listed; a new text starts over

  useEffect(() => {
    let live = true
    getArrTitles(toolId, instance)
      .then((got) => { if (live) setItems(got.items) })
      .catch((err) => { if (live) setError(getApiErrorMessage(err, `Could not load the ${nouns} in ${instance}`)) })
    return () => { live = false }
  }, [toolId, instance, nouns])

  const needle = text.trim().toLowerCase()
  const matches = useMemo(() => (items ?? []).filter((item) => item.title.toLowerCase().includes(needle)), [items, needle])
  const shown = matches.slice(0, limit)
  const change = (next: string) => { setText(next); setLimit(SHOWN) }
  const count = items === null ? '' : needle
    ? `${matches.length} of ${items.length} ${items.length === 1 ? noun : nouns} match`
    : `No filter: all ${items.length} ${items.length === 1 ? noun : nouns}`

  return (
    <Modal onClose={onClose} className="rename-tag-modal">
      <Modal.Header title="Title filter">
        <span className="rename-tag-where">{instance}</span>
      </Modal.Header>
      <Modal.Body>
        <p className="field-hint">Type part of a title. A run only takes the {nouns} whose title contains it. Click a title to use it. Blank takes them all.</p>
        <label className="rename-tag-search">
          <Search size={15} aria-hidden="true" />
          <input
            type="search"
            value={text}
            autoFocus
            placeholder={`Search ${nouns} in ${instance}`}
            aria-label="Title filter text"
            onChange={(e) => change(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onApply(text.trim()) } }}
          />
        </label>
        {error ? <p className="text-danger" role="alert">{error}</p> : (
          <div className="rename-tag-results">
            {items === null ? (
              <p className="rename-tag-note">Loading the {nouns} in {instance}…</p>
            ) : shown.length === 0 ? (
              <p className="rename-tag-note">{needle ? `No title in ${instance} contains "${text.trim()}". A run would find nothing to do.` : `${instance} has no ${nouns} yet.`}</p>
            ) : (
              <ul className="rename-tag-list" aria-label="Matching titles">
                {shown.map((item) => (
                  <li key={item.id}>
                    <button type="button" className="rename-tag-row" title="Use this title" onClick={() => change(item.title)}>
                      <span className="rename-tag-title">{item.title}</span>
                      {item.year ? <> <span className="rename-tag-year">{item.year}</span></> : null}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {shown.length < matches.length && (
          <div className="row">
            <span className="field-hint">Showing {shown.length} of {matches.length}.</span>
            <button type="button" className="btn-secondary btn-sm" onClick={() => setLimit((n) => n + SHOWN)}>
              Show {Math.min(SHOWN, matches.length - shown.length)} more
            </button>
          </div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <span className="rename-tag-count">{count}</span>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" onClick={() => onApply(text.trim())}>Apply</button>
      </Modal.Footer>
    </Modal>
  )
}

export default TitleFilterModal
