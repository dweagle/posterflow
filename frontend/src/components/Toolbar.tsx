import { ReactNode } from 'react'

type ToolbarProps = {
  title: string
  /** Shown in the hover tooltip. Rich content is allowed (e.g. <strong>, <code>). */
  description: ReactNode
  titleControl?: ReactNode
  children?: ReactNode
}

/**
 * The page toolbar used across the app: title, hover-info tooltip, and action buttons.
 * Styling is shared in styles/globals.css, so every page gets the same bar.
 */

function Toolbar({ title, description, titleControl, children }: ToolbarProps) {
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
        {titleControl}
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
