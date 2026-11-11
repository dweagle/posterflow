import { Layers, AlertCircle, Waves, Settings as SettingsIcon, FolderSync, ListTree } from 'lucide-react'
import Tabs, { type TabItem } from '../Tabs'

export type PosterManagerTab = 'priority' | 'settings' | 'unmatched' | 'flow' | 'rename' | 'border'

type PosterManagerTabsProps = {
  activeTab: PosterManagerTab
  onTabChange: (tab: PosterManagerTab) => void
  unmatchedCount: number
}

function PosterManagerTabs({ activeTab, onTabChange, unmatchedCount }: PosterManagerTabsProps) {
  const items: TabItem<PosterManagerTab>[] = [
    { key: 'flow', label: 'Workflow', icon: <Waves size={16} className="icon-workflow" /> },
    { key: 'rename', label: 'Asset Renamer', icon: <FolderSync size={16} className="icon-rename" /> },
    { key: 'border', label: 'Border Replacer', icon: <Layers size={16} className="icon-border" /> },
    {
      key: 'unmatched',
      label: 'Unmatched Assets',
      icon: <AlertCircle size={16} className="icon-unmatched" />,
      badge: unmatchedCount > 0 ? <span className="tab-badge">{unmatchedCount}</span> : undefined,
    },
    { key: 'priority', label: 'Drive Priority', icon: <ListTree size={16} className="icon-priority" /> },
    { key: 'settings', label: 'Settings', icon: <SettingsIcon size={16} className="icon-settings" /> },
  ]
  return <Tabs ariaLabel="Asset Manager tabs" items={items} value={activeTab} onChange={onTabChange} />
}

export default PosterManagerTabs
