import { AlertCircle, CheckCircle, Eye, Save, Waves, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { FlowConfig, FlowResult } from '../../api/client'

type FlowTabProps = {
  flowConfig: FlowConfig
  flowResult: FlowResult | null
  destination?: string
  hasUnsavedFlowChanges: boolean
  saving: boolean
  flowRunning: boolean
  formatPercent: (percent: number) => string
  onSaveFlowConfig: () => void
  onRunFlow: (dryRun: boolean) => void
  onChangeFlowConfig: (job: keyof FlowConfig, field: 'enabled' | 'stop_on_error', value: boolean) => void
}

function FlowTab({
  flowConfig,
  flowResult,
  destination,
  hasUnsavedFlowChanges,
  saving,
  flowRunning,
  formatPercent,
  onSaveFlowConfig,
  onRunFlow,
  onChangeFlowConfig,
}: FlowTabProps) {
  const navigate = useNavigate()

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
      <div className="flow-header">
        <div>
          <h2>Poster Workflow</h2>
          <p>Configure and run the complete poster management workflow</p>
        </div>
        <div className="action-buttons">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
          <button className={`btn-toolbar ${hasUnsavedFlowChanges ? 'btn-unsaved' : ''}`} onClick={onSaveFlowConfig} disabled={!hasUnsavedFlowChanges || saving} title={hasUnsavedFlowChanges ? 'Save changes' : 'No changes to save'}>
            <Save size={16} />
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
          <button className="btn-toolbar" onClick={() => onRunFlow(true)} disabled={flowRunning} title="Dry run workflow">
            <Eye size={16} />
            Dry Run
          </button>
          <button className="btn-toolbar run-flow-btn" onClick={() => onRunFlow(false)} disabled={flowRunning}>
            <Waves size={16} />
            {flowRunning ? 'Running...' : 'Run Workflow'}
          </button>
        </div>
      </div>

      <div className="flow-jobs">
        <div className={`flow-job-card ${!flowConfig.sync_drives.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">1</span>
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
              <span className="job-number">2</span>
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
            </div>
          )}
        </div>

        <div className={`flow-job-card ${!flowConfig.border_replacer.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">3</span>
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

        <div className={`flow-job-card ${!flowConfig.detect_unmatched.enabled ? 'disabled' : ''}`}>
          <div className="job-header">
            <div className="job-header-left">
              <span className="job-number">4</span>
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
