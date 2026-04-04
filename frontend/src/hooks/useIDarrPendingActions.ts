import { Dispatch, SetStateAction } from 'react'
import {
  addMakerIdarrIgnoredTitle,
  clearMakerIdarrPendingMatches,
  getApiErrorMessage,
  MakerIdarrPendingItem,
  removeMakerIdarrIgnoredTitle,
  resolveMakerIdarrPendingMatch,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UseIDarrPendingActionsParams {
  selectedSyncTargetIndex: number
  resolverItem: MakerIdarrPendingItem | null
  setResolving: Dispatch<SetStateAction<boolean>>
  showToast: (message: string, type?: ToastType) => void
  refreshPendingAndHandleResolverAdvance: (
    resolvedAssetKey: string,
    options?: { forceAdvance?: boolean },
  ) => Promise<void>
  loadPendingMatches: (options?: { silent?: boolean }) => Promise<MakerIdarrPendingItem[]>
  loadIgnoredTitles: () => Promise<void>
  loadCacheStats: () => Promise<void>
}

export const useIDarrPendingActions = ({
  selectedSyncTargetIndex,
  resolverItem,
  setResolving,
  showToast,
  refreshPendingAndHandleResolverAdvance,
  loadPendingMatches,
  loadIgnoredTitles,
  loadCacheStats,
}: UseIDarrPendingActionsParams) => {
  const handleDismissPending = async (item: MakerIdarrPendingItem) => {
    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: item.asset_key,
        action: 'dismiss',
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast('Pending match dismissed', 'success')
      if (resolverItem?.asset_key === item.asset_key) {
        await refreshPendingAndHandleResolverAdvance(item.asset_key)
      } else {
        await loadPendingMatches({ silent: true })
      }
      await loadCacheStats()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to dismiss pending match'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleResolveWithSuggested = async (item: MakerIdarrPendingItem) => {
    const tmdbId = item.suggested_ids?.tmdb_id ?? null
    const tvdbId = item.suggested_ids?.tvdb_id ?? null
    const imdbId = item.suggested_ids?.imdb_id?.trim() || null

    if (tmdbId === null && tvdbId === null && !imdbId) {
      showToast('No suggested IDs available for this item', 'error')
      return
    }

    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: item.asset_key,
        action: 'resolve',
        tmdb_id: tmdbId,
        tvdb_id: tvdbId,
        imdb_id: imdbId,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast('Pending match resolved with suggested IDs', 'success')
      if (resolverItem?.asset_key === item.asset_key) {
        await refreshPendingAndHandleResolverAdvance(item.asset_key)
      } else {
        await loadPendingMatches({ silent: true })
      }
      await loadIgnoredTitles()
      await loadCacheStats()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve with suggested IDs'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleIgnorePending = async (
    item: MakerIdarrPendingItem,
    options?: { forceAdvance?: boolean },
  ) => {
    try {
      setResolving(true)
      await addMakerIdarrIgnoredTitle({
        title: item.title,
        year: item.year ?? null,
        type: item.type,
        asset_key: item.asset_key,
        sync_target_index: selectedSyncTargetIndex,
      })
      await resolveMakerIdarrPendingMatch({
        asset_key: item.asset_key,
        action: 'ignore',
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast('Pending item added to ignore list', 'success')
      if (resolverItem?.asset_key === item.asset_key) {
        await refreshPendingAndHandleResolverAdvance(item.asset_key, options)
        await loadIgnoredTitles()
      } else {
        await Promise.all([loadPendingMatches({ silent: true }), loadIgnoredTitles()])
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to ignore pending item'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleRemoveIgnored = async (item: MakerIdarrPendingItem) => {
    try {
      setResolving(true)
      const response = await removeMakerIdarrIgnoredTitle({
        title: item.title,
        year: item.year ?? null,
        type: item.type,
        asset_key: item.asset_key,
        sync_target_index: selectedSyncTargetIndex,
      })
      if (response.restored_pending) {
        showToast('Removed from ignore list and restored to unmatched', 'success')
      } else {
        showToast('Removed from ignore list', 'success')
      }
      await Promise.all([loadIgnoredTitles(), loadPendingMatches({ silent: true }), loadCacheStats()])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to remove ignored title'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleClearAllPending = async () => {
    try {
      setResolving(true)
      const response = await clearMakerIdarrPendingMatches(selectedSyncTargetIndex)
      showToast(`Cleared ${response.deleted} pending unmatched item(s)`, 'success')
      await loadPendingMatches({ silent: true })
      await loadCacheStats()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to clear pending unmatched items'), 'error')
    } finally {
      setResolving(false)
    }
  }

  return {
    handleDismissPending,
    handleResolveWithSuggested,
    handleIgnorePending,
    handleRemoveIgnored,
    handleClearAllPending,
  }
}
