import { useEffect, useState } from 'react'
import { Pin, X } from 'lucide-react'
import DriveSearchPanel from './DriveSearchPanel'
import {
  PosterOverride,
  deletePosterOverride,
  getPosterOverrides,
  savePosterOverride,
  searchLibraryItems,
} from '../api/posterManager'
import SeasonSlotChips from './poster-manager/SeasonSlotChips'
import {
  ArtSlot,
  DrivePosterPick,
  FileBadge,
  OverrideTarget,
  libraryItemMatchesTarget,
  overrideMatchesTarget,
  overridePayloadFor,
  overrideTargetLabel,
  pickSlot,
  pinnedFileBadges,
} from '../utils/posterOverrideTarget'
import { useToast } from './Toast'
import './PosterDriveSearchModal.css'

export type InUseFile = { file: string; season: number | null; slot: ArtSlot | null }

type PosterDriveSearchModalProps = {
  /** Pre-fill the search box (usually the title being worked on). */
  initialQuery?: string
  /** Drive Usage only: every result gets a "Use" button that pins that file as the item's
   *  override (poster/season, or for artwork targets the piece matching the hit's type).
   *  Everywhere else this stays a plain drive search. */
  target?: OverrideTarget
  /** The item's current file per slot from the last rename: the slot being picked reads
   *  "In use" in place of Use. */
  inUse?: InUseFile[]
  onOverrideChange?: () => void
  /** Raise above another modal (e.g. the Drive Usage compare view). */
  stacked?: boolean
  onClose: () => void
}

/**
 * In-place drive poster search so makers can check the synced drives without
 * leaving the Requests card or Maker Tools search. Wraps the shared
 * DriveSearchPanel in a modal; Drive Usage passes a target to turn it into the
 * "pick any poster for this item" override picker.
 */
export default function PosterDriveSearchModal({
  initialQuery = '',
  target,
  inUse = [],
  onOverrideChange,
  stacked = false,
  onClose,
}: PosterDriveSearchModalProps) {
  const { showToast } = useToast()
  const [season, setSeason] = useState<number | null>(target?.season ?? null)
  // The show's seasons per the library; the caller's list is only what the drives offered.
  const [librarySeasons, setLibrarySeasons] = useState<number[] | null>(null)
  const [seasonsLoading, setSeasonsLoading] = useState(false)
  const [overrides, setOverrides] = useState<PosterOverride[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [onClose])

  useEffect(() => {
    if (!target) return
    getPosterOverrides().then(setOverrides).catch(() => {})
    // Keyed on identity fields: callers rebuild the target object on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.media_type, target?.tmdb_id, target?.title, target?.year])

  const isArtwork = (target?.domain ?? 'poster') === 'artwork'
  const isShowPoster = !!target && !isArtwork && target.media_type === 'show'

  useEffect(() => {
    if (!isShowPoster || !target) return
    let cancelled = false
    setSeasonsLoading(true)
    searchLibraryItems(target.title)
      .then((res) => {
        if (cancelled) return
        const hit = res.items.find((i) => libraryItemMatchesTarget(i, target))
        setLibrarySeasons(hit ? hit.seasons : target.seasons ?? [])
      })
      .catch(() => {
        if (!cancelled) setLibrarySeasons(target.seasons ?? [])
      })
      .finally(() => {
        if (!cancelled) setSeasonsLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target?.media_type, target?.tmdb_id, target?.title, target?.year])

  const knownSeasons = librarySeasons ?? target?.seasons ?? []
  useEffect(() => {
    if (librarySeasons && season != null && !librarySeasons.includes(season)) setSeason(null)
  }, [librarySeasons, season])

  const liveTarget: OverrideTarget | null = target ? { ...target, season: isShowPoster ? season : null } : null
  // Artwork targets pin one file per piece, so every slot's current pick counts as "using".
  const currentPicks = liveTarget ? overrides.filter((o) => overrideMatchesTarget(o, liveTarget, o.slot ?? null)) : []
  const pickedFiles = currentPicks.map((o) => o.file).filter((f): f is string => !!f)
  // The slot being picked: its current file reads "In use" where the Use button would be
  // (artwork hits match their own piece); files some other override already takes read "Pinned · …".
  const inUseFiles = inUse
    .filter((u) => (isArtwork ? u.slot != null : u.slot == null && (u.season ?? null) === (season ?? null)))
    .map((u) => u.file)
  const fileBadges: Record<string, FileBadge[]> = liveTarget
    ? pinnedFileBadges(overrides, new Set(currentPicks.map((o) => o.id)))
    : {}

  const handleUse = async (pick: DrivePosterPick) => {
    if (!liveTarget) return
    const slot = isArtwork ? pickSlot(pick) : null
    if (isArtwork && !slot) return
    const current = currentPicks.find((o) => (isArtwork ? (o.slot ?? null) === slot : true))
    const label = overrideTargetLabel(liveTarget, slot)
    setBusy(true)
    try {
      if (current && current.file === pick.file_path) {
        await deletePosterOverride(current.id)
        setOverrides((prev) => prev.filter((o) => o.id !== current.id))
        showToast(`Override removed for ${label}`)
      } else {
        const saved = await savePosterOverride(overridePayloadFor(liveTarget, pick))
        setOverrides((prev) => [...prev.filter((o) => o.id !== saved.id), saved])
        showToast(`Using "${pick.poster_name}" for ${label} on the next rename`)
      }
      onOverrideChange?.()
    } catch {
      showToast('Failed to save poster override', 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`drive-search-modal-overlay${stacked ? ' drive-search-modal-overlay--stacked' : ''}`} onMouseDown={onClose}>
      <div className="drive-search-modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="drive-search-modal-header">
          <h2>{target ? (isArtwork ? 'Pick Artwork' : 'Pick a Poster') : 'Search Drive Assets'}</h2>
          <button type="button" className="drive-search-modal-close" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        </div>
        {liveTarget && (
          <div className="drive-search-modal-target">
            <div className="drive-search-modal-target-line">
              <Pin size={14} />
              <span>
                Pinning for <strong>{overrideTargetLabel({ ...liveTarget, season: null })}</strong>
                {isArtwork && <> · each result fills its own piece (logo, background or square)</>}
                {isShowPoster && (
                  <> · {season == null ? 'show poster' : season === 0 ? 'Specials' : `Season ${season}`}</>
                )}
              </span>
            </div>
            {isShowPoster && (
              <div className="drive-search-modal-seasons">
                <SeasonSlotChips
                  seasons={knownSeasons}
                  value={season}
                  onChange={setSeason}
                  loading={seasonsLoading && librarySeasons == null}
                  posterLabel="Poster"
                />
              </div>
            )}
          </div>
        )}
        <div className="drive-search-modal-body">
          <DriveSearchPanel
            initialQuery={initialQuery}
            autoFocus
            fixedKind={liveTarget ? (isArtwork ? 'artwork' : 'posters') : undefined}
            onUse={liveTarget ? handleUse : undefined}
            useLabel="Use"
            useTitle={isArtwork ? 'Use this file for the item\'s matching piece on the next rename' : 'Use this poster for the item on the next rename'}
            pickedFiles={pickedFiles}
            inUseFiles={inUseFiles}
            fileBadges={fileBadges}
            useBusy={busy}
          />
        </div>
      </div>
    </div>
  )
}
