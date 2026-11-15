import { useEffect, useMemo, useState } from 'react'
import { Check, FolderOpen } from 'lucide-react'
import type { CommunityRequest } from '../../api/community'
import {
  getPosterExportUrl,
  listPosterExports,
  type PosterExportFile,
  type PosterExportListResponse,
} from '../../api/makerTools'
import { getApiErrorMessage } from '../../api/client'
import { uploadActionButtonLabel, type CommunityUploadAction } from './useIdarrQuickAdd'
import Modal from '../../components/Modal'

type View = 'matching' | 'all'

type Props = {
  request: CommunityRequest
  /** What posting does (Discord, Discord + IDarr, IDarr only); only changes the button label here. */
  action?: CommunityUploadAction
  onClose: () => void
  /** The exports chosen. They are already on the server, which uploads them by name. */
  onPost: (picks: PosterExportFile[]) => void
  /** Fall back to the browser's own file dialog. */
  onPickFromComputer: () => void
}

const fileKey = (f: Pick<PosterExportFile, 'name' | 'source'>) => `${f.source}:${f.name}`

/** "Season 2" / "Specials" / null for a plain poster. */
export function seasonLabel(season: number | null): string | null {
  if (season == null) return null
  return season === 0 ? 'Specials' : `Season ${season}`
}

/** Short age for the file list: "just now", "5m ago", "3h ago", "2d ago", else a date. */
export function ageLabel(mtimeSeconds: number, now = Date.now()): string {
  const s = Math.max(0, Math.round(now / 1000 - mtimeSeconds))
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 7 * 86400) return `${Math.floor(s / 86400)}d ago`
  return new Date(mtimeSeconds * 1000).toLocaleDateString()
}

/** Exact matches first, then other seasons of the same title, then the rest; newest first within each. */
export function sortForRequest(files: PosterExportFile[]): PosterExportFile[] {
  const rank = (m: PosterExportFile['match']) => (m === 'exact' ? 0 : m === 'item' ? 1 : 2)
  return [...files].sort((a, b) => rank(a.match) - rank(b.match) || b.mtime - a.mtime)
}

/** Pick finished posters that the panels already exported to the server and post them to the
 * request's Discord thread, skipping the OS file dialog (slow on Macs with network shares).
 * The server says which posters the request asks for and which exports start selected (the newest
 * of each): the same choice the editor panels' Upload button makes. */
export default function RequestUploadPickerModal({ request, action = 'discord', onClose, onPost, onPickFromComputer }: Props) {
  const [data, setData] = useState<PosterExportListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('matching')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [preview, setPreview] = useState<PosterExportFile | null>(null)
  const [autoCount, setAutoCount] = useState(0)

  const { tmdb_id, tvdb_id, title, year, media_type, season_number, notes } = request
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    listPosterExports({ tmdb_id, tvdb_id, title, year, media_type, season_number, notes })
      .then((res) => {
        if (cancelled) return
        const picked = res.files.filter((f) => f.selected)
        setData(res)
        setSelected(new Set(picked.map(fileKey)))
        setAutoCount(picked.length)
        setView(res.files.some((f) => f.match) ? 'matching' : 'all')
      })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, 'Failed to list exports')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tmdb_id, tvdb_id, title, year, media_type, season_number, notes])

  const all = useMemo(() => sortForRequest(data?.files ?? []), [data])
  const matching = useMemo(() => all.filter((f) => f.match), [all])
  const byKey = useMemo(() => new Map(all.map((f) => [fileKey(f), f])), [all])
  const shown = view === 'matching' ? matching : all
  const configured = (data?.folders.length ?? 0) > 0
  const missingFolders = (data?.folders ?? []).filter((f) => !f.exists)

  const toggle = (f: PosterExportFile) => {
    setSelected((s) => {
      const next = new Set(s)
      const k = fileKey(f)
      if (next.has(k)) next.delete(k)
      else next.add(k)
      return next
    })
  }

  const handlePost = () => {
    const picks = [...selected].map((k) => byKey.get(k)).filter((f): f is PosterExportFile => !!f)
    if (picks.length) onPost(picks)
  }

  // A one-season request is named at once; a multi-season one when the server has read its notes.
  const slotLabel = (data?.seasons ?? (season_number != null ? [season_number] : [])).map((n) => seasonLabel(n)).join(', ')
  const heading = `${title}${year ? ` (${year})` : ''}${slotLabel ? ` · ${slotLabel}` : ''}`

  return (
  <>
      <Modal onClose={onClose} className="request-export-picker" closeOnOverlay={false}>
        <Modal.Header title={<>Upload poster · {heading}</>} />
        <Modal.Body>
          {configured && (
            <div className="export-picker-tabs pf-subtabs">
              <button type="button" className={view === 'matching' ? 'active' : ''} onClick={() => setView('matching')}>
                Matching <span>{matching.length}</span>
              </button>
              <button type="button" className={view === 'all' ? 'active' : ''} onClick={() => setView('all')}>
                All exports <span>{all.length}</span>
              </button>
            </div>
          )}
          {error && <p className="tmdb-error">{error}</p>}
          {loading && <p className="export-picker-note">Loading exports…</p>}
          {!loading && autoCount > 0 && (
            <p className="export-picker-note ok">
              {autoCount === 1 ? 'Selected the newest matching export.' : `Selected the newest export for ${autoCount} posters.`}
              {' '}Change the selection if needed.
            </p>
          )}
          {!loading && data && !configured && (
            <p className="export-picker-note">
              No export folder is set. Add a poster export folder in Maker Tools and posters exported from the
              Photoshop and Photopea panels will show up here.
            </p>
          )}
          {missingFolders.map((f) => (
            <p key={f.source} className="export-picker-note warn">Folder not found on the server: {f.folder}</p>
          ))}
          {!loading && data && configured && shown.length === 0 && (
            <p className="export-picker-note">
              {view === 'matching' ? 'Nothing here matches this request. Check All exports.' : 'No exports found.'}
            </p>
          )}
          {data?.truncated && (
            <p className="export-picker-note warn">Large folder. Showing the newest 400 files only.</p>
          )}
          {shown.length > 0 && (
            <div className="tmdb-gallery-grid tmdb-gallery-grid--posters">
              {shown.map((f) => {
                const k = fileKey(f)
                const isSel = selected.has(k)
                const badge = f.collection ? 'Collection' : seasonLabel(f.season)
                return (
                  <div key={k} className="tmdb-gallery-item">
                    <div className="tmdb-gallery-thumb-wrapper">
                      <button
                        type="button"
                        className={`tmdb-gallery-thumb-btn${isSel ? ' export-picker-thumb--selected' : ''}`}
                        onClick={() => toggle(f)}
                        title={isSel ? 'Click to deselect' : 'Click to select'}
                        aria-pressed={isSel}
                        aria-label={f.name}
                      >
                        <img className="tmdb-gallery-thumb" src={getPosterExportUrl(f, true)} alt="" loading="lazy" />
                      </button>
                      {isSel && <span className="export-picker-check"><Check size={12} /></span>}
                      {badge && <span className="tmdb-gallery-origin-badge tmdb-gallery-origin-badge--bottom">{badge}</span>}
                    </div>
                    <div className="tmdb-gallery-item-meta">
                      <span className="export-picker-name" title={f.name}>{f.name}</span>
                      <span className="export-picker-age">
                        {ageLabel(f.mtime)}{f.match && !f.requested ? ' · not requested' : ''}
                      </span>
                      <button type="button" className="btn-link" onClick={() => setPreview(f)}>Preview</button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </Modal.Body>
        <Modal.Footer className="export-picker-footer">
          <button type="button" className="btn-secondary export-picker-computer" onClick={onPickFromComputer}>
            <FolderOpen size={14} /> From computer…
          </button>
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn-primary"
            disabled={selected.size === 0}
            onClick={handlePost}
          >
            {uploadActionButtonLabel(action, selected.size)}
          </button>
        </Modal.Footer>
      </Modal>

      {preview && (
        <div className="tmdb-lightbox-overlay" onClick={() => setPreview(null)}>
          <div className="tmdb-gallery-lightbox" onClick={(e) => e.stopPropagation()}>
            <img className="tmdb-gallery-lightbox-img" src={getPosterExportUrl(preview)} alt="Preview" />
            <div className="tmdb-gallery-lightbox-actions">
              <span className="tmdb-gallery-dims">{preview.name}</span>
            </div>
          </div>
          <button type="button" className="btn-icon btn-icon-filled tmdb-lightbox-close" onClick={() => setPreview(null)}>×</button>
        </div>
      )}
    </>
  )
}
