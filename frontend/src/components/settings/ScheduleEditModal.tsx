import { useEffect, useMemo, useState } from 'react'
import { Drive, Schedule, getMakerIdarrConfig, MakerIdarrSyncTarget } from '../../api/client'

type EditingScheduleState = {
  schedule: Schedule
  index: number
}

type ScheduleEditModalProps = {
  editingSchedule: EditingScheduleState | null
  drives: Drive[]
  scheduleSaving: boolean
  updateScheduleField: <K extends keyof Schedule>(field: K, value: Schedule[K]) => void
  onClose: () => void
  onSave: () => void
}

function ScheduleEditModal({
  editingSchedule,
  drives,
  scheduleSaving,
  updateScheduleField,
  onClose,
  onSave,
}: ScheduleEditModalProps) {
  const [idarrTargets, setIdarrTargets] = useState<Array<{ index: number; target: MakerIdarrSyncTarget }>>([])

  const isSyncSchedule = editingSchedule?.schedule.job_type === 'gdrive_sync' || editingSchedule?.schedule.job_type === 'sync'
  const isIdarrSchedule = editingSchedule?.schedule.job_type === 'idarr'

  useEffect(() => {
    let mounted = true

    const loadIdarrTargets = async () => {
      try {
        const config = await getMakerIdarrConfig()
        const targets = Array.isArray(config.sync_targets) ? config.sync_targets : []
        const validTargets = targets
          .map((target, index) => ({ index, target }))
          .filter((entry) => String(entry.target.source_dir || '').trim() !== '')
        if (mounted) {
          setIdarrTargets(validTargets)
        }
      } catch {
        if (mounted) {
          setIdarrTargets([])
        }
      }
    }

    loadIdarrTargets()

    return () => {
      mounted = false
    }
  }, [editingSchedule?.schedule.id])

  const selectedIdarrScope = useMemo(() => {
    const group = String(editingSchedule?.schedule.drive_group || '').trim()
    if (!group.startsWith('idarr_target_')) {
      return ''
    }
    return group
  }, [editingSchedule?.schedule.drive_group])

  const idarrSyncAfterRun = useMemo(() => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    if (!raw) return false
    try {
      const parsed = JSON.parse(raw)
      return Boolean(parsed?.sync_after_run)
    } catch {
      return false
    }
  }, [editingSchedule?.schedule.job_config])

  const updateIdarrSyncAfterRun = (value: boolean) => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    let existing: Record<string, unknown> = {}
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
          existing = parsed as Record<string, unknown>
        }
      } catch {
        // ignore malformed
      }
    }
    existing.sync_after_run = value
    updateScheduleField('job_config', JSON.stringify(existing))
  }

  if (!editingSchedule) {
    return null
  }

  return (
    <div className="modal-overlay">
      <div className="modal-content schedule-modal schedule-edit-modal">
        <div className="modal-header">
          <h2>Edit Schedule</h2>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          <div className="form-group">
            <label>Schedule Name</label>
            <input
              type="text"
              value={editingSchedule.schedule.name}
              onChange={(e) => updateScheduleField('name', e.target.value)}
              placeholder="e.g., Morning Sync, Daily Backup"
            />
          </div>

          <div className="form-group">
            <label>Scheduled Task</label>
            <select
              value={editingSchedule.schedule.job_type}
              onChange={(e) => {
                const value = e.target.value
                updateScheduleField('job_type', value)
                if (value === 'idarr') {
                  const fallbackScope = idarrTargets.length > 0 ? `idarr_target_${idarrTargets[0].index}` : null
                  updateScheduleField('drive_id', null)
                  updateScheduleField('drive_group', fallbackScope)
                }
              }}
            >
              <option value="poster_workflow">Poster Workflow</option>
              <option value="gdrive_sync">Drive Sync</option>
              <option value="poster_renamer">Poster Renamer</option>
              <option value="unmatched_assets">Unmatched Assets</option>
              <option value="border_replacer">Border Replacer</option>
              <option value="idarr">IDarr</option>
              <option value="maker_monitor">Maker Monitor</option>
            </select>
          </div>

          {isIdarrSchedule && (
            <div className="form-group">
              <label>IDarr Scope</label>
              <select
                value={selectedIdarrScope}
                onChange={(e) => {
                  const value = e.target.value || null
                  updateScheduleField('drive_id', null)
                  updateScheduleField('drive_group', value)
                }}
              >
                {idarrTargets.length === 0 ? (
                  <option value="">No IDarr sync targets configured</option>
                ) : (
                  idarrTargets.map(({ index, target }) => {
                    const label = String(target.label || '').trim() || `Target ${index + 1}`
                    const sourceDir = String(target.source_dir || '').trim()
                    return (
                      <option key={index} value={`idarr_target_${index}`}>
                        {`${label} — ${sourceDir}`}
                      </option>
                    )
                  })
                )}
              </select>
              <p className="field-hint">Choose which IDarr sync target this schedule should process.</p>
            </div>
          )}

          {isIdarrSchedule && (
            <div className="form-group">
              <label>Sync Personal Drive After Run</label>
              <div className="schedule-toggle">
                <label className="toggle-switch">
                  <input
                    type="checkbox"
                    checked={idarrSyncAfterRun}
                    onChange={(e) => updateIdarrSyncAfterRun(e.target.checked)}
                  />
                  <span className="toggle-slider"></span>
                </label>
                <span className="toggle-label">
                  {idarrSyncAfterRun ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <p className="field-hint">When enabled, IDarr will automatically sync the processed folder to your personal drive after each run.</p>
            </div>
          )}

          {isSyncSchedule && (
            <div className="form-group">
              <label>Drive Selection</label>
              <select
                value={
                  editingSchedule.schedule.drive_id
                    ? `drive_${editingSchedule.schedule.drive_id}`
                    : editingSchedule.schedule.drive_group
                      ? `group_${editingSchedule.schedule.drive_group}`
                      : 'all'
                }
                onChange={(e) => {
                  const value = e.target.value
                  if (value === 'all') {
                    updateScheduleField('drive_id', null)
                    updateScheduleField('drive_group', null)
                  } else if (value.startsWith('group_')) {
                    const group = value.substring(6)
                    updateScheduleField('drive_id', null)
                    updateScheduleField('drive_group', group)
                  } else if (value.startsWith('drive_')) {
                    const driveId = parseInt(value.substring(6), 10)
                    updateScheduleField('drive_id', driveId)
                    updateScheduleField('drive_group', null)
                  }
                }}
              >
                <option value="all">All Subscribed Drives</option>
                <option value="group_CL2K">All CL2K Drives</option>
                <option value="group_MM2K">All MM2K Drives</option>
                <option value="group_Custom">All Custom Drives</option>
                <optgroup label="Individual Drives">
                  {drives.filter((d) => d.subscribed).map((drive) => (
                    <option key={drive.id} value={`drive_${drive.id}`}>
                      {drive.display_name || drive.name}
                    </option>
                  ))}
                </optgroup>
              </select>
            </div>
          )}

          <div className="form-group">
            <label>Status</label>
            <div className="schedule-toggle">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={editingSchedule.schedule.enabled}
                  onChange={(e) => updateScheduleField('enabled', e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">
                {editingSchedule.schedule.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
          </div>

          <div className="form-group">
            <label>Schedule Type</label>
            <div className="schedule-type-buttons">
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'hourly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'hourly')}>
                Hourly
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'daily' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'daily')}>
                Daily
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'multiple_daily' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'multiple_daily')}>
                Multiple Daily
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'weekly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'weekly')}>
                Weekly
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'multiple_days' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'multiple_days')}>
                Multiple Days
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'monthly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'monthly')}>
                Monthly
              </button>
              <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'cron' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'cron')}>
                Cron
              </button>
            </div>
          </div>

          {editingSchedule.schedule.schedule_type === 'hourly' && (
            <div className="form-group">
              <label>Minute Offset</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value || ''}
                onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="0"
              />
              <p className="field-hint">Minute (0-59) when the job runs each hour (e.g., 0 = on the hour, 15 = quarter past, 30 = half past)</p>
            </div>
          )}

          {editingSchedule.schedule.schedule_type === 'daily' && (
            <div className="form-group">
              <label>Time (24-hour format)</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value || ''}
                onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="09:00"
              />
              <p className="field-hint">Format: HH:MM (e.g., 09:00 for 9 AM, 14:30 for 2:30 PM)</p>
            </div>
          )}

          {editingSchedule.schedule.schedule_type === 'multiple_daily' && (
            <div className="form-group">
              <label>Times (24-hour format)</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value || ''}
                onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="07:00,19:00"
              />
              <p className="field-hint">Comma-separated times (e.g., 07:00,12:00,19:00)</p>
            </div>
          )}

          {editingSchedule.schedule.schedule_type === 'weekly' && (
            <>
              <div className="form-group">
                <label>Select Day</label>
                <div className="day-toggle-buttons">
                  {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day, dayIndex) => {
                    const currentDay = editingSchedule.schedule.schedule_value?.split(':')[0]
                    const isSelected = currentDay === String(dayIndex)
                    return (
                      <button
                        key={dayIndex}
                        type="button"
                        className={`day-toggle-btn ${isSelected ? 'active' : ''}`}
                        onClick={() => {
                          const time = editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''
                          updateScheduleField('schedule_value', `${dayIndex}:${time}`)
                        }}
                      >
                        {day}
                      </button>
                    )
                  })}
                </div>
              </div>
              <div className="form-group">
                <label>Time (24-hour format)</label>
                <input
                  type="text"
                  value={editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''}
                  onChange={(e) => {
                    const day = editingSchedule.schedule.schedule_value?.split(':')[0] || '0'
                    updateScheduleField('schedule_value', `${day}:${e.target.value}`)
                  }}
                  onKeyDown={(e) => e.key === 'Enter' && onSave()}
                  placeholder="09:00"
                />
                <p className="field-hint">Format: HH:MM or comma-separated for multiple times (e.g., 09:00 or 09:00,14:30,19:00)</p>
              </div>
            </>
          )}

          {editingSchedule.schedule.schedule_type === 'monthly' && (
            <>
              <div className="form-group">
                <label>Day of Month</label>
                <input
                  type="text"
                  value={editingSchedule.schedule.schedule_value?.split(':')[0] || ''}
                  onChange={(e) => {
                    const time = editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''
                    updateScheduleField('schedule_value', `${e.target.value}:${time}`)
                  }}
                  placeholder="1"
                />
                <p className="field-hint">Day 1-31 (will use last day if month has fewer days)</p>
              </div>
              <div className="form-group">
                <label>Time (24-hour format)</label>
                <input
                  type="text"
                  value={editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''}
                  onChange={(e) => {
                    const day = editingSchedule.schedule.schedule_value?.split(':')[0] || '1'
                    updateScheduleField('schedule_value', `${day}:${e.target.value}`)
                  }}
                  onKeyDown={(e) => e.key === 'Enter' && onSave()}
                  placeholder="09:00"
                />
                <p className="field-hint">Format: HH:MM or comma-separated for multiple times (e.g., 09:00 or 09:00,14:30,19:00)</p>
              </div>
            </>
          )}

          {editingSchedule.schedule.schedule_type === 'multiple_days' && (
            <>
              <div className="form-group">
                <label>Select Days</label>
                <div className="day-toggle-buttons">
                  {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day, dayIndex) => {
                    const schedules = editingSchedule.schedule.schedule_value?.split('|') || []
                    const isSelected = schedules.some((s) => s.startsWith(`${dayIndex}:`))
                    return (
                      <button
                        key={dayIndex}
                        type="button"
                        className={`day-toggle-btn ${isSelected ? 'active' : ''}`}
                        onClick={() => {
                          const currentSchedules = editingSchedule.schedule.schedule_value?.split('|').filter((s) => s) || []
                          if (isSelected) {
                            const updated = currentSchedules.filter((s) => !s.startsWith(`${dayIndex}:`))
                            updateScheduleField('schedule_value', updated.join('|'))
                          } else {
                            const updated = [...currentSchedules, `${dayIndex}:`]
                            updateScheduleField('schedule_value', updated.join('|'))
                          }
                        }}
                      >
                        {day}
                      </button>
                    )
                  })}
                </div>
              </div>

              {editingSchedule.schedule.schedule_value && editingSchedule.schedule.schedule_value.split('|').filter((s) => s).length > 0 && (
                <div className="form-group">
                  <label>Times for Each Day (24-hour format)</label>
                  <p className="field-hint">Enter comma-separated times for each day (e.g., 07:00,19:00)</p>
                  <div className="day-time-list">
                    {['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'].map((dayName, dayIndex) => {
                      const schedules = editingSchedule.schedule.schedule_value?.split('|') || []
                      const daySchedule = schedules.find((s) => s.startsWith(`${dayIndex}:`))

                      if (!daySchedule) return null

                      const times = daySchedule.substring(2)

                      return (
                        <div key={dayIndex} className="day-time-item">
                          <span className="day-name">{dayName}</span>
                          <input
                            type="text"
                            value={times}
                            onChange={(e) => {
                              const currentSchedules = editingSchedule.schedule.schedule_value?.split('|').filter((s) => s) || []
                              const updated = currentSchedules.map((s) => (s.startsWith(`${dayIndex}:`) ? `${dayIndex}:${e.target.value}` : s))
                              updateScheduleField('schedule_value', updated.join('|'))
                            }}
                            placeholder="09:00,17:00"
                            className="day-time-input"
                          />
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </>
          )}

          {editingSchedule.schedule.schedule_type === 'cron' && (
            <div className="form-group">
              <label>Cron Expression</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value || ''}
                onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="0 2 * * *"
              />
              <p className="field-hint">
                Format: minute hour day month weekday<br />
                Examples:<br />
                • "0 2 * * *" = 2:00 AM daily<br />
                • "0 9,17 * * *" = 9:00 AM and 5:00 PM daily<br />
                • "30 */3 * * *" = Every 3 hours at :30 minutes<br />
                • "0 8 * * 1-5" = 8:00 AM weekdays only
              </p>
            </div>
          )}

          {editingSchedule.schedule.last_run && (
            <div className="schedule-status">
              <div className="status-item">
                <span className="status-label">Last Run:</span>
                <span className="status-value">{new Date(editingSchedule.schedule.last_run).toLocaleString()}</span>
              </div>
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" onClick={onSave} disabled={scheduleSaving}>
            {scheduleSaving ? 'Saving...' : 'Save Schedule'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default ScheduleEditModal
