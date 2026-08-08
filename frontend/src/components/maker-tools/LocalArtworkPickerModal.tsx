import { useCallback, useEffect, useState } from 'react'
import { Check, Plus } from 'lucide-react'
import {
  addLocalArtwork,
  getApiErrorMessage,
  getLocalArtworkFolder,
  getLocalArtworkImageUrl,
  setLocalArtworkFolder,
  type LocalArtworkFile,
  type LocalArtworkFolderResponse,
  type LocalArtworkSource,
  type TmdbSearchResult,
} from '../../api/client'
import { useToast } from '../Toast'

type PickType = 'background' | 'squareart'

const PICK_TYPES: PickType[] = ['background', 'squareart']
const TYPE_LABEL: Record<PickType, string> = { background: 'Backgrounds', squareart: 'Square Art' }
// Only the two always-available roots are badged; files from the chosen folder need no marker.
const SOURCE_BADGE: Partial<Record<LocalArtworkSource, string>> = { bundled: 'Included', art: 'Art folder' }

type Props = {
  item: TmdbSearchResult
  /** Index into the full IDarr sync_targets list of the chosen artwork scope. */
  syncTargetIndex: number
  scopeLabel: string | null
  onClose: () => void
}

/** Pick backgrounds / square art from a server-side folder and copy them into the scope under
 * the canonical IDarr name. The folder path is stored server-side, so it's remembered. */
export default function LocalArtworkPickerModal({ item, syncTargetIndex, scopeLabel, onClose }: Props) {
  const { showToast } = useToast()
  const [folderInput, setFolderInput] = useState('')
  const [data, setData] = useState<LocalArtworkFolderResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Partial<Record<PickType, LocalArtworkFile>>>({})
  const [added, setAdded] = useState<Partial<Record<PickType, string>>>({})   // type -> written filename
  const [addingAll, setAddingAll] = useState(false)
  const [overwrite, setOverwrite] = useState<{ subtype: PickType; file: LocalArtworkFile; filename: string } | null>(null)
  const [preview, setPreview] = useState<{ src: string; name: string; dims: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void getLocalArtworkFolder()
      .then((res) => {
        if (cancelled) return
        setData(res)
        setFolderInput(res.folder)
        setError(res.error ?? null)
      })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, 'Failed to load the artwork folder')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const loadFolder = useCallback(async () => {
    const folder = folderInput.trim()
    if (!folder) return
    setLoading(true)
    setError(null)
    try {
      const res = await setLocalArtworkFolder(folder)
      setData(res)
      setSelected({})
      setError(res.error ?? null)
    } catch (e) {
      setError(getApiErrorMessage(e, 'Failed to load the artwork folder'))
    } finally {
      setLoading(false)
    }
  }, [folderInput])

  // Paths are only unique within their own root, so identity is source + path.
  const sameFile = (a: LocalArtworkFile | undefined, b: LocalArtworkFile) =>
    !!a && a.path === b.path && a.source === b.source

  const toggleSelect = useCallback((type: PickType, f: LocalArtworkFile) => {
    setSelected((s) => (sameFile(s[type], f) ? { ...s, [type]: undefined } : { ...s, [type]: f }))
  }, [])

  const doAdd = useCallback(async (subtype: PickType, file: LocalArtworkFile, confirmOverwrite = false): Promise<'added' | 'exists' | 'error'> => {
    try {
      const res = await addLocalArtwork({
        sync_target_index: syncTargetIndex,
        tmdb_id: item.tmdb_id || undefined,   // 0 = id-less custom collection
        media_type: item.media_type,
        title: item.title,
        year: item.year || undefined,   // '' would fail the API's int parse
        tvdb_id: item.tvdb_id,
        imdb_id: item.imdb_id,
        subtype,
        path: file.path,
        source: file.source,
        confirm_overwrite: confirmOverwrite,
      })
      if (res.status === 'exists') {
        setOverwrite({ subtype, file, filename: res.written })
        return 'exists'
      }
      setAdded((a) => ({ ...a, [subtype]: res.written }))
      showToast(`Added ${res.written}`, 'success')
      return 'added'
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Add failed'), 'error')
      return 'error'
    }
  }, [syncTargetIndex, item, showToast])

  // `skip` = the type just handled through the overwrite prompt — the captured `added` state
  // predates it, so without the skip it would be posted (and prompted) again.
  const handleAddSelected = useCallback(async (skip?: PickType) => {
    setAddingAll(true)
    try {
      let allDone = true
      for (const type of PICK_TYPES) {
        if (type === skip) continue
        const file = selected[type]
        if (!file || added[type]) continue
        const outcome = await doAdd(type, file)
        if (outcome === 'exists') { allDone = false; break }   // resume from the prompt's answer
        if (outcome === 'error') allDone = false
      }
      if (allDone) onClose()   // everything selected landed — nothing left to do here
    } finally {
      setAddingAll(false)
    }
  }, [selected, added, doAdd, onClose])

  const pendingCount = PICK_TYPES.filter((t) => selected[t] && !added[t]).length

  return (
    <>
      <div className="modal-overlay">
        <div className="modal-content schedule-modal">
          <div className="modal-header">
            <h2>Add from folder — {item.year ? `${item.title} (${item.year})` : item.title}</h2>
            <button className="modal-close" onClick={onClose}>×</button>
          </div>
          <div className="modal-body">
            <div className="local-artwork-folder-row">
              <input
                type="text"
                value={folderInput}
                onChange={(e) => setFolderInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void loadFolder() }}
                placeholder="Absolute server path, e.g. /config/artwork"
              />
              <button type="button" className="btn-toolbar" onClick={() => void loadFolder()} disabled={loading || !folderInput.trim()}>
                {loading ? 'Loading…' : 'Load'}
              </button>
            </div>
            {error && <p className="tmdb-error">{error}</p>}
            {data?.truncated && (
              <p style={{ fontSize: '0.78rem', color: '#ffb74d', margin: '0 0 10px' }}>
                Large folder — showing the first 500 images only.
              </p>
            )}
            {data && !error && PICK_TYPES.map((type) => {
              const files = type === 'background' ? data.backgrounds : data.squareart
              return (
                <div key={type} style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                    <strong style={{ fontSize: '0.85rem' }}>{TYPE_LABEL[type]}</strong>
                    <span style={{ fontSize: '0.75rem', color: '#888' }}>{files.length}</span>
                    {added[type] && (
                      <span className="badge badge-green" style={{ fontSize: '0.62rem' }}>
                        <Check size={10} /> {added[type]}
                      </span>
                    )}
                  </div>
                  {files.length === 0
                    ? <p style={{ fontSize: '0.78rem', color: '#777', margin: 0 }}>None available — add a folder above, or drop files in the art folder.</p>
                    : (
                      <div className="tmdb-gallery-grid tmdb-gallery-grid--backdrops">
                        {files.map((f) => {
                          const isSel = sameFile(selected[type], f)
                          return (
                            <div key={`${f.source}:${f.path}`} className="tmdb-gallery-item">
                              <div className="tmdb-gallery-thumb-wrapper">
                                <button
                                  type="button"
                                  className={`tmdb-gallery-thumb-btn${isSel ? ' local-artwork-thumb--selected' : ''}`}
                                  onClick={() => setPreview({ src: getLocalArtworkImageUrl(f.path, f.source), name: f.name, dims: f.width && f.height ? `${f.width}×${f.height}` : '' })}
                                  title="Click to preview full size"
                                >
                                  <img
                                    className="tmdb-gallery-thumb"
                                    src={getLocalArtworkImageUrl(f.path, f.source)}
                                    alt=""
                                    loading="lazy"
                                    style={type === 'squareart' ? { aspectRatio: '1 / 1' } : undefined}
                                  />
                                </button>
                                {SOURCE_BADGE[f.source] && (
                                  <span className="tmdb-gallery-origin-badge">{SOURCE_BADGE[f.source]}</span>
                                )}
                              </div>
                              <div className="tmdb-gallery-item-meta">
                                <div className="tmdb-gallery-meta-row" style={{ gap: 6 }}>
                                  <span className="local-artwork-name" title={f.name}>{f.name}</span>
                                  {f.width && f.height ? <span className="tmdb-gallery-dims">{f.width}×{f.height}</span> : null}
                                </div>
                                <div style={{ display: 'flex', gap: 6 }}>
                                  <button
                                    type="button"
                                    className={`btn-toolbar ${isSel ? 'btn-primary' : ''}`}
                                    style={{ flex: 1, minWidth: 0, fontSize: '0.72rem', padding: '3px 6px', justifyContent: 'center' }}
                                    onClick={() => toggleSelect(type, f)}
                                  >
                                    {isSel ? <><Check size={12} /> Selected</> : <><Plus size={12} /> Select</>}
                                  </button>
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                </div>
              )
            })}
            {data?.art_dir && (
              <p style={{ fontSize: '0.75rem', color: '#777', margin: '0 0 4px' }}>
                Artwork you reuse often can live in <code>{data.art_dir}</code> — it&apos;s listed here for every
                item, alongside the artwork included with PosterFlow.
              </p>
            )}
            {scopeLabel && (
              <p style={{ fontSize: '0.75rem', color: '#777', margin: 0 }}>
                Picks are copied and renamed into scope: <strong>{scopeLabel}</strong> (IDarr renames + uploads on its next run).
              </p>
            )}
          </div>
          <div className="modal-footer">
            <button className="btn-secondary" onClick={onClose}>Close</button>
            <button
              className="btn-primary"
              style={{ justifyContent: 'center' }}
              disabled={addingAll || pendingCount === 0}
              onClick={() => void handleAddSelected()}
            >
              {addingAll ? 'Adding…' : `Add selected${pendingCount ? ` (${pendingCount})` : ''}`}
            </button>
          </div>
        </div>
      </div>

      {/* Full-size preview lightbox — same classes as the card's gallery */}
      {preview && (
        <div className="tmdb-lightbox-overlay" onClick={() => setPreview(null)}>
          <div className="tmdb-gallery-lightbox" onClick={(e) => e.stopPropagation()}>
            <img className="tmdb-gallery-lightbox-img" src={preview.src} alt="Preview" />
            <div className="tmdb-gallery-lightbox-actions">
              <span className="tmdb-gallery-dims">{preview.name}{preview.dims ? ` — ${preview.dims}` : ''}</span>
            </div>
          </div>
          <button type="button" className="tmdb-lightbox-close" onClick={() => setPreview(null)}>×</button>
        </div>
      )}

      {/* Overwrite confirmation — rendered last so it stacks above the picker (same z-index). */}
      {overwrite && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Overwrite existing artwork?</h2>
              <button className="modal-close" onClick={() => setOverwrite(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ color: '#ccc', lineHeight: 1.6, marginBottom: '0.75rem' }}>
                This scope already has a file with this name:
              </p>
              <div className="psd-not-found-filename"><code>{overwrite.filename}</code></div>
              {scopeLabel && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Scope:</span>
                  <code>{scopeLabel}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#ffb74d', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Overwriting moves the existing file to the IDarr duplicates folder and saves this one in its place.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setOverwrite(null)}>Cancel</button>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', background: '#f44336' }}
                onClick={() => {
                  const o = overwrite
                  setOverwrite(null)
                  void doAdd(o.subtype, o.file, true).then((outcome) => {
                    if (outcome === 'added') void handleAddSelected(o.subtype)
                  })
                }}
              >
                Overwrite
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
