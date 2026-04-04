import { Fingerprint, Settings as SettingsIcon } from 'lucide-react'

export type IDarrTab = 'IDarr' | 'settings'

type IDarrTabsProps = {
  activeTab: IDarrTab
  onTabChange: (tab: IDarrTab) => void
}

function IDarrTabs({ activeTab, onTabChange }: IDarrTabsProps) {
  return (
    <div className="poster-manager-tabs">
      <button className={activeTab === 'IDarr' ? 'active' : ''} onClick={() => onTabChange('IDarr')}>
        <Fingerprint size={16} className="tab-icon icon-idarr" />
        IDarr
      </button>
      <button className={activeTab === 'settings' ? 'active' : ''} onClick={() => onTabChange('settings')}>
        <SettingsIcon size={16} className="tab-icon icon-settings" />
        Settings
      </button>
    </div>
  )
}

export default IDarrTabs
