import { MutableRefObject, useEffect, useRef, useState } from 'react'
import {
  FlowConfig,
  FlowResult,
  IdarrFlowJobConfig,
  getApiErrorMessage,
  getFlowConfig,
  getJobs,
  runFlow,
  saveFlowConfig,
} from '../api/client'
import { getMakerIdarrConfig, MakerIdarrSyncTarget } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UsePosterManagerFlowParams {
  setSaving: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
  trackedWorkflowJobRef: MutableRefObject<number | null>
}

export const usePosterManagerFlow = ({
  setSaving,
  showToast,
  trackedWorkflowJobRef,
}: UsePosterManagerFlowParams) => {
  const [flowConfig, setFlowConfig] = useState<FlowConfig | null>(null)
  const [flowRunning, setFlowRunning] = useState(false)
  const [flowResult, setFlowResult] = useState<FlowResult | null>(null)
  const [hasUnsavedFlowChanges, setHasUnsavedFlowChanges] = useState(false)
  const [idarrSyncTargets, setIdarrSyncTargets] = useState<MakerIdarrSyncTarget[]>([])
  const [idarrShowInWorkflow, setIdarrShowInWorkflow] = useState(false)

  const originalFlowConfigRef = useRef<FlowConfig | null>(null)

  useEffect(() => {
    if (!flowConfig || !originalFlowConfigRef.current) return
    const hasChanged = JSON.stringify(flowConfig) !== JSON.stringify(originalFlowConfigRef.current)
    setHasUnsavedFlowChanges(hasChanged)
  }, [flowConfig])

  const fetchFlowConfig = async () => {
    try {
      const data = await getFlowConfig()
      setFlowConfig(data)
      originalFlowConfigRef.current = JSON.parse(JSON.stringify(data))
    } catch (error) {
      console.error('Error fetching flow config:', error)
      showToast('Failed to load flow configuration', 'error')
    }
  }

  const fetchIdarrSyncTargets = async () => {
    try {
      const idarrCfg = await getMakerIdarrConfig()
      setIdarrSyncTargets(idarrCfg.sync_targets || [])
      setIdarrShowInWorkflow(Boolean(idarrCfg.show_in_workflow))
    } catch {
      setIdarrSyncTargets([])
      setIdarrShowInWorkflow(false)
    }
  }

  const checkActiveWorkflow = async () => {
    try {
      const jobs = await getJobs()
      const activeWorkflow = jobs.find(
        job => job.job_type === 'Poster Workflow' && (job.status === 'running' || job.status === 'queued')
      )

      if (activeWorkflow) {
        setFlowRunning(true)
        trackedWorkflowJobRef.current = activeWorkflow.id
      } else {
        setFlowRunning(false)
      }
    } catch (error) {
      console.error('Error checking for active workflows:', error)
    }
  }

  const handleFlowConfigChange = (jobName: keyof FlowConfig, field: 'enabled' | 'stop_on_error' | 'delete_unknown', value: boolean) => {
    if (!flowConfig) return

    setFlowConfig({
      ...flowConfig,
      [jobName]: {
        ...flowConfig[jobName],
        [field]: value,
      },
    })
    setHasUnsavedFlowChanges(true)
  }

  const handleIdarrFlowConfigChange = (field: keyof IdarrFlowJobConfig, value: boolean | number[]) => {
    if (!flowConfig) return

    setFlowConfig({
      ...flowConfig,
      idarr: {
        ...flowConfig.idarr,
        [field]: value,
      },
    })
    setHasUnsavedFlowChanges(true)
  }

  const handleIdarrScopeToggle = (scopeIndex: number, included: boolean) => {
    if (!flowConfig) return
    const current = flowConfig.idarr.scope_indices
    const updated = included
      ? [...current, scopeIndex].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b)
      : current.filter(i => i !== scopeIndex)
    handleIdarrFlowConfigChange('scope_indices', updated)
  }

  const idarrScopeError = Boolean(
    flowConfig?.idarr.enabled &&
    idarrSyncTargets.length > 1 &&
    flowConfig.idarr.scope_indices.length === 0
  )

  const handleSaveFlowConfig = async () => {
    if (!flowConfig) return
    if (idarrScopeError) {
      showToast('Select at least one IDarr scope before saving', 'error')
      return
    }

    try {
      setSaving(true)
      await saveFlowConfig(flowConfig)
      originalFlowConfigRef.current = JSON.parse(JSON.stringify(flowConfig))
      setHasUnsavedFlowChanges(false)
      showToast('Flow configuration saved')
    } catch (error) {
      console.error('Error saving flow config:', error)
      showToast('Failed to save flow configuration', 'error')
    } finally {
      setSaving(false)
    }
  }

  const resetFlowConfigToOriginal = () => {
    if (!originalFlowConfigRef.current) return

    setFlowConfig(JSON.parse(JSON.stringify(originalFlowConfigRef.current)))
    setHasUnsavedFlowChanges(false)
  }

  const handleRunFlow = async (dryRun: boolean = false) => {
    if (!flowConfig) return

    try {
      setFlowRunning(true)
      setFlowResult(null)
      showToast(dryRun ? 'Starting workflow (dry run)...' : 'Starting workflow...', 'info')

      const result = await runFlow(dryRun ? { dry_run: true } : undefined)

      if (result.success && result.job_id) {
        showToast(result.message || 'Workflow started in background. Check Job Logs for progress.', 'info')
        trackedWorkflowJobRef.current = result.job_id

        setFlowResult({
          job_id: result.job_id,
          success: true,
          message: result.message,
          jobs_run: [],
          jobs_skipped: [],
          jobs_failed: [],
        })
      } else {
        setFlowRunning(false)
        showToast('Failed to start workflow', 'error')
      }
    } catch (error) {
      console.error('Error running flow:', error)
      setFlowRunning(false)
      showToast(getApiErrorMessage(error, 'Failed to run workflow'), 'error')
    }
  }

  return {
    flowConfig,
    flowRunning,
    flowResult,
    hasUnsavedFlowChanges,
    idarrSyncTargets,
    idarrShowInWorkflow,
    idarrScopeError,
    setFlowRunning,
    fetchFlowConfig,
    fetchIdarrSyncTargets,
    checkActiveWorkflow,
    handleFlowConfigChange,
    handleIdarrFlowConfigChange,
    handleIdarrScopeToggle,
    handleSaveFlowConfig,
    resetFlowConfigToOriginal,
    handleRunFlow,
  }
}
