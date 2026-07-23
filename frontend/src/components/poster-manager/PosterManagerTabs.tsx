import { Layers, AlertCircle, Waves, Settings as SettingsIcon, FolderSync, ListTree } from 'lucide-react'

export type PosterManagerTab = 'priority' | 'settings' | 'unmatched' | 'flow' | 'rename' | 'border'

type PosterManagerTabsProps = {
  activeTab: PosterManagerTab
  onTabChange: (tab: PosterManagerTab) => void
  unmatchedCount: number
}

function PosterManagerTabs({ activeTab, onTabChange, unmatchedCount }: PosterManagerTabsProps) {
  return (
    <div className="poster-manager-tabs">
      <button className={activeTab === 'flow' ? 'active' : ''} onClick={() => onTabChange('flow')}>
        <Waves size={16} className="tab-icon icon-workflow" />
        Workflow
      </button>
      <button className={activeTab === 'rename' ? 'active' : ''} onClick={() => onTabChange('rename')}>
        <FolderSync size={16} className="tab-icon icon-rename" />
        Asset Renamer
      </button>
      <button className={activeTab === 'border' ? 'active' : ''} onClick={() => onTabChange('border')}>
        <Layers size={16} className="tab-icon icon-border" />
        Border Replacer
      </button>
      <button className={activeTab === 'unmatched' ? 'active' : ''} onClick={() => onTabChange('unmatched')}>
        <AlertCircle size={16} className="tab-icon icon-unmatched" />
        Unmatched Assets
        {unmatchedCount > 0 && <span className="tab-badge">{unmatchedCount}</span>}
      </button>
      <button className={activeTab === 'priority' ? 'active' : ''} onClick={() => onTabChange('priority')}>
        <ListTree size={16} className="tab-icon icon-priority" />
        Drive Priority
      </button>
      <button className={activeTab === 'settings' ? 'active' : ''} onClick={() => onTabChange('settings')}>
        <SettingsIcon size={16} className="tab-icon icon-settings" />
        Settings
      </button>
    </div>
  )
}

export default PosterManagerTabs
