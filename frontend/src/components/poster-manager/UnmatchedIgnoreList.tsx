import { useEffect, useState } from 'react'
import { AlertTriangle, EyeOff, Loader2, Plus, X } from 'lucide-react'
import { type UnmatchedIgnoreItem, addUnmatchedIgnoreItem, getUnmatchedIgnoreItems, removeUnmatchedIgnoreItem } from '../../api/client'
import { useToast } from '../Toast'

const TYPE_LABEL: Record<UnmatchedIgnoreItem['media_type'], string> = {
  movie: 'Movie',
  series: 'Series',
  collection: 'Collection',
}

// Ignored unmatched items, listed under Detection Settings with per-row remove.
// Fed two ways: the eye-off button on the unmatched modals, or the manual form
// below the list (title must match the Plex/*arr title; year optional). Changes
// apply immediately — no save step — so onChanged lets the page refresh its stats.
function UnmatchedIgnoreList({ onChanged }: { onChanged?: () => void }) {
  const { showToast } = useToast()
  const [items, setItems] = useState<UnmatchedIgnoreItem[] | null>(null)
  const [removingIdx, setRemovingIdx] = useState<number | null>(null)
  const [addType, setAddType] = useState<UnmatchedIgnoreItem['media_type']>('movie')
  const [addTitle, setAddTitle] = useState('')
  const [addYear, setAddYear] = useState('')
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    getUnmatchedIgnoreItems()
      .then((res) => setItems(res.items))
      .catch(() => setItems([]))
  }, [])

  const handleRemove = async (item: UnmatchedIgnoreItem, idx: number) => {
    if (removingIdx !== null) return
    setRemovingIdx(idx)
    try {
      const res = await removeUnmatchedIgnoreItem(item)
      setItems(res.items)
      showToast(`"${item.title}" removed from the ignore list`)
      onChanged?.()
    } catch {
      showToast('Failed to remove item from ignore list', 'error')
    } finally {
      setRemovingIdx(null)
    }
  }

  // Year is required for movies/series so a title-only entry can't swallow every
  // same-titled item (remakes); collection titles are unique enough to skip it.
  const needsYear = addType !== 'collection'
  const yearNum = Number(addYear.trim())
  const yearValid = addYear.trim() !== '' && Number.isFinite(yearNum)

  const handleAdd = async () => {
    const title = addTitle.trim()
    if (!title || adding) return
    if (needsYear && !yearValid) {
      showToast("Enter a year — without one, every movie or series with this title would be ignored", 'error')
      return
    }
    const year = needsYear ? yearNum : null
    setAdding(true)
    try {
      const res = await addUnmatchedIgnoreItem({ media_type: addType, title, year })
      setItems(res.items)
      setAddTitle('')
      setAddYear('')
      showToast(`"${title}" added to the ignore list`)
      onChanged?.()
    } catch {
      showToast('Failed to add item to ignore list', 'error')
    } finally {
      setAdding(false)
    }
  }

  return (
    <div className="unmatched-ignore-list">
      <label style={{ display: 'block', fontWeight: 500 }}>Ignored Items</label>
      <small>
        Items hidden from unmatched detection. Add them with the <EyeOff size={11} style={{ verticalAlign: 'text-top' }} /> button
        on the unmatched lists or manually below. Removing one here makes it show as unmatched again.
      </small>
      <div className="unmatched-ignore-warning">
        <AlertTriangle size={13} />
        <span>Ignoring an item covers every asset type: hide a poster and its artwork is hidden too, and vice versa.</span>
      </div>
      {items === null ? (
        <div className="unmatched-ignore-empty"><Loader2 size={14} className="spin-icon" /></div>
      ) : items.length === 0 ? (
        <div className="unmatched-ignore-empty">No ignored items.</div>
      ) : (
        <div className="unmatched-ignore-rows">
          {items.map((item, idx) => (
            <div key={`${item.media_type}::${item.tmdb_id ?? ''}::${item.tvdb_id ?? ''}::${item.title}::${item.year ?? ''}`} className="unmatched-ignore-row">
              <span className={`unmatched-cat-badge unmatched-cat-badge--${item.media_type}`}>
                {TYPE_LABEL[item.media_type] ?? item.media_type}
              </span>
              <span className="unmatched-ignore-title">{item.title}</span>
              {item.year && <span className="item-year">({item.year})</span>}
              <button
                type="button"
                className="unmatched-ignore-remove"
                title="Remove from ignore list — show as unmatched again"
                onClick={() => handleRemove(item, idx)}
                disabled={removingIdx !== null}
              >
                {removingIdx === idx ? <Loader2 size={13} className="spin-icon" /> : <X size={13} />}
              </button>
            </div>
          ))}
        </div>
      )}
      <label style={{ display: 'block', fontWeight: 500, marginTop: '0.75rem' }}>Manual Entry</label>
      <div className="unmatched-ignore-add">
        <select
          value={addType}
          onChange={(e) => setAddType(e.target.value as UnmatchedIgnoreItem['media_type'])}
          aria-label="Item type"
        >
          <option value="movie">Movie</option>
          <option value="series">Series</option>
          <option value="collection">Collection</option>
        </select>
        <input
          type="text"
          placeholder="Title (as in Radarr/Sonarr; collections as in Plex)"
          value={addTitle}
          onChange={(e) => setAddTitle(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') handleAdd() }}
        />
        {needsYear && (
          <input
            type="number"
            className="unmatched-ignore-year"
            placeholder="Year"
            value={addYear}
            onChange={(e) => setAddYear(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAdd() }}
          />
        )}
        <button
          type="button"
          className="unmatched-ignore-add-btn"
          title="Add to ignore list"
          onClick={handleAdd}
          disabled={adding || !addTitle.trim()}
        >
          {adding ? <Loader2 size={13} className="spin-icon" /> : <Plus size={13} />}
          <span>Add</span>
        </button>
      </div>
      <small>Matching is case-insensitive on the exact title. Year is required for movies and series so same-titled items aren't ignored together; collections match by title alone.</small>
    </div>
  )
}

export default UnmatchedIgnoreList
