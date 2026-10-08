import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import { getMakerIdarrConfig, type MakerIdarrSyncTarget } from '../api/client'

export const IDARR_SYNC_TARGET_STORAGE_KEY = 'posterflow.idarr.selectedSyncTarget'

type SyncTargetIdentity = Pick<MakerIdarrSyncTarget, 'personal_drive_id' | 'source_dir' | 'label' | 'scope_token'>
type SyncTargetKind = Pick<MakerIdarrSyncTarget, 'is_asset_drive' | 'is_psd_drive'>

function legacyStorageValue(target: SyncTargetIdentity): string {
  const driveId = String(target.personal_drive_id || '').trim()
  const sourceDir = String(target.source_dir || '').trim()
  const label = String(target.label || '').trim()
  return `${driveId}::${sourceDir}::${label}`
}

// Stable id for a sync target; the scope token survives relabels and reorders.
export function getSyncTargetStorageValue(target: SyncTargetIdentity): string {
  const scopeToken = String(target.scope_token || '').trim()
  return scopeToken ? `scope:${scopeToken}` : legacyStorageValue(target)
}

export function getSyncTargetLabel(target: SyncTargetIdentity, index: number): string {
  return String(target.label || '').trim() || String(target.source_dir || '').trim() || `Drive ${index + 1}`
}

export function readStoredSyncTarget(): string | null {
  try {
    return localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)
  } catch {
    return null
  }
}

// -1 when nothing matches. A target selected before its first save has no scope token yet,
// so a stored drive/dir/label id still matches the token-bearing target the server returns.
export function resolveSyncTargetIndex(targets: SyncTargetIdentity[], stored: string | null): number {
  if (!stored) return -1
  const exact = targets.findIndex((target) => getSyncTargetStorageValue(target) === stored)
  if (exact >= 0) return exact
  return targets.findIndex((target) => legacyStorageValue(target) === stored)
}

// Artwork (asset) and PSD sync targets never take poster drops.
export function isPosterSyncTarget(target: SyncTargetKind): boolean {
  return !target.is_asset_drive && !target.is_psd_drive
}

// The stored selection when it is a poster drive, else the first poster drive; -1 when there is none.
export function resolvePosterSyncTargetIndex(targets: (SyncTargetIdentity & SyncTargetKind)[], stored: string | null): number {
  const index = resolveSyncTargetIndex(targets, stored)
  if (index >= 0 && isPosterSyncTarget(targets[index])) return index
  return targets.findIndex(isPosterSyncTarget)
}

export interface IdarrSyncTargetOption {
  index: number
  value: string
  label: string
}

interface Snapshot {
  targets: MakerIdarrSyncTarget[]
  loaded: boolean
  storedValue: string | null
}

// One module-level store so the sidebar picker, the IDarr page and every quick-add picker
// share a single selection and a single config fetch.
let snapshot: Snapshot = { targets: [], loaded: false, storedValue: readStoredSyncTarget() }
const listeners = new Set<() => void>()
let inflight: Promise<void> | null = null
let storageListenerAttached = false

function publish(patch: Partial<Snapshot>) {
  snapshot = { ...snapshot, ...patch }
  listeners.forEach((listener) => listener())
}

export function writeStoredSyncTarget(value: string): void {
  try {
    localStorage.setItem(IDARR_SYNC_TARGET_STORAGE_KEY, value)
  } catch {
    // Storage unavailable; the in-memory selection still updates below.
  }
  if (snapshot.storedValue !== value) publish({ storedValue: value })
}

export function setIdarrSyncTargets(targets: MakerIdarrSyncTarget[]): void {
  publish({ targets: [...targets], loaded: true })
}

export function refreshIdarrSyncTargets(): Promise<void> {
  if (inflight) return inflight
  inflight = Promise.resolve()
    .then(() => getMakerIdarrConfig())
    .then((config) => {
      setIdarrSyncTargets(Array.isArray(config.sync_targets) ? config.sync_targets : [])
    })
    .catch(() => {
      publish({ loaded: true })
    })
    .finally(() => {
      inflight = null
    })
  return inflight
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (!storageListenerAttached && typeof window !== 'undefined') {
    storageListenerAttached = true
    // Another tab changed the selection.
    window.addEventListener('storage', (event) => {
      if (event.key === null || event.key === IDARR_SYNC_TARGET_STORAGE_KEY) {
        publish({ storedValue: readStoredSyncTarget() })
      }
    })
  }
  return () => {
    listeners.delete(listener)
  }
}

const getSnapshot = () => snapshot

export function useIdarrSyncTarget() {
  const { targets, loaded, storedValue } = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  useEffect(() => {
    if (!snapshot.loaded) void refreshIdarrSyncTargets()
  }, [])

  const options = useMemo<IdarrSyncTargetOption[]>(
    () => targets.map((target, index) => ({
      index,
      value: getSyncTargetStorageValue(target),
      label: getSyncTargetLabel(target, index),
    })),
    [targets],
  )

  // A missing or stale stored value falls back to the first target, matching the IDarr page.
  const selectedIndex = useMemo(() => {
    const index = resolveSyncTargetIndex(targets, storedValue)
    if (index >= 0) return index
    return targets.length > 0 ? 0 : -1
  }, [targets, storedValue])

  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : null

  // Poster-only pickers (requests, maker cards) hide artwork and PSD drives; a shared selection
  // that points at one of those falls back to the first poster drive.
  const posterOptions = useMemo(
    () => options.filter((option) => isPosterSyncTarget(targets[option.index])),
    [options, targets],
  )
  const selectedPosterIndex = useMemo(() => resolvePosterSyncTargetIndex(targets, storedValue), [targets, storedValue])
  const selectedPosterOption = selectedPosterIndex >= 0 ? options[selectedPosterIndex] : null

  const setSelectedValue = useCallback((value: string) => {
    writeStoredSyncTarget(value)
  }, [])

  const setSelectedIndex = useCallback((index: number) => {
    const target = snapshot.targets[index]
    if (target) writeStoredSyncTarget(getSyncTargetStorageValue(target))
  }, [])

  return {
    targets,
    options,
    loaded,
    storedValue,
    selectedIndex,
    selectedValue: selectedOption?.value ?? '',
    selectedLabel: selectedOption?.label ?? '',
    selectedTarget: selectedIndex >= 0 ? targets[selectedIndex] : null,
    posterOptions,
    selectedPosterIndex,
    selectedPosterValue: selectedPosterOption?.value ?? '',
    setSelectedValue,
    setSelectedIndex,
    refresh: refreshIdarrSyncTargets,
  }
}
