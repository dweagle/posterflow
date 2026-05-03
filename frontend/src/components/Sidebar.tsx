import { NavLink, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { HardDriveDownload, LayoutDashboard, Logs, Settings, Image, Search, UploadCloud, Fingerprint, Wrench } from 'lucide-react'
import { useUnmatched } from '../contexts/UnmatchedContext'
import { formatJobType } from '../api/client'
import posterFlowIcon from '../assets/PosterFlow.webp'
import './Sidebar.css'

type VersionUpdateStatus = {
  current_version: string
  latest_version: string | null
  update_available: boolean
  releases_url: string | null
  release_notes: string | null
}

function Sidebar() {
  const location = useLocation()
  const { unmatchedCount, idarrPendingCount, jobs } = useUnmatched()
  const [version, setVersion] = useState<string>('0.1.0')
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  const [releasesUrl, setReleasesUrl] = useState<string>('')
  const [releaseNotes, setReleaseNotes] = useState<string | null>(null)
  const [showReleaseNotes, setShowReleaseNotes] = useState(false)
  const [displayProgress, setDisplayProgress] = useState<number>(0)
  const lastDisplayedJobIdRef = useRef<number | null>(null)
  const releaseNotesWrapperRef = useRef<HTMLDivElement | null>(null)

  const truncateText = (value: string | null, maxLength: number): string => {
    const normalized = String(value || '').trim()
    if (normalized.length <= maxLength) {
      return normalized
    }
    return `${normalized.slice(0, maxLength)}…`
  }

  const runningJobs = jobs
    .filter(job => job.status === 'running')
    .sort((a, b) => b.id - a.id)
  const currentRunningJob = runningJobs[0] || null
  const isDashboardRoute = location.pathname === '/'
  const showMiniProgress = Boolean(currentRunningJob) && !isDashboardRoute

  useEffect(() => {
    if (!currentRunningJob) {
      lastDisplayedJobIdRef.current = null
      setDisplayProgress(0)
      return
    }

    const incomingProgress = Math.max(0, Math.min(100, Number(currentRunningJob.progress || 0)))

    if (lastDisplayedJobIdRef.current !== currentRunningJob.id) {
      lastDisplayedJobIdRef.current = currentRunningJob.id
      setDisplayProgress(incomingProgress)
      return
    }

    setDisplayProgress(prev => Math.max(prev, incomingProgress))
  }, [currentRunningJob])

  useEffect(() => {
    let isMounted = true

    const loadVersionInfo = async () => {
      try {
        const response = await fetch('/api/version/update')
        if (!response.ok) {
          return
        }

        const payload = (await response.json()) as VersionUpdateStatus
        if (!isMounted) {
          return
        }

        if (payload.current_version) {
          setVersion(payload.current_version)
        }
        if (payload.update_available && payload.latest_version) {
          setLatestVersion(payload.latest_version)
          setReleaseNotes(payload.release_notes ?? null)
        } else {
          setLatestVersion(null)
          setReleaseNotes(null)
        }
        if (payload.releases_url) {
          setReleasesUrl(payload.releases_url)
        }
      } catch {
      }
    }

    void loadVersionInfo()

    return () => {
      isMounted = false
    }
  }, [])

  useEffect(() => {
    if (!showReleaseNotes) return
    const handleClickOutside = (e: MouseEvent) => {
      if (releaseNotesWrapperRef.current && !releaseNotesWrapperRef.current.contains(e.target as Node)) {
        setShowReleaseNotes(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [showReleaseNotes])

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <div className="logo-container">
          <img src={posterFlowIcon} alt="PosterFlow" className="logo-icon" />
          <div className="title-group">
            <h1>PosterFlow</h1>
            <p className="inspired-byline">DAPS Reimagined</p>
            <p className="version">v{version}</p>
            {latestVersion && releasesUrl && (
              <div className="version-update-wrapper" ref={releaseNotesWrapperRef}>
                <button
                  className="version-update-badge"
                  onClick={() => setShowReleaseNotes(prev => !prev)}
                  title={`Update available | Current: ${version} | Latest: ${latestVersion}`}
                >
                  Update Available
                </button>
                {showReleaseNotes && (
                  <div className="release-notes-popover">
                    <div className="release-notes-header">
                      <span>What's New in {latestVersion}</span>
                      <a href={releasesUrl} target="_blank" rel="noopener noreferrer">View on GitHub</a>
                    </div>
                    <div className="release-notes-body">
                      {releaseNotes
                        ? releaseNotes.split('\n').map((line, i) => <p key={i}>{line}</p>)
                        : <p>See GitHub releases for details.</p>
                      }
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
      
      <nav className="sidebar-nav">
        <NavLink to="/" end className={({ isActive }) => isActive ? 'active' : ''} data-label="Dashboard" aria-label="Dashboard">
          <span className="icon"><LayoutDashboard size={20} color="#ff8800" /></span>
          <span className="nav-label">Dashboard</span>
        </NavLink>

        <NavLink to="/poster-manager" className={({ isActive }) => isActive ? 'active' : ''} data-label="Poster Manager" aria-label="Poster Manager">
          <span className="icon"><Image size={20} color="#a855f7" /></span>
          <span className="nav-label">Poster Manager</span>
          {unmatchedCount > 0 && (
            <span className="sidebar-badge">{unmatchedCount}</span>
          )}
        </NavLink>

        <NavLink to="/drives" className={({ isActive }) => isActive ? 'active' : ''} data-label="GDrives" aria-label="GDrives">
          <span className="icon"><HardDriveDownload size={20} color="#4285F4" /></span>
          <span className="nav-label">GDrives</span>
        </NavLink>
        
        <NavLink to="/poster-search" className={({ isActive }) => isActive ? 'active' : ''} data-label="Poster Search" aria-label="Poster Search">
          <span className="icon"><Search size={20} color="#64b5f6" /></span>
          <span className="nav-label">Poster Search</span>
        </NavLink>

        <NavLink to="/plex-upload" className={({ isActive }) => isActive ? 'active' : ''} data-label="Plex Upload" aria-label="Plex Upload">
          <span className="icon"><UploadCloud size={20} color="#e5a00d" /></span>
          <span className="nav-label">Plex Upload</span>
        </NavLink>
        
        
        <NavLink to="/IDarr" className={({ isActive }) => isActive ? 'active' : ''} data-label="IDarr" aria-label="IDarr">
          <span className="icon"><Fingerprint size={20} color="#66bb6a" /></span>
          <span className="nav-label">IDarr</span>
          {idarrPendingCount > 0 && (
            <span className="sidebar-badge">{idarrPendingCount}</span>
          )}
        </NavLink>

        <NavLink to="/maker-tools" className={({ isActive }) => isActive ? 'active' : ''} data-label="Maker Tools" aria-label="Maker Tools">
          <span className="icon"><Wrench size={20} color="#64b5f6" /></span>
          <span className="nav-label">Maker Tools</span>
        </NavLink>

        <NavLink to="/logs" className={({ isActive }) => isActive ? 'active' : ''} data-label="Logs" aria-label="Logs">
          <span className="icon"><Logs size={20} color="#22c55e" /></span>
          <span className="nav-label">Logs</span>
        </NavLink>
        
        <NavLink to="/settings" className={({ isActive }) => isActive ? 'active' : ''} data-label="Settings" aria-label="Settings">
          <span className="icon"><Settings size={20} /></span>
          <span className="nav-label">Settings</span>
        </NavLink>
      </nav>

      {showMiniProgress && currentRunningJob && (
        <div className="sidebar-job-mini" aria-live="polite">
          <div className="sidebar-job-mini-title">Current Job</div>
          <div className="sidebar-job-mini-type" title={currentRunningJob.job_type}>{formatJobType(currentRunningJob.job_type)}</div>
          {currentRunningJob.message && (
            <div className="sidebar-job-mini-message" title={currentRunningJob.message}>
              {truncateText(currentRunningJob.message, 56)}
            </div>
          )}
          <div className="sidebar-job-mini-progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={displayProgress}>
            <div className="sidebar-job-mini-progress-fill" style={{ width: `${displayProgress}%` }} />
          </div>
          <div className="sidebar-job-mini-meta">{displayProgress}%</div>
        </div>
      )}
    </div>
  )
}

export default Sidebar