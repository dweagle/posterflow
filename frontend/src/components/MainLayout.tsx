import { Outlet, useLocation } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { Github, Menu } from 'lucide-react'
import Sidebar from './Sidebar'
import './MainLayout.css'

function MainLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setSidebarOpen(false)
  }, [location.pathname])

  return (
    <div className="main-layout">
      {sidebarOpen && (
        <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
      )}
      <Sidebar isOpen={sidebarOpen} />
      <div className="content">
        <button
          className="sidebar-toggle"
          onClick={() => setSidebarOpen(prev => !prev)}
          aria-label="Toggle navigation"
        >
          <Menu size={20} />
        </button>
        <a
          className="github-link"
          href="https://github.com/dweagle/posterflow"
          target="_blank"
          rel="noopener noreferrer"
          title="View on GitHub"
          aria-label="View on GitHub"
        >
          <Github size={20} />
        </a>
        <Outlet />
      </div>
    </div>
  )
}

export default MainLayout