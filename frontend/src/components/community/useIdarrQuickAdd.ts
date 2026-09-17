import { useEffect, useCallback, useSyncExternalStore } from 'react'
import { getMakerIdarrConfig, getSettings, saveSettings } from '../../api/client'
import { quickAddFilesToIdarr } from '../../utils/idarrQuickAdd'
import { useIdarrSyncTarget, resolveSyncTargetIndex, readStoredSyncTarget, type IdarrSyncTargetOption } from '../../hooks/useIdarrSyncTarget'

export type IdarrTargetOption = IdarrSyncTargetOption

// The toggle lives in the page-level bar while the drop handlers live in each tab, so the
// on/off state is shared across hook instances instead of loaded per instance.
let quickAddEnabled = false
let quickAddLoaded = false
let quickAddInflight: Promise<void> | null = null
const listeners = new Set<() => void>()

function publishEnabled(next: boolean) {
  quickAddEnabled = next
  listeners.forEach((listener) => listener())
}

function loadEnabled() {
  if (quickAddLoaded || quickAddInflight) return
  quickAddInflight = Promise.resolve()
    .then(() => getSettings())
    .then((s) => {
      quickAddLoaded = true
      publishEnabled((s.idarr_quick_add_community || '').trim().toLowerCase() === 'true')
    })
    .catch(() => {})
    .finally(() => {
      quickAddInflight = null
    })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const getEnabled = () => quickAddEnabled

/**
 * Shared "Image Drop also adds to IDarr" behaviour for the community cards.
 * Used by both the Requests and Lists tabs so the maker's local IDarr pipeline
 * works identically whichever tab a poster is dropped on. Also exposes the IDarr
 * sync targets + the selected one, so makers can pick the destination drive/scope
 * without leaving the page (the same selection the sidebar picker and IDarr page use).
 */
export function useIdarrQuickAdd() {
  const enabled = useSyncExternalStore(subscribe, getEnabled, getEnabled)
  const { options: targetOptions, selectedValue: selectedTargetValue, setSelectedValue: setSelectedTarget } = useIdarrSyncTarget()

  useEffect(() => {
    loadEnabled()
  }, [])

  const setEnabled = useCallback((next: boolean) => {
    publishEnabled(next)
    void saveSettings({ idarr_quick_add_community: String(next) })
  }, [])

  const doIdarrUpload = useCallback(async (files: File[]) => {
    try {
      const config = await getMakerIdarrConfig()
      const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
      if (!syncTargets.length) return

      const resolvedIndex = resolveSyncTargetIndex(syncTargets, readStoredSyncTarget())
      // Silent on purpose: a best-effort side channel next to the Discord post.
      await quickAddFilesToIdarr(resolvedIndex >= 0 ? resolvedIndex : 0, files, config)
    } catch {
      // Silently ignore — best-effort maker convenience
    }
  }, [])

  return { enabled, setEnabled, doIdarrUpload, targetOptions, selectedTargetValue, setSelectedTarget }
}
