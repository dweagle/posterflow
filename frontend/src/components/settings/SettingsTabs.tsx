import { Bell, Calendar, Cloud, Database, Server, Settings, Shield, Wrench } from 'lucide-react'

type SettingsTab = 'basic' | 'notifications' | 'rclone' | 'media' | 'scheduling' | 'backup' | 'maintenance' | 'security'

type SettingsTabsProps = {
  activeTab: SettingsTab
  onTabChange: (tab: SettingsTab) => void
}

function SettingsTabs({ activeTab, onTabChange }: SettingsTabsProps) {
  return (
    <div className="settings-tabs">
      <button className={activeTab === 'basic' ? 'active' : ''} onClick={() => onTabChange('basic')}>
        <Settings size={16} className="tab-icon" />
        General
      </button>
      <button className={activeTab === 'notifications' ? 'active' : ''} onClick={() => onTabChange('notifications')}>
        <Bell size={16} className="tab-icon" />
        Notifications
      </button>
      <button className={activeTab === 'scheduling' ? 'active' : ''} onClick={() => onTabChange('scheduling')}>
        <Calendar size={16} className="tab-icon" />
        Scheduling
      </button>
      <button className={activeTab === 'media' ? 'active' : ''} onClick={() => onTabChange('media')}>
        <Server size={16} className="tab-icon" />
        Media Servers
      </button>
      <button className={activeTab === 'rclone' ? 'active' : ''} onClick={() => onTabChange('rclone')}>
        <Cloud size={16} className="tab-icon" />
        Rclone Configuration
      </button>
      <button className={activeTab === 'backup' ? 'active' : ''} onClick={() => onTabChange('backup')}>
        <Database size={16} className="tab-icon" />
        Backup & Restore
      </button>
      <button className={activeTab === 'maintenance' ? 'active' : ''} onClick={() => onTabChange('maintenance')}>
        <Wrench size={16} className="tab-icon" />
        Maintenance
      </button>
      <button className={activeTab === 'security' ? 'active' : ''} onClick={() => onTabChange('security')}>
        <Shield size={16} className="tab-icon" />
        Security
      </button>
    </div>
  )
}

export default SettingsTabs
