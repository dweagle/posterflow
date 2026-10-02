import { useEffect, useMemo, useState } from 'react'
import { Check, FolderOpen } from 'lucide-react'
import type { CommunityRequest } from '../../api/community'
import {
  fetchPosterExportFile,
  getPosterExportUrl,
  listPosterExports,
  type PosterExportFile,
  type PosterExportListResponse,
} from '../../api/makerTools'
import { getApiErrorMessage } from '../../api/client'

type View = 'matching' | 'all'

type Props = {
  request: CommunityRequest
  onClose: () => void
  /** Files chosen from the server, wrapped as File objects for the normal upload path. */
  onPost: (files: File[]) => void
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

/** Poster slot = null for the plain poster, N for season N (0 = Specials). */
export type PosterSlot = number | null

/** "Seasons: 1, 2" on the first notes line (multi-season requests carry no season_number). */
export function requestedSeasonsFromNotes(notes: string | null | undefined): number[] {
  const first = (notes ?? '').split('\n')[0]
  if (!first.startsWith('Seasons: ')) return []
  return first.slice('Seasons: '.length).split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isFinite(n))
}

/** The slots a request asks for; 'all' for a whole show, where every exported slot is wanted. */
export function requestedSlots(request: Pick<CommunityRequest, 'media_type' | 'season_number' | 'notes'>): Set<PosterSlot> | 'all' {
  if (request.media_type === 'show') return 'all'
  if (request.media_type === 'season') {
    if (request.season_number != null) return new Set<PosterSlot>([request.season_number])
    const seasons = requestedSeasonsFromNotes(request.notes)
    return seasons.length ? new Set<PosterSlot>(seasons) : 'all'
  }
  return new Set<PosterSlot>([null])
}

/** The newest same-item export per requested slot: what the picker selects on open. */
export function autoSelect(files: PosterExportFile[], slots: Set<PosterSlot> | 'all'): PosterExportFile[] {
  const newest = new Map<PosterSlot, PosterExportFile>()
  for (const f of files.filter((f) => f.match).sort((a, b) => b.mtime - a.mtime)) {
    if (!newest.has(f.season)) newest.set(f.season, f)
  }
  return [...newest.values()].filter((f) => slots === 'all' || slots.has(f.season))
}

/** Pick finished posters that the panels already exported to the server and post them to the
 * request's Discord thread, skipping the OS file dialog (slow on Macs with network shares). */
export default function RequestUploadPickerModal({ request, onClose, onPost, onPickFromComputer }: Props) {
  const [data, setData] = useState<PosterExportListResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<View>('matching')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [preparing, setPreparing] = useState(false)
  const [preview, setPreview] = useState<PosterExportFile | null>(null)
  const [autoCount, setAutoCount] = useState(0)

  const { tmdb_id, tvdb_id, title, year, media_type, season_number, notes } = request
  const slots = useMemo(() => requestedSlots({ media_type, season_number, notes }), [media_type, season_number, notes])
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const seasons = slots === 'all' ? [] : [...slots].filter((n): n is number => n != null)
    listPosterExports({ tmdb_id, tvdb_id, title, year, seasons })
      .then((res) => {
        if (cancelled) return
        const picked = autoSelect(res.files, slots)
        setData(res)
        setSelected(new Set(picked.map(fileKey)))
        setAutoCount(picked.length)
        setView(res.files.some((f) => f.match) ? 'matching' : 'all')
      })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, 'Failed to list exports')) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [tmdb_id, tvdb_id, title, year, slots])

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

  const handlePost = async () => {
    const picks = [...selected].map((k) => byKey.get(k)).filter((f): f is PosterExportFile => !!f)
    if (!picks.length) return
    setPreparing(true)
    setError(null)
    try {
      const files = await Promise.all(picks.map((f) => fetchPosterExportFile(f)))
      onPost(files)
    } catch (e) {
      setError(getApiErrorMessage(e, 'Could not read the selected files'))
    } finally {
      setPreparing(false)
    }
  }

  const slotLabel = slots === 'all' ? '' : [...slots].filter((n): n is number => n != null).map((n) => seasonLabel(n)).join(', ')
  const heading = `${title}${year ? ` (${year})` : ''}${slotLabel ? ` · ${slotLabel}` : ''}`

  return (
    <>
      <div className="modal-overlay">
        <div className="modal-content schedule-modal request-export-picker">
          <div className="modal-header">
            <h2>Upload poster · {heading}</h2>
            <button className="modal-close" onClick={onClose}>×</button>
          </div>
          <div className="modal-body">
            {configured && (
              <div className="export-picker-tabs">
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
                  const badge = seasonLabel(f.season)
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
                          {ageLabel(f.mtime)}{f.match && slots !== 'all' && !slots.has(f.season) ? ' · not requested' : ''}
                        </span>
                        <button type="button" className="export-picker-preview" onClick={() => setPreview(f)}>Preview</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <div className="modal-footer export-picker-footer">
            <button type="button" className="btn-secondary export-picker-computer" onClick={onPickFromComputer}>
              <FolderOpen size={14} /> From computer…
            </button>
            <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
            <button
              type="button"
              className="btn-primary"
              style={{ justifyContent: 'center' }}
              disabled={preparing || selected.size === 0}
              onClick={() => void handlePost()}
            >
              {preparing ? 'Preparing…' : `Post${selected.size ? ` ${selected.size}` : ''} to Discord`}
            </button>
          </div>
        </div>
      </div>

      {preview && (
        <div className="tmdb-lightbox-overlay" onClick={() => setPreview(null)}>
          <div className="tmdb-gallery-lightbox" onClick={(e) => e.stopPropagation()}>
            <img className="tmdb-gallery-lightbox-img" src={getPosterExportUrl(preview)} alt="Preview" />
            <div className="tmdb-gallery-lightbox-actions">
              <span className="tmdb-gallery-dims">{preview.name}</span>
            </div>
          </div>
          <button type="button" className="tmdb-lightbox-close" onClick={() => setPreview(null)}>×</button>
        </div>
      )}
    </>
  )
}
