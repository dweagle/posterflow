import { Edit2, Plus, Trash2 } from 'lucide-react'
import { ReactNode } from 'react'
import { Schedule } from '../../api/client'

type SettingsSchedulingSectionProps = {
  schedules: Schedule[]
  onAddSchedule: () => void
  onToggleScheduleEnabled: (index: number) => void
  onEditSchedule: (index: number) => void
  onRemoveSchedule: (index: number) => void
  getScheduleSummary: (schedule: Schedule) => ReactNode
}

function SettingsSchedulingSection({
  schedules,
  onAddSchedule,
  onToggleScheduleEnabled,
  onEditSchedule,
  onRemoveSchedule,
  getScheduleSummary,
}: SettingsSchedulingSectionProps) {
  return (
    <div className="settings-section">
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
                      onClick={() => onRemoveSchedule(index)}
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
