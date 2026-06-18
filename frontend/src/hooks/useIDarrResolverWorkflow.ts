import { Dispatch, SetStateAction } from 'react'
import {
  getApiErrorMessage,
  getMakerIdarrPendingCandidates,
  MakerIdarrPendingCandidate,
  MakerIdarrPendingItem,
  MakerIdarrResolutionEvent,
} from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UseIDarrResolverWorkflowParams {
  selectedSyncTargetIndex: number
  showToast: (message: string, type?: ToastType) => void
  setResolverItem: Dispatch<SetStateAction<MakerIdarrPendingItem | null>>
  setResolverTmdbId: Dispatch<SetStateAction<string>>
  setResolverTmdbType: Dispatch<SetStateAction<'movie' | 'tv_series' | 'collection' | ''>>
  setResolverTvdbId: Dispatch<SetStateAction<string>>
  setResolverImdbId: Dispatch<SetStateAction<string>>
  setResolverCandidates: Dispatch<SetStateAction<MakerIdarrPendingCandidate[]>>
  setResolverCandidatesLoading: Dispatch<SetStateAction<boolean>>
  setResolverHistory: Dispatch<SetStateAction<MakerIdarrResolutionEvent[]>>
  setResolverManualSearch: Dispatch<SetStateAction<string>>
  setManualSectionOpen: Dispatch<SetStateAction<boolean>>
}

export const useIDarrResolverWorkflow = ({
  selectedSyncTargetIndex,
  showToast,
  setResolverItem,
  setResolverTmdbId,
  setResolverTmdbType,
  setResolverTvdbId,
  setResolverImdbId,
  setResolverCandidates,
  setResolverCandidatesLoading,
  setResolverHistory,
  setResolverManualSearch,
  setManualSectionOpen,
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
      setResolverCandidates(response.candidates || [])
      setResolverHistory(response.resolution_history || [])
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to fetch candidate matches'), 'error')
      setResolverCandidates([])
      setResolverHistory([])
    } finally {
      setResolverCandidatesLoading(false)
    }
  }

  const openResolver = async (item: MakerIdarrPendingItem) => {
    // Reset the manual section to closed and clear its search when loading a new item.
    setManualSectionOpen(false)
    setResolverManualSearch('')
    setResolverItem(item)
    setResolverTmdbId(item.suggested_ids?.tmdb_id ? String(item.suggested_ids.tmdb_id) : '')
    setResolverTmdbType('')
    setResolverTvdbId(item.suggested_ids?.tvdb_id ? String(item.suggested_ids.tvdb_id) : '')
    setResolverImdbId(item.suggested_ids?.imdb_id || '')
    setResolverCandidates(Array.isArray(item.candidates) ? item.candidates : [])
    setResolverHistory(Array.isArray(item.resolution_history) ? item.resolution_history : [])
    await loadResolverCandidates(item)
  }

  return {
    loadResolverCandidates,
    openResolver,
  }
}
