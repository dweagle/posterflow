import { DragEvent, useEffect, useRef, useState } from 'react'
import { GripVertical, Minus, Plus, Trash2 } from 'lucide-react'
import PriorityHeader from './PriorityHeader'
import { type PriorityScope } from './PriorityScopeSelector'
import { ArtworkDrive, getArtworkDrives } from '../../api/client'
import { useArtworkDrivePriority } from '../../hooks/useArtworkDrivePriority'
import { useToast } from '../Toast'

type ArtworkPriorityTabProps = {
  scope: PriorityScope
  onScopeChange: (scope: PriorityScope) => void
}

// Artwork-drive priority ordering. Mimics PriorityTab exactly (two-panel drag/drop),
// minus the poster style scheme: available drives are grouped Preset / Custom, and
// cards show file counts. Self-contained (fetches its own drives + manages save state).
function ArtworkPriorityTab({ scope, onScopeChange }: ArtworkPriorityTabProps) {
  const { showToast } = useToast()
  const [drives, setDrives] = useState<ArtworkDrive[]>([])
  const [saving, setSaving] = useState(false)
  const loadedRef = useRef(false)

  const {
    priorityList,
    draggedDrive,
    dragOverIndex,
    hasUnsavedChanges,
    loadPriority,
    handleDragStart,
    handleDragOver,
    handleDragOverStart,
    handleDragEnd,
    handleDragOverCard,
    handleDragLeave,
    handleDropInPriority,
    handleRemoveFromPriority,
    handleAddAllGroup,
    handleRemoveAllGroup,
    handleDropInAvailable,
    handleDragOverEnd,
    handleDriveTouchStart,
    savePriority,
  } = useArtworkDrivePriority({ drives, showToast })

  useEffect(() => {
    getArtworkDrives().then(setDrives).catch((e) => console.error('Error loading artwork drives:', e))
  }, [])

  useEffect(() => {
    if (drives.length > 0 && !loadedRef.current) {
      loadedRef.current = true
      loadPriority()
    }
    // loadPriority closes over the freshly-loaded drives; only run once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drives])

  const onSave = async () => {
    setSaving(true)
    try {
      await savePriority()
    } finally {
      setSaving(false)
    }
  }

  const getCardDropIndex = (e: DragEvent<HTMLElement>, index: number) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const midpointY = rect.top + rect.height / 2
    return e.clientY > midpointY ? index + 1 : index
  }

  const availablePreset = drives.filter((d) => !d.is_custom && d.subscribed && !priorityList.find((p) => p.id === d.id))
  const availableCustom = drives.filter((d) => d.is_custom && d.subscribed && !priorityList.find((p) => p.id === d.id))

  const renderAvailableCard = (drive: ArtworkDrive, accent: 'artwork' | 'custom') => (
    <div
      key={drive.id}
      className={`drive-card-small ${accent}`}
      draggable
      onDragStart={() => handleDragStart(drive)}
      onDragEnd={handleDragEnd}
      onTouchStart={(e) => handleDriveTouchStart(e, drive)}
    >
      <GripVertical size={14} className="drag-handle" />
      <div className="drive-info">
        <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
        <div className="drive-count">{drive.sync_file_count.toLocaleString()}</div>
      </div>
    </div>
  )

  return (
    <>
      <PriorityHeader
        scope={scope}
        onScopeChange={onScopeChange}
        saving={saving}
        hasUnsavedChanges={hasUnsavedChanges}
        onSave={onSave}
      />

      <div className="priority-tab">
        <div className="priority-content">
          <div className="available-drives" onDragOver={handleDragOver} onDrop={handleDropInAvailable}>
            <div className="panel-header-row">
              <h3>Available Drives</h3>
              <div className="bulk-action-btns">
                <button className="bulk-btn bulk-add artwork" onClick={() => handleAddAllGroup('preset')} title="Add all preset artwork drives to priority">
                  <Plus size={11} /><span>Preset</span>
                </button>
                <button className="bulk-btn bulk-add custom" onClick={() => handleAddAllGroup('custom')} title="Add all custom artwork drives to priority">
                  <Plus size={11} /><span>Custom</span>
                </button>
              </div>
            </div>
            <p className="section-hint">Drag drives to the priority list on the right. Only subscribed artwork drives from the <strong>GDrives → Assets</strong> tab appear here.</p>

            <div className="available-drives-scroll">
              <div className="drive-type-section">
                <h4 className="drive-type-header">Preset Drives</h4>
                <div className="drive-cards">
                  {availablePreset.map((drive) => renderAvailableCard(drive, 'artwork'))}
                </div>
              </div>

              <div className="drive-type-section">
                <h4 className="drive-type-header">Custom Drives</h4>
                <div className="drive-cards">
                  {availableCustom.map((drive) => renderAvailableCard(drive, 'custom'))}
                </div>
              </div>
            </div>
          </div>

          <div className="priority-list-container">
            <div className="panel-header-row">
              <h3>Priority Order</h3>
              <div className="bulk-action-btns">
                <button className="bulk-btn bulk-remove artwork" onClick={() => handleRemoveAllGroup('preset')} title="Remove all preset artwork drives from priority">
                  <Minus size={11} /><span>Preset</span>
                </button>
                <button className="bulk-btn bulk-remove custom" onClick={() => handleRemoveAllGroup('custom')} title="Remove all custom artwork drives from priority">
                  <Minus size={11} /><span>Custom</span>
                </button>
              </div>
            </div>
            <p className="section-hint">Choose the drives you want to use. Higher drives override lower ones</p>

            <div className={`priority-drop-zone ${priorityList.length === 0 ? 'empty' : ''}`} onDragOver={handleDragOver} onDrop={(e) => handleDropInPriority(e)} onDragLeave={handleDragLeave}>
              {priorityList.length === 0 ? (
                <div className="empty-message">
                  <GripVertical size={32} color="#666" />
                  <p>Drag drives here to set priority</p>
                </div>
              ) : (
                <>
                  <div
                    className="drop-zone-start"
                    onDragOver={handleDragOverStart}
                    onDrop={(e) => handleDropInPriority(e, 0)}
                    onDragLeave={handleDragLeave}
                  />

                  {priorityList.map((drive, index) => (
                    <div key={drive.id} className="priority-item-wrapper">
                      {dragOverIndex === index && draggedDrive?.id !== drive.id && (
                        <>
                          <div className="drop-side-indicator" />
                          <div className="drop-indicator" />
                        </>
                      )}

                      {(() => {
                        const isDraggedCard = draggedDrive?.id === drive.id
                        const showInsertBeforeCue = dragOverIndex === index && !isDraggedCard
                        const showInsertAfterCue = dragOverIndex === index + 1 && dragOverIndex !== priorityList.length && !isDraggedCard
                        const accent = drive.is_custom ? 'custom' : 'artwork'

                        return (
                          <div
                            data-priority-index={index}
                            className={`priority-drive-card ${accent} ${showInsertBeforeCue ? 'drop-target-before' : ''} ${showInsertAfterCue ? 'drop-target-after' : ''}`}
                            draggable
                            onDragStart={() => handleDragStart(drive)}
                            onDragEnd={handleDragEnd}
                            onDragOver={(e) => handleDragOverCard(e, index)}
                            onDrop={(e) => handleDropInPriority(e, getCardDropIndex(e, index))}
                            onDragLeave={handleDragLeave}
                            onTouchStart={(e) => handleDriveTouchStart(e, drive)}
                          >
                            <GripVertical size={16} className="drag-handle" />
                            <div className="priority-number">{index + 1}</div>
                            <div className="drive-info">
                              <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
                            </div>
                            <div className="drive-poster-count">{drive.sync_file_count.toLocaleString()} files</div>
                            <span className={`drive-badge drive-badge-${drive.is_custom ? 'custom' : 'artwork'}`}>
                              {drive.is_custom ? 'Custom' : 'Artwork'}
                            </span>
                            <button className="btn-remove" onClick={() => handleRemoveFromPriority(drive.id)} title="Remove from priority">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )
                      })()}
                    </div>
                  ))}
                  <div
                    className="drop-zone-end"
                    onDragOver={handleDragOverEnd}
                    onDrop={(e) => handleDropInPriority(e, priorityList.length)}
                    onDragLeave={handleDragLeave}
                  >
                    {dragOverIndex === priorityList.length && (
                      <>
                        <div className="drop-side-indicator" />
                        <div className="drop-indicator" />
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

export default ArtworkPriorityTab
