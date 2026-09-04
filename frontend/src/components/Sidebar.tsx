import { NavLink, useLocation } from 'react-router-dom'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { HardDriveDownload, LayoutDashboard, Logs, Settings, Image, Search, Fingerprint, Wrench, Globe, GripVertical, Eye, EyeOff, SlidersHorizontal, CircleStop, UploadCloud } from 'lucide-react'
import { useAppEvents } from '../contexts/AppEventsContext'
import { useDiscordAuth } from '../hooks/useDiscordAuth'
import { formatJobType, getMakerIdarrConfig, uploadMakerIdarrFiles, startIdarr, cancelJob, getApiErrorMessage, getMyCommunityRequestCounts, type MakerIdarrConfig } from '../api/client'
import { getSettings, saveSettings } from '../api/settings'
import { notifyIdarrTargetedRun } from '../utils/idarrTargetedRun'
import ConfirmDialog from './ConfirmDialog'
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

type NavItemDef = {
  id: string
  label: string
  to: string
  iconColor: string
  badge?: 'unmatched' | 'idarr' | 'community' | 'maker-monitor'
  isIdarr?: boolean
  isEnd?: boolean
}

type SidebarItemConfig = {
  id: string
  visible: boolean
  order: number
}

const NAV_ITEM_DEFS: NavItemDef[] = [
  { id: 'dashboard', label: 'Dashboard', to: '/', iconColor: '#ff8800', isEnd: true },
  { id: 'poster-manager', label: 'Asset Manager', to: '/poster-manager', iconColor: '#a855f7', badge: 'unmatched' },
  { id: 'drives', label: 'GDrives', to: '/drives', iconColor: '#4285F4' },
  { id: 'poster-search', label: 'Poster Search', to: '/poster-search', iconColor: '#64b5f6' },
  { id: 'plex-upload', label: 'Asset Upload', to: '/plex-upload', iconColor: '#e5a00d' },
  { id: 'community-requests', label: 'Requests', to: '/community-requests', iconColor: '#64b5f6', badge: 'community' },
  { id: 'idarr', label: 'IDarr', to: '/IDarr', iconColor: '#66bb6a', badge: 'idarr', isIdarr: true },
  { id: 'maker-tools', label: 'Maker Tools', to: '/maker-tools', iconColor: '#64b5f6', badge: 'maker-monitor' },
  { id: 'logs', label: 'Logs', to: '/logs', iconColor: '#22c55e' },
]

const DEFAULT_CONFIGS: SidebarItemConfig[] = NAV_ITEM_DEFS.map((item, i) => ({
  id: item.id,
  visible: true,
  order: i,
}))

function getNavIcon(id: string, color: string, size = 20) {
  switch (id) {
    case 'dashboard': return <LayoutDashboard size={size} color={color} />
    case 'poster-manager': return <Image size={size} color={color} />
    case 'drives': return <HardDriveDownload size={size} color={color} />
    case 'poster-search': return <Search size={size} color={color} />
    case 'plex-upload': return <UploadCloud size={size} color={color} />
    case 'community-requests': return <Globe size={size} color={color} />
    case 'idarr': return <Fingerprint size={size} color={color} />
    case 'maker-tools': return <Wrench size={size} color={color} />
    case 'logs': return <Logs size={size} color={color} />
    default: return null
  }
}

function Sidebar({ isOpen = false }: { isOpen?: boolean }) {
  const location = useLocation()
  const { unmatchedCount, idarrPendingCount, makerMonitorNeededCount, communityRequestCount, jobs } = useAppEvents()
  const { isConnected, isMaker, discordUserId } = useDiscordAuth()
  const { showToast } = useToast()
  // Requester's own active-request counts (pending + in progress) for the sidebar badges.
  const [myReqCounts, setMyReqCounts] = useState<{ pending: number; in_progress: number }>({ pending: 0, in_progress: 0 })
  const [isDragOverIdarr, setIsDragOverIdarr] = useState(false)
  const [idarrPickerFiles, setIdarrPickerFiles] = useState<File[] | null>(null)
  const [idarrPickerConfig, setIdarrPickerConfig] = useState<MakerIdarrConfig | null>(null)
  const [idarrPickerSelectedIndex, setIdarrPickerSelectedIndex] = useState(0)
  const [version, setVersion] = useState<string>('0.1.0')
  // Sidebar customization
  const [isCustomizing, setIsCustomizing] = useState(false)
  const [itemConfigs, setItemConfigs] = useState<SidebarItemConfig[]>(DEFAULT_CONFIGS)
  const [pendingConfigs, setPendingConfigs] = useState<SidebarItemConfig[]>(DEFAULT_CONFIGS)
  const [draggedId, setDraggedId] = useState<string | null>(null)
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [isSavingConfig, setIsSavingConfig] = useState(false)
  const [latestVersion, setLatestVersion] = useState<string | null>(null)
  const [versionsBehind, setVersionsBehind] = useState<number>(0)
  const [releasesUrl, setReleasesUrl] = useState<string>('')
  const [releaseNotes, setReleaseNotes] = useState<string | null>(null)
  const [showReleaseNotes, setShowReleaseNotes] = useState(false)
  const [displayProgress, setDisplayProgress] = useState<number>(0)
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false)
  const [stoppingJobId, setStoppingJobId] = useState<number | null>(null)
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

  const handleStopCurrentJob = async () => {
    if (!currentRunningJob) return
    const job = currentRunningJob
    setStoppingJobId(job.id)
    try {
      await cancelJob(job.id)
      showToast(`Stopping ${formatJobType(job.job_type)}…`, 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to stop job'), 'error')
      setStoppingJobId(null)
    }
  }

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
          void notifyIdarrTargetedRun(job.id, Boolean(config.auto_upload_quick_add), syncTargetIndex)
        } catch (error) {
          showToast(getApiErrorMessage(error, 'Files uploaded, but failed to start IDarr auto-rename'), 'error')
        }
      }
      window.dispatchEvent(new CustomEvent('idarr-sidebar-upload-complete', { detail: { syncTargetIndex } }))
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to upload files to IDarr'), 'error')
    }
  }

  // Poll the requester's own active-request counts for the sidebar badges.
  // Only for connected non-makers — makers see the work-queue badge instead.
  useEffect(() => {
    if (!isConnected || isMaker || !discordUserId) {
      setMyReqCounts({ pending: 0, in_progress: 0 })
      return
    }
    let cancelled = false
    const load = () => {
      getMyCommunityRequestCounts(discordUserId)
        .then((c) => { if (!cancelled) setMyReqCounts(c) })
        .catch(() => {})
    }
    load()
    const interval = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [isConnected, isMaker, discordUserId])

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

  // Clear the "stopping" state once the stopped job is no longer the current one.
  useEffect(() => {
    if (stoppingJobId !== null && (!currentRunningJob || currentRunningJob.id !== stoppingJobId)) {
      setStoppingJobId(null)
    }
  }, [currentRunningJob, stoppingJobId])

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

  // Load sidebar config from backend settings
  useEffect(() => {
    const loadSidebarConfig = async () => {
      try {
        const allSettings = await getSettings()
        if (allSettings.sidebar_config) {
          const saved = JSON.parse(allSettings.sidebar_config) as SidebarItemConfig[]
          const merged: SidebarItemConfig[] = []
          let maxOrder = saved.reduce((m, c) => Math.max(m, c.order), -1)
          // Preserve saved order/visibility
          for (const savedItem of [...saved].sort((a, b) => a.order - b.order)) {
            if (NAV_ITEM_DEFS.find(d => d.id === savedItem.id)) {
              merged.push(savedItem)
            }
          }
          // Add any new items that weren't in saved config
          for (const def of NAV_ITEM_DEFS) {
            if (!merged.find(c => c.id === def.id)) {
              maxOrder++
              merged.push({ id: def.id, visible: true, order: maxOrder })
            }
          }
          setItemConfigs(merged)
          setPendingConfigs(merged)
        }
      } catch {
        // Keep defaults on error
      }
    }
    void loadSidebarConfig()
  }, [])

  const handleStartCustomize = () => {
    setPendingConfigs([...itemConfigs])
    setIsCustomizing(true)
  }

  const handleCancelCustomize = () => {
    setPendingConfigs([...itemConfigs])
    setIsCustomizing(false)
    setDraggedId(null)
    setDragOverId(null)
  }

  const handleSaveCustomization = async () => {
    setIsSavingConfig(true)
    try {
      const normalized = pendingConfigs.map((c, i) => ({ ...c, order: i }))
      await saveSettings({ sidebar_config: JSON.stringify(normalized) })
      setItemConfigs(normalized)
      setIsCustomizing(false)
      setDraggedId(null)
      setDragOverId(null)
    } catch {
      showToast('Failed to save sidebar layout', 'error')
    } finally {
      setIsSavingConfig(false)
    }
  }

  const handleCustomizeDragStart = (id: string) => setDraggedId(id)

  const handleCustomizeDragOver = (e: React.DragEvent, id: string) => {
    e.preventDefault()
    if (dragOverId !== id) setDragOverId(id)
  }

  const handleCustomizeDrop = (targetId: string) => {
    if (!draggedId || draggedId === targetId) {
      setDraggedId(null)
      setDragOverId(null)
      return
    }
    setPendingConfigs(prev => {
      const arr = [...prev]
      const fromIdx = arr.findIndex(c => c.id === draggedId)
      const toIdx = arr.findIndex(c => c.id === targetId)
      if (fromIdx === -1 || toIdx === -1) return prev
      const [removed] = arr.splice(fromIdx, 1)
      arr.splice(toIdx, 0, removed)
      return arr.map((c, i) => ({ ...c, order: i }))
    })
    setDraggedId(null)
    setDragOverId(null)
  }

  const handleCustomizeDragEnd = () => {
    setDraggedId(null)
    setDragOverId(null)
  }

  const toggleItemVisibility = (id: string) => {
    setPendingConfigs(prev => prev.map(c => c.id === id ? { ...c, visible: !c.visible } : c))
  }

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
        {!isCustomizing && (
          <>
            {itemConfigs
              .filter(c => c.visible)
              .sort((a, b) => a.order - b.order)
              .map(config => {
                const def = NAV_ITEM_DEFS.find(d => d.id === config.id)
                if (!def) return null

                if (def.isIdarr) {
                  return (
                    <NavLink
                      key={def.id}
                      to={def.to}
                      className={({ isActive }) =>
                        [isActive ? 'active' : '', isDragOverIdarr ? 'idarr-drop-active' : ''].filter(Boolean).join(' ')
                      }
                      data-label={def.label}
                      aria-label={def.label}
                      onDragOver={handleIdarrDragOver}
                      onDragLeave={handleIdarrDragLeave}
                      onDrop={handleIdarrDrop}
                    >
                      <span className="icon">{getNavIcon(def.id, def.iconColor)}</span>
                      <span className="nav-label">{def.label}</span>
                      {idarrPendingCount > 0 && <span className="sidebar-badge">{idarrPendingCount}</span>}
                    </NavLink>
                  )
                }

                // Badges shown on this item, grouped on the right. The community item can
                // show several: the maker work-queue count (all pending) plus the user's own
                // pending / in-progress requests — makers who also request see all of them.
                const itemBadges: ReactNode[] = []
                if (def.badge === 'unmatched' && unmatchedCount > 0) {
                  itemBadges.push(<span key="unmatched" className="sidebar-badge">{unmatchedCount}</span>)
                }
                if (def.badge === 'maker-monitor' && makerMonitorNeededCount > 0) {
                  itemBadges.push(<span key="maker-monitor" className="sidebar-badge" title="Monitored items needing posters">{makerMonitorNeededCount}</span>)
                }
                if (def.badge === 'community') {
                  if (isMaker && communityRequestCount > 0) {
                    itemBadges.push(<span key="queue" className="sidebar-badge" title="Open requests in the queue">{communityRequestCount}</span>)
                  }
                  if (isConnected && myReqCounts.pending > 0) {
                    itemBadges.push(<span key="pending" className="sidebar-req-badge sidebar-req-badge--pending" title="Your pending requests">{myReqCounts.pending}</span>)
                  }
                  if (isConnected && myReqCounts.in_progress > 0) {
                    itemBadges.push(<span key="in_progress" className="sidebar-req-badge sidebar-req-badge--in-progress" title="Your requests in progress">{myReqCounts.in_progress}</span>)
                  }
                }

                return (
                  <NavLink
                    key={def.id}
                    to={def.to}
                    end={def.isEnd}
                    className={({ isActive }) => isActive ? 'active' : ''}
                    data-label={def.label}
                    aria-label={def.label}
                  >
                    <span className="icon">{getNavIcon(def.id, def.iconColor)}</span>
                    <span className="nav-label">{def.label}</span>
                    {itemBadges.length > 0 && <span className="sidebar-badges">{itemBadges}</span>}
                  </NavLink>
                )
              })}

            <NavLink to="/settings" className={({ isActive }) => isActive ? 'active' : ''} data-label="Settings" aria-label="Settings">
              <span className="icon"><Settings size={20} /></span>
              <span className="nav-label">Settings</span>
            </NavLink>

            <div className="sidebar-customize-btn-row">
              <button className="sidebar-customize-trigger" onClick={handleStartCustomize} title="Customize sidebar layout" type="button">
                <SlidersHorizontal size={13} />
                <span>Customize</span>
              </button>
            </div>
          </>
        )}

        {isCustomizing && (
          <>
            <div className="sidebar-customize-header">
              Drag to reorder · toggle to show/hide
            </div>
            <div className="sidebar-customize-list">
              {pendingConfigs.map(config => {
                const def = NAV_ITEM_DEFS.find(d => d.id === config.id)
                if (!def) return null
                const iconColor = config.visible ? def.iconColor : '#555'
                return (
                  <div
                    key={config.id}
                    className={[
                      'sidebar-customize-item',
                      draggedId === config.id ? 'dragging' : '',
                      dragOverId === config.id ? 'drag-over' : '',
                      !config.visible ? 'hidden' : '',
                    ].filter(Boolean).join(' ')}
                    draggable
                    onDragStart={() => handleCustomizeDragStart(config.id)}
                    onDragOver={(e) => handleCustomizeDragOver(e, config.id)}
                    onDrop={() => handleCustomizeDrop(config.id)}
                    onDragEnd={handleCustomizeDragEnd}
                  >
                    <span className="sidebar-customize-grip">
                      <GripVertical size={15} color="#666" />
                    </span>
                    <span className="icon sidebar-customize-icon">{getNavIcon(def.id, iconColor, 18)}</span>
                    <span className="sidebar-customize-label">{def.label}</span>
                    <button
                      className="sidebar-customize-toggle"
                      onClick={() => toggleItemVisibility(config.id)}
                      title={config.visible ? 'Hide this item' : 'Show this item'}
                      type="button"
                    >
                      {config.visible
                        ? <Eye size={15} color="#64b5f6" />
                        : <EyeOff size={15} color="#555" />
                      }
                    </button>
                  </div>
                )
              })}

              {/* Settings is always visible — not configurable */}
              <div className="sidebar-customize-item sidebar-customize-item--locked">
                <span className="sidebar-customize-grip sidebar-customize-grip--locked">
                  <GripVertical size={15} color="#444" />
                </span>
                <span className="icon sidebar-customize-icon"><Settings size={18} color="#666" /></span>
                <span className="sidebar-customize-label sidebar-customize-label--locked">Settings</span>
                <span className="sidebar-customize-locked-badge">locked</span>
              </div>
            </div>

            <div className="sidebar-customize-btn-row sidebar-customize-btn-row--active">
              <button className="sidebar-customize-cancel" onClick={handleCancelCustomize} type="button">
                Cancel
              </button>
              <button
                className="sidebar-customize-done"
                onClick={() => void handleSaveCustomization()}
                disabled={isSavingConfig}
                type="button"
              >
                {isSavingConfig ? 'Saving…' : 'Done'}
              </button>
            </div>
          </>
        )}
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
          <div className="sidebar-job-mini-footer">
            <span className="sidebar-job-mini-meta">{displayProgress}%</span>
            <button
              type="button"
              className="sidebar-job-mini-stop"
              onClick={() => setStopConfirmOpen(true)}
              disabled={stoppingJobId === currentRunningJob.id}
              title="Stop this job"
              aria-label="Stop this job"
            >
              <CircleStop size={13} />
              <span>{stoppingJobId === currentRunningJob.id ? 'Stopping…' : 'Stop'}</span>
            </button>
          </div>
        </div>
      )}
    </div>

    <ConfirmDialog
      isOpen={stopConfirmOpen}
      title="Stop Job"
      message={currentRunningJob ? `Stop "${formatJobType(currentRunningJob.job_type)}"? Any work in progress will be halted.` : ''}
      confirmText="Stop Job"
      cancelText="Keep Running"
      variant="danger"
      onConfirm={() => {
        setStopConfirmOpen(false)
        void handleStopCurrentJob()
      }}
      onCancel={() => setStopConfirmOpen(false)}
    />

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