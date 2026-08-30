import { AlertCircle, CheckCircle, Eye, Info, Plus, Save, Trash2, Waves, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { FlowConfig, FlowResult, IdarrFlowJobConfig, MakerIdarrSyncTarget, Workflow } from '../../api/client'
import ConfirmDialog from '../ConfirmDialog'
import Toolbar from '../Toolbar'

type FlowTabProps = {
  workflows: Workflow[]
  selectedWorkflowId: number | null
  workflowName: string
  flowConfig: FlowConfig
  flowResult: FlowResult | null
  destination?: string
  hasUnsavedFlowChanges: boolean
  saving: boolean
  flowRunning: boolean
  idarrSyncTargets: MakerIdarrSyncTarget[]
  idarrShowInWorkflow: boolean
  idarrScopeError: boolean
  formatPercent: (percent: number) => string
  onSelectWorkflow: (id: number) => void
  onChangeWorkflowName: (name: string) => void
  onCreateWorkflow: () => Promise<void> | void
  onDeleteWorkflow: () => void
  onSaveFlowConfig: () => void
  onRunFlow: (dryRun: boolean) => void
  onChangeFlowConfig: (job: keyof FlowConfig, field: 'enabled' | 'stop_on_error' | 'delete_unknown' | 'posters' | 'artwork', value: boolean) => void
  onChangeIdarrFlowConfig: (field: keyof IdarrFlowJobConfig, value: boolean | number[]) => void
  onToggleIdarrScope: (scopeIndex: number, included: boolean) => void
}

function FlowTab({
  workflows,
  selectedWorkflowId,
  workflowName,
  flowConfig,
  flowResult,
  destination,
  hasUnsavedFlowChanges,
  saving,
  flowRunning,
  idarrSyncTargets,
  idarrShowInWorkflow,
  idarrScopeError,
  formatPercent,
  onSelectWorkflow,
  onChangeWorkflowName,
  onCreateWorkflow,
  onDeleteWorkflow,
  onSaveFlowConfig,
  onRunFlow,
  onChangeFlowConfig,
  onChangeIdarrFlowConfig,
  onToggleIdarrScope,
}: FlowTabProps) {
  const navigate = useNavigate()

  const nameInputRef = useRef<HTMLInputElement>(null)
  const [pending, setPending] = useState<{ type: 'switch'; id: number } | { type: 'create' } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const selectedName = workflows.find((w) => w.id === selectedWorkflowId)?.name ?? workflowName

  const runCreate = async () => {
    await onCreateWorkflow()
    // Focus and select the default name so the user can rename it immediately.
    requestAnimationFrame(() => {
      nameInputRef.current?.focus()
      nameInputRef.current?.select()
    })
  }

  const handleSelectWorkflow = (id: number) => {
    if (id === selectedWorkflowId) return
    if (hasUnsavedFlowChanges) {
      setPending({ type: 'switch', id })
    } else {
      onSelectWorkflow(id)
    }
  }

  const handleCreateWorkflow = () => {
    if (hasUnsavedFlowChanges) {
      setPending({ type: 'create' })
    } else {
      void runCreate()
    }
  }

  const confirmPending = () => {
    if (!pending) return
    if (pending.type === 'switch') {
      onSelectWorkflow(pending.id)
    } else {
      void runCreate()
    }
    setPending(null)
  }

  const showIdarr = idarrShowInWorkflow
  const totalSteps = showIdarr ? 6 : 5
  const stepOffset = showIdarr ? 1 : 0

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  return (
    <div className="flow-container">
      <Toolbar title="Poster Workflow" description="Configure and run the complete poster management workflow">
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
        </div>
        <button className={`btn-toolbar ${hasUnsavedFlowChanges ? 'btn-unsaved' : ''}`} onClick={onSaveFlowConfig} disabled={idarrScopeError || !hasUnsavedFlowChanges || saving} title={idarrScopeError ? 'Select at least one IDarr scope' : hasUnsavedFlowChanges ? undefined : 'No changes to save'}>
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Workflow'}
        </button>
        <button className="btn-toolbar" onClick={() => onRunFlow(true)} disabled={flowRunning} title="Dry run workflow">
          <Eye size={16} />
          Dry Run
        </button>
        <button className="btn-toolbar btn-primary run-flow-btn" onClick={() => onRunFlow(false)} disabled={flowRunning}>
          <Waves size={16} />
          {flowRunning ? 'Running...' : 'Run Workflow'}
        </button>
      </Toolbar>

      <div className="workflow-selector-bar">
        <div className="workflow-selector-field">
          <label htmlFor="workflow-select">Workflow</label>
          <select
            id="workflow-select"
            className="workflow-select"
            value={selectedWorkflowId ?? ''}
            onChange={(e) => handleSelectWorkflow(Number(e.target.value))}
          >
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}{w.is_default ? ' (default)' : ''}
              </option>
            ))}
          </select>
        </div>
        <div className="workflow-selector-field grow">
          <label htmlFor="workflow-name">Name</label>
          <input
            id="workflow-name"
            ref={nameInputRef}
            className="workflow-name-input"
            type="text"
            value={workflowName}
            onChange={(e) => onChangeWorkflowName(e.target.value)}
            placeholder="Workflow name"
          />
        </div>
        <button className="btn-toolbar" onClick={handleCreateWorkflow} disabled={saving} title="Create a new workflow">
          <Plus size={16} />
          New
        </button>
        <button
          className="btn-toolbar btn-danger"
          onClick={() => setConfirmDelete(true)}
          disabled={saving || workflows.length <= 1}
          title={workflows.length <= 1 ? 'Cannot delete the only workflow' : 'Delete this workflow'}
        >
          <Trash2 size={16} />
          Delete
        </button>
      </div>

      <div className="flow-jobs">
        {showIdarr && (
          <div className={`flow-job-card ${!flowConfig.idarr.enabled ? 'disabled' : ''}`}>
            <div className="job-header">
              <div className="job-header-left">
                <span className="job-number">1</span>
                <span className="job-title">IDarr Rename</span>
              </div>
              <label className="toggle-switch">
                <input type="checkbox" checked={flowConfig.idarr.enabled} onChange={(e) => onChangeIdarrFlowConfig('enabled', e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
            </div>
            <p className="job-description">Process and rename poster-maker files using IDarr before syncing</p>
            {flowConfig.idarr.enabled && (
              <div className="job-options">
                <label className="checkbox-option">
                  <input type="checkbox" checked={flowConfig.idarr.stop_on_error} onChange={(e) => onChangeIdarrFlowConfig('stop_on_error', e.target.checked)} />
                  <span>Stop workflow if this step fails</span>
                </label>
                <label className="checkbox-option">
                  <input type="checkbox" checked={flowConfig.idarr.sync_after_run} onChange={(e) => onChangeIdarrFlowConfig('sync_after_run', e.target.checked)} />
                  <span>Queue personal drive sync after IDarr runs</span>
                </label>
                {idarrSyncTargets.length > 1 && (
                  <div className="idarr-scope-selector">
                    <span className="scope-selector-label">Scopes to run:</span>
                    {idarrSyncTargets.map((target, i) => {
                      const isSelected = flowConfig.idarr.scope_indices.includes(i)
                      return (
                        <label key={i} className="checkbox-option">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={(e) => onToggleIdarrScope(i, e.target.checked)}
                          />
                          <span>{target.label || `Target ${i + 1}`}</span>
                        </label>
                      )
                    })}
                    {idarrScopeError && (
                      <span className="scope-error">* At least one scope must be selected.</span>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        <div className={`flow-job-card ${!flowConfig.sync_drives.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{stepOffset + 1}</span>
              <span className="job-title">Sync Google Drives</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.sync_drives.enabled} onChange={(e) => onChangeFlowConfig('sync_drives', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Download the latest assets from your subscribed Google Drives</p>
          {flowConfig.sync_drives.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.sync_drives.stop_on_error} onChange={(e) => onChangeFlowConfig('sync_drives', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
              {/* Separate drive types with separate syncs — sync what this run will organize. */}
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.sync_drives.posters} onChange={(e) => onChangeFlowConfig('sync_drives', 'posters', e.target.checked)} />
                <span>Sync poster drives</span>
              </label>
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.sync_drives.artwork} onChange={(e) => onChangeFlowConfig('sync_drives', 'artwork', e.target.checked)} />
                <span>Sync artwork drives (logos, backgrounds, square art)</span>
              </label>
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.rename_assets.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{stepOffset + 2}</span>
              <span className="job-title">Rename & Organize Assets</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.rename_assets.enabled} onChange={(e) => onChangeFlowConfig('rename_assets', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Match posters and artwork to your media library and organize them in {destination || '/data/posters/organized'} (uses the Include selection on the Asset Renamer tab)</p>
          {flowConfig.rename_assets.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.rename_assets.stop_on_error} onChange={(e) => onChangeFlowConfig('rename_assets', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
              <div className="checkbox-option-row">
                <div className="checkbox-option-with-info">
                  <label className="checkbox-option">
                    <input type="checkbox" checked={!!flowConfig.cleanup_assets?.enabled} onChange={(e) => onChangeFlowConfig('cleanup_assets', 'enabled', e.target.checked)} />
                    <span>Asset Cleanup</span>
                  </label>
                  <span className="toolbar-info" tabIndex={0}>
                    <Info size={14} />
                    <div className="toolbar-tooltip">Removes asset folders for media no longer in Radarr/Sonarr/your media servers, plus stale duplicate folders left after a rename. Runs after rename/border, before Asset Upload.</div>
                  </span>
                </div>
                {flowConfig.cleanup_assets?.enabled && (
                  <div className="checkbox-option-with-info">
                    <label className="checkbox-option">
                      <input type="checkbox" checked={!!flowConfig.cleanup_assets?.delete_unknown} onChange={(e) => onChangeFlowConfig('cleanup_assets', 'delete_unknown', e.target.checked)} />
                      <span>Also remove unknown/stray folders</span>
                    </label>
                    <span className="toolbar-info" tabIndex={0}>
                      <Info size={14} />
                      <div className="toolbar-tooltip">Also deletes folders that match no movie/show/collection and have no usable ID or title (custom or hand-dropped folders). Riskier — leave off unless your assets folder is strictly managed by Radarr/Sonarr/your media servers.</div>
                    </span>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.border_replacer.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{stepOffset + 3}</span>
              <span className="job-title">Run Border Replacer</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.border_replacer.enabled} onChange={(e) => onChangeFlowConfig('border_replacer', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Add custom colored borders or remove borders from posters</p>
          {flowConfig.border_replacer.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.border_replacer.stop_on_error} onChange={(e) => onChangeFlowConfig('border_replacer', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.plex_upload.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{stepOffset + 4}</span>
              <span className="job-title">Asset Upload</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.plex_upload.enabled} onChange={(e) => onChangeFlowConfig('plex_upload', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Upload organized posters to your media server libraries. Automatically re-uploads when a better style poster (e.g. CL2K over MM2K) replaces an existing one.</p>
          {flowConfig.plex_upload.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.plex_upload.stop_on_error} onChange={(e) => onChangeFlowConfig('plex_upload', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.detect_unmatched.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{totalSteps}</span>
              <span className="job-title">Detect Unmatched Assets</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.detect_unmatched.enabled} onChange={(e) => onChangeFlowConfig('detect_unmatched', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Check which media in your library is missing assets — one pass covers posters and artwork (logos, backgrounds, square art)</p>
          {flowConfig.detect_unmatched.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.detect_unmatched.stop_on_error} onChange={(e) => onChangeFlowConfig('detect_unmatched', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
            </div>
          )}
        </div>
      </div>

      {flowResult && (
        <div className="flow-results">
          <h3>Workflow Status</h3>

          {flowResult.message && (
            <div className="results-section">
              <div className="result-item info">
                <CheckCircle size={16} />
                <span>{flowResult.message}</span>
              </div>
              <p className="flow-job-meta">Job ID: {flowResult.job_id} • Monitor progress in Job Logs</p>
            </div>
          )}

          {flowResult.jobs_run && flowResult.jobs_run.length > 0 && (
            <div className="results-section">
              <h4><CheckCircle size={16} /> Completed Jobs ({flowResult.jobs_run.length})</h4>
              {flowResult.jobs_run.map((job, idx) => (
                <div key={idx} className="result-item success compact">
                  <strong>{job.job.replace(/_/g, ' ')}</strong>
                  <span className="result-metrics">
                    {job.drives_synced !== undefined && <span>{job.drives_synced} drives synced</span>}
                    {job.stats && <span>{job.stats.total_matched || 0} posters organized</span>}
                    {job.unmatched_count !== undefined && <span>{job.unmatched_count} unmatched found</span>}
                  </span>
                </div>
              ))}
            </div>
          )}

          {flowResult.jobs_skipped && flowResult.jobs_skipped.length > 0 && (
            <div className="results-section">
              <h4><AlertCircle size={16} /> Skipped Jobs ({flowResult.jobs_skipped.length})</h4>
              {flowResult.jobs_skipped.map((job, idx) => (
                <div key={idx} className="result-item skipped">
                  <strong>{job.job.replace(/_/g, ' ')}</strong> - {job.reason}
                </div>
              ))}
            </div>
          )}

          {flowResult.jobs_failed && flowResult.jobs_failed.length > 0 && (
            <div className="results-section">
              <h4><X size={16} /> Failed Jobs ({flowResult.jobs_failed.length})</h4>
              {flowResult.jobs_failed.map((job, idx) => (
                <div key={idx} className="result-item error">
                  <strong>{job.job.replace(/_/g, ' ')}</strong> - {job.error}
                </div>
              ))}
            </div>
          )}

          {flowResult.unmatched_detection && (
            <div className="results-section">
              <h4>Unmatched Summary</h4>
              <div className="unmatched-summary">
                <span>{flowResult.unmatched_detection.summary.grand_total.total} Total Items</span>
                <span className="divider">•</span>
                <span className={flowResult.unmatched_detection.summary.grand_total.unmatched > 0 ? 'missing' : 'complete'}>
                  {flowResult.unmatched_detection.summary.grand_total.unmatched} Missing
                </span>
                <span className="divider">•</span>
                <span>{formatPercent(flowResult.unmatched_detection.summary.grand_total.percent_complete)}% Complete</span>
              </div>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        isOpen={pending !== null}
        title="Unsaved changes"
        message={
          pending?.type === 'create'
            ? 'You have unsaved changes to this workflow. Discard them and create a new workflow?'
            : 'You have unsaved changes to this workflow. Discard them and switch?'
        }
        confirmText="Discard & continue"
        cancelText="Keep editing"
        variant="warning"
        onConfirm={confirmPending}
        onCancel={() => setPending(null)}
      />

      <ConfirmDialog
        isOpen={confirmDelete}
        title="Delete workflow"
        message={`Delete workflow "${selectedName}"? This cannot be undone.`}
        confirmText="Delete"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => {
          setConfirmDelete(false)
          onDeleteWorkflow()
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  )
}

export default FlowTab
