import { useState, useMemo } from 'react'
import type { MouseEvent, ReactNode } from 'react'
import { ListPlus, Loader2, Search, X, Check } from 'lucide-react'
import CommunityStatusBadge from './CommunityStatusBadge'
import ArrMissingBadge from './ArrMissingBadge'
import SortControls from './SortControls'
import { type ItemType, sortItems, useSortPrefs } from './itemSort'
import { type StyleClaimStatus } from '../../hooks/useCommunityClaimStatus'

export interface SelectableListItem {
  key: string
  title: string
  year: number | null
  // Media type for the colored badge (matches the app's unmatched-cat-badge--*).
  badgeType?: 'movie' | 'series' | 'season' | 'collection' | null
  posterUrl?: string | null
  // Same supplementary badges the Unmatched rows show.
  claimStatus?: StyleClaimStatus | null
  available?: boolean | null
  source?: string | null
}

const BADGE_LABEL: Record<'movie' | 'series' | 'season' | 'collection', string> = {
  movie: 'Movie',
  series: 'Series',
  season: 'Season',
  collection: 'Collection',
}

type CreateListModalProps = {
  items: SelectableListItem[]
  submitting: boolean
  // The parent maps the selected keys to ListItemInputs and publishes them.
  onAdd: (selectedKeys: string[]) => void
  onClose: () => void
  // Optional footer control (the required-style toggle). When `canAdd` is false
  // the publish button stays disabled — the parent uses this to require a style.
  styleControl?: ReactNode
  canAdd?: boolean
}

/**
 * Lets the user pick a subset of items to publish to the community Lists tab,
 * instead of the all-or-nothing "Add to Lists". Shared by the Unmatched and
 * Poster Style modals — each supplies its own rows (key/title/year/badge) and an
 * onAdd that turns the chosen keys into list items. Starts with nothing
 * selected so the user picks exactly what to publish.
 */
export default function CreateListModal({ items, submitting, onAdd, onClose, styleControl, canAdd = true }: CreateListModalProps) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set())
  const [query, setQuery] = useState('')
  const [prefs, setPrefs] = useSortPrefs('createListSort')

  // Add the type/seasonCount the shared sort + group helpers need, derived from
  // each row's badge so the picker can sort/filter like the parent modals.
  const sortable = useMemo(
    () =>
      items.map((it) => ({
        ...it,
        type: (it.badgeType === 'collection'
          ? 'collection'
          : it.badgeType === 'series' || it.badgeType === 'season'
            ? 'show'
            : 'movie') as ItemType,
        seasonCount: it.badgeType === 'season' ? 1 : 0,
      })),
    [items],
  )

  // Only offer the type pills / seasons sort when the picker actually has them.
  const showGroup = useMemo(() => new Set(sortable.map((i) => i.type)).size > 1, [sortable])
  const hasSeasons = useMemo(() => sortable.some((i) => i.seasonCount > 0), [sortable])

  const lower = query.trim().toLowerCase()
  const visible = useMemo(() => {
    let rows = sortable
    if (showGroup && prefs.group !== 'all') rows = rows.filter((i) => i.type === prefs.group)
    if (lower) rows = rows.filter((i) => i.title.toLowerCase().includes(lower))
    return sortItems(rows, prefs)
  }, [sortable, showGroup, prefs, lower])

  const toggle = (key: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  // Select-all / clear act on the currently visible (filtered) rows only.
  const allVisibleSelected = visible.length > 0 && visible.every((i) => selected.has(i.key))
  const toggleAllVisible = () =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (allVisibleSelected) visible.forEach((i) => next.delete(i.key))
      else visible.forEach((i) => next.add(i.key))
      return next
    })

  const count = selected.size
  const handleOverlay = (e: MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget && !submitting) onClose()
  }

  return (
    <div className="modal-overlay" onClick={handleOverlay}>
      <div className="modal-content schedule-modal create-list-modal">
        <div className="modal-header">
          <h2>Create List</h2>
          <button className="modal-close" onClick={onClose} disabled={submitting}>×</button>
        </div>

        <div className="modal-body">
          <p className="create-list-sub">Pick the items to publish to the community Lists tab.</p>

          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${items.length} items…`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button className="search-clear-btn" onClick={() => setQuery('')} title="Clear search">
                <X size={14} />
              </button>
            )}
          </div>

          <SortControls prefs={prefs} onChange={setPrefs} showGroup={showGroup} showSeasons={hasSeasons} />

          <div className="create-list-toolbar">
            <button type="button" className="create-list-selectall" onClick={toggleAllVisible} disabled={visible.length === 0}>
              {allVisibleSelected ? 'Clear' : 'Select all'}{lower || (showGroup && prefs.group !== 'all') ? ' (filtered)' : ''}
            </button>
            <span className="create-list-count">{count} of {items.length} selected</span>
          </div>

          <div className="create-list-rows">
            {visible.map((item) => {
              const checked = selected.has(item.key)
              return (
                <button
                  type="button"
                  key={item.key}
                  className={`create-list-row${checked ? ' selected' : ''}`}
                  onClick={() => toggle(item.key)}
                >
                  {item.posterUrl
                    ? <img src={item.posterUrl} alt="" className="create-list-poster" loading="lazy" />
                    : <span className="create-list-poster create-list-poster--empty" />}
                  <span className="create-list-title">
                    {item.title}{item.year ? <span className="create-list-year"> ({item.year})</span> : null}
                  </span>
                  <div className="create-list-meta">
                    {item.badgeType && (
                      <span className={`unmatched-cat-badge unmatched-cat-badge--${item.badgeType}`}>
                        {BADGE_LABEL[item.badgeType]}
                      </span>
                    )}
                    <CommunityStatusBadge status={item.claimStatus ?? null} />
                    <ArrMissingBadge available={item.available} source={item.source} />
                  </div>
                  <span className="create-list-check">{checked && <Check size={16} />}</span>
                </button>
              )
            })}
            {visible.length === 0 && <div className="create-list-empty">No items match your search.</div>}
          </div>
        </div>

        <div className="modal-footer">
          {styleControl}
          <div className="modal-footer-actions">
            <button className="btn-secondary" onClick={onClose} disabled={submitting}>Cancel</button>
            <button
              className="btn-primary"
              onClick={() => onAdd([...selected])}
              disabled={submitting || count === 0 || !canAdd}
              title={!canAdd ? 'Select a poster style (CL2K or MM2K)' : undefined}
            >
              {submitting ? <Loader2 size={14} className="spin-icon" /> : <ListPlus size={14} />}
              {' '}Add {count} to Lists
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
