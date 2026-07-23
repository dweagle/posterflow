import { Download, RefreshCw, Save, Search } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import Toolbar from '../Toolbar'
import UnmatchedTypeSelector, { UnmatchedScope } from './UnmatchedTypeSelector'

type UnmatchedHeaderProps = {
  scope: UnmatchedScope
  onScopeChange: (scope: UnmatchedScope) => void
  settingsActive: boolean
  onShowSettings: () => void
  saving: boolean
  hasUnsavedChanges: boolean
  detecting: boolean
  hasResults: boolean
  onSaveSettings: () => void
  onDetect: () => void
  onViewSearch: () => void
  onDownloadReport: () => void
}

/**
 * The single Unmatched toolbar + asset-type switch row. Both the poster and artwork
 * views render this, so the header stays identical while only the content below swaps.
 */
function UnmatchedHeader({
  scope,
  onScopeChange,
  settingsActive,
  onShowSettings,
  saving,
  hasUnsavedChanges,
  detecting,
  hasResults,
  onSaveSettings,
  onDetect,
  onViewSearch,
  onDownloadReport,
}: UnmatchedHeaderProps) {
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
    <>
      <Toolbar title="Unmatched Assets" description="Media in your library missing the selected asset type">
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
        </div>
        <button
          className={`btn-toolbar ${hasUnsavedChanges ? 'btn-unsaved' : ''}`}
          onClick={onSaveSettings}
          disabled={saving || !hasUnsavedChanges}
          title={hasUnsavedChanges ? 'Save changes' : 'No changes to save'}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        <button className="btn-toolbar btn-primary" onClick={onDetect} disabled={detecting}>
          <RefreshCw size={16} />
          {detecting ? 'Detecting...' : 'Detect Unmatched'}
        </button>
        {hasResults && (
          <>
            <button className="btn-toolbar btn-primary" onClick={onViewSearch} title="View and search missing items">
              <Search size={16} />
              View / Search
            </button>
            <button className="btn-toolbar" onClick={onDownloadReport} title="Download report of missing items">
              <Download size={16} />
              Download Report
            </button>
          </>
        )}
      </Toolbar>

      <div className="unmatched-type-row">
        <UnmatchedTypeSelector
          value={scope}
          onChange={onScopeChange}
          settingsActive={settingsActive}
          onShowSettings={onShowSettings}
        />
      </div>
    </>
  )
}

export default UnmatchedHeader
