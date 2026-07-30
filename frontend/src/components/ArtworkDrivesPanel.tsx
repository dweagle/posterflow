import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getArtworkDrives, subscribeArtworkDrive, unsubscribeArtworkDrive, syncArtworkDrive, syncAllArtworkDrives,
  updateArtworkDrive, createCustomArtworkDrive, deleteArtworkDrive, reloadArtworkDrives,
  ArtworkDrive, ARTWORK_TYPES, ARTWORK_TYPE_LABELS, type ArtworkType, getApiErrorMessage,
} from '../api/client'
import AddCustomDriveModal from './AddCustomDriveModal'
import ArtworkSubscribeModal from './ArtworkSubscribeModal'
import DriveEditModal from './DriveEditModal'
import ConfirmDialog from './ConfirmDialog'
import GdriveStorageModal from './GdriveStorageModal'
import { HardDriveDownload, RefreshCw, Settings as SettingsIcon, Settings2, BookmarkMinus, Trash2, RotateCw, Plus, Info, Copy, Check, ExternalLink } from 'lucide-react'
import { useToast } from './Toast'
import { useAppEvents } from '../contexts/AppEventsContext'
import Toolbar from './Toolbar'
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

type DriveUpdates = { custom_path: string | null; sync_enabled: boolean; drive_id?: string; synced_types?: ArtworkType[] }

// Indexed count for one artwork type, so a type being switched off can say what it will delete.
const typeCount = (drive: ArtworkDrive, type: ArtworkType): number =>
  type === 'logo' ? drive.logo_count : type === 'background' ? drive.background_count : drive.squareart_count

const typeCounts = (drive: ArtworkDrive): Partial<Record<ArtworkType, number>> =>
  Object.fromEntries(ARTWORK_TYPES.map(t => [t, typeCount(drive, t)]))

// Panel for artwork drives (logos / backdrops / square art). Mirrors the poster-drive
// grid card-for-card and button-for-button; the only difference is the "Artwork" badge
// (no MM2K/CL2K style). Priority is managed on Asset Manager → Drive Priority (Artwork scope), not here.
function ArtworkDrivesPanel() {
  const [drives, setDrives] = useState<ArtworkDrive[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('all')
  const [sortBy, setSortBy] = useState<string>('name-asc')
  const [syncingDrive, setSyncingDrive] = useState<number | null>(null)
  const [syncingAll, setSyncingAll] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [editingDrive, setEditingDrive] = useState<ArtworkDrive | null>(null)
  const [showAddModal, setShowAddModal] = useState(false)
  const [showStorageModal, setShowStorageModal] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<{ show: boolean; driveId: number | null; driveName: string; isDeprecated: boolean }>({ show: false, driveId: null, driveName: '', isDeprecated: false })
  const [deleteFiles, setDeleteFiles] = useState(false)
  const [subscribePrompt, setSubscribePrompt] = useState<{ driveId: number; driveName: string; askPriority: boolean } | null>(null)
  const [typeRemoval, setTypeRemoval] = useState<{ driveId: number; driveName: string; updates: DriveUpdates; removed: ArtworkType[]; fileCount: number } | null>(null)
  const { showToast } = useToast()
  const { jobs } = useAppEvents()
  const navigate = useNavigate()
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

  const openIdTooltip = (id: number, e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const center = rect.left + rect.width / 2
    const half = 170
    setTooltipAlign(center + half > window.innerWidth - 8 ? 'left' : center - half < 8 ? 'right' : 'center')
    showIdTooltip(id)
  }

  const hideIdTooltip = () => {
    tooltipHideTimer.current = setTimeout(() => setIdTooltip(null), 200)
  }

  useEffect(() => {
    fetchDrives()
  }, [])

  // Refresh counts when an asset-sync job finishes.
  useEffect(() => {
    let shouldRefresh = false
    for (const job of jobs) {
      const affects = job.job_type.startsWith('Artwork Sync')
      const prev = prevJobStatusRef.current[job.id]
      if (jobsInitializedRef.current && affects && (job.status === 'completed' || job.status === 'failed') && prev !== job.status) {
        shouldRefresh = true
      }
      prevJobStatusRef.current[job.id] = job.status
    }
    jobsInitializedRef.current = true
    if (shouldRefresh) fetchDrives()
  }, [jobs])

  const fetchDrives = async () => {
    try {
      const data = await getArtworkDrives()
      setDrives(data)
    } catch (error) {
      console.error('Error fetching artwork drives:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSubscribe = (driveId: number) => {
    const drive = drives.find(d => d.id === driveId)
    setSubscribePrompt({
      driveId,
      driveName: drive?.display_name || drive?.name || 'this drive',
      // Priority is only asked while the pref is still 'ask'; the type question always shows.
      askPriority: getAddToPriorityPref('artwork') === 'ask',
    })
  }

  const resolveSubscribePrompt = (types: ArtworkType[], addToPriority: boolean, dontAskAgain: boolean) => {
    const prompt = subscribePrompt
    setSubscribePrompt(null)
    if (!prompt) return
    const pref = getAddToPriorityPref('artwork')
    const add = pref === 'ask' ? addToPriority : pref === 'always'
    if (pref === 'ask' && dontAskAgain) setAddToPriorityPref('artwork', addToPriority ? 'always' : 'never')
    void doSubscribe(prompt.driveId, add, types)
  }

  const doSubscribe = async (driveId: number, addToPriority: boolean, types?: ArtworkType[]) => {
    try {
      const result = await subscribeArtworkDrive(driveId, addToPriority, types)
      fetchDrives()
      showToast(
        result?.added_to_priority
          ? 'Subscribed and added to Asset Manager → Drive Priority (Artwork).'
          : 'Subscribed. Remember to add this drive to your list in Asset Manager → Drive Priority (Artwork).',
        'info',
      )
      if (types && types.length < ARTWORK_TYPES.length) {
        showToast(`Syncing ${types.map(t => ARTWORK_TYPE_LABELS[t].toLowerCase()).join(' + ')} only.`, 'info')
      }
      if (result?.scan_job_id) {
        showToast('Initial scan queued. Check Dashboard for progress.', 'info')
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to subscribe'), 'error')
    }
  }

  const handleUnsubscribe = async (driveId: number) => {
    try {
      await unsubscribeArtworkDrive(driveId)
      fetchDrives()
      showToast('Unsubscribed.', 'info')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to unsubscribe'), 'error')
    }
  }

  const handleBulkSubscribe = async () => {
    const targets = drives.filter(d => !d.subscribed && !d.is_deprecated)
    if (targets.length === 0) {
      showToast('All artwork drives are already subscribed', 'info')
      return
    }
    try {
      await Promise.all(targets.map(d => subscribeArtworkDrive(d.id)))
      fetchDrives()
      showToast(`Subscribed to ${targets.length} artwork drive(s)`)
    } catch (error) {
      console.error('Error bulk subscribing:', error)
      showToast('Failed to subscribe to some drives', 'error')
    }
  }

  const handleBulkUnsubscribe = async () => {
    const targets = drives.filter(d => d.subscribed)
    if (targets.length === 0) {
      showToast('No artwork drives are subscribed', 'info')
      return
    }
    try {
      await Promise.all(targets.map(d => unsubscribeArtworkDrive(d.id)))
      fetchDrives()
      showToast(`Unsubscribed from ${targets.length} artwork drive(s)`)
    } catch (error) {
      console.error('Error bulk unsubscribing:', error)
      showToast('Failed to unsubscribe from some drives', 'error')
    }
  }

  const handleSync = async (driveId: number) => {
    setSyncingDrive(driveId)
    try {
      await syncArtworkDrive(driveId)
      showToast('Artwork sync started! Check Dashboard for progress.')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to start sync'), 'error')
    } finally {
      setSyncingDrive(null)
    }
  }

  const handleSyncAll = async () => {
    const subscribedCount = drives.filter(d => d.subscribed).length
    if (subscribedCount === 0) {
      showToast('No subscribed artwork drives to sync', 'error')
      return
    }
    setSyncingAll(true)
    try {
      await syncAllArtworkDrives()
      showToast(`Artwork sync started for ${subscribedCount} drives! Check Dashboard for progress.`)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to start sync'), 'error')
    } finally {
      setSyncingAll(false)
    }
  }

  const handleSaveDrive = async (driveId: number, updates: DriveUpdates) => {
    const drive = drives.find(d => d.id === driveId)
    // Dropping a type deletes its synced files, so confirm before the PATCH does it.
    if (drive && updates.synced_types) {
      const removed = drive.synced_types.filter(t => !updates.synced_types!.includes(t))
      const fileCount = removed.reduce((total, t) => total + typeCount(drive, t), 0)
      if (removed.length > 0 && fileCount > 0) {
        setTypeRemoval({ driveId, driveName: drive.display_name || drive.name, updates, removed, fileCount })
        return
      }
    }
    await applyDriveUpdate(driveId, updates)
  }

  const applyDriveUpdate = async (driveId: number, updates: DriveUpdates) => {
    try {
      await updateArtworkDrive(driveId, {
        custom_path: updates.custom_path || null,
        sync_enabled: updates.sync_enabled,
        drive_id: updates.drive_id,
        synced_types: updates.synced_types,
      })
      fetchDrives()
      setEditingDrive(null)
      showToast('Artwork drive updated successfully')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to update artwork drive'), 'error')
    }
  }

  const handleAddCustom = async (drive: CustomDriveModalInput) => {
    try {
      await createCustomArtworkDrive({
        name: drive.name,
        drive_id: drive.drive_id || undefined,
        custom_path: drive.custom_path ?? undefined,
        subscribed: drive.subscribed,
        sync_enabled: drive.sync_enabled,
      })
      fetchDrives()
      setShowAddModal(false)
      showToast('Custom artwork drive added successfully')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to add custom artwork drive'), 'error')
    }
  }

  const handleDelete = async (driveId: number) => {
    setDeleteConfirm({ show: false, driveId: null, driveName: '', isDeprecated: false })
    try {
      await deleteArtworkDrive(driveId, deleteFiles)
      fetchDrives()
      showToast(`Artwork drive deleted${deleteFiles ? ' (files removed)' : ''}`)
      setDeleteFiles(false)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to delete artwork drive'), 'error')
    }
  }

  const confirmDeleteDrive = (driveId: number, driveName: string, isDeprecated: boolean = false) => {
    setDeleteConfirm({ show: true, driveId, driveName, isDeprecated })
    setDeleteFiles(false)
  }

  const handleCopyId = async (driveId: number, driveIdStr: string) => {
    try {
      await navigator.clipboard.writeText(driveIdStr)
      setCopiedId(driveId)
      setTimeout(() => setCopiedId(null), 2000)
    } catch {
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

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const handleReload = async () => {
    setReloading(true)
    try {
      const result = await reloadArtworkDrives()
      if (result.success) {
        const changes = []
        if (result.added) changes.push(`${result.added} added`)
        if (result.updated) changes.push(`${result.updated} updated`)
        if (result.deprecated) changes.push(`${result.deprecated} deprecated`)
        if (result.reactivated) changes.push(`${result.reactivated} reactivated`)
        showToast(changes.length > 0 ? `Artwork drives reloaded! ${changes.join(', ')}` : 'Artwork drives reloaded - no changes detected')
        fetchDrives()
      } else {
        showToast(`Failed to reload: ${result.error || 'Unknown error'}`, 'error')
      }
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to reload artwork drives'), 'error')
    } finally {
      setReloading(false)
    }
  }

  const filteredDrives = drives.filter(drive => {
    if (filter === 'all') return true
    if (filter === 'custom') return drive.is_custom
    if (filter === 'preset') return !drive.is_custom
    return true
  })

  const sortDrives = (list: ArtworkDrive[]) => [...list].sort((a, b) => {
    switch (sortBy) {
      case 'name-asc': return a.name.localeCompare(b.name)
      case 'name-desc': return b.name.localeCompare(a.name)
      case 'files-desc': return b.sync_file_count - a.sync_file_count
      case 'files-asc': return a.sync_file_count - b.sync_file_count
      case 'subscribed':
        if (a.subscribed === b.subscribed) return a.name.localeCompare(b.name)
        return a.subscribed ? -1 : 1
      case 'unsubscribed':
        if (a.subscribed === b.subscribed) return a.name.localeCompare(b.name)
        return a.subscribed ? 1 : -1
      default: return a.name.localeCompare(b.name)
    }
  })

  const sortedDrives = sortDrives(filteredDrives)
  const presetDrives = sortedDrives.filter(d => !d.is_custom)
  const customDrives = sortedDrives.filter(d => d.is_custom)
  const subscribedCount = drives.filter(d => d.subscribed).length

  const renderDriveCard = (drive: ArtworkDrive) => (
    <div key={drive.id} className={`drive-card ${drive.subscribed ? 'subscribed' : ''} ${drive.is_deprecated ? 'deprecated' : ''} drive-type-artwork`} style={idTooltip === drive.id ? { zIndex: 50 } : undefined}>
      {drive.is_deprecated && (
        <div className="deprecated-overlay">
          <div className="deprecated-badge">⚠️ DEPRECATED</div>
          <p className="deprecated-message">This drive has been removed from the community list</p>
          <button className="btn-delete-icon" onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)} title="Delete drive">
            <Trash2 size={18} />
          </button>
        </div>
      )}
      {drive.is_custom && !drive.is_deprecated && (
        <button className="btn-delete-icon" onClick={() => confirmDeleteDrive(drive.id, drive.name, drive.is_deprecated)} title="Delete drive">
          <Trash2 size={18} />
        </button>
      )}
      <div className="drive-header">
        <div className="drive-icon"><HardDriveDownload size={18} color="#f59e0b" /></div>
        <div className="drive-badge drive-badge-artwork">Artwork</div>
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
          📁 {drive.artwork_count.toLocaleString()} in DB
          {drive.sync_file_count != null && drive.sync_file_count !== drive.artwork_count && (
            <span className="file-count-mismatch" title="Filesystem count differs from database">
              {' '}• {drive.sync_file_count.toLocaleString()} on disk
            </span>
          )}
        </p>
        {drive.artwork_count > 0 && (
          <div className="artwork-type-counts">
            {drive.synced_types.includes('logo') && <span>{drive.logo_count.toLocaleString()} logos</span>}
            {drive.synced_types.includes('background') && <span>{drive.background_count.toLocaleString()} backdrops</span>}
            {drive.synced_types.includes('squareart') && <span>{drive.squareart_count.toLocaleString()} square art</span>}
          </div>
        )}
        {drive.synced_types.length < ARTWORK_TYPES.length && (
          <span className="artwork-type-badge" title="This drive only syncs the artwork types listed">
            {drive.synced_types.map(t => ARTWORK_TYPE_LABELS[t]).join(' + ')} only
          </span>
        )}
        <p className="last-synced">
          {drive.last_synced
            ? `Synced: ${new Date(drive.last_synced).toLocaleDateString()} ${new Date(drive.last_synced).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
            : 'Not synced yet'}
        </p>
      </div>

      <div className="drive-actions">
        {drive.subscribed ? (
          <>
            <button className="btn-icon btn-sync" onClick={() => handleSync(drive.id)} disabled={syncingDrive === drive.id} title={syncingDrive === drive.id ? 'Starting sync...' : 'Sync drive'}>
              <RefreshCw size={16} />
            </button>
            <button className="btn-icon btn-edit" onClick={() => setEditingDrive(drive)} title="Drive settings">
              <SettingsIcon size={16} />
            </button>
            <button className="btn-icon btn-subscribed" onClick={() => handleUnsubscribe(drive.id)} title="Unsubscribe from drive">
              <BookmarkMinus size={16} />
            </button>
          </>
        ) : (
          <>
            <button className="btn-icon btn-edit" onClick={() => setEditingDrive(drive)} title="Drive settings">
              <SettingsIcon size={16} />
            </button>
            <button className="btn-subscribe" onClick={() => handleSubscribe(drive.id)} title="Subscribe to drive">
              Subscribe
            </button>
          </>
        )}
      </div>
    </div>
  )

  if (loading) {
    return <div className="loading">Loading artwork drives...</div>
  }

  return (
    <>
      <Toolbar
        title="Artwork Drives"
        description={
          <>
            Subscribe to Google Drives that hold <strong>logos, backdrops, and square art</strong>. Managed separately from posters. Each drive should contain <code>logos/</code>, <code>backgrounds/</code>, and <code>squareart/</code> folders. Set drive priority on <strong>Asset Manager → Drive Priority</strong> (Artwork scope).
          </>
        }
      >
          <div className="btn-pair">
            <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings} title="Open scheduling settings">
              Scheduling
            </button>
            <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings} title="Open Discord notification settings">
              Discord
            </button>
          </div>
          <button className="btn-toolbar btn-toolbar-link" onClick={() => setShowStorageModal(true)} title="Configure asset storage location">
            <Settings2 size={16} /> Configure
          </button>
          <button className="btn-toolbar btn-reload" onClick={handleReload} disabled={reloading} title="Reload artwork drive list from remote source">
            {reloading ? (<><RotateCw size={16} className="spinning" /> Reloading...</>) : (<><RotateCw size={16} /> Reload</>)}
          </button>
          <button className="btn-toolbar btn-add-custom" onClick={() => setShowAddModal(true)} title="Add a custom artwork drive">
            <Plus size={16} /> Custom
          </button>
          <button className="btn-toolbar btn-sync-all" onClick={handleSyncAll} disabled={syncingAll || subscribedCount === 0} title={subscribedCount > 0 ? `Sync all ${subscribedCount} subscribed artwork drives` : 'No subscribed artwork drives to sync'}>
            {syncingAll ? 'Syncing...' : (<><RefreshCw size={16} /> Sync All{subscribedCount > 0 ? ` (${subscribedCount})` : ''}</>)}
          </button>
      </Toolbar>

      {/* Bulk Actions */}
      <div className="bulk-actions">
        <div className="bulk-action-group">
          <span className="bulk-label">All Artwork Drives:</span>
          <button className="btn-bulk" onClick={handleBulkSubscribe}>Subscribe All</button>
          <button className="btn-bulk" onClick={handleBulkUnsubscribe}>Unsubscribe All</button>
        </div>
      </div>

      {/* Drives controls: filter + sort */}
      <div className="drives-controls">
        <div className="filter-buttons">
          <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>All</button>
          <button className={filter === 'preset' ? 'active' : ''} onClick={() => setFilter('preset')}>Preset</button>
          <button className={filter === 'custom' ? 'active' : ''} onClick={() => setFilter('custom')}>Custom</button>
        </div>
        <div className="sort-dropdown">
          <label htmlFor="asset-sort-select">Sort:</label>
          <select id="asset-sort-select" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
            <option value="name-asc">Name (A-Z)</option>
            <option value="name-desc">Name (Z-A)</option>
            <option value="files-desc">Most Files</option>
            <option value="files-asc">Fewest Files</option>
            <option value="subscribed">Subscribed First</option>
            <option value="unsubscribed">Unsubscribed First</option>
          </select>
        </div>
      </div>

      {sortedDrives.length === 0 ? (
        <div className="artwork-empty">
          <HardDriveDownload size={32} />
          <p>No artwork drives yet.</p>
          <p>Add a <strong>Custom</strong> drive that contains <code>logos/</code>, <code>backgrounds/</code>, and <code>squareart/</code> folders to get started.</p>
        </div>
      ) : filter === 'all' ? (
        // Grouped: presets first, custom drives below (matches the poster drive layout)
        <>
          {presetDrives.length > 0 && (
            <div className="drive-section">
              <h2 className="section-header">Preset Drives</h2>
              <div className="drives-grid">{presetDrives.map(renderDriveCard)}</div>
            </div>
          )}
          {customDrives.length > 0 && (
            <div className="drive-section">
              <h2 className="section-header">Custom Drives</h2>
              <div className="drives-grid">{customDrives.map(renderDriveCard)}</div>
            </div>
          )}
        </>
      ) : (
        <div className="drives-grid">{sortedDrives.map(renderDriveCard)}</div>
      )}

      {editingDrive && (
        <DriveEditModal
          drive={editingDrive}
          artworkTypeCounts={typeCounts(editingDrive)}
          onClose={() => setEditingDrive(null)}
          onSave={handleSaveDrive}
        />
      )}

      {subscribePrompt && (
        <ArtworkSubscribeModal
          driveName={subscribePrompt.driveName}
          askPriority={subscribePrompt.askPriority}
          onCancel={() => setSubscribePrompt(null)}
          onConfirm={resolveSubscribePrompt}
        />
      )}

      {showAddModal && (
        <AddCustomDriveModal
          onClose={() => setShowAddModal(false)}
          onAdd={handleAddCustom}
        />
      )}

      {showStorageModal && (
        <GdriveStorageModal
          variant="artwork"
          onClose={() => setShowStorageModal(false)}
        />
      )}

      <ConfirmDialog
        isOpen={typeRemoval !== null}
        title="Remove synced artwork?"
        message={typeRemoval
          ? `Turning off ${typeRemoval.removed.map(t => ARTWORK_TYPE_LABELS[t].toLowerCase()).join(' and ')} for "${typeRemoval.driveName}" deletes ${typeRemoval.fileCount.toLocaleString()} already-synced file(s) from its local folder. Future syncs will skip those folders.`
          : ''}
        confirmText="Remove"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => {
          const pending = typeRemoval
          setTypeRemoval(null)
          if (pending) void applyDriveUpdate(pending.driveId, pending.updates)
        }}
        onCancel={() => setTypeRemoval(null)}
      />

      <ConfirmDialog
        isOpen={deleteConfirm.show}
        title={deleteConfirm.isDeprecated ? 'Delete Deprecated Artwork Drive' : 'Delete Custom Artwork Drive'}
        message={deleteConfirm.isDeprecated
          ? `This drive "${deleteConfirm.driveName}" has been removed from the community list. Would you like to remove it from your system?`
          : `Are you sure you want to delete "${deleteConfirm.driveName}"?`}
        confirmText="Delete Drive"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => deleteConfirm.driveId && handleDelete(deleteConfirm.driveId)}
        onCancel={() => { setDeleteConfirm({ show: false, driveId: null, driveName: '', isDeprecated: false }); setDeleteFiles(false) }}
      >
        <label className="delete-files-checkbox">
          <input type="checkbox" checked={deleteFiles} onChange={(e) => setDeleteFiles(e.target.checked)} />
          <span>Also delete all artwork files from disk (cannot be undone)</span>
        </label>
      </ConfirmDialog>
    </>
  )
}

export default ArtworkDrivesPanel
