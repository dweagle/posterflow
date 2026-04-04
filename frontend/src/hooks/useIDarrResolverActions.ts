import { Dispatch, SetStateAction } from 'react'
import {
  getApiErrorMessage,
  MakerIdarrPendingCandidate,
  MakerIdarrPendingItem,
  resolveMakerIdarrPendingMatch,
  reviewMakerIdarrPendingCandidate,
  startIdarr,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

type CandidateReview = {
  status?: 'accept' | 'reject'
  reviewed_at?: string
  note?: string | null
}

type CandidateReviews = Record<string, CandidateReview>

interface UseIDarrResolverActionsParams {
  selectedSyncTargetIndex: number
  resolverItem: MakerIdarrPendingItem | null
  resolverTmdbId: string
  resolverTvdbId: string
  resolverImdbId: string
  resolverCandidates: MakerIdarrPendingCandidate[]
  resolverCandidateReviews: CandidateReviews
  filteredResolverCandidates: MakerIdarrPendingCandidate[]
  setResolverCandidateReviews: Dispatch<SetStateAction<CandidateReviews>>
  setResolverCandidates: Dispatch<SetStateAction<MakerIdarrPendingCandidate[]>>
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
  resolverCandidates,
  resolverCandidateReviews,
  filteredResolverCandidates,
  setResolverCandidateReviews,
  setResolverCandidates,
  setResolving,
  showToast,
  refreshPendingAndHandleResolverAdvance,
  loadCacheStats,
  loadIgnoredTitles,
}: UseIDarrResolverActionsParams) => {
  const applyReviewsToCandidates = (nextReviews: CandidateReviews) => {
    setResolverCandidates((previous) => previous.map((item) => ({
      ...item,
      review: nextReviews[String(item.tmdb_id)],
    })))
  }

  const handleReviewCandidate = async (candidate: MakerIdarrPendingCandidate, action: 'accept' | 'reject' | 'clear') => {
    if (!resolverItem) {
      return
    }

    try {
      setResolving(true)
      const response = await reviewMakerIdarrPendingCandidate({
        asset_key: resolverItem.asset_key,
        tmdb_id: candidate.tmdb_id,
        action,
        sync_target_index: selectedSyncTargetIndex,
      })
      const nextReviews = response.candidate_reviews || {}
      setResolverCandidateReviews(nextReviews)
      setResolverCandidates((previous) => previous.map((item) => {
        if (item.tmdb_id !== candidate.tmdb_id) {
          return item
        }
        const review = nextReviews[String(candidate.tmdb_id)]
        return {
          ...item,
          review,
        }
      }))
      showToast(`Candidate ${action}ed`, 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to review candidate'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleRejectVisibleCandidates = async () => {
    if (!resolverItem) {
      return
    }

    const targets = filteredResolverCandidates.filter((candidate) => resolverCandidateReviews[String(candidate.tmdb_id)]?.status !== 'reject')
    if (targets.length === 0) {
      showToast('No visible candidates to reject', 'error')
      return
    }

    try {
      setResolving(true)
      const nextReviews = { ...resolverCandidateReviews }
      for (const candidate of targets) {
        const response = await reviewMakerIdarrPendingCandidate({
          asset_key: resolverItem.asset_key,
          tmdb_id: candidate.tmdb_id,
          action: 'reject',
          sync_target_index: selectedSyncTargetIndex,
        })
        Object.assign(nextReviews, response.candidate_reviews || {})
      }

      setResolverCandidateReviews(nextReviews)
      applyReviewsToCandidates(nextReviews)
      showToast(`Rejected ${targets.length} candidate(s)`, 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to reject visible candidates'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleClearReviewedCandidates = async () => {
    if (!resolverItem) {
      return
    }

    const targets = resolverCandidates.filter((candidate) => {
      const status = resolverCandidateReviews[String(candidate.tmdb_id)]?.status
      return status === 'accept' || status === 'reject'
    })

    if (targets.length === 0) {
      showToast('No reviewed candidates to clear', 'error')
      return
    }

    try {
      setResolving(true)
      let nextReviews = { ...resolverCandidateReviews }
      for (const candidate of targets) {
        const response = await reviewMakerIdarrPendingCandidate({
          asset_key: resolverItem.asset_key,
          tmdb_id: candidate.tmdb_id,
          action: 'clear',
          sync_target_index: selectedSyncTargetIndex,
        })
        nextReviews = response.candidate_reviews || nextReviews
      }

      setResolverCandidateReviews(nextReviews)
      applyReviewsToCandidates(nextReviews)
      showToast(`Cleared ${targets.length} review(s)`, 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to clear candidate reviews'), 'error')
    } finally {
      setResolving(false)
    }
  }

  const handleResolvePending = async (options?: { forceAdvance?: boolean }) => {
    if (!resolverItem) {
      return
    }

    const tmdbValue = resolverTmdbId.trim()
    const tvdbValue = resolverTvdbId.trim()
    const imdbValue = resolverImdbId.trim()

    if (!tmdbValue && !tvdbValue && !imdbValue) {
      showToast('Enter at least one ID to resolve', 'error')
      return
    }

    const tmdbId = tmdbValue ? Number(tmdbValue) : null
    const tvdbId = tvdbValue ? Number(tvdbValue) : null

    if ((tmdbValue && !Number.isFinite(tmdbId)) || (tvdbValue && !Number.isFinite(tvdbId))) {
      showToast('TMDB/TVDB IDs must be numeric', 'error')
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
      showToast('Enter at least one ID to resolve', 'error')
      return
    }

    const tmdbId = tmdbValue ? Number(tmdbValue) : null
    const tvdbId = tvdbValue ? Number(tvdbValue) : null

    if ((tmdbValue && !Number.isFinite(tmdbId)) || (tvdbValue && !Number.isFinite(tvdbId))) {
      showToast('TMDB/TVDB IDs must be numeric', 'error')
      return
    }

    // Capture filenames before resolve since resolverItem may change after advancing
    const sourceFilenames = (resolverItem.source_filenames ?? []).filter(Boolean)

    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: resolverItem.asset_key,
        action: 'resolve',
        tmdb_id: tmdbId,
        tvdb_id: tvdbId,
        imdb_id: imdbValue || null,
        sync_target_index: selectedSyncTargetIndex,
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

  const handleResolveWithCandidate = async (candidate: MakerIdarrPendingCandidate) => {
    if (!resolverItem) {
      return
    }

    try {
      setResolving(true)
      await resolveMakerIdarrPendingMatch({
        asset_key: resolverItem.asset_key,
        action: 'resolve',
        tmdb_id: candidate.tmdb_id,
        tvdb_id: candidate.tvdb_id ?? null,
        imdb_id: candidate.imdb_id ?? null,
        sync_target_index: selectedSyncTargetIndex,
      })
      showToast(`Resolved with TMDB ${candidate.tmdb_id}`, 'success')
      await refreshPendingAndHandleResolverAdvance(resolverItem.asset_key, { forceAdvance: true })
      await Promise.all([loadCacheStats(), loadIgnoredTitles()])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to resolve with selected candidate'), 'error')
    } finally {
      setResolving(false)
    }
  }

  return {
    handleReviewCandidate,
    handleRejectVisibleCandidates,
    handleClearReviewedCandidates,
    handleResolvePending,
    handleResolveAndRename,
    handleResolveWithCandidate,
  }
}
