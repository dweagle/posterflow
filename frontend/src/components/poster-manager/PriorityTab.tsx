import { Download, GripVertical, List, Minus, Plus, Save, Trash2 } from 'lucide-react'
import Toolbar from './Toolbar'
import { DragEvent, TouchEvent as ReactTouchEvent, useState } from 'react'
import { Drive } from '../../api/client'
import { FallbackItem, PosterStyleStats } from '../../api/posterManager'
import PosterStyleModal from './PosterStyleModal'

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
  onDriveTouchStart: (e: ReactTouchEvent<HTMLElement>, drive: Drive) => void
  onRemoveFromPriority: (driveId: number) => void
  onAddAllStyle: (style: 'MM2K' | 'CL2K' | 'Custom') => void
  onRemoveAllStyle: (style: 'MM2K' | 'CL2K' | 'Custom') => void
  styleStats: PosterStyleStats | null
  tmdbApiKeyConfigured: boolean
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
  onDriveTouchStart,
  onRemoveFromPriority,
  onAddAllStyle,
  onRemoveAllStyle,
  styleStats,
  tmdbApiKeyConfigured,
}: PriorityTabProps) {
  const PREVIEW_LIMIT = 5
  const [openFallbackStyle, setOpenFallbackStyle] = useState<string | null>(null)

  // Compute style usage data outside JSX for clean modal access
  const styleCounts = styleStats?.style_counts ?? {}
  const styleFallbacks = styleStats?.style_fallbacks ?? {}
  const styleTotal = Object.values(styleCounts).reduce((a, b) => a + b, 0)
  const COMMUNITY_STYLES = ['MM2K', 'CL2K']
  const priorityStyles = [...new Set(priorityList.map(d => d.style_type).filter(Boolean))]
  const preferredStyle =
    priorityStyles.find(s => COMMUNITY_STYLES.includes(s ?? '') && styleCounts[s ?? ''] !== undefined) ??
    priorityStyles.find(s => s && styleCounts[s] !== undefined) ??
    Object.keys(styleCounts)[0] ??
    ''
  const styleEntries = Object.entries(styleCounts).sort(([a], [b]) => {
    if (a === preferredStyle) return -1
    if (b === preferredStyle) return 1
    return (styleCounts[b] ?? 0) - (styleCounts[a] ?? 0)
  })
  const fallbackEntries = styleEntries.slice(1).filter(([style, n]) => n > 0 && COMMUNITY_STYLES.includes(style))
  const openFallbackItems: FallbackItem[] = openFallbackStyle ? (styleFallbacks[openFallbackStyle] ?? []) : []

  const handleDownload = (style: string, items: FallbackItem[]) => {
    const content = [
      `# Missing ${preferredStyle} posters (used ${style} as fallback)`,
      `# Generated: ${new Date().toLocaleString()}`,
      `# Total: ${items.length}`,
      '',
      ...items.map(item => {
        // Strip trailing (YYYY) from title if year is already appended separately
        const cleanTitle = item.year
          ? item.title.replace(/\s*\(\d{4}\)\s*$/, '').trim()
          : item.title
        let line = item.year ? `${cleanTitle} (${item.year})` : cleanTitle
        if (item.type === 'show' && item.season != null) {
          line += item.season === 0 ? ' — Specials' : ` — Season ${item.season}`
        }
        if (item.type === 'collection') line += ' [Collection]'
        return line
      }),
    ].join('\n')
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `missing-${preferredStyle.toLowerCase()}-posters.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const getCardDropIndex = (e: DragEvent<HTMLElement>, index: number) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const midpointY = rect.top + rect.height / 2
    return e.clientY > midpointY ? index + 1 : index
  }

  return (
    <>
      <Toolbar title="Configure Drive Priority" description="Select which poster styles to use and set their priority order. Higher priority drives will override lower ones.">
        <button className={`btn-toolbar ${hasUnsavedPriorityChanges ? 'btn-unsaved' : ''}`} onClick={onSavePriority} disabled={!hasUnsavedPriorityChanges || saving}>
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
      </Toolbar>

      <div className="priority-tab">
        {styleStats && styleTotal > 0 && (
          <div className="style-usage-panel">
            <div className="style-usage-header">
              <span className="style-usage-title">Last Rename — Style Usage</span>
              <span className="style-usage-total">{styleTotal.toLocaleString()} items matched</span>
            </div>
            <div className="style-usage-bars">
              {styleEntries.map(([style, count]) => {
                const pct = styleTotal > 0 ? (count / styleTotal) * 100 : 0
                const styleKey = style.toLowerCase().replace(/[^a-z0-9]/g, '')
                return (
                  <div key={style} className={`style-usage-row style-usage-${styleKey}`}>
                    <span className={`style-badge style-${styleKey}`}>{style}</span>
                    <div className="style-usage-bar-track">
                      <div className="style-usage-bar-fill" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="style-usage-count">{count.toLocaleString()}</span>
                  </div>
                )
              })}
            </div>
            {fallbackEntries.length > 0 && preferredStyle && (
              <div className="style-usage-fallback-note">
                {fallbackEntries.map(([style, count]) => {
                  const items: FallbackItem[] = styleFallbacks[style] ?? []
                  const styleKey = style.toLowerCase().replace(/[^a-z0-9]/g, '')
                  const preferredKey = preferredStyle.toLowerCase().replace(/[^a-z0-9]/g, '')
                  return (
                    <div key={style} className="style-usage-fallback-card">
                      <div className="style-usage-fallback-card-header">
                        <span className="style-usage-fallback-label">
                          {count.toLocaleString()} item{count !== 1 ? 's' : ''} used{' '}
                          <span className={`style-badge style-${styleKey}`}>{style}</span>{' '}
                          instead of{' '}
                          <span className={`style-badge style-${preferredKey}`}>{preferredStyle}</span>
                        </span>
                        <button className="style-usage-download-btn" onClick={() => handleDownload(style, items)} title="Download list">
                          <Download size={13} />
                          Download
                        </button>
                      </div>
                      <div className="style-usage-preview-list">
                        {items.slice(0, PREVIEW_LIMIT).map((item, i) => {
                          const badgeClass = item.type === 'movie' ? 'movie' : item.type === 'collection' ? 'collection' : 'series'
                          const badgeLabel = item.type === 'movie' ? 'Movie' : item.type === 'collection' ? 'Collection' : 'Show'
                          const isSeasonItem = item.type === 'show' && item.season != null
                          return (
                            <div key={i} className="style-usage-preview-item">
                              <span>{item.title}</span>
                              {item.year && <span className="item-year">({item.year})</span>}
                              {isSeasonItem ? (
                                <span className="unmatched-cat-badge unmatched-cat-badge--season">
                                  {item.season === 0 ? 'Specials' : `Season ${item.season}`}
                                </span>
                              ) : (
                                <span className={`unmatched-cat-badge unmatched-cat-badge--${badgeClass}`}>{badgeLabel}</span>
                              )}
                            </div>
                          )
                        })}
                        {items.length > PREVIEW_LIMIT && (
                          <div className="style-usage-preview-more">+ {items.length - PREVIEW_LIMIT} more...</div>
                        )}
                      </div>
                      <button className="card-view-all-btn" onClick={() => setOpenFallbackStyle(style)}>
                        <List size={14} />
                        View All {count.toLocaleString()} Items
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
        {openFallbackStyle && preferredStyle && (
          <PosterStyleModal
            preferredStyle={preferredStyle}
            fallbackStyle={openFallbackStyle}
            items={openFallbackItems}
            tmdbApiKeyConfigured={tmdbApiKeyConfigured}
            onClose={() => setOpenFallbackStyle(null)}
            onDownload={() => handleDownload(openFallbackStyle, openFallbackItems)}
          />
        )}
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
                        <div key={drive.id} className="drive-card-small mm2k" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd} onTouchStart={(e) => onDriveTouchStart(e, drive)}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
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
                        <div key={drive.id} className="drive-card-small cl2k" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd} onTouchStart={(e) => onDriveTouchStart(e, drive)}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
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
                        <div key={drive.id} className="drive-card-small custom" draggable onDragStart={() => onDragStart(drive)} onDragEnd={onDragEnd} onTouchStart={(e) => onDriveTouchStart(e, drive)}>
                          <GripVertical size={14} className="drag-handle" />
                          <div className="drive-info">
                            <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
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
                            data-priority-index={index}
                            className={`priority-drive-card ${drive.is_custom ? 'custom' : drive.style_type.toLowerCase()} ${showInsertBeforeCue ? 'drop-target-before' : ''} ${showInsertAfterCue ? 'drop-target-after' : ''}`}
                            draggable
                            onDragStart={() => onDragStart(drive)}
                            onDragEnd={onDragEnd}
                            onDragOver={(e) => onDragOverCard(e, index)}
                            onDrop={(e) => onDropInPriority(e, getCardDropIndex(e, index))}
                            onDragLeave={onDragLeave}
                            onTouchStart={(e) => onDriveTouchStart(e, drive)}
                          >
                            <GripVertical size={16} className="drag-handle" />
                            <div className="priority-number">{index + 1}</div>
                            <div className="drive-info">
                              <div className="drive-name" title={drive.display_name || drive.name}>{drive.display_name || drive.name}</div>
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
