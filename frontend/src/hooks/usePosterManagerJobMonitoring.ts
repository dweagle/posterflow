import { useEffect, useRef } from 'react'
import { useUnmatched } from '../contexts/UnmatchedContext'

interface UsePosterManagerJobMonitoringOptions {
  trackedWorkflowJobRef: React.MutableRefObject<number | null>
  trackedUnmatchedJobRef: React.MutableRefObject<number | null>
  setFlowRunning: (running: boolean) => void
  setDetectingUnmatched: (detecting: boolean) => void
  refreshStats: () => Promise<void>
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export function usePosterManagerJobMonitoring({
  trackedWorkflowJobRef,
  trackedUnmatchedJobRef,
  setFlowRunning,
  setDetectingUnmatched,
  refreshStats,
  showToast,
}: UsePosterManagerJobMonitoringOptions) {
  const { jobs } = useUnmatched()
  const lastJobStatusRef = useRef<{ [key: string]: string }>({})
  const refreshStatsRef = useRef(refreshStats)
  const showToastRef = useRef(showToast)

  useEffect(() => {
    refreshStatsRef.current = refreshStats
  }, [refreshStats])

  useEffect(() => {
    showToastRef.current = showToast
  }, [showToast])

  useEffect(() => {
    jobs.forEach((job) => {
      const jobKey = `${job.job_type}_${job.id}`
      const previousStatus = lastJobStatusRef.current[jobKey]

      if (
        trackedWorkflowJobRef.current === job.id &&
        job.job_type === 'Poster Workflow' &&
        job.status === 'completed' &&
        previousStatus !== 'completed'
      ) {
        setFlowRunning(false)
        trackedWorkflowJobRef.current = null
        showToastRef.current('Workflow completed successfully', 'success')
      } else if (
        trackedWorkflowJobRef.current === job.id &&
        job.job_type === 'Poster Workflow' &&
        job.status === 'failed' &&
        previousStatus !== 'failed'
      ) {
        setFlowRunning(false)
        trackedWorkflowJobRef.current = null
        showToastRef.current('Workflow failed. Check Job Logs for details.', 'error')
      }

      if (
        trackedUnmatchedJobRef.current === job.id &&
        job.job_type === 'Unmatched Detection' &&
        job.status === 'completed' &&
        previousStatus !== 'completed'
      ) {
        setDetectingUnmatched(false)
        trackedUnmatchedJobRef.current = null
        refreshStatsRef.current()
        showToastRef.current('Unmatched detection completed!', 'success')
      } else if (
        trackedUnmatchedJobRef.current === job.id &&
        job.job_type === 'Unmatched Detection' &&
        job.status === 'failed' &&
        previousStatus !== 'failed'
      ) {
        setDetectingUnmatched(false)
        trackedUnmatchedJobRef.current = null
        showToastRef.current('Unmatched detection failed. Check logs for details.', 'error')
      }

      lastJobStatusRef.current[jobKey] = job.status
    })
  }, [jobs, setDetectingUnmatched, setFlowRunning, trackedUnmatchedJobRef, trackedWorkflowJobRef])
}
