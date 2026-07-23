import { useCallback } from 'react'
import {
  getApiErrorMessage,
  runBorderReplacer,
  startAssetRename,
  startUnmatchedDetection,
} from '../api/client'

interface UsePosterManagerActionsOptions {
  borderReplacerConfigured: boolean
  setRenaming: React.Dispatch<React.SetStateAction<boolean>>
  setDetectingUnmatched: React.Dispatch<React.SetStateAction<boolean>>
  setRunningBorderReplacer: React.Dispatch<React.SetStateAction<boolean>>
  trackedUnmatchedJobRef: React.MutableRefObject<number | null>
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export function usePosterManagerActions({
  borderReplacerConfigured,
  setRenaming,
  setDetectingUnmatched,
  setRunningBorderReplacer,
  trackedUnmatchedJobRef,
  showToast,
}: UsePosterManagerActionsOptions) {
  const handleDetectUnmatched = useCallback(async () => {
    try {
      setDetectingUnmatched(true)
      const result = await startUnmatchedDetection()

      if (result.job_id) {
        trackedUnmatchedJobRef.current = result.job_id
      }

      showToast('Unmatched detection started...')
    } catch (error) {
      console.error('Error starting unmatched detection:', error)
      showToast(getApiErrorMessage(error, 'Failed to start detection'), 'error')
      setDetectingUnmatched(false)
    }
  }, [setDetectingUnmatched, showToast, trackedUnmatchedJobRef])

  const handleStartAssetRename = useCallback(async (dryRun: boolean = false) => {
    try {
      setRenaming(true)
      const result = await startAssetRename(dryRun)
      if (!result.jobs || result.jobs.length === 0) {
        showToast('Nothing selected to rename — pick asset types under Include.', 'info')
        return
      }
      showToast(dryRun ? 'Asset Renamer dry run started...' : 'Asset Renamer started...')
    } catch (error) {
      console.error('Error starting Asset Renamer:', error)
      showToast(getApiErrorMessage(error, 'Failed to start Asset Renamer'), 'error')
    } finally {
      setRenaming(false)
    }
  }, [setRenaming, showToast])

  const handleRunBorderReplacer = useCallback(async (dryRun: boolean = false) => {
    if (!borderReplacerConfigured) {
      showToast(
        'Border Replacer configuration is incomplete. Add a border color, add holiday colors, or enable Remove Borders.',
        'error'
      )
      return
    }

    try {
      setRunningBorderReplacer(true)
      const result = await runBorderReplacer(dryRun ? { dry_run: true } : undefined)

      if (result.success) {
        showToast(
          dryRun
            ? `Border replacer dry run started (Job ID: ${result.job_id})`
            : `Border replacer started (Job ID: ${result.job_id})`
        )
      }
    } catch (error) {
      console.error('Error running border replacer:', error)
      showToast(getApiErrorMessage(error, 'Failed to run border replacer'), 'error')
    } finally {
      setRunningBorderReplacer(false)
    }
  }, [borderReplacerConfigured, setRunningBorderReplacer, showToast])

  return {
    handleDetectUnmatched,
    handleStartAssetRename,
    handleRunBorderReplacer,
  }
}
