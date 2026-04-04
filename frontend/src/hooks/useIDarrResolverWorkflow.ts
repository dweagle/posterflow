import { Dispatch, SetStateAction } from 'react'
import {
  getApiErrorMessage,
  getMakerIdarrPendingCandidates,
  MakerIdarrPendingCandidate,
  MakerIdarrPendingItem,
  MakerIdarrResolutionEvent,
} from '../api/client'

type CandidateReview = {
  status?: 'accept' | 'reject'
  reviewed_at?: string
  note?: string | null
}

type CandidateReviews = Record<string, CandidateReview>

type ToastType = 'success' | 'error' | 'info'

interface UseIDarrResolverWorkflowParams {
  selectedSyncTargetIndex: number
  resolverItem: MakerIdarrPendingItem | null
  resolverSkipReviewedNav: boolean
  getFirstUnresolvedItem: () => MakerIdarrPendingItem | null
  getNextPendingItem: (skipReviewed: boolean) => MakerIdarrPendingItem | null
  getPreviousPendingItem: (skipReviewed: boolean) => MakerIdarrPendingItem | null
  getNextUnresolvedItem: () => MakerIdarrPendingItem | null
  getPreviousUnresolvedItem: () => MakerIdarrPendingItem | null
  showToast: (message: string, type?: ToastType) => void
  setResolverItem: Dispatch<SetStateAction<MakerIdarrPendingItem | null>>
  setResolverTmdbId: Dispatch<SetStateAction<string>>
  setResolverTvdbId: Dispatch<SetStateAction<string>>
  setResolverImdbId: Dispatch<SetStateAction<string>>
  setResolverCandidates: Dispatch<SetStateAction<MakerIdarrPendingCandidate[]>>
  setResolverCandidatesLoading: Dispatch<SetStateAction<boolean>>
  setResolverHistory: Dispatch<SetStateAction<MakerIdarrResolutionEvent[]>>
  setResolverCandidateReviews: Dispatch<SetStateAction<CandidateReviews>>
}

export const useIDarrResolverWorkflow = ({
  selectedSyncTargetIndex,
  resolverItem,
  resolverSkipReviewedNav,
  getFirstUnresolvedItem,
  getNextPendingItem,
  getPreviousPendingItem,
  getNextUnresolvedItem,
  getPreviousUnresolvedItem,
  showToast,
  setResolverItem,
  setResolverTmdbId,
  setResolverTvdbId,
  setResolverImdbId,
  setResolverCandidates,
  setResolverCandidatesLoading,
  setResolverHistory,
  setResolverCandidateReviews,
}: UseIDarrResolverWorkflowParams) => {
  const loadResolverCandidates = async (item: MakerIdarrPendingItem) => {
    try {
      setResolverCandidatesLoading(true)
      const response = await getMakerIdarrPendingCandidates({
        asset_key: item.asset_key,
        title: item.title,
        year: item.year ?? null,
        type: item.type,
        sync_target_index: selectedSyncTargetIndex,
      })
      const candidates = response.candidates || []
      const reviews = response.candidate_reviews || {}
      setResolverCandidates(candidates)
      setResolverHistory(response.resolution_history || [])
      setResolverCandidateReviews(reviews)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to fetch candidate matches'), 'error')
      setResolverCandidates([])
      setResolverHistory([])
      setResolverCandidateReviews({})
    } finally {
      setResolverCandidatesLoading(false)
    }
  }

  const openResolver = async (item: MakerIdarrPendingItem) => {
    setResolverItem(item)
    setResolverTmdbId(item.suggested_ids?.tmdb_id ? String(item.suggested_ids.tmdb_id) : '')
    setResolverTvdbId(item.suggested_ids?.tvdb_id ? String(item.suggested_ids.tvdb_id) : '')
    setResolverImdbId(item.suggested_ids?.imdb_id || '')
    const initialCandidates = Array.isArray(item.candidates) ? item.candidates : []
    const initialReviews = item.candidate_reviews || {}
    setResolverCandidates(initialCandidates)
    setResolverHistory(Array.isArray(item.resolution_history) ? item.resolution_history : [])
    setResolverCandidateReviews(initialReviews)
    await loadResolverCandidates(item)
  }

  const handleOpenNextPending = async () => {
    if (!resolverItem) {
      return
    }

    const nextItem = getNextPendingItem(resolverSkipReviewedNav)
    if (!nextItem) {
      return
    }

    await openResolver(nextItem)
  }

  const handleOpenPreviousPending = async () => {
    if (!resolverItem) {
      return
    }

    const previousItem = getPreviousPendingItem(resolverSkipReviewedNav)
    if (!previousItem) {
      return
    }

    await openResolver(previousItem)
  }

  const handleOpenFirstUnresolved = async () => {
    const firstUnresolved = getFirstUnresolvedItem()
    if (!firstUnresolved) {
      showToast('No unresolved pending items', 'error')
      return
    }
    await openResolver(firstUnresolved)
  }

  const handleOpenNextUnresolved = async () => {
    if (!resolverItem) {
      showToast('Open a pending item first', 'error')
      return
    }

    const nextItem = getNextUnresolvedItem()
    if (!nextItem) {
      showToast('No next unresolved pending item', 'error')
      return
    }

    await openResolver(nextItem)
  }

  const handleOpenPreviousUnresolved = async () => {
    if (!resolverItem) {
      showToast('Open a pending item first', 'error')
      return
    }

    const previousItem = getPreviousUnresolvedItem()
    if (!previousItem) {
      showToast('No previous unresolved pending item', 'error')
      return
    }

    await openResolver(previousItem)
  }

  return {
    loadResolverCandidates,
    openResolver,
    handleOpenNextPending,
    handleOpenPreviousPending,
    handleOpenFirstUnresolved,
    handleOpenNextUnresolved,
    handleOpenPreviousUnresolved,
  }
}
