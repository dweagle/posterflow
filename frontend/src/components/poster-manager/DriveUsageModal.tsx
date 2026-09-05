import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Columns2, Download, Search, X } from 'lucide-react'
import {
  CompareCandidate,
  CompareTarget,
  DriveUsage,
  FallbackItem,
  PosterOverride,
  deletePosterOverride,
  getDriveImageUrl,
  getPosterOverrides,
  savePosterOverride,
} from '../../api/posterManager'
import SortControls from './SortControls'
import { SLOT_LABELS, SLOT_ORDER, sortItems, useSortPrefs } from './itemSort'
import { useToast } from '../Toast'

type UsageView = 'used' | 'outranked'

// Pseudo-drive id for the aggregate "All Drives" view (no override targets there).
export const ALL_DRIVES_ID = '__all__'

type DriveUsageModalProps = {
  drive: DriveUsage
  items: FallbackItem[]
  outrankedItems: FallbackItem[]
  compareForItem?: (item: FallbackItem, target: CompareTarget) => CompareCandidate[]
  availableCountFor?: (item: FallbackItem, target: CompareTarget) => number
  overrideDomain?: 'poster' | 'artwork'
  noun?: string
  onClose: () => void
  onDownload: (mode: UsageView) => void
  // Fired after an override is saved or removed so parents can refresh counts.
  onOverridesChange?: () => void
  // Arrows swapping the modal to the neighboring drive.
  onNavigateDrive?: (delta: number) => void
  hasPrevDrive?: boolean
  hasNextDrive?: boolean
}

const typeBadge = (type: string) => (
  <span className={`unmatched-cat-badge unmatched-cat-badge--${type === 'movie' ? 'movie' : type === 'collection' ? 'collection' : 'series'}`}>
    {type === 'movie' ? 'Movie' : type === 'collection' ? 'Collection' : 'Show'}
  </span>
)

// Lightbox step; season: null = main poster, N = season, undefined = artwork slot.
type PreviewEntry = { file: string; label: string | null; slot?: string; season?: number | null; driveName?: string | null }

// An item's files collapsed to one row; previewFiles = its lightbox order (poster, seasons, slots).
type GroupedItem = FallbackItem & {
  seasons: number[]
  slots: string[]
  hasMain: boolean
  seasonCount: number
  mainFile: string | null
  mainDriveName: string | null
  seasonFiles: { season: number; file: string; driveName: string | null }[]
  slotFiles: { slot: string; file: string; driveName: string | null }[]
  previewFiles: PreviewEntry[]
  driveSources: { name: string; style: string | null }[]
}

function matchesItem(ov: PosterOverride, g: GroupedItem): boolean {
  if (ov.media_type !== g.type) return false
  if (ov.tmdb_id && g.tmdb_id) return ov.tmdb_id === g.tmdb_id
  const clean = g.year ? g.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : g.title
  return ov.title.trim().toLowerCase() === clean.toLowerCase() && (ov.year ?? null) === (g.year ?? null)
}

export default function DriveUsageModal({
  drive,
  items,
  outrankedItems,
  compareForItem,
  availableCountFor,
  overrideDomain = 'poster',
  noun = 'poster',
  onClose,
  onDownload,
  onOverridesChange,
  onNavigateDrive,
  hasPrevDrive = false,
  hasNextDrive = false,
}: DriveUsageModalProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [prefs, setPrefs] = useSortPrefs('driveUsageSort')
  const [view, setView] = useState<UsageView>(items.length > 0 ? 'used' : 'outranked')
  const [previewIndex, setPreviewIndex] = useState<number | null>(null)

  const isAllDrives = drive.drive_id === ALL_DRIVES_ID

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  // Drive switch: close the lightbox and flip an empty view to the populated one.
  useEffect(() => {
    setPreviewIndex(null)
    setView((v) => {
      if (v === 'used' && items.length === 0 && outrankedItems.length > 0) return 'outranked'
      if (v === 'outranked' && outrankedItems.length === 0 && items.length > 0) return 'used'
      return v
    })
    // Keyed on drive change only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drive.drive_id])

  const activeItems = view === 'used' ? items : outrankedItems

  const lowerQuery = searchQuery.trim().toLowerCase()
  const filteredItems = lowerQuery
    ? activeItems.filter((item) => item.title.toLowerCase().includes(lowerQuery))
    : activeItems

  const groupedItems = useMemo<GroupedItem[]>(() => {
    const result: GroupedItem[] = []
    const map = new Map<string, GroupedItem>()
    for (const item of filteredItems) {
      const key = `${item.type}::${item.title}::${item.year}`
      const isMainPoster = !item.slot && (item.type !== 'show' || item.season == null)
      const existing = map.get(key)
      if (existing) {
        // Guards keep All Drives from stacking duplicate seasons; first drive's file wins.
        if (item.type === 'show' && item.season != null && !item.slot) {
          if (!existing.seasons.includes(item.season)) {
            existing.seasons.push(item.season)
            if (item.file) existing.seasonFiles.push({ season: item.season, file: item.file, driveName: item.drive_name ?? null })
          }
        } else if (item.type === 'show' && !item.slot) {
          existing.hasMain = true
          if (item.file && !existing.mainFile) {
            existing.mainFile = item.file
            existing.mainDriveName = item.drive_name ?? null
          }
        }
        if (item.slot && !existing.slots.includes(item.slot)) {
          existing.slots.push(item.slot)
          if (item.file) existing.slotFiles.push({ slot: item.slot, file: item.file, driveName: item.drive_name ?? null })
        }
        if (item.drive_name && !existing.driveSources.some((s) => s.name === item.drive_name)) {
          existing.driveSources.push({ name: item.drive_name, style: item.drive_style ?? null })
        }
      } else {
        const isSeason = item.type === 'show' && item.season != null && !item.slot
        const grouped: GroupedItem = {
          ...item,
          season: null,
          seasons: isSeason ? [item.season!] : [],
          slots: item.slot ? [item.slot] : [],
          hasMain: item.type !== 'show' || item.season == null,
          seasonCount: 0,
          mainFile: isMainPoster && item.file ? item.file : null,
          mainDriveName: isMainPoster && item.file ? item.drive_name ?? null : null,
          seasonFiles: isSeason && item.file ? [{ season: item.season!, file: item.file, driveName: item.drive_name ?? null }] : [],
          slotFiles: item.slot && item.file ? [{ slot: item.slot, file: item.file, driveName: item.drive_name ?? null }] : [],
          previewFiles: [],
          driveSources: item.drive_name ? [{ name: item.drive_name, style: item.drive_style ?? null }] : [],
        }
        map.set(key, grouped)
        result.push(grouped)
      }
    }
    for (const item of result) {
      item.seasons.sort((a, b) => a - b)
      item.seasonCount = item.seasons.length
      item.slots.sort((a, b) => SLOT_ORDER.indexOf(a) - SLOT_ORDER.indexOf(b))
      item.seasonFiles.sort((a, b) => a.season - b.season)
      item.slotFiles.sort((a, b) => SLOT_ORDER.indexOf(a.slot) - SLOT_ORDER.indexOf(b.slot))
      const files: PreviewEntry[] = []
      if (item.mainFile) files.push({ file: item.mainFile, label: item.seasonFiles.length > 0 ? 'Poster' : null, season: null, driveName: item.mainDriveName })
      item.seasonFiles.forEach((sf) => files.push({ file: sf.file, label: sf.season === 0 ? 'Specials' : `Season ${sf.season}`, season: sf.season, driveName: sf.driveName }))
      item.slotFiles.forEach((sf) => files.push({ file: sf.file, label: SLOT_LABELS[sf.slot] ?? sf.slot, slot: sf.slot, driveName: sf.driveName }))
      item.previewFiles = files
    }
    return result
  }, [filteredItems])

  // Filter inside the memo: a fresh identity would reset the chunked row count.
  const sortedItems = useMemo(() => {
    const typeFiltered = prefs.group === 'all' ? groupedItems : groupedItems.filter((i) => i.type === prefs.group)
    return sortItems(typeFiltered, prefs)
  }, [groupedItems, prefs])

  // Flat lightbox order over the visible rows; arrows continue into the next entry.
  const previewSequence = useMemo(
    () =>
      sortedItems.flatMap((g) =>
        g.previewFiles.map((pf) => ({
          ...pf,
          title: g.year ? g.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : g.title,
          year: g.year,
          group: g,
        }))
      ),
    [sortedItems]
  )
  // Artwork-only type filter for the lightbox arrows.
  const [previewTypeFilter, setPreviewTypeFilter] = useState('all')
  const activeSequence = useMemo(
    () =>
      previewTypeFilter === 'all' || overrideDomain !== 'artwork'
        ? previewSequence
        : previewSequence.filter((e) => e.slot === previewTypeFilter),
    [previewSequence, previewTypeFilter, overrideDomain]
  )
  const preview = previewIndex != null ? activeSequence[previewIndex] ?? null : null
  const applyPreviewFilter = (f: string) => {
    const seq = f === 'all' ? previewSequence : previewSequence.filter((e) => e.slot === f)
    if (seq.length === 0) return
    // Stay on the current item where possible.
    const idx = preview
      ? seq.findIndex((e) => (f === 'all' ? e.file === preview.file : e.group === preview.group))
      : -1
    setPreviewTypeFilter(f)
    setPreviewIndex(idx >= 0 ? idx : 0)
  }

  // User overrides ("use this drive's poster for this item") — loaded once per modal open.
  const { showToast } = useToast()
  const [overrides, setOverrides] = useState<PosterOverride[]>([])
  const [overrideBusy, setOverrideBusy] = useState(false)
  useEffect(() => {
    getPosterOverrides().then(setOverrides).catch(() => {})
  }, [])

  type OverrideEntry = { group: GroupedItem; season?: number | null; slot?: string | null }
  const slotOverrideFor = (entry: OverrideEntry) =>
    overrides.find((o) =>
      (o.domain ?? 'poster') === overrideDomain &&
      o.scope === 'slot' &&
      (o.season ?? null) === (entry.season ?? null) &&
      (o.slot ?? null) === (entry.slot ?? null) &&
      matchesItem(o, entry.group))
  const setOverrideFor = (entry: OverrideEntry) =>
    overrides.find((o) => (o.domain ?? 'poster') === overrideDomain && o.scope === 'set' && matchesItem(o, entry.group))

  const overridesRef = useRef(overrides)
  overridesRef.current = overrides

  const toggleOverride = useCallback(async (
    entry: OverrideEntry,
    scope: 'slot' | 'set',
    driveId: string = drive.drive_id,
  ) => {
    const existing = overridesRef.current.find((o) =>
      (o.domain ?? 'poster') === overrideDomain &&
      o.scope === scope &&
      (scope === 'set' || ((o.season ?? null) === (entry.season ?? null) && (o.slot ?? null) === (entry.slot ?? null))) &&
      matchesItem(o, entry.group))
    setOverrideBusy(true)
    try {
      if (existing && existing.drive_id === driveId) {
        await deletePosterOverride(existing.id)
        setOverrides((prev) => prev.filter((o) => o.id !== existing.id))
      } else {
        const g = entry.group
        const saved = await savePosterOverride({
          media_type: g.type,
          tmdb_id: g.tmdb_id ?? null,
          tvdb_id: g.tvdb_id ?? null,
          imdb_id: g.imdb_id ?? null,
          title: g.year ? g.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : g.title,
          year: g.year ?? null,
          domain: overrideDomain,
          scope,
          season: overrideDomain === 'poster' && scope === 'slot' ? entry.season ?? null : null,
          slot: overrideDomain === 'artwork' && scope === 'slot' ? entry.slot ?? null : null,
          drive_id: driveId,
        })
        setOverrides((prev) => [...prev.filter((o) => o.id !== saved.id), saved])
      }
      onOverridesChange?.()
    } catch {
      showToast('Failed to save poster override', 'error')
    } finally {
      setOverrideBusy(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drive.drive_id, overrideDomain, showToast, onOverridesChange])

  // Side-by-side compare: this item's used file next to every drive's unused candidate.
  const [compare, setCompare] = useState<{ item: GroupedItem; season: number | null; slot: string | null } | null>(null)
  // Big preview for a clicked compare image; Escape closes it before the compare modal.
  const [comparePreview, setComparePreview] = useState<{ file: string; label: string; slot: string | null } | null>(null)
  useEffect(() => {
    if (!compare) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (comparePreview) setComparePreview(null)
        else setCompare(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [compare, comparePreview])
  useEffect(() => setCompare(null), [drive.drive_id])
  useEffect(() => setComparePreview(null), [compare])

  // Imperative fixed-position tooltip: delayed, mouse-only, no row re-renders.
  const rowTipRef = useRef<HTMLDivElement | null>(null)
  const rowTipTimer = useRef<number | null>(null)
  const showRowTip = useCallback((e: React.PointerEvent<HTMLElement>, text: string) => {
    if (e.pointerType !== 'mouse') return
    const rect = e.currentTarget.getBoundingClientRect()
    if (rowTipTimer.current) window.clearTimeout(rowTipTimer.current)
    rowTipTimer.current = window.setTimeout(() => {
      const el = rowTipRef.current
      if (!el) return
      el.textContent = text
      const below = window.innerHeight - rect.bottom > 96
      el.style.top = below ? `${rect.bottom + 6}px` : 'auto'
      el.style.bottom = below ? 'auto' : `${window.innerHeight - rect.top + 6}px`
      el.style.right = `${window.innerWidth - rect.right}px`
      el.style.display = 'block'
    }, 600)
  }, [])
  const hideRowTip = useCallback(() => {
    if (rowTipTimer.current) {
      window.clearTimeout(rowTipTimer.current)
      rowTipTimer.current = null
    }
    if (rowTipRef.current) rowTipRef.current.style.display = 'none'
  }, [])
  useEffect(() => hideRowTip(), [view, drive.drive_id, hideRowTip])

  const stepPreview = (delta: number) => {
    setPreviewIndex((i) => (i == null ? i : Math.min(activeSequence.length - 1, Math.max(0, i + delta))))
  }

  useEffect(() => {
    if (previewIndex == null) return
    const last = activeSequence.length - 1
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') setPreviewIndex((i) => (i == null ? i : Math.max(0, i - 1)))
      else if (e.key === 'ArrowRight') setPreviewIndex((i) => (i == null ? i : Math.min(last, i + 1)))
      else if (e.key === 'Escape') setPreviewIndex(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [previewIndex, activeSequence.length])

  const titleCount = useMemo(() => {
    return new Set(activeItems.map((it) => `${it.type}::${it.title}::${it.year}`)).size
  }, [activeItems])

  // Rows mount in chunks as the list scrolls, so huge drives open instantly.
  const ROW_CHUNK = 60
  const [visibleCount, setVisibleCount] = useState(ROW_CHUNK)
  useEffect(() => setVisibleCount(ROW_CHUNK), [sortedItems])
  const handleListScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    hideRowTip()
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 600) {
      setVisibleCount((c) => (c < sortedItems.length ? c + ROW_CHUNK : c))
    }
  }, [hideRowTip, sortedItems.length])

  const styleKey = (drive.style ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')

  // Row compare-button predicate: the default target, or null without 2+ candidates.
  const compareDefaultTarget = (it: GroupedItem): { season: number | null; slot: string | null } | null => {
    const isArt = overrideDomain === 'artwork'
    if (isArt ? it.slotFiles.length === 0 : it.mainFile == null && it.seasonFiles.length === 0) return null
    const target = isArt
      ? { season: null, slot: null }
      : { season: it.mainFile != null ? null : it.seasonFiles[0]?.season ?? null, slot: null }
    const count = !availableCountFor
      ? 2
      : isArt
        ? Math.max(0, ...it.slots.map((s) => availableCountFor(it, { season: null, slot: s })))
        : availableCountFor(it, target)
    return count >= 2 ? target : null
  }

  // Memoized so nav/tooltip work never re-renders the cards.
  const renderedRows = useMemo(() => (
    sortedItems.slice(0, visibleCount).map((item) => {
              const cleanTitle = item.year ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim() : item.title
              const badgeClass = item.type === 'movie' ? 'movie' : item.type === 'collection' ? 'collection' : 'series'
              const badgeLabel = item.type === 'movie' ? 'Movie' : item.type === 'collection' ? 'Collection' : 'Show'
              // Card button covers the whole set (shows/artwork) or the single poster.
              const isPosterRow = item.mainFile != null || item.seasonFiles.length > 0
              const isArtworkRow = item.slotFiles.length > 0
              const overridableRow = overrideDomain === 'artwork' ? isArtworkRow : isPosterRow
              const rowScope: 'slot' | 'set' = overrideDomain === 'artwork' || item.type === 'show' ? 'set' : 'slot'
              const rowEntry = { group: item, season: null, slot: null }
              const rowOv = rowScope === 'set' ? setOverrideFor(rowEntry) : slotOverrideFor(rowEntry)
              const rowOnDrive = rowOv?.drive_id === drive.drive_id
              const showRowBtn = !isAllDrives && overridableRow && (view === 'outranked' || rowOnDrive)
              return (
                <div key={`${item.type}::${item.title}::${item.year}`} className="unmatched-item drive-usage-item">
                  {(() => {
                    const thumbFile = item.previewFiles[0]?.file ?? item.file
                    if (!thumbFile) return null
                    return (
                      <button
                        type="button"
                        className="drive-usage-thumb-btn"
                        onClick={() => {
                          setPreviewTypeFilter('all')
                          const idx = previewSequence.findIndex((e) => e.file === thumbFile)
                          setPreviewIndex(idx >= 0 ? idx : null)
                        }}
                        title="Preview"
                      >
                        <img src={getDriveImageUrl(thumbFile)} alt="" className="drive-usage-thumb" loading="lazy" />
                      </button>
                    )
                  })()}
                  <div className="drive-usage-item-body">
                    <div className="unmatched-item-top">
                      <div className="unmatched-item-meta">
                        <span className="item-title">{cleanTitle}</span>
                        {item.year && <span className="item-year">({item.year})</span>}
                        <span className={`unmatched-cat-badge unmatched-cat-badge--${badgeClass}`}>{badgeLabel}</span>
                      </div>
                    </div>
                    {(item.seasons.length > 0 || item.slots.length > 0 || (isAllDrives && item.mainFile != null)) && (() => {
                      // Badges grouped by source drive: {drive} [its slots/seasons] {drive} [...].
                      type BadgeEntry = { key: string; label: string; file: string | null; tip: string; cls: string }
                      const groups: { driveName: string | null; entries: BadgeEntry[] }[] = []
                      const push = (driveName: string | null, entry: BadgeEntry) => {
                        let group = groups.find((g) => g.driveName === driveName)
                        if (!group) {
                          group = { driveName, entries: [] }
                          groups.push(group)
                        }
                        group.entries.push(entry)
                      }
                      // All Drives: every item gets a main-poster chip with drive attribution.
                      if (isAllDrives && item.mainFile) {
                        push(item.mainDriveName, {
                          key: 'main', label: 'Poster', file: item.mainFile, tip: 'Preview the poster',
                          cls: 'unmatched-cat-badge--season',
                        })
                      }
                      item.seasons.forEach((s) => {
                        const label = s === 0 ? 'Specials' : `Season ${s}`
                        const sf = item.seasonFiles.find((f) => f.season === s)
                        push(sf?.driveName ?? null, {
                          key: `s${s}`, label, file: sf?.file ?? null, tip: `Preview the ${label} poster`,
                          cls: 'unmatched-cat-badge--season',
                        })
                      })
                      item.slots.forEach((s) => {
                        const label = SLOT_LABELS[s] ?? s
                        const sf = item.slotFiles.find((f) => f.slot === s)
                        push(sf?.driveName ?? null, {
                          key: `a${s}`, label, file: sf?.file ?? null, tip: `Preview the ${label.toLowerCase()}`,
                          cls: 'unmatched-cat-badge--artwork',
                        })
                      })
                      const styleOf = (name: string) => item.driveSources.find((d) => d.name === name)?.style
                      return (
                        <div className="unmatched-seasons-row">
                          {groups.map((group) => (
                            <Fragment key={group.driveName ?? 'none'}>
                              {isAllDrives && group.driveName && (
                                <span
                                  className={`drive-usage-source-badge drive-usage-source-badge--${(styleOf(group.driveName) ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')}`}
                                  title={group.driveName}
                                >
                                  {group.driveName}
                                </span>
                              )}
                              {group.entries.map((entry) =>
                                entry.file ? (
                                  <button
                                    key={entry.key}
                                    type="button"
                                    className={`unmatched-cat-badge ${entry.cls} drive-usage-season-badge`}
                                    onClick={() => {
                                      setPreviewTypeFilter('all')
                                      const idx = previewSequence.findIndex((e) => e.file === entry.file)
                                      if (idx >= 0) setPreviewIndex(idx)
                                    }}
                                    title={entry.tip}
                                  >
                                    {entry.label}
                                  </button>
                                ) : (
                                  <span key={entry.key} className={`unmatched-cat-badge ${entry.cls}`}>{entry.label}</span>
                                )
                              )}
                            </Fragment>
                          ))}
                        </div>
                      )
                    })()}
                  </div>
                  <div className="drive-usage-item-actions">
                    {showRowBtn && (
                      <button
                        type="button"
                        className={`drive-usage-view-tab drive-usage-override-btn drive-usage-row-use${rowOnDrive ? ' active' : ''}`}
                        onClick={() => { hideRowTip(); toggleOverride(rowEntry, rowScope) }}
                        disabled={overrideBusy}
                        onPointerEnter={(e) => showRowTip(e, rowOnDrive
                          ? 'Stop overriding - the next rename goes back to normal priority'
                          : overrideDomain === 'artwork'
                            ? `Use ${drive.name}'s artwork for this item on the next rename`
                            : rowScope === 'set'
                              ? `Use ${drive.name}'s poster and every season it offers for this show`
                              : `Use this ${drive.name} poster on the next rename instead of the higher-priority drive's`)}
                        onPointerLeave={hideRowTip}
                      >
                        {rowOnDrive
                          ? rowScope === 'set' ? '✓ Using set' : '✓ Using'
                          : rowScope === 'set' ? 'Use set' : 'Use'}
                      </button>
                    )}
                    {compareForItem && (overrideDomain === 'artwork' ? isArtworkRow : isPosterRow) && (() => {
                      // Artwork opens the whole-set view; posters open on main/first season.
                      const isArt = overrideDomain === 'artwork'
                      const target: CompareTarget = isArt
                        ? { season: null, slot: null }
                        : { season: item.mainFile != null ? null : item.seasonFiles[0]?.season ?? null, slot: null }
                      const compareCount = !availableCountFor
                        ? 2
                        : isArt
                          ? Math.max(0, ...item.slots.map((s) => availableCountFor(item, { season: null, slot: s })))
                          : availableCountFor(item, target)
                      if (compareCount < 2) return null
                      return (
                        <button
                          type="button"
                          className="drive-usage-view-tab drive-usage-row-use drive-usage-compare-btn"
                          onClick={() => { hideRowTip(); setCompare({ item, season: target.season, slot: target.slot }) }}
                          onPointerEnter={(e) => showRowTip(e, `Compare ${compareCount} available ${noun}s for this item side by side`)}
                          onPointerLeave={hideRowTip}
                        >
                          <Columns2 size={13} />
                          {compareCount}
                        </button>
                      )
                    })()}
                  </div>
                </div>
              )
            })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [sortedItems, visibleCount, overrides, overrideBusy, view, isAllDrives, previewSequence, drive.drive_id, drive.name, compareForItem, availableCountFor, overrideDomain, noun, showRowTip, hideRowTip, toggleOverride])

  return (
    <div className="modal-overlay" onClick={handleOverlayClick}>
      <div className="modal-content schedule-modal list-items-modal drive-usage-modal">
        <div className="modal-header">
          <h2 className="drive-usage-modal-title">
            {onNavigateDrive && (
              <span className="drive-usage-drive-nav-group">
                <button
                  type="button"
                  className="drive-usage-drive-nav"
                  onClick={() => onNavigateDrive(-1)}
                  disabled={!hasPrevDrive}
                  title="Previous drive"
                >
                  <ChevronLeft size={17} />
                </button>
                <button
                  type="button"
                  className="drive-usage-drive-nav"
                  onClick={() => onNavigateDrive(1)}
                  disabled={!hasNextDrive}
                  title="Next drive"
                >
                  <ChevronRight size={17} />
                </button>
              </span>
            )}
            <span>
              {drive.name}{drive.style && <> <span className={`style-badge style-${styleKey}`}>{drive.style}</span></>}
            </span>
          </h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>

        <div className="modal-body">
          <div className="drive-usage-view-tabs" role="group" aria-label="Used or not used">
            <button
              type="button"
              className={`drive-usage-view-tab${view === 'used' ? ' active' : ''}`}
              onClick={() => setView('used')}
            >
              Used ({items.length.toLocaleString()})
            </button>
            <button
              type="button"
              className={`drive-usage-view-tab${view === 'outranked' ? ' active' : ''}`}
              onClick={() => setView('outranked')}
            >
              Not used ({outrankedItems.length.toLocaleString()})
            </button>
          </div>

          <p className="style-fallback-modal-subtitle">
            {view === 'used' ? (
              <>
                {activeItems.length.toLocaleString()} {noun}{activeItems.length !== 1 ? 's' : ''} across{' '}
                {titleCount.toLocaleString()} title{titleCount !== 1 ? 's' : ''} came from{' '}
                {isAllDrives ? 'your drives' : 'this drive'} during the last rename.
              </>
            ) : (
              <>
                {activeItems.length.toLocaleString()} {noun}{activeItems.length !== 1 ? 's' : ''} across{' '}
                {titleCount.toLocaleString()} title{titleCount !== 1 ? 's' : ''} matched from{' '}
                {isAllDrives ? 'your drives' : 'this drive'}, but a higher-priority drive was used.
                {!isAllDrives && ' Moving this drive up the priority list would use these.'}
              </>
            )}
          </p>

          <div className="unmatched-search-bar">
            <Search size={15} className="search-bar-icon" />
            <input
              type="text"
              className="unmatched-search-input"
              placeholder={`Search ${activeItems.length} ${noun}s…`}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button className="search-clear-btn" onClick={() => setSearchQuery('')} title="Clear search">
                <X size={14} />
              </button>
            )}
          </div>

          <SortControls prefs={prefs} onChange={setPrefs} showSeasons={false} />

          <div className="unmatched-list" onScroll={handleListScroll}>
            <p className="list-count">
              {sortedItems.length !== groupedItems.length
                ? `${sortedItems.length} of ${groupedItems.length} titles`
                : `${groupedItems.length} titles`}
            </p>
            {renderedRows}
          </div>
        </div>

        <div className="modal-footer">
          <div className="modal-footer-actions">
            <button className="btn-secondary" onClick={onClose}>Close</button>
            <button className="btn-primary" onClick={() => onDownload(view)} disabled={activeItems.length === 0}>
              <Download size={14} />
              Download List
            </button>
          </div>
        </div>
      </div>

      {preview && previewIndex != null && (
        <div
          className={`modal-overlay tmdb-poster-preview-overlay drive-usage-preview-overlay${preview.slot ? ' drive-usage-preview-overlay--art' : ''}`}
          onClick={() => setPreviewIndex(null)}
        >
          <div className="tmdb-poster-preview-modal drive-usage-preview" onClick={(e) => e.stopPropagation()}>
            <img
              src={getDriveImageUrl(preview.file)}
              alt="Poster preview"
              className={`tmdb-poster-preview-image${preview.slot ? ` drive-usage-preview-${preview.slot}` : ''}`}
            />
            <div className="drive-usage-preview-caption">
              <span>
                {preview.title}
                {preview.year ? ` (${preview.year})` : ''}
                {preview.label ? ` - ${preview.label}` : ''}
                {preview.driveName ? ` · ${preview.driveName}` : ''}
              </span>
              {typeBadge(preview.group.type)}
              <span className="drive-usage-preview-pos">{previewIndex + 1} / {activeSequence.length}</span>
            </div>
            {!isAllDrives && (overrideDomain === 'poster' ? preview.season !== undefined : preview.slot !== undefined) && (() => {
              const entry = { group: preview.group, season: preview.season ?? null, slot: preview.slot ?? null }
              const slotNoun = overrideDomain === 'artwork'
                ? (SLOT_LABELS[preview.slot ?? ''] ?? 'artwork').toLowerCase()
                : 'poster'
              const slotOv = slotOverrideFor(entry)
              const setOv = setOverrideFor(entry)
              const slotOnDrive = slotOv?.drive_id === drive.drive_id
              const setOnDrive = setOv?.drive_id === drive.drive_id
              const showSlotBtn = view === 'outranked' || slotOnDrive
              const showSetBtn = (overrideDomain === 'artwork' || preview.group.type === 'show') && (view === 'outranked' || setOnDrive)
              if (!showSlotBtn && !showSetBtn) return null
              return (
                <div className="drive-usage-preview-actions">
                  {showSlotBtn && (
                    <button
                      type="button"
                      className={`drive-usage-view-tab drive-usage-override-btn drive-usage-tip${slotOnDrive ? ' active' : ''}`}
                      onClick={() => toggleOverride(entry, 'slot')}
                      disabled={overrideBusy}
                      data-tooltip={slotOnDrive
                        ? 'Stop overriding - the next rename goes back to normal priority'
                        : `Use this ${drive.name} ${slotNoun} on the next rename instead of the higher-priority drive's`}
                    >
                      {slotOnDrive ? `✓ Using this ${slotNoun} - Remove` : `Use this ${slotNoun}`}
                    </button>
                  )}
                  {showSetBtn && (
                    <button
                      type="button"
                      className={`drive-usage-view-tab drive-usage-override-btn drive-usage-tip${setOnDrive ? ' active' : ''}`}
                      onClick={() => toggleOverride(entry, 'set')}
                      disabled={overrideBusy}
                      data-tooltip={setOnDrive
                        ? 'Stop overriding the whole set - the next rename goes back to normal priority'
                        : overrideDomain === 'artwork'
                          ? `Use ${drive.name}'s artwork set for this item`
                          : `Use ${drive.name}'s poster and every season it offers for this show`}
                    >
                      {setOnDrive
                        ? '✓ Using whole set - Remove'
                        : overrideDomain === 'artwork' ? 'Use artwork set from this drive' : 'Use whole set from this drive'}
                    </button>
                  )}
                </div>
              )
            })()}
          </div>
          <div className="drive-usage-preview-nav-row" onClick={(e) => e.stopPropagation()}>
            {overrideDomain === 'artwork' && (
              <div className="drive-usage-preview-filter">
                {['all', ...SLOT_ORDER].map((f) => (
                  <button
                    key={f}
                    type="button"
                    className={`drive-usage-view-tab${previewTypeFilter === f ? ' active' : ''}`}
                    onClick={() => applyPreviewFilter(f)}
                    disabled={f !== 'all' && !previewSequence.some((e) => e.slot === f)}
                  >
                    {f === 'all' ? 'All' : SLOT_LABELS[f] ?? f}
                  </button>
                ))}
              </div>
            )}
            <div className="drive-usage-preview-nav-arrows">
              <button
                type="button"
                className="drive-usage-preview-nav prev"
                onClick={() => stepPreview(-1)}
                disabled={previewIndex === 0}
                title="Previous"
              >
                <ChevronLeft size={26} />
              </button>
              <button
                type="button"
                className="drive-usage-preview-nav next"
                onClick={() => stepPreview(1)}
                disabled={previewIndex === activeSequence.length - 1}
                title="Next"
              >
                <ChevronRight size={26} />
              </button>
            </div>
          </div>
          {/* Preload neighbors so arrowing feels instant. */}
          {activeSequence[previewIndex + 1] && (
            <img src={getDriveImageUrl(activeSequence[previewIndex + 1].file)} alt="" style={{ display: 'none' }} />
          )}
          {previewIndex > 0 && activeSequence[previewIndex - 1] && (
            <img src={getDriveImageUrl(activeSequence[previewIndex - 1].file)} alt="" style={{ display: 'none' }} />
          )}
        </div>
      )}

      <div className="drive-usage-float-tip" ref={rowTipRef} style={{ display: 'none' }} />

      {compare && compareForItem && (() => {
        const cleanTitle = compare.item.year
          ? compare.item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
          : compare.item.title
        const candidates = compareForItem(compare.item, { season: compare.season, slot: compare.slot })
        const entry = { group: compare.item, season: compare.season, slot: compare.slot }
        // Whole-set view: every drive's pieces stacked in one column.
        const setColumns = overrideDomain !== 'artwork' ? [] : (() => {
          const byDrive = new Map<string, { name: string; pieces: Map<string, { file: string; used: boolean }> }>()
          for (const s of SLOT_ORDER) {
            for (const c of compareForItem(compare.item, { season: null, slot: s })) {
              const d = byDrive.get(c.drive_id) ?? { name: c.drive_name, pieces: new Map() }
              d.pieces.set(s, { file: c.file, used: c.used })
              byDrive.set(c.drive_id, d)
            }
          }
          return [...byDrive.entries()].map(([drive_id, d]) => ({ drive_id, ...d }))
        })()
        // Step to the nearest item that also has candidates to compare.
        const itemKey = (g: GroupedItem) => `${g.type}::${g.title}::${g.year}`
        const curIdx = sortedItems.findIndex((g) => itemKey(g) === itemKey(compare.item))
        const findComparable = (dir: number) => {
          for (let i = curIdx + dir; i >= 0 && i < sortedItems.length; i += dir) {
            const t = compareDefaultTarget(sortedItems[i])
            if (t) return { item: sortedItems[i], ...t }
          }
          return null
        }
        const prevComparable = findComparable(-1)
        const nextComparable = findComparable(1)
        const slotOv = slotOverrideFor(entry)
        const setOv = setOverrideFor(entry)
        const slotNoun = compare.slot ? (SLOT_LABELS[compare.slot] ?? compare.slot).toLowerCase() : 'poster'
        const artworkSetView = overrideDomain === 'artwork' && compare.slot == null
        const slotLabel = artworkSetView
          ? 'Artwork set'
          : compare.slot
            ? SLOT_LABELS[compare.slot] ?? compare.slot
            : compare.season == null ? null : compare.season === 0 ? 'Specials' : `Season ${compare.season}`
        return (
          <>
          <div className="modal-overlay drive-usage-compare-overlay" onClick={() => setCompare(null)}>
            <div className="drive-usage-compare-modal" onClick={(e) => e.stopPropagation()}>
              <div className="drive-usage-compare-header">
                <span className="drive-usage-compare-title">
                  {cleanTitle}
                  {compare.item.year ? ` (${compare.item.year})` : ''}
                  {slotLabel ? ` - ${slotLabel}` : ''}
                </span>
                {typeBadge(compare.item.type)}
                <button className="modal-close" onClick={() => setCompare(null)}>×</button>
              </div>
              {overrideDomain === 'artwork' && compare.item.slots.length > 0 && (
                <div className="drive-usage-compare-slots">
                  <button
                    type="button"
                    className={`drive-usage-view-tab${compare.slot == null ? ' active' : ''}`}
                    onClick={() => setCompare({ item: compare.item, season: null, slot: null })}
                  >
                    All
                  </button>
                  {compare.item.slots.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`drive-usage-view-tab${compare.slot === s ? ' active' : ''}`}
                      onClick={() => setCompare({ item: compare.item, season: null, slot: s })}
                    >
                      {SLOT_LABELS[s] ?? s}
                    </button>
                  ))}
                </div>
              )}
              {overrideDomain === 'poster' && compare.item.type !== 'show' && (
                <div className="drive-usage-compare-slots drive-usage-compare-slots-ghost" aria-hidden="true">
                  <button type="button" className="drive-usage-view-tab" tabIndex={-1}>Poster</button>
                </div>
              )}
              {overrideDomain === 'poster' && compare.item.type === 'show' && (
                <div className="drive-usage-compare-slots">
                  <button
                    type="button"
                    className={`drive-usage-view-tab${compare.season == null ? ' active' : ''}`}
                    onClick={() => setCompare({ item: compare.item, season: null, slot: null })}
                  >
                    Poster
                  </button>
                  {compare.item.seasons.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`drive-usage-view-tab${compare.season === s ? ' active' : ''}`}
                      onClick={() => setCompare({ item: compare.item, season: s, slot: null })}
                    >
                      {s === 0 ? 'Specials' : `S${s}`}
                    </button>
                  ))}
                </div>
              )}
              {artworkSetView ? (
                <div className="drive-usage-compare-row drive-usage-compare-setrow">
                  {setColumns.map((col) => {
                    const setActive = setOv?.drive_id === col.drive_id
                    return (
                      <div key={col.drive_id} className="drive-usage-compare-col drive-usage-compare-setcol">
                        {SLOT_ORDER.map((s) => {
                          const piece = col.pieces.get(s)
                          const pieceEntry = { group: compare.item, season: null, slot: s }
                          const pieceActive = slotOverrideFor(pieceEntry)?.drive_id === col.drive_id
                          return (
                            <div key={s} className="drive-usage-compare-piece">
                              <span className="drive-usage-compare-piece-label">
                                <span>
                                  {SLOT_LABELS[s] ?? s}
                                  {piece?.used && <span className="drive-usage-compare-piece-used"> · in use</span>}
                                </span>
                                {piece && !piece.used && (
                                  <button
                                    type="button"
                                    className={`drive-usage-view-tab drive-usage-override-btn drive-usage-row-use${pieceActive ? ' active' : ''}`}
                                    onClick={() => { hideRowTip(); toggleOverride(pieceEntry, 'slot', col.drive_id) }}
                                    disabled={overrideBusy}
                                    onPointerEnter={(e) => showRowTip(e, pieceActive
                                      ? 'Stop overriding - the next rename goes back to normal priority'
                                      : `Use ${col.name}'s ${(SLOT_LABELS[s] ?? s).toLowerCase()} for this item on the next rename instead of the higher-priority drive's`)}
                                    onPointerLeave={hideRowTip}
                                  >
                                    {pieceActive ? '✓ Using' : 'Use'}
                                  </button>
                                )}
                              </span>
                              {piece ? (
                                <img
                                  src={getDriveImageUrl(piece.file)}
                                  alt=""
                                  className={`drive-usage-compare-piece-img piece-${s}${piece.used ? ' in-use' : ''}`}
                                  loading="lazy"
                                  onClick={() => setComparePreview({ file: piece.file, label: `${col.name} - ${SLOT_LABELS[s] ?? s}`, slot: s })}
                                />
                              ) : (
                                <div className={`drive-usage-compare-piece-img piece-${s} drive-usage-compare-empty`}>
                                  not offered
                                </div>
                              )}
                            </div>
                          )
                        })}
                        <div className="drive-usage-compare-label" title={col.name}>{col.name}</div>
                        <button
                          type="button"
                          className={`drive-usage-view-tab drive-usage-override-btn${setActive ? ' active' : ''}`}
                          onClick={() => { hideRowTip(); toggleOverride(entry, 'set', col.drive_id) }}
                          disabled={overrideBusy}
                          onPointerEnter={(e) => showRowTip(e, setActive
                            ? 'Stop overriding the whole set - the next rename goes back to normal priority'
                            : `Use ${col.name}'s artwork set - every piece it offers - on the next rename`)}
                          onPointerLeave={hideRowTip}
                        >
                          {setActive ? '✓ Using set - Remove' : 'Use set'}
                        </button>
                      </div>
                    )
                  })}
                  {setColumns.length === 0 && (
                    <p className="drive-usage-hint">No artwork found for this item on the last rename.</p>
                  )}
                </div>
              ) : (
              <div className="drive-usage-compare-row">
                {(overrideDomain === 'artwork'
                  ? setColumns.map((col) => ({
                      key: col.drive_id,
                      name: col.name,
                      c: candidates.find((x) => x.drive_id === col.drive_id) ?? null,
                    }))
                  : candidates.map((c) => ({ key: c.drive_id, name: c.drive_name, c: c as CompareCandidate | null }))
                ).map(({ key, name, c }) => {
                  const slotActive = c != null && slotOv?.drive_id === c.drive_id
                  const setActive = c != null && setOv?.drive_id === c.drive_id
                  return (
                    <div key={key} className="drive-usage-compare-col">
                      {c ? (
                        <img
                          src={getDriveImageUrl(c.file)}
                          alt=""
                          className={`drive-usage-compare-img${compare.slot ? ` compare-img-${compare.slot}` : ''}`}
                          loading="lazy"
                          onClick={() => setComparePreview({ file: c.file, label: slotLabel ? `${name} - ${slotLabel}` : name, slot: compare.slot })}
                        />
                      ) : (
                        <div className={`drive-usage-compare-img${compare.slot ? ` compare-img-${compare.slot}` : ''} drive-usage-compare-empty`}>
                          not offered
                        </div>
                      )}
                      <div className="drive-usage-compare-label" title={name}>{name}</div>
                      {c == null ? null : c.used ? (
                        <span className="drive-usage-compare-used">In use</span>
                      ) : (
                        <div className="drive-usage-compare-actions">
                          <button
                            type="button"
                            className={`drive-usage-view-tab drive-usage-override-btn${slotActive ? ' active' : ''}`}
                            onClick={() => { hideRowTip(); toggleOverride(entry, 'slot', c.drive_id) }}
                            disabled={overrideBusy}
                            onPointerEnter={(e) => showRowTip(e, slotActive
                              ? 'Stop overriding - the next rename goes back to normal priority'
                              : `Use ${name}'s ${slotNoun} on the next rename instead of the higher-priority drive's`)}
                            onPointerLeave={hideRowTip}
                          >
                            {slotActive ? '✓ Using - Remove' : `Use this ${slotNoun}`}
                          </button>
                          {(overrideDomain === 'artwork' || compare.item.type === 'show') && (
                            <button
                              type="button"
                              className={`drive-usage-view-tab drive-usage-override-btn${setActive ? ' active' : ''}`}
                              onClick={() => { hideRowTip(); toggleOverride(entry, 'set', c.drive_id) }}
                              disabled={overrideBusy}
                              onPointerEnter={(e) => showRowTip(e, setActive
                                ? 'Stop overriding the whole set - the next rename goes back to normal priority'
                                : overrideDomain === 'artwork'
                                  ? `Use ${name}'s artwork set - every piece it offers - on the next rename`
                                  : `Use ${name}'s poster and every season it offers for this show`)}
                              onPointerLeave={hideRowTip}
                            >
                              {setActive ? '✓ Using set - Remove' : 'Use set'}
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
                {candidates.length === 0 && (overrideDomain !== 'artwork' || setColumns.length === 0) && (
                  <p className="drive-usage-hint">No {noun}s found for this slot on the last rename.</p>
                )}
              </div>
              )}
            </div>
            <div className="drive-usage-preview-nav-row" onClick={(e) => e.stopPropagation()}>
              <button
                type="button"
                className="drive-usage-preview-nav prev"
                onClick={() => prevComparable && setCompare({ item: prevComparable.item, season: prevComparable.season, slot: prevComparable.slot })}
                disabled={!prevComparable}
                title="Previous comparable item"
              >
                <ChevronLeft size={22} />
              </button>
              <button
                type="button"
                className="drive-usage-preview-nav next"
                onClick={() => nextComparable && setCompare({ item: nextComparable.item, season: nextComparable.season, slot: nextComparable.slot })}
                disabled={!nextComparable}
                title="Next comparable item"
              >
                <ChevronRight size={22} />
              </button>
            </div>
          </div>
          {comparePreview && (
            <div
              className="modal-overlay tmdb-poster-preview-overlay drive-usage-compare-preview-overlay"
              onClick={() => setComparePreview(null)}
            >
              <div className="tmdb-poster-preview-modal drive-usage-preview">
                <img
                  src={getDriveImageUrl(comparePreview.file)}
                  alt="Preview"
                  className={`tmdb-poster-preview-image${comparePreview.slot ? ` drive-usage-preview-${comparePreview.slot}` : ''}`}
                />
                <div className="drive-usage-preview-caption">
                  <span>{comparePreview.label}</span>
                </div>
              </div>
            </div>
          )}
          </>
        )
      })()}
    </div>
  )
}
