import type { ReactNode } from 'react'

// The page tab strip: one row of underlined tabs that wraps into left-packed rows when it must.
//
//   <Tabs ariaLabel="Settings tabs" value={tab} onChange={setTab} items={[
//     { key: 'basic', label: 'General', icon: <Settings size={16} /> },
//   ]} />

export type TabItem<K extends string> = {
  key: K
  label: ReactNode
  icon?: ReactNode
  badge?: ReactNode
}

interface TabsProps<K extends string> {
  items: TabItem<K>[]
  value: K
  onChange: (key: K) => void
  ariaLabel: string
  className?: string
}

function Tabs<K extends string>({ items, value, onChange, ariaLabel, className }: TabsProps<K>) {
  return (
    <div className={className ? `pf-tabs ${className}` : 'pf-tabs'} role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="tab"
          aria-selected={value === item.key}
          className={value === item.key ? 'active' : undefined}
          onClick={() => onChange(item.key)}
        >
          {item.icon}
          {item.label}
          {item.badge}
        </button>
      ))}
    </div>
  )
}

export default Tabs
