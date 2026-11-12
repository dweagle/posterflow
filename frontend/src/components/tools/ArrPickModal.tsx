import { useEffect, useMemo, useState } from 'react'
import Modal from '../Modal'
import { getApiErrorMessage } from '../../api/client'
import { getArrOptions, type ArrOption } from '../../api/tools'
import './RenameTagModal.css'

// What an upgrader's two list filters pick from: the arr's tags, or its quality profiles.
export type ArrPickKind = 'tags' | 'profiles'

const COPY: Record<ArrPickKind, { title: string; what: string; hint: (nouns: string) => string }> = {
  tags: {
    title: 'Only tags',
    what: 'tags',
    hint: (nouns) => `Tick the tags a run keeps to. Only ${nouns} carrying one of them are searched. None ticked searches any.`,
  },
  profiles: {
    title: 'Quality profiles',
    what: 'quality profiles',
    hint: (nouns) => `Tick the quality profiles a run keeps to. Only ${nouns} on one of them are searched. None ticked searches any.`,
  },
}

const namesOf = (text: string) => text.split(',').map((name) => name.trim()).filter(Boolean)

interface Props {
  kind: ArrPickKind
  toolId: string
  instance: string
  value: string // the field as it stands: names separated by commas
  exclude?: string[] // tags that cannot be a filter: the tool's own search and ignore tags
  noun: string // one item: 'movie' or 'series'
  nouns: string
  onClose: () => void
  onApply: (value: string) => void
}

// The tags or quality profiles of one arr instance, read from the arr each time, with how many
// items carry or use each. Ticking fills the filter field; nothing is saved until the profile is.
// A name in the field that the arr does not have stays listed, so it is unticked on purpose.
function ArrPickModal({ kind, toolId, instance, value, exclude = [], noun, nouns, onClose, onApply }: Props) {
  const copy = COPY[kind]
  const [options, setOptions] = useState<ArrOption[] | null>(null)
  const [error, setError] = useState('')
  const [picked, setPicked] = useState<Set<string>>(() => new Set(namesOf(value).map((name) => name.toLowerCase())))

  useEffect(() => {
    let live = true
    getArrOptions(toolId, instance)
      .then((got) => { if (live) setOptions(kind === 'tags' ? got.tags : got.quality_profiles) })
      .catch((err) => { if (live) setError(getApiErrorMessage(err, `Could not load the ${copy.what} of ${instance}`)) })
    return () => { live = false }
  }, [toolId, instance, kind, copy.what])

  const rows = useMemo(() => {
    const left = new Set(exclude.map((name) => name.trim().toLowerCase()).filter(Boolean))
    const have = (options ?? []).filter((option) => !left.has(option.name.toLowerCase()))
    const known = new Set(have.map((option) => option.name.toLowerCase()))
    const gone = namesOf(value).filter((name) => !known.has(name.toLowerCase()))
    return [...gone.map((name) => ({ name, count: null as number | null })), ...have]
  }, [options, exclude, value])

  const toggle = (name: string, on: boolean) => setPicked((prev) => {
    const next = new Set(prev)
    if (on) next.add(name.toLowerCase())
    else next.delete(name.toLowerCase())
    return next
  })
  const chosen = rows.filter((row) => picked.has(row.name.toLowerCase()))

  return (
    <Modal onClose={onClose} size="sm">
      <Modal.Header title={copy.title}>
        <span className="rename-tag-where">{instance}</span>
      </Modal.Header>
      <Modal.Body>
        <p className="field-hint">{copy.hint(nouns)}</p>
        {error ? <p className="text-danger" role="alert">{error}</p> : (
          <div className="rename-tag-results">
            {options === null ? (
              <p className="rename-tag-note">Loading the {copy.what} of {instance}…</p>
            ) : rows.length === 0 ? (
              <p className="rename-tag-note">{instance} has no {copy.what} to pick from.</p>
            ) : (
              <ul className="rename-tag-list" aria-label={copy.title}>
                {rows.map((row) => (
                  <li key={row.name}>
                    <label className="checkbox-label rename-tag-row">
                      <input type="checkbox" checked={picked.has(row.name.toLowerCase())} onChange={(e) => toggle(row.name, e.target.checked)} />
                      <span className="rename-tag-title">{row.name}</span>
                      {' '}
                      <span className="rename-tag-year">{row.count === null ? `not in ${instance}` : `${row.count} ${row.count === 1 ? noun : nouns}`}</span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <span className="rename-tag-count">{chosen.length} picked</span>
        <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
        <button type="button" className="btn-primary" disabled={options === null} onClick={() => onApply(chosen.map((row) => row.name).join(', '))}>Apply</button>
      </Modal.Footer>
    </Modal>
  )
}

export default ArrPickModal
