import { useEffect, useMemo, useState } from 'react'
import { Download, Upload } from 'lucide-react'
import Modal from '../Modal'
import { getApiErrorMessage } from '../../api/client'
import {
  exportOverlays,
  getOverlayExportOptions,
  importOverlays,
  type OverlayImportContents,
  type OverlayImportResult,
  type OverlayTypeInfo,
  type OverlaysConfig,
} from '../../api/overlays'

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`
const toggled = <T,>(set: Set<T>, value: T): Set<T> => {
  const next = new Set(set)
  if (next.has(value)) next.delete(value); else next.add(value)
  return next
}

type ExportProps = {
  types: OverlayTypeInfo[]
  // The saved settings: an export is made from what is saved.
  config: OverlaysConfig
  // The overlay being edited, ticked to start with.
  current?: { type: string; index: number }
  onClose: () => void
  onDone: (count: number) => void
}

// Pick overlays to save as one file: one, some or all of them.
export function OverlayExportDialog({ types, config, current, onClose, onDone }: ExportProps) {
  const groups = useMemo(
    () => types.map((t) => ({ info: t, overlays: config.types[t.key]?.overlays ?? [] })).filter((g) => g.overlays.length > 0),
    [types, config],
  )
  const every = useMemo(() => groups.flatMap((g) => g.overlays.map((_o, i) => `${g.info.key}:${i}`)), [groups])
  const [picked, setPicked] = useState<Set<string>>(() => new Set(current && every.includes(`${current.type}:${current.index}`) ? [`${current.type}:${current.index}`] : []))
  const [replaced, setReplaced] = useState<Record<string, number>>({})
  const [withImages, setWithImages] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    getOverlayExportOptions()
      .then((options) => { if (live) { setReplaced(options.images); setWithImages(new Set(Object.keys(options.images))) } })
      .catch(() => { /* the overlays export without the replaced images */ })
    return () => { live = false }
  }, [])

  const pickedOf = (type: string) => every.some((id) => picked.has(id) && id.startsWith(`${type}:`))
  const run = async () => {
    setBusy(true)
    setError('')
    try {
      const picks = every.filter((id) => picked.has(id)).map((id) => ({ type: id.slice(0, id.lastIndexOf(':')), index: Number(id.slice(id.lastIndexOf(':') + 1)) }))
      await exportOverlays(picks, [...withImages].filter((type) => pickedOf(type)))
      onDone(picks.length)
    } catch (err) {
      setError(getApiErrorMessage(err, 'The export failed'))
      setBusy(false)
    }
  }

  return (
    <Modal onClose={onClose} busy={busy} className="overlay-share-dialog">
      <Modal.Header title="Export Overlays" />
      <Modal.Body>
        <p className="overlay-share-lead">Pick the overlays to put in the file. Another Posterflow can import it. Library picks, on and off switches, and server or Arr instance names stay out.</p>
        <div className="overlay-share-tools">
          <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" onClick={() => setPicked(new Set(every))}>Select All</button>
          <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" onClick={() => setPicked(new Set())}>Clear</button>
          <span className="overlay-share-count">{picked.size} of {every.length} picked</span>
        </div>
        {groups.map(({ info, overlays }) => {
          const badges = info.schema.find((f) => f.kind === 'badges')
          return (
            <fieldset key={info.key} className="overlay-share-group">
              <legend>{info.label}</legend>
              {overlays.map((o, i) => {
                const id = `${info.key}:${i}`
                const rows = badges && Array.isArray(o.options[badges.key]) ? (o.options[badges.key] as unknown[]).length : null
                return (
                  <label key={id} className="checkbox-label overlay-share-item">
                    <input type="checkbox" checked={picked.has(id)} onChange={() => setPicked((prev) => toggled(prev, id))} />
                    <span>{o.name.trim() || `Overlay ${i + 1}`}</span>
                    {rows !== null && <small>{plural(rows, 'badge')}</small>}
                  </label>
                )
              })}
              {replaced[info.key] > 0 && pickedOf(info.key) && (
                <label className="checkbox-label overlay-share-item overlay-share-images">
                  <input type="checkbox" checked={withImages.has(info.key)} onChange={() => setWithImages((prev) => toggled(prev, info.key))} />
                  <span>Include the {plural(replaced[info.key], 'image')} you replaced</span>
                </label>
              )}
            </fieldset>
          )
        })}
        {error && <p className="overlay-share-error" role="alert">{error}</p>}
      </Modal.Body>
      <Modal.Footer>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className="btn-primary" onClick={run} disabled={busy || picked.size === 0}>
          <Download size={14} /> {busy ? 'Exporting…' : picked.size === 0 ? 'Export' : `Export ${plural(picked.size, 'Overlay')}`}
        </button>
      </Modal.Footer>
    </Modal>
  )
}

type ImportProps = {
  file: File
  contents: OverlayImportContents
  onClose: () => void
  onImported: (result: OverlayImportResult) => void
}

// What a file holds, to pick from. The overlays picked are added to the page switched off.
export function OverlayImportDialog({ file, contents, onClose, onImported }: ImportProps) {
  const [picked, setPicked] = useState<Set<number>>(() => new Set(contents.overlays.map((o) => o.index)))
  // The images of a type are ticked to start with unless they would replace some of the user's own.
  const [withImages, setWithImages] = useState<Set<string>>(() => new Set(contents.images.filter((i) => i.replaces === 0).map((i) => i.type)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const groups = useMemo(() => {
    const byType = new Map<string, { label: string; overlays: OverlayImportContents['overlays'] }>()
    contents.overlays.forEach((o) => {
      const group = byType.get(o.type) ?? { label: o.type_label, overlays: [] }
      group.overlays.push(o)
      byType.set(o.type, group)
    })
    return [...byType.entries()]
  }, [contents])

  const run = async () => {
    setBusy(true)
    setError('')
    try {
      onImported(await importOverlays(file, contents.overlays.filter((o) => picked.has(o.index)).map((o) => o.index), [...withImages]))
    } catch (err) {
      setError(getApiErrorMessage(err, 'The import failed'))
      setBusy(false)
    }
  }
  const nothing = picked.size === 0 && withImages.size === 0

  return (
    <Modal onClose={onClose} busy={busy} className="overlay-share-dialog">
      <Modal.Header title="Import Overlays" />
      <Modal.Body>
        <p className="overlay-share-lead">
          <strong>{contents.file || file.name}</strong> Pick what to bring in. Imported overlays are added switched off. Nothing is saved until you click Save.
        </p>
        {contents.overlays.length > 0 && (
          <div className="overlay-share-tools">
            <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" onClick={() => setPicked(new Set(contents.overlays.map((o) => o.index)))}>Select All</button>
            <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" onClick={() => setPicked(new Set())}>Clear</button>
            <span className="overlay-share-count">{picked.size} of {contents.overlays.length} picked</span>
          </div>
        )}
        {groups.map(([type, group]) => (
          <fieldset key={type} className="overlay-share-group">
            <legend>{group.label}</legend>
            {group.overlays.map((o) => {
              const notes = [...(o.missing_images > 0 ? [`${plural(o.missing_images, 'image')} of its badges ${o.missing_images === 1 ? 'is' : 'are'} not in the file.`] : []), ...o.notes]
              return (
                <div key={o.index} className="overlay-share-entry">
                  <label className="checkbox-label overlay-share-item">
                    <input type="checkbox" checked={picked.has(o.index)} onChange={() => setPicked((prev) => toggled(prev, o.index))} />
                    <span>{o.name}</span>
                    {o.badges !== null && <small>{plural(o.badges, 'badge')}{o.images > 0 ? `, ${plural(o.images, 'image')}` : ''}</small>}
                  </label>
                  {notes.map((note) => <small key={note} className="overlay-share-note">{note}</small>)}
                </div>
              )
            })}
          </fieldset>
        ))}
        {contents.images.length > 0 && (
          <fieldset className="overlay-share-group">
            <legend>Replaced Images</legend>
            {contents.images.map((i) => (
              <div key={i.type} className="overlay-share-entry">
                <label className="checkbox-label overlay-share-item">
                  <input type="checkbox" checked={withImages.has(i.type)} onChange={() => setWithImages((prev) => toggled(prev, i.type))} />
                  <span>Use the file&rsquo;s {plural(i.count, `${i.label} ${i.noun}`)}</span>
                </label>
                <small className={`overlay-share-note ${i.replaces > 0 ? 'warn' : ''}`}>
                  {i.replaces > 0 ? `${plural(i.replaces, 'image')} of your own ${i.replaces === 1 ? 'is' : 'are'} replaced. ` : ''}Images are stored right away, also when you do not save.
                </small>
              </div>
            ))}
          </fieldset>
        )}
        {contents.unknown > 0 && (
          <p className="overlay-share-note">{plural(contents.unknown, 'overlay')} in the file cannot be read by this version. {contents.unknown === 1 ? 'It is' : 'They are'} left out.</p>
        )}
        {error && <p className="overlay-share-error" role="alert">{error}</p>}
      </Modal.Body>
      <Modal.Footer>
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className="btn-primary" onClick={run} disabled={busy || nothing}>
          <Upload size={14} /> {busy ? 'Importing…' : picked.size === 0 ? 'Import' : `Import ${plural(picked.size, 'Overlay')}`}
        </button>
      </Modal.Footer>
    </Modal>
  )
}
