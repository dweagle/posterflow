import { useMemo } from 'react'
import { CompareCandidate, CompareTarget, DriveUsage, FallbackItem, PosterStyleStats } from '../api/posterManager'

const targetKey = (it: Pick<FallbackItem, 'type' | 'tmdb_id' | 'title' | 'year'>, target: CompareTarget) =>
  `${it.type}::${it.tmdb_id ?? `${it.title}::${it.year ?? ''}`}::${target.season ?? null}::${target.slot ?? ''}`

const itemMatches = (it: FallbackItem, target_item: FallbackItem, target: CompareTarget) =>
  it.type === target_item.type &&
  (it.tmdb_id && target_item.tmdb_id
    ? it.tmdb_id === target_item.tmdb_id
    : it.title === target_item.title && (it.year ?? null) === (target_item.year ?? null)) &&
  (it.season ?? null) === (target.season ?? null) &&
  (it.slot ?? null) === (target.slot ?? null)

function buildUsageData(
  driveUsage: DriveUsage[],
  itemsByDrive: Record<string, FallbackItem[]>,
  outrankedByDrive: Record<string, FallbackItem[]>,
) {
  const itemsForDrive = (driveId: string) => itemsByDrive[driveId] ?? []
  const outrankedForDrive = (driveId: string) => outrankedByDrive[driveId] ?? []

  // Precomputed per item/slot candidate counts so long lists don't scan per card.
  const counts = new Map<string, number>()
  const bump = (it: FallbackItem) => {
    if (!it.file) return
    const key = targetKey(it, { season: it.season ?? null, slot: it.slot ?? null })
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }
  Object.values(itemsByDrive).flat().forEach(bump)
  Object.values(outrankedByDrive).flat().forEach(bump)

  const availableCountFor = (item: FallbackItem, target: CompareTarget) =>
    counts.get(targetKey(item, target)) ?? 0

  // The used candidate plus each drive's unused one, in usage order.
  const compareForItem = (target_item: FallbackItem, target: CompareTarget): CompareCandidate[] => {
    const out: CompareCandidate[] = []
    for (const d of driveUsage) {
      const winner = (itemsByDrive[d.drive_id] ?? []).find((it) => itemMatches(it, target_item, target) && it.file)
      if (winner) {
        out.push({ drive_id: d.drive_id, drive_name: d.name, file: winner.file!, used: true })
        break
      }
    }
    for (const d of driveUsage) {
      const alt = (outrankedByDrive[d.drive_id] ?? []).find((it) => itemMatches(it, target_item, target) && it.file)
      if (alt) out.push({ drive_id: d.drive_id, drive_name: d.name, file: alt.file!, used: false })
    }
    return out
  }

  return { driveUsage, itemsForDrive, outrankedForDrive, compareForItem, availableCountFor }
}

// Poster-scope accessors for the Drive Usage report.
export function usePosterUsageData(styleStats: PosterStyleStats | null) {
  return useMemo(() => {
    const driveUsage = styleStats?.drive_usage ?? []
    const itemsByDrive: Record<string, FallbackItem[]> = {}
    for (const it of Object.values(styleStats?.style_fallbacks ?? {}).flat()) {
      if (it.drive_id) (itemsByDrive[it.drive_id] ??= []).push(it)
    }
    return buildUsageData(driveUsage, itemsByDrive, styleStats?.drive_outranked ?? {})
  }, [styleStats])
}

// Artwork-scope accessors, same shape.
export function useArtworkUsageData(styleStats: PosterStyleStats | null) {
  return useMemo(
    () =>
      buildUsageData(
        styleStats?.artwork_drive_usage ?? [],
        styleStats?.artwork_drive_items ?? {},
        styleStats?.artwork_drive_outranked ?? {},
      ),
    [styleStats]
  )
}
