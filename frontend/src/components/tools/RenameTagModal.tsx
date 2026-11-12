import { useEffect, useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import Modal from '../Modal'
import { useToast } from '../Toast'
import { getApiErrorMessage } from '../../api/client'
import { getRenameTagged, saveRenameTagged, type RenameTagItem } from '../../api/tools'
import './RenameTagModal.css'

const SHOWN = 100 // rows listed at first, tagged or matching a search, and how many more each Show more adds

// A renamer's two tags: the cycle tag marks what a pass has checked, the ignore tag what it skips.
// An upgrader's two: the search tag marks what a cycle has searched, its ignore tag what it never searches.
export type RenameTagKind = 'cycle' | 'ignore' | 'searched' | 'search-ignore'

const COPY: Record<RenameTagKind, {
  title: (nouns: string) => string
  hint: (nouns: string) => string
  state: string
  added: (count: string) => string
  removed: (n: number) => string
  empty: (nouns: string) => string
}> = {
  ignore: {
    title: (nouns) => `Ignored ${nouns}`,
    hint: (nouns) => `The renamer leaves these ${nouns} alone. Search to add one. Untick one to have it renamed again.`,
    state: 'ignored',
    added: (count) => `${count} now ignored`,
    removed: (n) => `${n} no longer ignored`,
    empty: (nouns) => `No ${nouns} carry the tag yet. Search to add one.`,
  },
  cycle: {
    title: (nouns) => `Checked ${nouns}`,
    hint: (nouns) => `The renamer checked these ${nouns} this pass and skips them until the pass ends. Untick one to have it checked on the next run. Search to mark one as checked.`,
    state: 'checked',
    added: (count) => `${count} marked checked`,
    removed: (n) => `${n} to check again`,
    empty: (nouns) => `No ${nouns} carry the tag yet. Search to mark one as checked.`,
  },
  'search-ignore': {
    title: (nouns) => `Ignored ${nouns}`,
    hint: (nouns) => `The upgrader never searches these ${nouns}. Search to add one. Untick one to have it searched again.`,
    state: 'ignored',
    added: (count) => `${count} now ignored`,
    removed: (n) => `${n} no longer ignored`,
    empty: (nouns) => `No ${nouns} carry the tag yet. Search to add one.`,
  },
  searched: {
    title: (nouns) => `Searched ${nouns}`,
    hint: (nouns) => `The upgrader searched these ${nouns} this cycle and skips them until the cycle ends. Untick one to have it searched on a next run. Search to mark one as searched.`,
    state: 'searched',
    added: (count) => `${count} marked searched`,
    removed: (n) => `${n} to search again`,
    empty: (nouns) => `No ${nouns} carry the tag yet. Search to mark one as searched.`,
  },
}

interface Props {
  kind: RenameTagKind
  toolId: string
  instance: string
  tag: string
  noun: string // one item: 'movie' or 'series'
  nouns: string
  onClose: () => void
}

// The movies or series that carry one of an arr tool's tags in the arr, changed by hand. Without a
// search the list shows the tagged ones; a search finds any item in the library. Either list
// shows 100 rows, and Show more adds the next 100. Ticking a box marks the change, and Save sends
// them all to the arr at once. The dialog keeps one size: the rows scroll inside their box.
function RenameTagModal({ kind, toolId, instance, tag, noun, nouns, onClose }: Props) {
  const { showToast } = useToast()
  const copy = COPY[kind]
  const [items, setItems] = useState<RenameTagItem[] | null>(null)
  const [error, setError] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(SHOWN) // rows listed; a new search starts over
  const [changed, setChanged] = useState<Record<number, boolean>>({}) // item id → tagged once saved, for each box that differs from the arr
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let live = true
    getRenameTagged(toolId, instance, tag)
      .then((list) => { if (live) setItems(list.items) })
      .catch((err) => { if (live) setError(getApiErrorMessage(err, `Could not load the ${nouns} in ${instance}`)) })
    return () => { live = false }
  }, [toolId, instance, tag, nouns])

  const tagged = (item: RenameTagItem) => changed[item.id] ?? item.tagged
  const toggle = (item: RenameTagItem, on: boolean) => setChanged((prev) => {
    const next = { ...prev }
    if (on === item.tagged) delete next[item.id]
    else next[item.id] = on
    return next
  })

  const needle = query.trim().toLowerCase()
  // An item unticked here stays in the list until Save, so it can be ticked again.
  const matches = useMemo(() => (items ?? []).filter((item) => (needle
    ? `${item.title} ${item.year ?? ''}`.toLowerCase().includes(needle)
    : item.tagged || item.id in changed)), [items, needle, changed])
  const shown = matches.slice(0, limit)
  const count = items ? items.filter(tagged).length : 0
  const pending = Object.keys(changed).length

  const save = async () => {
    const add = Object.entries(changed).filter(([, on]) => on).map(([id]) => Number(id))
    const remove = Object.entries(changed).filter(([, on]) => !on).map(([id]) => Number(id))
    setSaving(true)
    try {
      await saveRenameTagged(toolId, { instance, tag, add, remove })
      const parts = [
        add.length ? copy.added(`${add.length} ${add.length === 1 ? noun : nouns}`) : '',
        remove.length ? copy.removed(remove.length) : '',
      ].filter(Boolean)
      showToast(`${instance}: ${parts.join(', ')}`, 'success')
      onClose()
    } catch (err) {
      showToast(getApiErrorMessage(err, `Could not save the tags in ${instance}`), 'error')
      setSaving(false)
    }
  }

  return (
    <Modal onClose={onClose} busy={saving} className="rename-tag-modal">
      <Modal.Header title={copy.title(nouns)}>
        <span className="rename-tag-where">{instance} · tag {tag}</span>
      </Modal.Header>
      <Modal.Body>
        <p className="field-hint">{copy.hint(nouns)}</p>
        <label className="rename-tag-search">
          <Search size={15} aria-hidden="true" />
          <input type="search" value={query} autoFocus placeholder={`Search ${nouns} in ${instance}`} aria-label={`Search ${nouns}`} onChange={(e) => { setQuery(e.target.value); setLimit(SHOWN) }} />
        </label>
        {error ? <p className="text-danger" role="alert">{error}</p> : (
          <div className="rename-tag-results">
            {items === null ? (
              <p className="rename-tag-note">Loading the {nouns} in {instance}…</p>
            ) : shown.length === 0 ? (
              <p className="rename-tag-note">{needle ? `Nothing in ${instance} matches "${query.trim()}".` : copy.empty(nouns)}</p>
            ) : (
              <ul className="rename-tag-list" aria-label={needle ? 'Search results' : copy.title(nouns)}>
                {shown.map((item) => (
                  <li key={item.id}>
                    <label className="checkbox-label rename-tag-row">
                      <input type="checkbox" checked={tagged(item)} disabled={saving} onChange={(e) => toggle(item, e.target.checked)} />
                      <span className="rename-tag-title">{item.title}</span>
                      {item.year ? <> <span className="rename-tag-year">{item.year}</span></> : null}
                    </label>
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
        <span className="rename-tag-count">{count} {copy.state}{pending ? ` · ${pending} unsaved` : ''}</span>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>Cancel</button>
        <button type="button" className="btn-primary" onClick={() => { void save() }} disabled={!pending || saving}>{saving ? 'Saving…' : 'Save'}</button>
      </Modal.Footer>
    </Modal>
  )
}

export default RenameTagModal
