import { useState } from 'react'
import { PosterManagerTab } from '../components/poster-manager/PosterManagerTabs'

interface UsePosterManagerTabGuardParams {
  activeTab: PosterManagerTab
  setActiveTab: (tab: PosterManagerTab) => void
  hasUnsavedChanges: boolean
  hasUnsavedFlowChanges: boolean
  hasUnsavedPriorityChanges: boolean
  hasUnsavedBorderChanges: boolean
  hasUnsavedLibraryChanges: boolean
  hasUnsavedIncludeChanges: boolean
}

export const usePosterManagerTabGuard = ({
  activeTab,
  setActiveTab,
  hasUnsavedChanges,
  hasUnsavedFlowChanges,
  hasUnsavedPriorityChanges,
  hasUnsavedBorderChanges,
  hasUnsavedLibraryChanges,
  hasUnsavedIncludeChanges,
}: UsePosterManagerTabGuardParams) => {
  const [showUnsavedModal, setShowUnsavedModal] = useState(false)
  const [pendingTabChange, setPendingTabChange] = useState<PosterManagerTab | null>(null)

  const handleTabChange = (newTab: PosterManagerTab) => {
    let hasCurrentTabUnsavedChanges = false
    if (activeTab === 'settings') hasCurrentTabUnsavedChanges = hasUnsavedChanges
    else if (activeTab === 'flow') hasCurrentTabUnsavedChanges = hasUnsavedFlowChanges
    else if (activeTab === 'priority') hasCurrentTabUnsavedChanges = hasUnsavedPriorityChanges
    else if (activeTab === 'border') hasCurrentTabUnsavedChanges = hasUnsavedBorderChanges
    else if (activeTab === 'rename') hasCurrentTabUnsavedChanges = hasUnsavedLibraryChanges || hasUnsavedBorderChanges || hasUnsavedIncludeChanges
    else if (activeTab === 'unmatched') hasCurrentTabUnsavedChanges = hasUnsavedLibraryChanges

    if (hasCurrentTabUnsavedChanges) {
      setPendingTabChange(newTab)
      setShowUnsavedModal(true)
    } else {
      setActiveTab(newTab)
    }
  }

  const handleDiscardChanges = () => {
    if (pendingTabChange) {
      setActiveTab(pendingTabChange)
      setPendingTabChange(null)
    }
    setShowUnsavedModal(false)
  }

  const handleCancelTabChange = () => {
    setPendingTabChange(null)
    setShowUnsavedModal(false)
  }

  return {
    showUnsavedModal,
    handleTabChange,
    handleDiscardChanges,
    handleCancelTabChange,
  }
}
