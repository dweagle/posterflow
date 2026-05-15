import { NavLink, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState } from 'react'
import { HardDriveDownload, LayoutDashboard, Logs, Settings, Image, Search, UploadCloud, Fingerprint, Wrench } from 'lucide-react'
import { useUnmatched } from '../contexts/UnmatchedContext'
import { formatJobType, getMakerIdarrConfig, uploadMakerIdarrFiles, startIdarr, getApiErrorMessage, type MakerIdarrConfig } from '../api/client'
import { useToast } from './Toast'
import posterFlowIcon from '../assets/PosterFlow.webp'
import './Sidebar.css'

type VersionUpdateStatus = {
  current_version: string
  latest_version: string | null
  update_available: boolean
  versions_behind: number
  releases_url: string | null
  release_notes: string | null
}

function Sidebar({ isOpen = false }: { isOpen?: boolean }) {
  const location = useLocation()
  const { unmatchedCount, idarrPendingCount, jobs } = useUnmatched()
  const { showToast } = useToast()
  const [isDragOverIdarr, setIsDragOverIdarr] = useState(false)
  const [idarrPickerFiles, setIdarrPickerFiles] = useState<File[] | null>(null)
  const [idarrPickerConfig, setIdarrPickerConfig] = useState<MakerIdarrConfig | null>(null)
  const [idarrPickerSelectedIndex, setIdarrPickerSelectedIndex] = useState(0)
  const [version, setVersion] = useState<string>('0.1.0')
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  const [versionsBehind, setVersionsBehind] = useState<number>(0)
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

  const handleIdarrDragOver = (e: React.DragEvent) => {
    if (e.dataTransfer.types.includes('Files')) {
      e.preventDefault()
      e.stopPropagation()
      if (!isDragOverIdarr) setIsDragOverIdarr(true)
    }
  }

  const handleIdarrDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOverIdarr(false)
  }

  const handleIdarrDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOverIdarr(false)
    const dataFiles = e.dataTransfer?.files
    if (!dataFiles?.length) return
    const files = Array.from(dataFiles)

    void (async () => {
      try {
        const config = await getMakerIdarrConfig()
        const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
        if (!syncTargets.length) {
          showToast('No IDarr sync targets configured', 'error')
          return
        }

        // Use stored sync target index preference if available
        const storedKey = 'posterflow.idarr.selectedSyncTarget'
        const storedValue = localStorage.getItem(storedKey)
        let resolvedIndex = -1
        if (storedValue) {
          resolvedIndex = syncTargets.findIndex((t) => {
            const scopeToken = String(t.scope_token || '').trim()
            if (scopeToken) return `scope:${scopeToken}` === storedValue
            return `${String(t.personal_drive_id || '')}::${String(t.source_dir || '')}::${String(t.label || '')}` === storedValue
          })
        }

        // If no stored preference matched and there are multiple targets, show picker
        if (resolvedIndex < 0 && syncTargets.length > 1) {
          setIdarrPickerConfig(config)
          setIdarrPickerFiles(files)
          setIdarrPickerSelectedIndex(0)
          return
        }

        const syncTargetIndex = resolvedIndex >= 0 ? resolvedIndex : 0
        await performIdarrUpload(config, syncTargetIndex, files)
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to upload files to IDarr'), 'error')
      }
    })()
  }

  const performIdarrUpload = async (config: MakerIdarrConfig, syncTargetIndex: number, files: File[]) => {
    const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
    const selectedTarget = syncTargets[syncTargetIndex]
    if (!String(selectedTarget?.source_dir || '').trim()) {
      showToast('Selected IDarr sync target has no Sync Folder configured', 'error')
      return
    }
    try {
      const response = await uploadMakerIdarrFiles(syncTargetIndex, files)
      const skippedMessage = response.skipped_count > 0 ? `, ${response.skipped_count} skipped` : ''
      showToast(`IDarr: uploaded ${response.uploaded_count} file(s)${skippedMessage}`, 'success')
      if (config.auto_rename_quick_add && response.uploaded_count > 0) {
        try {
          const job = await startIdarr(false, syncTargetIndex, response.uploaded, config.auto_upload_quick_add)
          showToast(`IDarr auto-rename started (Job ID: ${job.id})`, 'success')
        } catch (error) {
          showToast(getApiErrorMessage(error, 'Files uploaded, but failed to start IDarr auto-rename'), 'error')
        }
      }
      window.dispatchEvent(new CustomEvent('idarr-sidebar-upload-complete', { detail: { syncTargetIndex } }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to upload files to IDarr'), 'error')
    }
  }

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
          setVersionsBehind(payload.versions_behind ?? 1)
          setReleaseNotes(payload.release_notes ?? null)
        } else {
          setLatestVersion(null)
          setVersionsBehind(0)
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
    <>
    <div className={`sidebar${isOpen ? ' sidebar--open' : ''}`}>
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
                      <span>{versionsBehind > 1 ? `${versionsBehind} versions behind` : `What's New in ${latestVersion}`}</span>
                      <a href={releasesUrl} target="_blank" rel="noopener noreferrer">View on GitHub</a>
                    </div>
                    <div className="release-notes-body">
                      {releaseNotes
                        ? releaseNotes.split('\n').map((line, i) =>
                            line.startsWith('### ')
                              ? <p key={i} className="release-notes-version-heading">{line.slice(4)}</p>
                              : line.trim() ? <p key={i}>{line}</p> : null
                          )
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
        
        
        <NavLink
          to="/IDarr"
          className={({ isActive }) => [isActive ? 'active' : '', isDragOverIdarr ? 'idarr-drop-active' : ''].filter(Boolean).join(' ')}
          data-label="IDarr"
          aria-label="IDarr"
          onDragOver={handleIdarrDragOver}
          onDragLeave={handleIdarrDragLeave}
          onDrop={handleIdarrDrop}
        >
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

      {idarrPickerFiles && idarrPickerConfig && (() => {
        const pickerConfig = idarrPickerConfig
        return (
        <div className="modal-overlay" onClick={() => { setIdarrPickerFiles(null); setIdarrPickerConfig(null) }}>
          <div className="modal-content schedule-modal idarr-target-picker-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Select IDarr Sync Target</h2>
              <button className="modal-close" onClick={() => { setIdarrPickerFiles(null); setIdarrPickerConfig(null) }}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ color: '#ccc', marginBottom: '1rem' }}>Choose which sync target to upload the dropped file(s) to:</p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {(pickerConfig.sync_targets ?? []).map((target, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => setIdarrPickerSelectedIndex(i)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '0.65rem 0.9rem',
                      background: idarrPickerSelectedIndex === i ? '#1a2a3a' : '#252525',
                      border: `1px solid ${idarrPickerSelectedIndex === i ? '#64b5f6' : '#424242'}`,
                      borderRadius: '6px',
                      cursor: 'pointer',
                      color: idarrPickerSelectedIndex === i ? '#64b5f6' : '#ccc',
                      fontWeight: idarrPickerSelectedIndex === i ? 600 : 400,
                      fontSize: '0.9rem',
                      textAlign: 'left',
                      transition: 'border-color 0.15s, background 0.15s, color 0.15s',
                    }}
                  >
                    <span>{target.label || target.source_dir || `Target ${i + 1}`}</span>
                    {idarrPickerSelectedIndex === i && (
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#64b5f6', flexShrink: 0 }} />
                    )}
                  </button>
                ))}
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => { setIdarrPickerFiles(null); setIdarrPickerConfig(null) }}>Cancel</button>
              <button
                className="btn-primary"
                onClick={() => {
                  const files = idarrPickerFiles
                  const index = idarrPickerSelectedIndex
                  setIdarrPickerFiles(null)
                  setIdarrPickerConfig(null)
                  void performIdarrUpload(pickerConfig, index, files)
                }}
              >
                Upload
              </button>
            </div>
          </div>
        </div>
        )
      })()}
    </>
  )
}

export default Sidebar