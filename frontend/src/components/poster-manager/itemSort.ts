// Shared sort/group logic for the Poster Style + Unmatched Items modals.
import { useCallback, useState } from 'react'

export type ItemType = 'movie' | 'show' | 'collection'
export type SortField = 'title' | 'year' | 'seasons' | 'added' | 'released'
export type SortDir = 'asc' | 'desc'
export type GroupFilter = 'all' | ItemType

export interface SortPrefs {
  group: GroupFilter
  field: SortField
  dir: SortDir
}

// Minimal shape the comparator/grouping needs. Both modals' item types satisfy
// this structurally (with extra fields of their own). Dates are ISO strings
// (arr added / digital-physical release), optional — views without them just
// don't offer the date sorts.
export interface SortableItem {
  title: string
  year: number | null
  type: ItemType
  seasonCount: number
  added?: string | null
  releaseDate?: string | null
}

const DEFAULT_PREFS: SortPrefs = { group: 'all', field: 'title', dir: 'asc' }

// Strip a trailing "(YYYY)" so title comparisons ignore an appended year.
function normTitle(title: string): string {
  return title.replace(/\s*\(\d{4}\)\s*$/, '').trim().toLowerCase()
}

export function compareItems(a: SortableItem, b: SortableItem, field: SortField, dir: SortDir): number {
  const sign = dir === 'asc' ? 1 : -1

  if (field === 'year') {
    // Items without a year always sink to the bottom, regardless of direction.
    if (a.year == null && b.year == null) {
      /* fall through to tiebreak */
    } else if (a.year == null) {
      return 1
    } else if (b.year == null) {
      return -1
    } else if (a.year !== b.year) {
      return (a.year - b.year) * sign
    }
  } else if (field === 'seasons') {
    if (a.seasonCount !== b.seasonCount) return (a.seasonCount - b.seasonCount) * sign
  } else if (field === 'added' || field === 'released') {
    // ISO strings compare lexically; items without a date always sink to the bottom.
    const av = (field === 'added' ? a.added : a.releaseDate) ?? null
    const bv = (field === 'added' ? b.added : b.releaseDate) ?? null
    if (av == null && bv == null) {
      /* fall through to tiebreak */
    } else if (av == null) {
      return 1
    } else if (bv == null) {
      return -1
    } else if (av !== bv) {
      return (av < bv ? -1 : 1) * sign
    }
  } else {
    const t = normTitle(a.title).localeCompare(normTitle(b.title))
    if (t !== 0) return t * sign
  }

  // Stable, direction-independent tiebreak: title, then year.
  const t = normTitle(a.title).localeCompare(normTitle(b.title))
  if (t !== 0) return t
  return (a.year ?? 0) - (b.year ?? 0)
}

export function sortItems<T extends SortableItem>(items: T[], prefs: SortPrefs): T[] {
  return [...items].sort((a, b) => compareItems(a, b, prefs.field, prefs.dir))
}

// Persisted sort prefs (per storageKey, in localStorage).
export function useSortPrefs(storageKey: string): [SortPrefs, (patch: Partial<SortPrefs>) => void] {
  const [prefs, setPrefs] = useState<SortPrefs>(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) }
    } catch {
      /* ignore bad/blocked storage */
    }
    return DEFAULT_PREFS
  })

  const update = useCallback((patch: Partial<SortPrefs>) => {
    setPrefs((prev) => {
      const next = { ...prev, ...patch }
      try {
        localStorage.setItem(storageKey, JSON.stringify(next))
      } catch {
        /* ignore */
      }
      return next
    })
  }, [storageKey])

  return [prefs, update]
}
