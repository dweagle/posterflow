import { GripVertical, Minus, Plus, Save, Trash2 } from 'lucide-react'
import { DragEvent } from 'react'
import { Drive } from '../../api/client'

type PriorityTabProps = {
  hasUnsavedPriorityChanges: boolean
  saving: boolean
  onSavePriority: () => void
  enabledStyles: Set<string>
  onToggleStyle: (style: 'MM2K' | 'CL2K' | 'Custom') => void
  drives: Drive[]
  priorityList: Drive[]
  draggedDrive: Drive | null
  dragOverIndex: number | null
  onDragStart: (drive: Drive) => void
  onDragOver: (e: DragEvent) => void
  onDragEnd: () => void
  onDropInAvailable: (e: DragEvent) => void
  onDragOverStart: (e: DragEvent) => void
  onDragOverCard: (e: DragEvent, index: number) => void
  onDropInPriority: (e: DragEvent, index?: number) => void
  onDragOverEnd: (e: DragEvent) => void
  onDragLeave: (e: DragEvent) => void
  onRemoveFromPriority: (driveId: number) => void
  onAddAllStyle: (style: 'MM2K' | 'CL2K' | 'Custom') => void
  onRemoveAllStyle: (style: 'MM2K' | 'CL2K' | 'Custom') => void
}

function PriorityTab({
  hasUnsavedPriorityChanges,
  saving,
  onSavePriority,
  enabledStyles,
  onToggleStyle,
  drives,
  priorityList,
  draggedDrive,
  dragOverIndex,
  onDragStart,
  onDragOver,
  onDragEnd,
  onDropInAvailable,
  onDragOverStart,
  onDragOverCard,
  onDropInPriority,
  onDragOverEnd,
  onDragLeave,
  onRemoveFromPriority,
  onAddAllStyle,
  onRemoveAllStyle,
}: PriorityTabProps) {
  const getCardDropIndex = (e: DragEvent<HTMLElement>, index: number) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const midpointY = rect.top + rect.height / 2
    return e.clientY > midpointY ? index + 1 : index
  }

  return (
    <>
      <div className="toolbar">
        <div>
          <h2>Configure Drive Priority</h2>
          <p>Select which poster styles to use and set their priority order. Higher priority drives will override lower ones.</p>
        </div>
        <div className="action-buttons">
          <button className={`btn-toolbar ${hasUnsavedPriorityChanges ? 'btn-unsaved' : ''}`} onClick={onSavePriority} disabled={!hasUnsavedPriorityChanges || saving}>
            <Save size={16} />
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      <div className="priority-tab">
        <div className="priority-content">
          <div className="available-drives" onDragOver={onDragOver} onDrop={onDropInAvailable}>
            <div className="panel-header-row">
              <h3>Available Drives</h3>
              <div className="bulk-action-btns">
                <button className="bulk-btn bulk-add mm2k" onClick={() => onAddAllStyle('MM2K')} title="Add all MM2K drives to priority">
                  <Plus size={11} /><span>MM2K</span>
                </button>
                <button className="bulk-btn bulk-add cl2k" onClick={() => onAddAllStyle('CL2K')} title="Add all CL2K drives to priority">
                  <Plus size={11} /><span>CL2K</span>
                </button>
                <button className="bulk-btn bulk-add custom" onClick={() => onAddAllStyle('Custom')} title="Add all Custom drives to priority">
                  <Plus size={11} /><span>Custom</span>
                </button>
              </div>
            </div>
            <p className="section-hint">Drag drives to the priority list on the right. Only subscribed drives from the <strong>GDrives</strong> page appear here.</p>
            <div className="style-checkboxes-inline">
              <label className="style-checkbox-inline">
                <input type="checkbox" checked={enabledStyles.has('MM2K')} onChange={() => onToggleStyle('MM2K')} />
                <span className="style-badge style-mm2k">MM2K</span>
              </label>
              <label className="style-checkbox-inline">
                <input type="checkbox" checked={enabledStyles.has('CL2K')} onChange={() => onToggleStyle('CL2K')} />
                <span className="style-badge style-cl2k">CL2K</span>
              </label>
              <label className="style-checkbox-inline">
                <input type="checkbox" checked={enabledStyles.has('Custom')} onChange={() => onToggleStyle('Custom')} />
                <span className="style-badge style-custom">Custom</span>
              </label>
            </div>

            <div className="available-drives-scroll">
              {enabledStyles.has('MM2K') && (
                <div className="drive-type-section">
                  <h4 className="drive-type-header">MM2K Drives</h4>
                  <div className="drive-cards">
                    {drives
                      .filter((d) => d.style_type === 'MM2K' && !priorityList.find((p) => p.id === d.id))
                      .map((drive) => (
                        <div key={drive.id} className="drive-card-small mm2k" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.name}>{drive.name}</div>
                            <div className="drive-count">{drive.poster_count.toLocaleString()}</div>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {enabledStyles.has('CL2K') && (
                <div className="drive-type-section">
                  <h4 className="drive-type-header">CL2K Drives</h4>
                  <div className="drive-cards">
                    {drives
                      .filter((d) => d.style_type === 'CL2K' && !priorityList.find((p) => p.id === d.id))
                      .map((drive) => (
                        <div key={drive.id} className="drive-card-small cl2k" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.name}>{drive.name}</div>
                            <div className="drive-count">{drive.poster_count.toLocaleString()}</div>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {enabledStyles.has('Custom') && (
                <div className="drive-type-section">
                  <h4 className="drive-type-header">Custom Drives</h4>
                  <div className="drive-cards">
                    {drives
                      .filter((d) => d.is_custom && !priorityList.find((p) => p.id === d.id))
                      .map((drive) => (
                        <div key={drive.id} className="drive-card-small custom" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.name}>{drive.name}</div>
                            <div className="drive-count">{drive.poster_count.toLocaleString()}</div>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="priority-list-container">
            <div className="panel-header-row">
              <h3>Priority Order</h3>
              <div className="bulk-action-btns">
                <button className="bulk-btn bulk-remove mm2k" onClick={() => onRemoveAllStyle('MM2K')} title="Remove all MM2K drives from priority">
                  <Minus size={11} /><span>MM2K</span>
                </button>
                <button className="bulk-btn bulk-remove cl2k" onClick={() => onRemoveAllStyle('CL2K')} title="Remove all CL2K drives from priority">
                  <Minus size={11} /><span>CL2K</span>
                </button>
                <button className="bulk-btn bulk-remove custom" onClick={() => onRemoveAllStyle('Custom')} title="Remove all Custom drives from priority">
                  <Minus size={11} /><span>Custom</span>
                </button>
              </div>
            </div>
            <p className="section-hint">Choose the drives you want to use. Higher drives override lower ones</p>

            <div className={`priority-drop-zone ${priorityList.length === 0 ? 'empty' : ''}`} onDragOver={onDragOver} onDrop={(e) => onDropInPriority(e)} onDragLeave={onDragLeave}>
              {priorityList.length === 0 ? (
                <div className="empty-message">
                  <GripVertical size={32} color="#666" />
                  <p>Drag drives here to set priority</p>
                </div>
              ) : (
                <>
                  <div
                    className="drop-zone-start"
                    onDragOver={onDragOverStart}
                    onDrop={(e) => onDropInPriority(e, 0)}
                    onDragLeave={onDragLeave}
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

                        return (
                          <div
                            className={`priority-drive-card ${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()} ${showInsertBeforeCue ? 'drop-target-before' : ''} ${showInsertAfterCue ? 'drop-target-after' : ''}`}
                            draggable
                            onDragStart={() => onDragStart(drive)}
                            onDragEnd={onDragEnd}
                            onDragOver={(e) => onDragOverCard(e, index)}
                            onDrop={(e) => onDropInPriority(e, getCardDropIndex(e, index))}
                            onDragLeave={onDragLeave}
                          >
                            <GripVertical size={16} className="drag-handle" />
                            <div className="priority-number">{index + 1}</div>
                            <div className="drive-info">
                              <div className="drive-name" title={drive.name}>{drive.name}</div>
                            </div>
                            <div className="drive-poster-count">{drive.poster_count.toLocaleString()} posters</div>
                            <span className={`drive-badge drive-badge-${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()}`}>
                              {drive.is_custom ? 'Custom' : drive.style_type}
                            </span>
                            <button className="btn-remove" onClick={() => onRemoveFromPriority(drive.id)} title="Remove from priority">
                              <Trash2 size={14} />
                            </button>
                          </div>
                        )
                      })()}
                    </div>
                  ))}
                  <div
                    className="drop-zone-end"
                    onDragOver={onDragOverEnd}
                    onDrop={(e) => onDropInPriority(e, priorityList.length)}
                    onDragLeave={onDragLeave}
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

export default PriorityTab
