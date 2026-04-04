import { Outlet } from 'react-router-dom'
import { Github } from 'lucide-react'
import Sidebar from './Sidebar'
import './MainLayout.css'

function MainLayout() {
  return (
    <div className="main-layout">
      <Sidebar />
      <div className="content">
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