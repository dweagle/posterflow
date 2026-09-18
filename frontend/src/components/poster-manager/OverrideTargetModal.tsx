import { FormEvent, useEffect, useRef, useState } from 'react'
import { Loader2, RefreshCw, Search, X } from 'lucide-react'
import { LibraryItem, PosterOverride, getDriveImageUrl, savePosterOverride, searchLibraryItems } from '../../api/posterManager'
import {
  ART_SLOT_LABELS,
  DrivePosterPick,
  libraryItemToTarget,
  overridePayloadFor,
  overrideTargetLabel,
  parsePosterName,
  pickSlot,
} from '../../utils/posterOverrideTarget'
import { useToast } from '../Toast'
import { useFloatTip } from '../FloatTip'
import SeasonSlotChips from './SeasonSlotChips'
import '../PosterDriveSearchModal.css'

type OverrideTargetModalProps = {
  pick: DrivePosterPick
  onClose: () => void
  onSaved?: (override: PosterOverride) => void
}

type TypeFilter = 'all' | LibraryItem['media_type']

const FILTERS: { key: TypeFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'movie', label: 'Movies' },
  { key: 'show', label: 'Shows' },
  { key: 'collection', label: 'Collections' },
]

const SEARCH_DEBOUNCE_MS = 250

const typeBadge = (t: LibraryItem['media_type']) => (
  <span className={`unmatched-cat-badge unmatched-cat-badge--${t === 'movie' ? 'movie' : t === 'collection' ? 'collection' : 'series'}`}>
    {t === 'movie' ? 'Movie' : t === 'collection' ? 'Collection' : 'Show'}
  </span>
)

const itemKey = (i: LibraryItem) => `${i.media_type}::${i.tmdb_id ?? ''}::${i.title}::${i.year ?? ''}`

// "Use for…" from a poster search hit: find the library item (the renamer's own media list),
// choose the poster/season slot, pin the file.
export default function OverrideTargetModal({ pick, onClose, onSaved }: OverrideTargetModalProps) {
  const { showToast } = useToast()
  const { show: showTip, hide: hideTip, tip } = useFloatTip()
  const parsed = parsePosterName(pick.poster_name)
  const [query, setQuery] = useState(parsed.query)
  const [filter, setFilter] = useState<TypeFilter>('all')
  const [results, setResults] = useState<LibraryItem[] | null>(null)
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<LibraryItem | null>(null)
  const [season, setSeason] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const requestSeq = useRef(0)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const runSearch = async (q: string, refresh = false) => {
    const trimmed = q.trim()
    if (!trimmed && !refresh) {
      setResults([])
      return
    }
    const seq = ++requestSeq.current
    setLoading(true)
    if (refresh) setRefreshing(true)
    setError(null)
    try {
      const res = await searchLibraryItems(trimmed, { refresh })
      if (seq !== requestSeq.current) return
      setResults(res.items)
      setLibraryTotal(res.total)
      setSelected((prev) => (prev && res.items.some((i) => itemKey(i) === itemKey(prev)) ? prev : null))
    } catch (err) {
      if (seq !== requestSeq.current) return
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(detail || 'Library search failed')
      setResults([])
    } finally {
      if (seq === requestSeq.current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }

  // Search as the user types; the mount run covers the prefilled poster name.
  useEffect(() => {
    const id = setTimeout(() => runSearch(query), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query])

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    runSearch(query)
  }

  const visible = (results ?? []).filter((i) => filter === 'all' || i.media_type === filter)
  // Artwork picks fill the piece matching the file's type; only posters have a season slot.
  const slot = pickSlot(pick)
  const isShow = selected?.media_type === 'show' && !slot
  const target = selected ? libraryItemToTarget(selected, isShow ? season : null, slot ? 'artwork' : 'poster') : null
  const targetLabel = target ? overrideTargetLabel(target, slot) : ''

  // The season parsed from the file name is preselected only when the library has it.
  const choose = (item: LibraryItem) => {
    setSelected(item)
    setSeason(parsed.season != null && item.seasons.includes(parsed.season) ? parsed.season : null)
  }

  const handleSave = async () => {
    if (!target) return
    setSaving(true)
    try {
      const saved = await savePosterOverride(overridePayloadFor(target, pick))
      showToast(`Using "${pick.poster_name}" for ${targetLabel} on the next rename`)
      onSaved?.(saved)
      onClose()
    } catch {
      showToast('Failed to save poster override', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="drive-search-modal-overlay drive-search-modal-overlay--stacked" onMouseDown={onClose}>
      <div className="drive-search-modal override-target-modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="drive-search-modal-header">
          <h2>Use Poster For…</h2>
          <button type="button" className="drive-search-modal-close" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        <div className="drive-search-modal-body override-target-body">
          <div className="override-target-pick">
            <img src={getDriveImageUrl(pick.file_path)} alt="" className="override-target-pick-thumb" />
            <div className="override-target-pick-meta">
              <div className="override-target-pick-name">{pick.poster_name}</div>
              <div className="override-target-pick-drive">
                {pick.drive_name}
                {slot ? ` · ${ART_SLOT_LABELS[slot]}` : ''}
              </div>
            </div>
          </div>

          <p className="override-target-hint">
            {slot
              ? `Pick the library item this ${ART_SLOT_LABELS[slot].toLowerCase()} is for. The next rename places this file as its ${ART_SLOT_LABELS[slot].toLowerCase()} instead of whatever the drive priority would choose.`
              : 'Pick the library item this poster is for. The next rename places this file as its poster instead of whatever the drive priority would choose.'}
          </p>

          <form className="poster-search-toolbar" onSubmit={handleSubmit}>
            <div className="poster-search-input-wrap">
              <Search size={18} className="poster-search-input-icon" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search your library..."
                aria-label="Search your library"
                autoFocus
              />
            </div>
            <button type="submit" className="poster-search-button" disabled={loading}>
              {loading ? 'Searching...' : 'Search'}
            </button>
          </form>
          <div className="poster-search-filter-row">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`poster-filter-btn${filter === f.key ? ' active' : ''}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
            <button
              type="button"
              className="poster-filter-btn override-target-refresh"
              onClick={() => { hideTip(); runSearch(query, true) }}
              disabled={loading}
              onPointerEnter={(e) => showTip(e, 'Re-read the media sources (the library list is cached for a few minutes)')}
              onPointerLeave={hideTip}
            >
              <RefreshCw size={12} className={refreshing ? 'override-target-spin' : undefined} />
              Refresh library
            </button>
          </div>

          <div className="override-target-results" role="listbox" aria-label="Library results">
            {loading && results === null && (
              <div className="override-target-empty">
                <Loader2 size={16} className="override-target-spin" /> Reading your library… the first search fetches every configured source.
              </div>
            )}
            {error && <div className="override-target-empty override-target-error">{error}</div>}
            {!error && results !== null && visible.length === 0 && !loading && (
              <div className="override-target-empty">
                {results.length === 0
                  ? `No library items match${libraryTotal != null ? ` (${libraryTotal} items in the renamer's libraries)` : ''}.`
                  : 'No matches of that type.'}
              </div>
            )}
            {visible.map((item) => {
              const active = selected != null && itemKey(selected) === itemKey(item)
              const img = item.poster_url || item.thumb_url
              return (
                <button
                  key={itemKey(item)}
                  type="button"
                  role="option"
                  aria-selected={active}
                  className={`override-target-row${active ? ' active' : ''}`}
                  onClick={() => choose(item)}
                >
                  {img ? (
                    <img src={img} alt="" className="override-target-thumb" loading="lazy" />
                  ) : (
                    <div className="override-target-thumb override-target-thumb--empty" />
                  )}
                  <span className="override-target-row-title">
                    {item.title}
                    {item.year ? <span className="item-year"> ({item.year})</span> : null}
                    {item.source ? <span className="override-target-row-source"> · {item.source}</span> : null}
                  </span>
                  {typeBadge(item.media_type)}
                </button>
              )
            })}
          </div>

          {isShow && (
            <div className="override-target-slot">
              <span className="poster-search-filter-label">Use as:</span>
              <SeasonSlotChips seasons={selected?.seasons ?? []} value={season} onChange={setSeason} />
            </div>
          )}
        </div>
        <div className="override-target-footer">
          <button type="button" className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="poster-search-button"
            onClick={() => { hideTip(); handleSave() }}
            disabled={!target || saving}
            onPointerEnter={(e) => showTip(e, target ? `Pin this file for ${targetLabel}` : 'Pick an item first')}
            onPointerLeave={hideTip}
          >
            {saving ? 'Saving…' : target ? `Use for ${targetLabel}` : 'Use for…'}
          </button>
        </div>
      </div>
      {tip}
    </div>
  )
}
