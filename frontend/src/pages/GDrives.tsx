import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDrives, subscribeDrive, unsubscribeDrive, startSync, startSyncAll, updateDrive, createCustomDrive, deleteDrive, reloadDrives, Drive, getApiErrorMessage } from '../api/client'
import DriveEditModal from '../components/DriveEditModal'
import AddCustomDriveModal from '../components/AddCustomDriveModal'
import ConfirmDialog from '../components/ConfirmDialog'
import GdriveStorageModal from '../components/GdriveStorageModal'
import ArtworkDrivesPanel from '../components/ArtworkDrivesPanel'
import { HardDriveDownload, RefreshCw, Settings as SettingsIcon, Settings2, BookmarkMinus, Trash2, RotateCw, Plus, Info, Copy, Check, ExternalLink, Image, Layers } from 'lucide-react'
import { useToast } from '../components/Toast'
import { useAppEvents } from '../contexts/AppEventsContext'
import './GDrives.css'
import Toolbar from '../components/Toolbar'
import { getAddToPriorityPref, setAddToPriorityPref } from '../utils/priorityPrompt'

// Build a Google Drive folder URL, skipping placeholder IDs for custom drives without a real ID
const driveFolderUrl = (driveId: string): string | null =>
  driveId && !driveId.startsWith('manual-')
    ? `https://drive.google.com/drive/folders/${driveId}`
    : null

type CustomDriveModalInput = {
  name: string
  drive_id: string
  custom_path: string | null
  subscribed: boolean
  sync_enabled: boolean
}

function GDrives() {
  const navigate = useNavigate()
  const [drives, setDrives] = useState<Drive[]>([])
  const [loading, setLoading] = useState(true)
  const [scope, setScope] = useState<'posters' | 'artwork'>(
    () => (localStorage.getItem('posterflow.gdrives.scope') === 'artwork' ? 'artwork' : 'posters')
  )
  const [filter, setFilter] = useState<string>('all')
  const [sortBy, setSortBy] = useState<string>('name-asc')
  const [syncingDrive, setSyncingDrive] = useState<number | null>(null)
  const [syncingAll, setSyncingAll] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [editingDrive, setEditingDrive] = useState<Drive | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<{show: boolean, driveId: number | null, driveName: string, isDeprecated: boolean}>({show: false, driveId: null, driveName: '', isDeprecated: false})
  const [priorityPrompt, setPriorityPrompt] = useState<{show: boolean, driveId: number | null, driveName: string}>({show: false, driveId: null, driveName: ''})
  const [promptDontAsk, setPromptDontAsk] = useState(false)
  const [deleteFiles, setDeleteFiles] = useState(false)
  const [showStorageModal, setShowStorageModal] = useState(false)
  const { showToast } = useToast()
  const { jobs } = useAppEvents()
  const prevJobStatusRef = useRef<Record<number, string>>({})
  const jobsInitializedRef = useRef(false)
  const [idTooltip, setIdTooltip] = useState<number | null>(null)
  const [tooltipAlign, setTooltipAlign] = useState<'left' | 'center' | 'right'>('center')
  const [copiedId, setCopiedId] = useState<number | null>(null)
  const tooltipHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const showIdTooltip = (id: number) => {
    if (tooltipHideTimer.current) clearTimeout(tooltipHideTimer.current)
    setIdTooltip(id)
  }

  // Flip the tooltip's anchor based on the icon's position so it never spills off-screen
  const openIdTooltip = (id: number, e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const center = rect.left + rect.width / 2
    const half = 170 // ~half of tooltip max-width plus a small margin
    setTooltipAlign(center + half > window.innerWidth - 8 ? 'left' : center - half < 8 ? 'right' : 'center')
    showIdTooltip(id)
  }

  const hideIdTooltip = () => {
    tooltipHideTimer.current = setTimeout(() => setIdTooltip(null), 200)
  }

  useEffect(() => {
    fetchDrives()
  }, [])

  // Sync/workflow jobs run in the background and update poster/file counts
  // server-side, so refresh the drive list whenever one finishes.
  useEffect(() => {
    let shouldRefresh = false
    for (const job of jobs) {
      const affectsDrives = job.job_type.startsWith('Sync') || job.job_type === 'Poster Workflow'
      const prev = prevJobStatusRef.current[job.id]
      if (
        jobsInitializedRef.current &&
        affectsDrives &&
        (job.status === 'completed' || job.status === 'failed') &&
        prev !== job.status
      ) {
        shouldRefresh = true
      }
      prevJobStatusRef.current[job.id] = job.status
    }
    jobsInitializedRef.current = true
    if (shouldRefresh) {
      fetchDrives()
    }
  }, [jobs])

  const runWithBooleanLoading = async (
    setLoadingState: (value: boolean) => void,
    action: () => Promise<void>
  ) => {
    setLoadingState(true)
    try {
      await action()
    } finally {
      setLoadingState(false)
    }
  }

  const runWithDriveLoading = async (driveId: number, action: () => Promise<void>) => {
    setSyncingDrive(driveId)
    try {
      await action()
    } finally {
      setSyncingDrive(null)
    }
  }

  const fetchWithLogging = async (action: () => Promise<void>, errorMessage: string) => {
    try {
      await action()
    } catch (error) {
      console.error(errorMessage, error)
    }
  }

  const fetchDrives = async () => {
    await fetchWithLogging(async () => {
      const data = await getDrives()
      setDrives(data)
    }, 'Error fetching drives:')
    if (loading) {
      setLoading(false)
    }
  }

  const handleSubscribe = (driveId: number) => {
    const pref = getAddToPriorityPref('poster')
    if (pref === 'always') { void doSubscribe(driveId, true); return }
    if (pref === 'never') { void doSubscribe(driveId, false); return }
    const drive = drives.find(d => d.id === driveId)
    setPromptDontAsk(false)
    setPriorityPrompt({ show: true, driveId, driveName: drive?.display_name || drive?.name || 'this drive' })
  }

  const doSubscribe = async (driveId: number, addToPriority: boolean) => {
    await fetchWithLogging(async () => {
      const result = await subscribeDrive(driveId, addToPriority)
      fetchDrives()
      if (result.restored_to_priority) {
        showToast('Subscribed. This drive was restored to its previous position in Asset Manager → Drive Priority.', 'info')
      } else if (result.added_to_priority) {
        showToast('Subscribed and added to Asset Manager → Drive Priority.', 'info')
      } else {
        showToast('Subscribed. Remember to add this drive to your list in Asset Manager → Drive Priority.', 'info')
      }
      if (result.scan_job_id) {
        showToast('Initial scan queued. Check Dashboard for progress.', 'info')
      }
    }, 'Error subscribing:')
  }

  const resolvePriorityPrompt = (add: boolean) => {
    if (promptDontAsk) setAddToPriorityPref('poster', add ? 'always' : 'never')
    const id = priorityPrompt.driveId
    setPriorityPrompt({ show: false, driveId: null, driveName: '' })
    if (id != null) void doSubscribe(id, add)
  }

  const handleUnsubscribe = async (driveId: number) => {
    await fetchWithLogging(async () => {
      const result = await unsubscribeDrive(driveId)
      fetchDrives()
      if (result.removed_from_priority) {
        showToast('Unsubscribed. This drive was removed from Asset Manager → Drive Priority.', 'info')
      }
    }, 'Error unsubscribing:')
  }

  const handleBulkSubscribe = async (styleType: string) => {
    const drivesToSubscribe = drives.filter(d => {
      if (styleType === 'custom') return d.is_custom && !d.subscribed
      return d.style_type === styleType && !d.subscribed
    })

    if (drivesToSubscribe.length === 0) {
      showToast(`All ${styleType} drives are already subscribed`, 'info')
      return
    }

    try {
      const results = await Promise.all(drivesToSubscribe.map(d => subscribeDrive(d.id)))
      const restoredCount = results.filter(result => result.restored_to_priority).length
      const newCount = drivesToSubscribe.length - restoredCount
      fetchDrives()
      showToast(`Subscribed to ${drivesToSubscribe.length} ${styleType} drives`)
      if (restoredCount > 0) {
        showToast(`${restoredCount} drive(s) were restored to Asset Manager → Drive Priority`, 'info')
      }
      if (newCount > 0) {
        showToast(`Remember to add ${newCount} new drive(s) to your list in Asset Manager → Drive Priority`, 'info')
      }
    } catch (error) {
      console.error('Error bulk subscribing:', error)
      showToast('Failed to subscribe to some drives', 'error')
    }
  }

  const handleBulkUnsubscribe = async (styleType: string) => {
    const drivesToUnsubscribe = drives.filter(d => {
      if (styleType === 'custom') return d.is_custom && d.subscribed
      return d.style_type === styleType && d.subscribed
    })

    if (drivesToUnsubscribe.length === 0) {
      showToast(`No ${styleType} drives are subscribed`, 'info')
      return
    }

    try {
      const results = await Promise.all(drivesToUnsubscribe.map(d => unsubscribeDrive(d.id)))
      const prunedCount = results.filter(result => result.removed_from_priority).length
      fetchDrives()
      showToast(`Unsubscribed from ${drivesToUnsubscribe.length} ${styleType} drives`)
      if (prunedCount > 0) {
        showToast(`${prunedCount} drive(s) were also removed from Asset Manager → Drive Priority`, 'info')
      }
    } catch (error) {
      console.error('Error bulk unsubscribing:', error)
      showToast('Failed to unsubscribe from some drives', 'error')
    }
  }

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const handleSync = async (driveId: number) => {
    await runWithDriveLoading(driveId, async () => {
      try {
        await startSync(driveId)
        showToast('Sync started! Check Dashboard for progress.')
      } catch (error) {
        console.error('Error starting sync:', error)
        showToast(getApiErrorMessage(error, 'Failed to start sync'), 'error')
      }
    })
  }

  const handleSyncAll = async () => {
    const subscribedCount = drives.filter(d => d.subscribed).length
    
    if (subscribedCount === 0) {
      showToast('No subscribed drives to sync', 'error')
      return
    }
    
    await runWithBooleanLoading(setSyncingAll, async () => {
      try {
        await startSyncAll()
        showToast(`Sync started for ${subscribedCount} drives! Check Dashboard for progress.`)
      } catch (error) {
        console.error('Error starting sync all:', error)
        showToast(getApiErrorMessage(error, 'Failed to start sync'), 'error')
      }
    })
  }

  const handleSaveDrive = async (driveId: number, updates: { custom_path: string | null; sync_enabled: boolean; drive_id?: string }) => {
    try {
      await updateDrive(driveId, {
        custom_path: updates.custom_path || null,
        sync_enabled: updates.sync_enabled,
        drive_id: updates.drive_id,
      })
      fetchDrives()
      setEditingDrive(null)
      showToast('Drive updated successfully')
    } catch (error) {
      console.error('Error updating drive:', error)
      showToast('Failed to update drive', 'error')
    }
  }

  const handleAddCustomDrive = async (drive: CustomDriveModalInput) => {
    try {
      await createCustomDrive({
        ...drive,
        style_type: 'Custom',
        custom_path: drive.custom_path ?? undefined,
        subscribed: drive.subscribed,
        sync_enabled: drive.sync_enabled,
      })
      fetchDrives()
      setShowAddModal(false)
      showToast('Custom drive added successfully')
    } catch (error) {
      console.error('Error adding custom drive:', error)
      showToast(getApiErrorMessage(error, 'Failed to add custom drive'), 'error')
    }
  }

  const handleDeleteDrive = async (driveId: number) => {
    setDeleteConfirm({show: false, driveId: null, driveName: '', isDeprecated: false})
    try {
      await deleteDrive(driveId, deleteFiles)
      fetchDrives()
      showToast(`Drive deleted successfully${deleteFiles ? ' (files removed)' : ''}`)
      setDeleteFiles(false)
    } catch (error) {
      console.error('Error deleting drive:', error)
      showToast(getApiErrorMessage(error, 'Failed to delete drive'), 'error')
    }
  }

  const confirmDeleteDrive = (driveId: number, driveName: string, isDeprecated: boolean = false) => {
    setDeleteConfirm({show: true, driveId, driveName, isDeprecated})
    setDeleteFiles(false)
  }

  const handleCopyId = async (driveId: number, driveIdStr: string) => {
    try {
      await navigator.clipboard.writeText(driveIdStr)
      setCopiedId(driveId)
      setTimeout(() => setCopiedId(null), 2000)
    } catch {
      // Fallback for non-secure (HTTP) contexts where Clipboard API is unavailable
      try {
        const textArea = document.createElement('textarea')
        textArea.value = driveIdStr
        textArea.style.position = 'fixed'
        textArea.style.left = '-9999px'
        textArea.style.top = '-9999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(textArea)
        setCopiedId(driveId)
        setTimeout(() => setCopiedId(null), 2000)
      } catch (fallbackError) {
        console.error('Failed to copy drive ID:', fallbackError)
      }
    }
  }

  const handleReloadDrives = async () => {
    await runWithBooleanLoading(setReloading, async () => {
      try {
        const result = await reloadDrives()

        if (result.success) {
          const added = result.added || 0
          const updated = result.updated || 0
          const deprecated = result.deprecated || 0
          const reactivated = result.reactivated || 0

          const changes = []
          if (added > 0) changes.push(`${added} added`)
          if (updated > 0) changes.push(`${updated} updated`)
          if (deprecated > 0) changes.push(`${deprecated} deprecated`)
          if (reactivated > 0) changes.push(`${reactivated} reactivated`)

          const message = changes.length > 0
            ? `Drives reloaded! ${changes.join(', ')}`
            : 'Drives reloaded - no changes detected'

          showToast(message)
          fetchDrives()
        } else {
          showToast(`Failed to reload drives: ${result.error || 'Unknown error'}`, 'error')
        }
      } catch (error) {
        console.error('Error reloading drives:', error)
        showToast(getApiErrorMessage(error, 'Failed to reload drives'), 'error')
      }
    })
  }

  const filteredDrives = drives.filter(drive => {
    if (filter === 'all') return true
    if (filter === 'custom') return drive.is_custom
    return drive.style_type === filter
  })

  // Sort drives based on sortBy value
  const sortedDrives = [...filteredDrives].sort((a, b) => {
    switch (sortBy) {
      case 'name-asc':
        return a.name.localeCompare(b.name)
      case 'name-desc':
        return b.name.localeCompare(a.name)
      case 'posters-desc':
        return b.poster_count - a.poster_count
      case 'posters-asc':
        return a.poster_count - b.poster_count
      case 'subscribed':
        if (a.subscribed === b.subscribed) return a.name.localeCompare(b.name)
        return a.subscribed ? -1 : 1
      case 'unsubscribed':
        if (a.subscribed === b.subscribed) return a.name.localeCompare(b.name)
        return a.subscribed ? 1 : -1
      default:
        return a.name.localeCompare(b.name)
    }
  })

  // Group drives by type when showing "All"
  const groupedDrives = filter === 'all' 
    ? {
        'CL2K': sortedDrives.filter(d => d.style_type === 'CL2K'),
        'MM2K': sortedDrives.filter(d => d.style_type === 'MM2K'),
        'Custom': sortedDrives.filter(d => d.is_custom)
      }
    : null

  const subscribedCount = drives.filter(d => d.subscribed).length

  if (loading) {
    return <div className="loading">Loading drives...</div>
  }

  return (
    <div className="page-container gdrives">
      <div className="gdrives-header">
        <h1>Google Drives</h1>
        <p>{scope === 'artwork' ? 'Logo, backdrop & square-art drives' : 'Subscribe to community poster collections'}</p>
      </div>

      <div className="gdrives-tabs">
        <button
          className={scope === 'posters' ? 'active' : ''}
          onClick={() => { setScope('posters'); localStorage.setItem('posterflow.gdrives.scope', 'posters') }}
        >
          <Image size={16} className="tab-icon" />
          Posters
        </button>
        <button
          className={scope === 'artwork' ? 'active' : ''}
          onClick={() => { setScope('artwork'); localStorage.setItem('posterflow.gdrives.scope', 'artwork') }}
        >
          <Layers size={16} className="tab-icon" />
          Artwork
        </button>
      </div>

      {scope === 'artwork' ? (
        <ArtworkDrivesPanel />
      ) : (
      <>
      <Toolbar
        title="Poster Drives"
        description={
          <>
            Subscribe to community poster collections from Google Drive. Use <strong>Scheduling</strong> to automate syncs, <strong>Configure</strong> to set your local storage path, and <strong>Reload</strong> to refresh the community drive list.
          </>
        }
      >
          <div className="btn-pair">
            <button
              className="btn-toolbar btn-toolbar-link"
              onClick={openSchedulingSettings}
              title="Open scheduling settings"
            >
              Scheduling
            </button>
            <button
              className="btn-toolbar btn-toolbar-link"
              onClick={openNotificationSettings}
              title="Open Discord notification settings"
            >
              Discord
            </button>
          </div>
          <button
            className="btn-toolbar btn-toolbar-link"
            onClick={() => setShowStorageModal(true)}
            title="Configure poster storage location"
          >
            <Settings2 size={16} /> Configure
          </button>
          <button 
            className="btn-toolbar btn-reload" 
            onClick={handleReloadDrives}
            disabled={reloading}
            title="Reload community drives from remote source"
          >
            {reloading ? (<><RotateCw size={16} className="spinning" /> Reloading...</>) : (<><RotateCw size={16} /> Reload</>)}
          </button>
          <button 
            className="btn-toolbar btn-add-custom" 
            onClick={() => setShowAddModal(true)}
            title="Add a custom Google Drive"
          >
            <Plus size={16} /> Custom
          </button>
          <button
            className="btn-toolbar btn-sync-all"
            onClick={handleSyncAll}
            disabled={syncingAll || subscribedCount === 0}
            title={subscribedCount > 0 ? `Sync all ${subscribedCount} subscribed drives` : 'No subscribed drives to sync'}
          >
            {syncingAll ? 'Syncing...' : (<><RefreshCw size={16} /> Sync All{subscribedCount > 0 ? ` (${subscribedCount})` : ''}</>)}
          </button>
      </Toolbar>

      {/* Bulk Actions */}
      <div className="bulk-actions">
        <div className="bulk-action-group">
          <span className="bulk-label">CL2K:</span>
          <button className="btn-bulk" onClick={() => handleBulkSubscribe('CL2K')}>Subscribe All</button>
          <button className="btn-bulk" onClick={() => handleBulkUnsubscribe('CL2K')}>Unsubscribe All</button>
        </div>
        <div className="bulk-action-group">
          <span className="bulk-label">MM2K:</span>
          <button className="btn-bulk" onClick={() => handleBulkSubscribe('MM2K')}>Subscribe All</button>
          <button className="btn-bulk" onClick={() => handleBulkUnsubscribe('MM2K')}>Unsubscribe All</button>
        </div>
        <div className="bulk-action-group">
          <span className="bulk-label">Custom:</span>
          <button className="btn-bulk" onClick={() => handleBulkSubscribe('custom')}>Subscribe All</button>
          <button className="btn-bulk" onClick={() => handleBulkUnsubscribe('custom')}>Unsubscribe All</button>
        </div>
      </div>

      {/* Drives controls: filter + sort */}
      <div className="drives-controls">
        <div className="filter-buttons">
          <button 
            className={filter === 'all' ? 'active' : ''} 
            onClick={() => setFilter('all')}
          >
            All
          </button>
          <button 
            className={filter === 'CL2K' ? 'active' : ''} 
            onClick={() => setFilter('CL2K')}
          >
            CL2K
          </button>
          <button 
            className={filter === 'MM2K' ? 'active' : ''} 
            onClick={() => setFilter('MM2K')}
          >
            MM2K
          </button>
          <button 
            className={filter === 'custom' ? 'active' : ''} 
            onClick={() => setFilter('custom')}
          >
            Custom
          </button>
        </div>
        <div className="sort-dropdown">
          <label htmlFor="sort-select">Sort:</label>
          <select 
            id="sort-select"
            value={sortBy} 
            onChange={(e) => setSortBy(e.target.value)}
          >
            <option value="name-asc">Name (A-Z)</option>
            <option value="name-desc">Name (Z-A)</option>
            <option value="posters-desc">Most Posters</option>
            <option value="posters-asc">Fewest Posters</option>
            <option value="subscribed">Subscribed First</option>
            <option value="unsubscribed">Unsubscribed First</option>
          </select>
        </div>
      </div>

      {groupedDrives ? (
        // Show grouped drives with section headers
        <>
          {Object.entries(groupedDrives).map(([type, typeDrives]) => 
            typeDrives.length > 0 ? (
              <div key={type} className="drive-section">
                <h2 className="section-header">{type} Drives</h2>
                <div className="drives-grid">
                  {typeDrives.map(drive => (
                    <div key={drive.id} className={`drive-card ${drive.subscribed ? 'subscribed' : ''} ${drive.is_deprecated ? 'deprecated' : ''} drive-type-${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()}`} style={idTooltip === drive.id ? { zIndex: 50 } : undefined}>
                      {drive.is_deprecated && (
                        <div className="deprecated-overlay">
                          <div className="deprecated-badge">⚠️ DEPRECATED</div>
                          <p className="deprecated-message">This drive has been removed from the community list</p>
                          <button 
                            className="btn-delete-icon" 
                            onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)}
                            title="Delete drive"
                          >
                            <Trash2 size={18} />
                          </button>
                        </div>
                      )}
                      {drive.is_custom && !drive.is_deprecated && (
                        <button 
                          className="btn-delete-icon" 
                          onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)}
                          title="Delete drive"
                        >
                          <Trash2 size={18} />
                        </button>
                      )}
                      <div className="drive-header">
                        <div className="drive-icon"><HardDriveDownload size={18} color="#4285F4" /></div>
                        <div className={`drive-badge drive-badge-${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()}`}>
                          {drive.is_custom ? 'Custom' : drive.style_type}
                        </div>
                        <div
                          className="drive-id-wrapper"
                          onMouseEnter={(e) => openIdTooltip(drive.id, e)}
                          onMouseLeave={hideIdTooltip}
                        >
                          <button className="btn-drive-id-info">
                            <Info size={13} />
                          </button>
                          {idTooltip === drive.id && (
                            <div
                              className="drive-id-tooltip"
                              data-align={tooltipAlign}
                              onMouseEnter={() => showIdTooltip(drive.id)}
                              onMouseLeave={hideIdTooltip}
                            >
                              {drive.description && (
                                <span className="drive-id-tooltip-desc">{drive.description}</span>
                              )}
                              <div className="drive-id-tooltip-row">
                                <span className="drive-id-tooltip-text">{drive.drive_id}</span>
                                <button
                                  className="btn-copy-id"
                                  onClick={(e) => { e.stopPropagation(); handleCopyId(drive.id, drive.drive_id) }}
                                  title="Copy to clipboard"
                                >
                                  {copiedId === drive.id ? <Check size={11} /> : <Copy size={11} />}
                                </button>
                              </div>
                              {driveFolderUrl(drive.drive_id) && (
                                <a
                                  className="drive-id-tooltip-link"
                                  href={driveFolderUrl(drive.drive_id)!}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => e.stopPropagation()}
                                >
                                  <ExternalLink size={11} /> Open in Drive
                                </a>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                      <h3>{drive.display_name || drive.name}</h3>
                      <div className="drive-meta">
                        {drive.custom_path && (
                          <p className="custom-path">Path: {drive.custom_path}</p>
                        )}
                        <p className="poster-count">
                          📁 {drive.poster_count.toLocaleString()} in DB
                          {drive.sync_file_count != null && drive.sync_file_count !== drive.poster_count && (
                            <span className="file-count-mismatch" title="Filesystem count differs from database">
                              {' '}• {drive.sync_file_count.toLocaleString()} on disk
                            </span>
                          )}
                        </p>
                        <p className="last-synced">
                          {drive.last_synced 
                            ? `Synced: ${new Date(drive.last_synced).toLocaleDateString()} ${new Date(drive.last_synced).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`
                            : 'Not synced yet'
                          }
                        </p>
                      </div>
                      
                      <div className="drive-actions">
                        {drive.subscribed ? (
                          <>
                            <button 
                              className="btn-icon btn-sync" 
                              onClick={() => handleSync(drive.id)}
                              disabled={syncingDrive === drive.id}
                              title={syncingDrive === drive.id ? 'Starting sync...' : 'Sync drive'}
                            >
                              <RefreshCw size={16} />
                            </button>
                            <button 
                              className="btn-icon btn-edit" 
                              onClick={() => setEditingDrive(drive)}
                              title="Drive settings"
                            >
                              <SettingsIcon size={16} />
                            </button>
                            <button 
                              className="btn-icon btn-subscribed" 
                              onClick={() => handleUnsubscribe(drive.id)}
                              title="Unsubscribe from drive"
                            >
                              <BookmarkMinus size={16} />
                            </button>
                          </>
                        ) : (
                          <>
                            <button 
                              className="btn-icon btn-edit" 
                              onClick={() => setEditingDrive(drive)}
                              title="Drive settings"
                            >
                              <SettingsIcon size={16} />
                            </button>
                            <button 
                              className="btn-subscribe" 
                              onClick={() => handleSubscribe(drive.id)}
                              title="Subscribe to drive"
                            >
                              Subscribe
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null
          )}
        </>
      ) : (
        // Show ungrouped filtered drives
        <div className="drives-grid">
          {sortedDrives.map(drive => (
            <div key={drive.id} className={`drive-card ${drive.subscribed ? 'subscribed' : ''} ${drive.is_deprecated ? 'deprecated' : ''} drive-type-${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()}`} style={idTooltip === drive.id ? { zIndex: 50 } : undefined}>
              {drive.is_deprecated && (
                <div className="deprecated-overlay">
                  <div className="deprecated-badge">⚠️ DEPRECATED</div>
                  <p className="deprecated-message">This drive has been removed from the community list</p>
                  <button 
                    className="btn-delete-icon" 
                    onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)}
                    title="Delete drive"
                  >
                    <Trash2 size={18} />
                  </button>
                </div>
              )}
              {drive.is_custom && !drive.is_deprecated && (
                <button 
                  className="btn-delete-icon" 
                  onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)}
                  title="Delete drive"
                >
                  <Trash2 size={18} />
                </button>
              )}
              <div className="drive-header">
                <div className="drive-icon">🗄️</div>
                <div className={`drive-badge drive-badge-${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()}`}>
                  {drive.is_custom ? 'Custom' : drive.style_type}
                </div>
                <div
                  className="drive-id-wrapper"
                  onMouseEnter={(e) => openIdTooltip(drive.id, e)}
                  onMouseLeave={hideIdTooltip}
                >
                  <button className="btn-drive-id-info">
                    <Info size={13} />
                  </button>
                  {idTooltip === drive.id && (
                    <div
                      className="drive-id-tooltip"
                      data-align={tooltipAlign}
                      onMouseEnter={() => showIdTooltip(drive.id)}
                      onMouseLeave={hideIdTooltip}
                    >
                      {drive.description && (
                        <span className="drive-id-tooltip-desc">{drive.description}</span>
                      )}
                      <div className="drive-id-tooltip-row">
                        <span className="drive-id-tooltip-text">{drive.drive_id}</span>
                        <button
                          className="btn-copy-id"
                          onClick={(e) => { e.stopPropagation(); handleCopyId(drive.id, drive.drive_id) }}
                          title="Copy to clipboard"
                        >
                          {copiedId === drive.id ? <Check size={11} /> : <Copy size={11} />}
                        </button>
                      </div>
                      {driveFolderUrl(drive.drive_id) && (
                        <a
                          className="drive-id-tooltip-link"
                          href={driveFolderUrl(drive.drive_id)!}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <ExternalLink size={11} /> Open in Drive
                        </a>
                      )}
                    </div>
                  )}
                </div>
              </div>
              <h3>{drive.display_name || drive.name}</h3>
              <div className="drive-meta">
                {drive.custom_path && (
                  <p className="custom-path">Path: {drive.custom_path}</p>
                )}
                <p className="poster-count">
                  📁 {drive.poster_count.toLocaleString()} in DB
                  {drive.sync_file_count != null && drive.sync_file_count !== drive.poster_count && (
                    <span className="file-count-mismatch" title="Filesystem count differs from database">
                      {' '}• {drive.sync_file_count.toLocaleString()} on disk
                    </span>
                  )}
                </p>
                <p className="last-synced">
                  {drive.last_synced 
                    ? `Synced: ${new Date(drive.last_synced).toLocaleDateString()} ${new Date(drive.last_synced).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`
                    : 'Not synced yet'
                  }
                </p>
              </div>
              
              <div className="drive-actions">
                {drive.subscribed ? (
                  <>
                    <button 
                      className="btn-icon btn-sync" 
                      onClick={() => handleSync(drive.id)}
                      disabled={syncingDrive === drive.id}
                      title={syncingDrive === drive.id ? 'Starting sync...' : 'Sync drive'}
                    >
                      <RefreshCw size={16} />
                    </button>
                    <button 
                      className="btn-icon btn-edit" 
                      onClick={() => setEditingDrive(drive)}
                      title="Drive settings"
                    >
                      <SettingsIcon size={16} />
                    </button>
                    <button 
                      className="btn-icon btn-subscribed" 
                      onClick={() => handleUnsubscribe(drive.id)}
                      title="Unsubscribe from drive"
                    >
                      <BookmarkMinus size={16} />
                    </button>
                  </>
                ) : (
                  <>
                    <button 
                      className="btn-icon btn-edit" 
                      onClick={() => setEditingDrive(drive)}
                      title="Drive settings"
                    >
                      <SettingsIcon size={16} />
                    </button>
                    <button 
                      className="btn-subscribe" 
                      onClick={() => handleSubscribe(drive.id)}
                      title="Subscribe to drive"
                    >
                      Subscribe
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {editingDrive && (
        <DriveEditModal
          drive={editingDrive}
          onClose={() => setEditingDrive(null)}
          onSave={handleSaveDrive}
        />
      )}

      {showAddModal && (
        <AddCustomDriveModal
          onClose={() => setShowAddModal(false)}
          onAdd={handleAddCustomDrive}
        />
      )}

      {showStorageModal && (
        <GdriveStorageModal
          onClose={() => setShowStorageModal(false)}
        />
      )}

      <ConfirmDialog
        isOpen={priorityPrompt.show}
        title="Add to Drive Priority?"
        message={`Also add "${priorityPrompt.driveName}" to your Drive Priority list so it's used for matching? You can reorder or remove it anytime in Asset Manager → Drive Priority.`}
        confirmText="Add to Priority"
        cancelText="Just Subscribe"
        variant="info"
        onConfirm={() => resolvePriorityPrompt(true)}
        onCancel={() => resolvePriorityPrompt(false)}
      >
        <label className="delete-files-checkbox">
          <input type="checkbox" checked={promptDontAsk} onChange={(e) => setPromptDontAsk(e.target.checked)} />
          <span>Don't ask again for poster drives</span>
        </label>
      </ConfirmDialog>

      <ConfirmDialog
        isOpen={deleteConfirm.show}
        title={deleteConfirm.isDeprecated ? "Delete Deprecated Drive" : "Delete Custom Drive"}
        message={deleteConfirm.isDeprecated 
          ? `This drive "${deleteConfirm.driveName}" has been removed from the community list. Would you like to remove it from your system?`
          : `Are you sure you want to delete "${deleteConfirm.driveName}"?`
        }
        confirmText="Delete Drive"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => deleteConfirm.driveId && handleDeleteDrive(deleteConfirm.driveId)}
        onCancel={() => {
          setDeleteConfirm({show: false, driveId: null, driveName: '', isDeprecated: false})
          setDeleteFiles(false)
        }}
      >
        <label className="delete-files-checkbox">
          <input 
            type="checkbox" 
            checked={deleteFiles} 
            onChange={(e) => setDeleteFiles(e.target.checked)}
          />
          <span>Also delete all poster files from disk (cannot be undone)</span>
        </label>
      </ConfirmDialog>
      </>
      )}
    </div>
  )
}

export default GDrives