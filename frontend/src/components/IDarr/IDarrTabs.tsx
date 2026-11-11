import { Fingerprint, Settings as SettingsIcon } from 'lucide-react'
import Tabs, { type TabItem } from '../Tabs'

export type IDarrTab = 'IDarr' | 'settings'

type IDarrTabsProps = {
  activeTab: IDarrTab
  onTabChange: (tab: IDarrTab) => void
}

const ITEMS: TabItem<IDarrTab>[] = [
  { key: 'IDarr', label: 'IDarr', icon: <Fingerprint size={16} className="icon-idarr" /> },
  { key: 'settings', label: 'Settings', icon: <SettingsIcon size={16} className="icon-settings" /> },
]

function IDarrTabs({ activeTab, onTabChange }: IDarrTabsProps) {
  return <Tabs ariaLabel="IDarr tabs" items={ITEMS} value={activeTab} onChange={onTabChange} />
}

export default IDarrTabs
