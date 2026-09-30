import { Edit2, Plus, Trash2 } from 'lucide-react'
import { ReactNode, useState } from 'react'
import { Schedule } from '../../api/client'
import { browserTimeZone, timeZoneOptions } from '../../utils/datetime'

type SettingsSchedulingSectionProps = {
  schedules: Schedule[]
  onAddSchedule: () => void
  onToggleScheduleEnabled: (index: number) => void
  onEditSchedule: (index: number) => void
  onRemoveSchedule: (index: number) => void
  getScheduleSummary: (schedule: Schedule) => ReactNode
  appTimezone: string
  onChangeAppTimezone: (value: string) => void
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
  onChangeAppTimezone,
  effectiveTimezone,
  onSaveAppTimezone,
  saving,
}: SettingsSchedulingSectionProps) {
  const [confirmDeleteIndex, setConfirmDeleteIndex] = useState<number | null>(null)

  const browserZone = browserTimeZone()
  // Prefill, not a placeholder. A greyed-out hint leaves the field looking empty, and saving
  // that persists "" — which silently hands every schedule back to the server's host zone.
  const zoneValue = appTimezone || browserZone
  const zoneOptions = timeZoneOptions()
  // A stored or typed zone can be missing from Intl's list (aliases, newer zones); without
  // this the select would silently show nothing selected.
  const zoneChoices =
    zoneOptions.length > 0 && !zoneOptions.includes(zoneValue) ? [zoneValue, ...zoneOptions] : zoneOptions
  const isPrefilled = !appTimezone
  const effectiveDiffers = !!effectiveTimezone && effectiveTimezone !== zoneValue

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
        <div className="setting-item">
          <div className="setting-info">
            <label htmlFor="app-timezone-field">Application Timezone</label>
            <p className="setting-description">
              Schedule times and calendar days are interpreted in this zone. Every timestamp is
              stored in UTC and shown in your own browser zone.
            </p>
          </div>
          <div className="settings-input-row">
            {zoneChoices.length > 0 ? (
              <select
                id="app-timezone-field"
                value={zoneValue}
                onChange={(e) => onChangeAppTimezone(e.target.value)}
              >
                {zoneChoices.map((zone) => (
                  <option key={zone} value={zone}>{zone}</option>
                ))}
              </select>
            ) : (
              // No Intl.supportedValuesOf: free text is the only honest control left.
              <input
                id="app-timezone-field"
                type="text"
                value={zoneValue}
                onChange={(e) => onChangeAppTimezone(e.target.value)}
                placeholder="e.g. Europe/Amsterdam"
              />
            )}
            <button
              type="button"
              className="btn-primary btn-inline-save"
              onClick={() => onSaveAppTimezone(zoneValue)}
              disabled={saving}
            >
              {saving ? 'Saving...' : 'Save Timezone'}
            </button>
          </div>
        </div>
        {(isPrefilled || effectiveDiffers) && (
          <p className="schedule-summary">
            <span className="summary-disabled">
              {isPrefilled && `Prefilled from your browser zone (${browserZone}). `}
              {effectiveDiffers && `Not saved yet — the server is running in ${effectiveTimezone}, so that is what schedules use until you save.`}
            </span>
          </p>
        )}
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
