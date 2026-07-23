import { Dispatch, SetStateAction } from 'react'
import {
  getApiErrorMessage,
  MakerIdarrPendingCandidate,
  MakerIdarrPendingItem,
  resolveMakerIdarrPendingMatch,
  startIdarr,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UseIDarrResolverActionsParams {
  selectedSyncTargetIndex: number
  resolverItem: MakerIdarrPendingItem | null
  resolverTmdbId: string
  resolverTvdbId: string
  resolverImdbId: string
  resolverTmdbType: 'movie' | 'tv_series' | 'collection' | ''
  setResolving: Dispatch<SetStateAction<boolean>>
  showToast: (message: string, type?: ToastType) => void
  refreshPendingAndHandleResolverAdvance: (
    resolvedAssetKey: string,
    options?: { forceAdvance?: boolean },
  ) => Promise<void>
  loadCacheStats: () => Promise<void>
  loadIgnoredTitles: () => Promise<void>
}

export const useIDarrResolverActions = ({
  selectedSyncTargetIndex,
  resolverItem,
  resolverTmdbId,
  resolverTvdbId,
  resolverImdbId,
  resolverTmdbType,
  setResolving,
  showToast,
  refreshPendingAndHandleResolverAdvance,
  loadCacheStats,
  loadIgnoredTitles,
}: UseIDarrResolverActionsParams) => {
  const handleResolvePending = async (options?: { forceAdvance?: boolean }) => {
    if (!resolverItem) {
      return
    }

    const tmdbValue = resolverTmdbId.trim()
    const tvdbValue = resolverTvdbId.trim()
    const imdbValue = resolverImdbId.trim()

    if (!tmdbValue && !tvdbValue && !imdbValue) {
      showToast('Select a candidate card, or enter IDs manually to resolve', 'error')
      return
    }

    const tmdbId = tmdbValue ? Number(tmdbValue) : null
    const tvdbId = tvdbValue ? Number(tvdbValue) : null

    if ((tmdbValue && !Number.isFinite(tmdbId)) || (tvdbValue && !Number.isFinite(tvdbId))) {
      showToast('TMDB/TVDB IDs must be numeric', 'error')
      return
    }

    if (tmdbValue && !resolverTmdbType) {
      showToast('Select a media type (Movie, TV Show, or Collection) before resolving with a TMDB ID', 'error')
      return
    }

    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: resolverItem.asset_key,
        action: 'resolve',
        tmdb_id: tmdbId,
        tvdb_id: tvdbId,
        imdb_id: imdbValue || null,
        tmdb_type: resolverTmdbType || null,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast('Pending match resolved', 'success')
      await refreshPendingAndHandleResolverAdvance(resolverItem.asset_key, options)
      await loadCacheStats()
      await loadIgnoredTitles()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve pending match'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleResolveAndRename = async () => {
    if (!resolverItem) {
      return
    }

    const tmdbValue = resolverTmdbId.trim()
    const tvdbValue = resolverTvdbId.trim()
    const imdbValue = resolverImdbId.trim()

    if (!tmdbValue && !tvdbValue && !imdbValue) {
      showToast('Select a candidate card, or enter IDs manually to resolve', 'error')
      return
    }

    const tmdbId = tmdbValue ? Number(tmdbValue) : null
    const tvdbId = tvdbValue ? Number(tvdbValue) : null

    if ((tmdbValue && !Number.isFinite(tmdbId)) || (tvdbValue && !Number.isFinite(tvdbId))) {
      showToast('TMDB/TVDB IDs must be numeric', 'error')
      return
    }

    if (tmdbValue && !resolverTmdbType) {
      showToast('Select a media type (Movie, TV Show, or Collection) before resolving with a TMDB ID', 'error')
      return
    }

    const sourceFilenames = (resolverItem.source_filenames ?? []).filter(Boolean)

    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: resolverItem.asset_key,
        action: 'resolve',
        tmdb_id: tmdbId,
        tvdb_id: tvdbId,
        imdb_id: imdbValue || null,
        tmdb_type: resolverTmdbType || null,
        sync_target_index: selectedSyncTargetIndex,
        mark_as_renamed: sourceFilenames.length > 0,
      })
      showToast('Pending match resolved', 'success')

      if (sourceFilenames.length > 0) {
        const job = await startIdarr(false, selectedSyncTargetIndex, sourceFilenames)
        showToast(`Rename started (Job ID: ${job.id})`, 'info')
      } else {
        showToast('Resolved — no source files found to rename', 'info')
      }

      await refreshPendingAndHandleResolverAdvance(resolverItem.asset_key, { forceAdvance: true })
      await loadCacheStats()
      await loadIgnoredTitles()
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve and rename'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleResolveWithCandidate = async (
    candidate: MakerIdarrPendingCandidate,
    options?: { forceAdvance?: boolean; andRename?: boolean },
  ) => {
    if (!resolverItem) {
      return
    }

    const candidateTmdbType = candidate.media_type === 'show' ? 'tv_series' : candidate.media_type === 'collection' ? 'collection' : 'movie'

    const sourceFilenames = options?.andRename
      ? (resolverItem.source_filenames ?? []).filter(Boolean)
      : []

    try {
      setResolving(true)

      await resolveMakerIdarrPendingMatch({
        asset_key: resolverItem.asset_key,
        action: 'resolve',
        tmdb_id: candidate.tmdb_id,
        tvdb_id: candidate.tvdb_id ?? null,
        imdb_id: candidate.imdb_id ?? null,
        tmdb_type: candidateTmdbType,
        sync_target_index: selectedSyncTargetIndex,
        mark_as_renamed: sourceFilenames.length > 0,
      })
      showToast(`Resolved with TMDB ${candidate.tmdb_id}`, 'success')

      if (sourceFilenames.length > 0) {
        const job = await startIdarr(false, selectedSyncTargetIndex, sourceFilenames)
        showToast(`Rename started (Job ID: ${job.id})`, 'info')
      } else if (options?.andRename) {
        showToast('Resolved — no source files found to trigger rename', 'info')
      }

      await refreshPendingAndHandleResolverAdvance(resolverItem.asset_key, { forceAdvance: options?.forceAdvance ?? true })
      await Promise.all([loadCacheStats(), loadIgnoredTitles()])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve with selected candidate'), 'error')
    } finally {
      setResolving(false)
    }
  }

  return {
    handleResolvePending,
    handleResolveAndRename,
    handleResolveWithCandidate,
  }
}
