import { ReactNode } from 'react'

type ToolbarProps = {
  title: string
  description: string
  children?: ReactNode
}

function Toolbar({ title, description, children }: ToolbarProps) {
  return (
    <div className="toolbar">
      <div className="toolbar-title">
        <h2>{title}</h2>
        <div className="toolbar-info">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="16" x2="12" y2="12" />
            <line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
          <div className="toolbar-tooltip">{description}</div>
        </div>
      </div>
      {children && (
        <div className="action-buttons">
          {children}
        </div>
      )}
    </div>
  )
}

export default Toolbar
