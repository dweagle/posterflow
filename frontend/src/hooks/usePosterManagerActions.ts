import { useCallback } from 'react'
import {
  PosterConfig,
  getApiErrorMessage,
  runBorderReplacer,
  startPosterRename,
  startUnmatchedDetection,
} from '../api/client'

interface UsePosterManagerActionsOptions {
  config: PosterConfig | null
  borderReplacerConfigured: boolean
  setRenaming: React.Dispatch<React.SetStateAction<boolean>>
  setDetectingUnmatched: React.Dispatch<React.SetStateAction<boolean>>
  setRunningBorderReplacer: React.Dispatch<React.SetStateAction<boolean>>
  trackedUnmatchedJobRef: React.MutableRefObject<number | null>
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export function usePosterManagerActions({
  config,
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

  const handleStartRename = useCallback(async (dryRun: boolean = false) => {
    if (!config) return

    try {
      setRenaming(true)
      const runConfig = { ...config, dry_run: dryRun }
      const result = await startPosterRename(runConfig)
      showToast(dryRun ? 'Poster Renamer dry run started...' : 'Poster Renamer started...')

      if (result.unmatched_detection) {
        showToast('Unmatched detection also completed automatically', 'info')
      }
    } catch (error) {
      console.error('Error starting Poster Renamer:', error)
      showToast(getApiErrorMessage(error, 'Failed to start Poster Renamer'), 'error')
    } finally {
      setRenaming(false)
    }
  }, [config, setRenaming, showToast])

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
    handleStartRename,
    handleRunBorderReplacer,
  }
}
