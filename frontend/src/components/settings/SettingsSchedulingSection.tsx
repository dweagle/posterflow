import { Edit2, Plus, Trash2 } from 'lucide-react'
import { ReactNode, useEffect, useMemo, useState } from 'react'
import { Schedule, getTimezones } from '../../api/client'
import { browserTimeZone, sameTimeZone } from '../../utils/datetime'

type SettingsSchedulingSectionProps = {
  schedules: Schedule[]
  onAddSchedule: () => void
  onToggleScheduleEnabled: (index: number) => void
  onEditSchedule: (index: number) => void
  onRemoveSchedule: (index: number) => void
  getScheduleSummary: (schedule: Schedule) => ReactNode
  appTimezone: string
  effectiveTimezone: string
  onSaveAppTimezone: (value: string) => void
  saving: boolean
}

function SettingsSchedulingSection({
  schedules,
  onAddSchedule,
  onToggleScheduleEnabled,
  onEditSchedule,
  onRemoveSchedule,
  getScheduleSummary,
  appTimezone,
  effectiveTimezone,
  onSaveAppTimezone,
  saving,
}: SettingsSchedulingSectionProps) {
  const [confirmDeleteIndex, setConfirmDeleteIndex] = useState<number | null>(null)
  // null until the user picks, so the select follows the saved value ('' = host timezone)
  const [zoneChoice, setZoneChoice] = useState<string | null>(null)
  const zone = zoneChoice ?? appTimezone
  // the server lists the zones it can run; browser lists carry legacy names it may reject
  const [serverZones, setServerZones] = useState<string[]>([])
  useEffect(() => {
    getTimezones().then(setServerZones).catch(() => setServerZones([]))
  }, [])
  const zones = useMemo(
    () => (appTimezone && !serverZones.includes(appTimezone) ? [appTimezone, ...serverZones] : serverZones),
    [appTimezone, serverZones],
  )
  const browserZone = browserTimeZone()

  const handleDeleteClick = (index: number) => {
    setConfirmDeleteIndex(index)
  }

  const handleConfirmDelete = () => {
    if (confirmDeleteIndex !== null) {
      onRemoveSchedule(confirmDeleteIndex)
      setConfirmDeleteIndex(null)
    }
  }

  const handleCancelDelete = () => {
    setConfirmDeleteIndex(null)
  }

  const pendingScheduleName =
    confirmDeleteIndex !== null ? (schedules[confirmDeleteIndex]?.name || 'this schedule') : ''

  return (
    <div className="settings-section">
      {confirmDeleteIndex !== null && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal confirmation-modal">
            <div className="modal-header">
              <h2>Delete Schedule</h2>
              <button className="modal-close" onClick={handleCancelDelete}>×</button>
            </div>
            <div className="modal-body">
              <p>Are you sure you want to delete <strong>{pendingScheduleName}</strong>? This action cannot be undone.</p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={handleCancelDelete}>Cancel</button>
              <button className="btn-danger" onClick={handleConfirmDelete}>Delete</button>
            </div>
          </div>
        </div>
      )}
      <div className="server-section">
        <div className="server-section-header">
          <h3>Timezone</h3>
        </div>
        <div className="setting-item api-key-setting">
          <div className="setting-info">
            <label htmlFor="app-timezone">Schedule Timezone</label>
            <p className="setting-description">
              Schedule times and the dashboard's daily counts use this timezone.
              {effectiveTimezone && ` Schedules currently run in ${effectiveTimezone}.`}
              {effectiveTimezone && !sameTimeZone(browserZone, effectiveTimezone) && ` Your browser is in ${browserZone}.`}
            </p>
          </div>
          <div className="settings-input-row api-key-control">
            <select id="app-timezone" value={zone} onChange={(e) => setZoneChoice(e.target.value)}>
              <option value="">Host timezone (TZ)</option>
              {zones.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            <button
              type="button"
              className="btn-primary btn-inline-save"
              onClick={() => onSaveAppTimezone(zone)}
              disabled={saving || zone === appTimezone}
            >
              Save
            </button>
          </div>
        </div>
      </div>
      <div className="server-section">
        <div className="server-section-header">
          <h3>Scheduled Tasks</h3>
          <button className="btn-add-instance" onClick={onAddSchedule}>
            <Plus size={16} />
            Add Schedule
          </button>
        </div>

        <div className="server-cards-grid">
          {schedules.map((schedule, index) => {
            return (
              <div key={schedule.id || `new-${index}`} className="server-card locked">
                <div className="card-header">
                  <span className="card-title">{schedule.name || 'New Schedule'}</span>
                  <div className="card-actions">
                    <label className="toggle-switch" title={schedule.enabled ? 'Enabled' : 'Disabled'}>
                      <input
                        type="checkbox"
                        checked={schedule.enabled}
                        onChange={() => onToggleScheduleEnabled(index)}
                      />
                      <span className="toggle-slider"></span>
                    </label>
                    <button
                      type="button"
                      className="btn-edit-instance"
                      onClick={() => onEditSchedule(index)}
                      title="Edit schedule"
                    >
                      <Edit2 size={16} />
                    </button>
                    <button
                      type="button"
                      className="btn-remove-instance"
                      onClick={() => handleDeleteClick(index)}
                      title="Remove schedule"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
                <div className="card-body">
                  {schedule.schedule_value || schedule.schedule_type === 'hourly' ? (
                    <div className="schedule-summary">{getScheduleSummary(schedule)}</div>
                  ) : (
                    <div className="schedule-summary"><span className="summary-disabled">Not configured</span></div>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {schedules.length === 0 && (
          <div className="loading-message">
            No schedules configured. Click "Add Schedule" to create one.
          </div>
        )}
      </div>
    </div>
  )
}

export default SettingsSchedulingSection
