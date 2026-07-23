import { MutableRefObject, useEffect, useRef, useState } from 'react'
import {
  FlowConfig,
  FlowResult,
  IdarrFlowJobConfig,
  Workflow,
  createWorkflow,
  deleteWorkflow,
  getApiErrorMessage,
  getJobs,
  listWorkflows,
  runFlow,
  updateWorkflow,
} from '../api/client'
import { getMakerIdarrConfig, MakerIdarrSyncTarget } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

const SELECTED_WORKFLOW_STORAGE_KEY = 'posterflow.posterManager.selectedWorkflowId'

const deepCopy = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

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
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<number | null>(null)
  const [workflowName, setWorkflowName] = useState('')
  const [flowConfig, setFlowConfig] = useState<FlowConfig | null>(null)
  const [flowRunning, setFlowRunning] = useState(false)
  const [flowResult, setFlowResult] = useState<FlowResult | null>(null)
  const [hasUnsavedFlowChanges, setHasUnsavedFlowChanges] = useState(false)
  const [idarrSyncTargets, setIdarrSyncTargets] = useState<MakerIdarrSyncTarget[]>([])
  const [idarrShowInWorkflow, setIdarrShowInWorkflow] = useState(false)

  const originalFlowConfigRef = useRef<FlowConfig | null>(null)
  const originalWorkflowNameRef = useRef<string>('')

  useEffect(() => {
    if (!flowConfig || !originalFlowConfigRef.current) return
    const configChanged = JSON.stringify(flowConfig) !== JSON.stringify(originalFlowConfigRef.current)
    const nameChanged = workflowName !== originalWorkflowNameRef.current
    setHasUnsavedFlowChanges(configChanged || nameChanged)
  }, [flowConfig, workflowName])

  const applyWorkflow = (wf: Workflow) => {
    setSelectedWorkflowId(wf.id)
    setWorkflowName(wf.name)
    setFlowConfig(wf.config)
    originalFlowConfigRef.current = deepCopy(wf.config)
    originalWorkflowNameRef.current = wf.name
    setHasUnsavedFlowChanges(false)
    try {
      localStorage.setItem(SELECTED_WORKFLOW_STORAGE_KEY, String(wf.id))
    } catch {
      // ignore storage failures
    }
  }

  // Loads the saved workflows and selects the persisted / default one.
  // Named fetchFlowConfig to keep the existing lifecycle wiring.
  const fetchFlowConfig = async () => {
    try {
      const list = await listWorkflows()
      setWorkflows(list)
      if (list.length === 0) {
        setFlowConfig(null)
        setSelectedWorkflowId(null)
        return
      }
      let storedId: number | null = null
      try {
        const raw = localStorage.getItem(SELECTED_WORKFLOW_STORAGE_KEY)
        storedId = raw ? Number(raw) : null
      } catch {
        storedId = null
      }
      const selected =
        list.find((w) => w.id === storedId) ||
        list.find((w) => w.is_default) ||
        list[0]
      applyWorkflow(selected)
    } catch (error) {
      console.error('Error fetching workflows:', error)
      showToast('Failed to load workflows', 'error')
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

  const handleFlowConfigChange = (jobName: keyof FlowConfig, field: 'enabled' | 'stop_on_error' | 'delete_unknown' | 'posters' | 'artwork', value: boolean) => {
    if (!flowConfig) return

    setFlowConfig({
      ...flowConfig,
      [jobName]: {
        ...flowConfig[jobName],
        [field]: value,
      },
    })
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
  }

  const handleIdarrScopeToggle = (scopeIndex: number, included: boolean) => {
    if (!flowConfig) return
    const current = flowConfig.idarr.scope_indices
    const updated = included
      ? [...current, scopeIndex].filter((v, i, a) => a.indexOf(v) === i).sort((a, b) => a - b)
      : current.filter(i => i !== scopeIndex)
    handleIdarrFlowConfigChange('scope_indices', updated)
  }

  const handleWorkflowNameChange = (name: string) => {
    setWorkflowName(name)
  }

  const idarrScopeError = Boolean(
    flowConfig?.idarr.enabled &&
    idarrSyncTargets.length > 1 &&
    flowConfig.idarr.scope_indices.length === 0
  )

  const handleSaveFlowConfig = async () => {
    if (!flowConfig || selectedWorkflowId == null) return
    if (idarrScopeError) {
      showToast('Select at least one IDarr scope before saving', 'error')
      return
    }
    const trimmedName = workflowName.trim()
    if (!trimmedName) {
      showToast('Workflow name cannot be empty', 'error')
      return
    }

    try {
      setSaving(true)
      const updated = await updateWorkflow(selectedWorkflowId, { name: trimmedName, config: flowConfig })
      setWorkflows(prev => prev.map(w => (w.id === updated.id ? updated : w)))
      applyWorkflow(updated)
      showToast('Workflow saved')
    } catch (error) {
      console.error('Error saving workflow:', error)
      showToast(getApiErrorMessage(error, 'Failed to save workflow'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const selectWorkflow = (id: number) => {
    if (id === selectedWorkflowId) return
    const wf = workflows.find(w => w.id === id)
    if (wf) applyWorkflow(wf)
  }

  const createNewWorkflow = async () => {
    const existing = new Set(workflows.map(w => w.name))
    let name = 'New Workflow'
    let counter = 2
    while (existing.has(name)) {
      name = `New Workflow ${counter}`
      counter += 1
    }
    try {
      setSaving(true)
      const created = await createWorkflow(name)
      const list = await listWorkflows()
      setWorkflows(list)
      applyWorkflow(created)
      showToast(`Created workflow "${created.name}"`)
    } catch (error) {
      console.error('Error creating workflow:', error)
      showToast(getApiErrorMessage(error, 'Failed to create workflow'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const deleteSelectedWorkflow = async () => {
    if (selectedWorkflowId == null) return
    if (workflows.length <= 1) {
      showToast('Cannot delete the only workflow', 'error')
      return
    }
    try {
      setSaving(true)
      await deleteWorkflow(selectedWorkflowId)
      const list = await listWorkflows()
      setWorkflows(list)
      const next = list.find(w => w.is_default) || list[0]
      if (next) applyWorkflow(next)
      showToast('Workflow deleted')
    } catch (error) {
      console.error('Error deleting workflow:', error)
      showToast(getApiErrorMessage(error, 'Failed to delete workflow'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const resetFlowConfigToOriginal = () => {
    if (!originalFlowConfigRef.current) return

    setFlowConfig(deepCopy(originalFlowConfigRef.current))
    setWorkflowName(originalWorkflowNameRef.current)
    setHasUnsavedFlowChanges(false)
  }

  const handleRunFlow = async (dryRun: boolean = false) => {
    if (!flowConfig || selectedWorkflowId == null) return

    try {
      setFlowRunning(true)
      setFlowResult(null)
      showToast(dryRun ? 'Starting workflow (dry run)...' : 'Starting workflow...', 'info')

      const result = await runFlow({ dry_run: dryRun, workflow_id: selectedWorkflowId })

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
    workflows,
    selectedWorkflowId,
    workflowName,
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
    handleWorkflowNameChange,
    handleSaveFlowConfig,
    selectWorkflow,
    createNewWorkflow,
    deleteSelectedWorkflow,
    resetFlowConfigToOriginal,
    handleRunFlow,
  }
}
