import { Dispatch, SetStateAction } from 'react'
import {
  getApiErrorMessage,
  getMakerIdarrLastRun,
  MakerIdarrConfig,
  MakerIdarrLastRun,
  revertMakerIdarrLatestRun,
  runMakerIdarrCacheMaintenance,
  saveMakerIdarrConfig,
  startIdarr,
  startIdarrSync,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

type CacheMaintenanceAction = 'clear_all' | 'prune_unmatched' | 'purge_stale'

interface UseIDarrOperationalActionsParams {
  config: MakerIdarrConfig
  selectedSyncTargetIndex: number
  onConfigPersisted?: () => void
  setSaving: Dispatch<SetStateAction<boolean>>
  setRunning: Dispatch<SetStateAction<boolean>>
  setSyncing: Dispatch<SetStateAction<boolean>>
  setCacheMaintaining: Dispatch<SetStateAction<boolean>>
  setReverting: Dispatch<SetStateAction<boolean>>
  setLastRun: Dispatch<SetStateAction<MakerIdarrLastRun | null>>
  showToast: (message: string, type?: ToastType) => void
  loadPendingMatches: () => Promise<unknown>
  loadCacheStats: () => Promise<void>
  loadIgnoredTitles: () => Promise<void>
}

export const useIDarrOperationalActions = ({
  config,
  selectedSyncTargetIndex,
  onConfigPersisted,
  setSaving,
  setRunning,
  setSyncing,
  setCacheMaintaining,
  setReverting,
  setLastRun,
  showToast,
  loadPendingMatches,
  loadCacheStats,
  loadIgnoredTitles,
}: UseIDarrOperationalActionsParams) => {
  const handleSave = async () => {
    try {
      setSaving(true)
      await saveMakerIdarrConfig(config)
      onConfigPersisted?.()
      showToast('IDarr settings saved', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save IDarr settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleRun = async (dryRun: boolean) => {
    try {
      setRunning(true)
      await saveMakerIdarrConfig(config)
      onConfigPersisted?.()
      const job = await startIdarr(dryRun, selectedSyncTargetIndex)
      showToast(`${dryRun ? 'IDarr dry run' : 'IDarr run'} started (Job ID: ${job.id})`, 'success')
      const refreshed = await getMakerIdarrLastRun(selectedSyncTargetIndex)
      setLastRun(refreshed && Object.keys(refreshed).length > 0 ? refreshed : null)
      await loadPendingMatches()
      await loadCacheStats()
      await loadIgnoredTitles()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to start IDarr'), 'error')
    } finally {
      setRunning(false)
    }
  }

  const handleRunAndSync = async () => {
    try {
      setRunning(true)
      await saveMakerIdarrConfig(config)
      onConfigPersisted?.()
      const job = await startIdarr(false, selectedSyncTargetIndex, undefined, true)
      showToast(`IDarr run & sync started (Job ID: ${job.id})`, 'success')
      const refreshed = await getMakerIdarrLastRun(selectedSyncTargetIndex)
      setLastRun(refreshed && Object.keys(refreshed).length > 0 ? refreshed : null)
      await loadPendingMatches()
      await loadCacheStats()
      await loadIgnoredTitles()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to start IDarr run & sync'), 'error')
    } finally {
      setRunning(false)
    }
  }

  const runCacheMaintenance = async (action: CacheMaintenanceAction) => {
    try {
      setCacheMaintaining(true)
      const response = await runMakerIdarrCacheMaintenance({
        action,
        days: action === 'purge_stale' ? config.frequency_days : undefined,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Cache ${action} complete: ${response.deleted} deleted`, 'success')
      await Promise.all([loadCacheStats(), loadPendingMatches()])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Cache maintenance failed'), 'error')
    } finally {
      setCacheMaintaining(false)
    }
  }

  const handleRevertLatestRun = async () => {
    try {
      setReverting(true)
      const response = await revertMakerIdarrLatestRun({
        dry_run: false,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Revert complete: ${response.reverted} reverted, ${response.skipped} skipped, ${response.errors} errors`, response.errors > 0 ? 'error' : 'success')
      const refreshed = await getMakerIdarrLastRun(selectedSyncTargetIndex)
      setLastRun(refreshed && Object.keys(refreshed).length > 0 ? refreshed : null)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to revert latest IDarr run'), 'error')
    } finally {
      setReverting(false)
    }
  }

  const handleSync = async () => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const selectedTarget = syncTargets[selectedSyncTargetIndex]

    if (!selectedTarget) {
      showToast('Select a sync target first', 'error')
      return
    }

    try {
      setSyncing(true)

      const hasDriveId = String(selectedTarget.personal_drive_id || '').trim().length > 0
      const hasSourceDir = String(selectedTarget.source_dir || '').trim().length > 0
      if (!hasDriveId || !hasSourceDir) {
        showToast('Selected sync target needs both Drive ID and Sync Folder', 'error')
        return
      }

      const job = await startIdarrSync(undefined, {
        sync_target_index: selectedSyncTargetIndex,
        source_dir: selectedTarget.source_dir.trim() || undefined,
      })

      showToast(`IDarr personal sync started (Job ID: ${job.id})`, 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to start IDarr personal sync'), 'error')
    } finally {
      setSyncing(false)
    }
  }

  return {
    handleSave,
    handleRun,
    handleRunAndSync,
    runCacheMaintenance,
    handleRevertLatestRun,
    handleSync,
  }
}
