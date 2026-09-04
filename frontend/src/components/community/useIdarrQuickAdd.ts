import { useState, useEffect, useCallback } from 'react'
import { getMakerIdarrConfig, uploadMakerIdarrFiles, startIdarr, getSettings, saveSettings, type MakerIdarrSyncTarget } from '../../api/client'
import { notifyIdarrTargetedRun } from '../../utils/idarrTargetedRun'

const IDARR_SYNC_TARGET_STORAGE_KEY = 'posterflow.idarr.selectedSyncTarget'

// Same stable identifier IDarr uses to persist the selected sync target, so the
// community picker stays in sync with the IDarr page / sidebar.
function syncTargetStorageValue(target: MakerIdarrSyncTarget): string {
  const scopeToken = String(target.scope_token || '').trim()
  if (scopeToken) return `scope:${scopeToken}`
  const driveId = String(target.personal_drive_id || '').trim()
  const sourceDir = String(target.source_dir || '').trim()
  const label = String(target.label || '').trim()
  return `${driveId}::${sourceDir}::${label}`
}

export interface IdarrTargetOption {
  value: string
  label: string
}

/**
 * Shared "Image Drop also adds to IDarr" behaviour for the community cards.
 * Used by both the Requests and Lists tabs so the maker's local IDarr pipeline
 * works identically whichever tab a poster is dropped on. Also exposes the IDarr
 * sync targets + the selected one, so makers can pick the destination drive/scope
 * without leaving the page (shared with IDarr via the same localStorage key).
 */
export function useIdarrQuickAdd() {
  const [enabled, setEnabledState] = useState(false)
  const [targetOptions, setTargetOptions] = useState<IdarrTargetOption[]>([])
  const [selectedTargetValue, setSelectedTargetValueState] = useState<string>('')

  useEffect(() => {
    getSettings()
      .then((s) => setEnabledState((s.idarr_quick_add_community || '').trim().toLowerCase() === 'true'))
      .catch(() => {})
  }, [])

  // Load the available IDarr sync targets and resolve the current selection.
  useEffect(() => {
    getMakerIdarrConfig()
      .then((config) => {
        const targets = Array.isArray(config.sync_targets) ? config.sync_targets : []
        setTargetOptions(
          targets.map((t, i) => ({
            value: syncTargetStorageValue(t),
            label: t.label || t.source_dir || `Target ${i + 1}`,
          })),
        )
        const stored = localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)
        const storedExists = stored != null && targets.some((t) => syncTargetStorageValue(t) === stored)
        setSelectedTargetValueState(
          storedExists ? (stored as string) : targets[0] ? syncTargetStorageValue(targets[0]) : '',
        )
      })
      .catch(() => {})
  }, [])

  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next)
    void saveSettings({ idarr_quick_add_community: String(next) })
  }, [])

  // Persist the chosen target to the shared key so it matches the IDarr page.
  const setSelectedTarget = useCallback((value: string) => {
    setSelectedTargetValueState(value)
    localStorage.setItem(IDARR_SYNC_TARGET_STORAGE_KEY, value)
  }, [])

  const doIdarrUpload = useCallback(async (files: File[]) => {
    try {
      const config = await getMakerIdarrConfig()
      const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
      if (!syncTargets.length) return

      const storedValue = localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)
      let resolvedIndex = -1
      if (storedValue) {
        resolvedIndex = syncTargets.findIndex((t) => syncTargetStorageValue(t) === storedValue)
      }
      const syncTargetIndex = resolvedIndex >= 0 ? resolvedIndex : 0

      const response = await uploadMakerIdarrFiles(syncTargetIndex, files)
      if (config.auto_rename_quick_add && response.uploaded_count > 0) {
        const job = await startIdarr(false, syncTargetIndex, response.uploaded, config.auto_upload_quick_add)
        // The maker is on the requests page, not IDarr — pop a notice if anything went pending.
        void notifyIdarrTargetedRun(job.id, Boolean(config.auto_upload_quick_add), syncTargetIndex)
      }
    } catch {
      // Silently ignore — best-effort maker convenience
    }
  }, [])

  return { enabled, setEnabled, doIdarrUpload, targetOptions, selectedTargetValue, setSelectedTarget }
}
