import { useMemo } from 'react'
import { MakerIdarrPendingItem } from '../api/client'

export type PendingListFilter = 'all' | 'unresolved' | 'reviewed'

interface PendingReviewStatus {
  isReviewed: boolean
  acceptedCount: number
  rejectedCount: number
}

interface UseIDarrPendingReviewParams {
  pendingItems: MakerIdarrPendingItem[]
  pendingListFilter: PendingListFilter
  resolverItem: MakerIdarrPendingItem | null
}

export const getPendingReviewStatusForItem = (item: MakerIdarrPendingItem): PendingReviewStatus => {
  const reviews = item.candidate_reviews || {}
  const statuses = Object.values(reviews).map((entry) => entry?.status)
  const acceptedCount = statuses.filter((status) => status === 'accept').length
  const rejectedCount = statuses.filter((status) => status === 'reject').length

  return {
    isReviewed: acceptedCount > 0 || rejectedCount > 0,
    acceptedCount,
    rejectedCount,
  }
}

const isPendingItemUnresolved = (item: MakerIdarrPendingItem): boolean => {
  return !getPendingReviewStatusForItem(item).isReviewed
}

export const useIDarrPendingReview = ({
  pendingItems,
  pendingListFilter,
  resolverItem,
}: UseIDarrPendingReviewParams) => {
  const pendingSummary = useMemo(() => {
    const total = pendingItems.length
    const reviewed = pendingItems.filter((item) => !isPendingItemUnresolved(item)).length

    return {
      total,
      reviewed,
      unresolved: total - reviewed,
    }
  }, [pendingItems])

  const filteredPendingItems = useMemo(() => {
    if (pendingListFilter === 'all') {
      return pendingItems
    }
    if (pendingListFilter === 'unresolved') {
      return pendingItems.filter((item) => isPendingItemUnresolved(item))
    }
    return pendingItems.filter((item) => !isPendingItemUnresolved(item))
  }, [pendingItems, pendingListFilter])

  const resolverProgress = useMemo(() => {
    if (!resolverItem) {
      return { current: 0, total: pendingItems.length }
    }

    const index = pendingItems.findIndex((item) => item.asset_key === resolverItem.asset_key)
    if (index < 0) {
      return { current: 0, total: pendingItems.length }
    }

    return { current: index + 1, total: pendingItems.length }
  }, [pendingItems, resolverItem])

  const getFirstUnresolvedItem = () => {
    return pendingItems.find((item) => isPendingItemUnresolved(item)) || null
  }

  const getNextPendingItem = (skipReviewed: boolean): MakerIdarrPendingItem | null => {
    if (!resolverItem || pendingItems.length <= 1) {
      return null
    }

    const currentIndex = pendingItems.findIndex((item) => item.asset_key === resolverItem.asset_key)
    if (currentIndex < 0) {
      return null
    }

    if (skipReviewed) {
      for (let offset = 1; offset <= pendingItems.length; offset += 1) {
        const nextIndex = (currentIndex + offset) % pendingItems.length
        const candidateItem = pendingItems[nextIndex]
        if (candidateItem.asset_key !== resolverItem.asset_key && isPendingItemUnresolved(candidateItem)) {
          return candidateItem
        }
      }
      return null
    }

    return pendingItems[currentIndex + 1] || pendingItems[0] || null
  }

  const getPreviousPendingItem = (skipReviewed: boolean): MakerIdarrPendingItem | null => {
    if (!resolverItem || pendingItems.length <= 1) {
      return null
    }

    const currentIndex = pendingItems.findIndex((item) => item.asset_key === resolverItem.asset_key)
    if (currentIndex < 0) {
      return null
    }

    if (skipReviewed) {
      for (let offset = 1; offset <= pendingItems.length; offset += 1) {
        const previousIndex = (currentIndex - offset + pendingItems.length) % pendingItems.length
        const candidateItem = pendingItems[previousIndex]
        if (candidateItem.asset_key !== resolverItem.asset_key && isPendingItemUnresolved(candidateItem)) {
          return candidateItem
        }
      }
      return null
    }

    return pendingItems[currentIndex - 1] || pendingItems[pendingItems.length - 1] || null
  }

  const getNextUnresolvedItem = (): MakerIdarrPendingItem | null => {
    if (!resolverItem || pendingItems.length <= 1) {
      return null
    }

    const currentIndex = pendingItems.findIndex((item) => item.asset_key === resolverItem.asset_key)
    if (currentIndex < 0) {
      return null
    }

    for (let offset = 1; offset <= pendingItems.length; offset += 1) {
      const nextIndex = (currentIndex + offset) % pendingItems.length
      const nextItem = pendingItems[nextIndex]
      if (nextItem.asset_key !== resolverItem.asset_key && isPendingItemUnresolved(nextItem)) {
        return nextItem
      }
    }

    return null
  }

  const getPreviousUnresolvedItem = (): MakerIdarrPendingItem | null => {
    if (!resolverItem || pendingItems.length <= 1) {
      return null
    }

    const currentIndex = pendingItems.findIndex((item) => item.asset_key === resolverItem.asset_key)
    if (currentIndex < 0) {
      return null
    }

    for (let offset = 1; offset <= pendingItems.length; offset += 1) {
      const previousIndex = (currentIndex - offset + pendingItems.length) % pendingItems.length
      const previousItem = pendingItems[previousIndex]
      if (previousItem.asset_key !== resolverItem.asset_key && isPendingItemUnresolved(previousItem)) {
        return previousItem
      }
    }

    return null
  }

  return {
    pendingSummary,
    filteredPendingItems,
    resolverProgress,
    getPendingReviewStatus: getPendingReviewStatusForItem,
    getFirstUnresolvedItem,
    getNextPendingItem,
    getPreviousPendingItem,
    getNextUnresolvedItem,
    getPreviousUnresolvedItem,
  }
}
