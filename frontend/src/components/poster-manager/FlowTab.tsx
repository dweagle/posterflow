import { AlertCircle, CheckCircle, Eye, Info, Save, Waves, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { FlowConfig, FlowResult, IdarrFlowJobConfig, MakerIdarrSyncTarget } from '../../api/client'
import Toolbar from './Toolbar'

type FlowTabProps = {
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
  onSaveFlowConfig: () => void
  onRunFlow: (dryRun: boolean) => void
  onChangeFlowConfig: (job: keyof FlowConfig, field: 'enabled' | 'stop_on_error' | 'delete_unknown', value: boolean) => void
  onChangeIdarrFlowConfig: (field: keyof IdarrFlowJobConfig, value: boolean | number[]) => void
  onToggleIdarrScope: (scopeIndex: number, included: boolean) => void
}

function FlowTab({
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
  onSaveFlowConfig,
  onRunFlow,
  onChangeFlowConfig,
  onChangeIdarrFlowConfig,
  onToggleIdarrScope,
}: FlowTabProps) {
  const navigate = useNavigate()

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
          {saving ? 'Saving...' : 'Save Settings'}
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
          <p className="job-description">Download latest posters from all subscribed Google Drives</p>
          {flowConfig.sync_drives.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.sync_drives.stop_on_error} onChange={(e) => onChangeFlowConfig('sync_drives', 'stop_on_error', e.target.checked)} />
                <span>Stop workflow if this step fails</span>
              </label>
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.rename_posters.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">{stepOffset + 2}</span>
              <span className="job-title">Rename & Organize Posters</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.rename_posters.enabled} onChange={(e) => onChangeFlowConfig('rename_posters', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Match posters to your media library and organize them in {destination || '/data/posters/organized'}</p>
          {flowConfig.rename_posters.enabled && (
            <div className="job-options">
              <label className="checkbox-option">
                <input type="checkbox" checked={flowConfig.rename_posters.stop_on_error} onChange={(e) => onChangeFlowConfig('rename_posters', 'stop_on_error', e.target.checked)} />
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
                    <div className="toolbar-tooltip">Removes asset folders for media no longer in Radarr/Sonarr/Plex, plus stale duplicate folders left after a rename. Runs after rename/border, before Plex upload.</div>
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
                      <div className="toolbar-tooltip">Also deletes folders that match no movie/show/collection and have no usable ID or title (custom or hand-dropped folders). Riskier — leave off unless your assets folder is strictly Radarr/Sonarr/Plex-managed.</div>
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
              <span className="job-title">Upload to Plex</span>
            </div>
            <label className="toggle-switch">
              <input type="checkbox" checked={flowConfig.plex_upload.enabled} onChange={(e) => onChangeFlowConfig('plex_upload', 'enabled', e.target.checked)} />
              <span className="toggle-slider"></span>
            </label>
          </div>
          <p className="job-description">Upload organized posters to your Plex libraries. Automatically re-uploads when a better style poster (e.g. CL2K over MM2K) replaces an existing one.</p>
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
          <p className="job-description">Check which media in your library is missing posters</p>
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
    </div>
  )
}

export default FlowTab
