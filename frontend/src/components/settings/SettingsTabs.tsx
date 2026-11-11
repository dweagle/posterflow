import { Bell, Calendar, Cloud, Database, FileCode, Server, Settings, Shield, Wrench } from 'lucide-react'
import Tabs, { type TabItem } from '../Tabs'

type SettingsTab = 'basic' | 'notifications' | 'rclone' | 'media' | 'scheduling' | 'backup' | 'maintenance' | 'security' | 'scripts'

type SettingsTabsProps = {
  activeTab: SettingsTab
  onTabChange: (tab: SettingsTab) => void
}

const ITEMS: TabItem<SettingsTab>[] = [
  { key: 'basic', label: 'General', icon: <Settings size={16} /> },
  { key: 'notifications', label: 'Notifications', icon: <Bell size={16} /> },
  { key: 'scheduling', label: 'Scheduling', icon: <Calendar size={16} /> },
  { key: 'media', label: 'Media Servers', icon: <Server size={16} /> },
  { key: 'rclone', label: 'Rclone', icon: <Cloud size={16} /> },
  { key: 'backup', label: 'Backup & Restore', icon: <Database size={16} /> },
  { key: 'maintenance', label: 'Maintenance', icon: <Wrench size={16} /> },
  { key: 'security', label: 'Security', icon: <Shield size={16} /> },
  { key: 'scripts', label: 'Scripts', icon: <FileCode size={16} /> },
]

function SettingsTabs({ activeTab, onTabChange }: SettingsTabsProps) {
  return <Tabs ariaLabel="Settings tabs" items={ITEMS} value={activeTab} onChange={onTabChange} />
}

export default SettingsTabs
