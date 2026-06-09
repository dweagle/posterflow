import { useMemo, useState } from 'react'
import { Eye, Play, Save, Trash2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import Toolbar from './Toolbar'

type HolidayPreset = {
  name: string
  schedule: string
  colors: string[]
}

type HolidaySchedule = {
  name: string
  schedule: string
  colors: string[]
}

const MONTHS = [
  { value: '01', label: 'Jan', days: 31 },
  { value: '02', label: 'Feb', days: 29 },
  { value: '03', label: 'Mar', days: 31 },
  { value: '04', label: 'Apr', days: 30 },
  { value: '05', label: 'May', days: 31 },
  { value: '06', label: 'Jun', days: 30 },
  { value: '07', label: 'Jul', days: 31 },
  { value: '08', label: 'Aug', days: 31 },
  { value: '09', label: 'Sep', days: 30 },
  { value: '10', label: 'Oct', days: 31 },
  { value: '11', label: 'Nov', days: 30 },
  { value: '12', label: 'Dec', days: 31 },
]

const HOLIDAY_PRESETS: HolidayPreset[] = [
  { name: "🎆 New Year's Day", schedule: 'range(12/30-01/02)', colors: ['#00BFFF', '#FFD700'] },
  { name: "💘 Valentine's Day", schedule: 'range(02/05-02/15)', colors: ['#D41F3A', '#FFC0CB'] },
  { name: '🐣 Easter', schedule: 'range(03/31-04/02)', colors: ['#FFB6C1', '#87CEFA', '#98FB98'] },
  { name: "🌸 Mother's Day", schedule: 'range(05/10-05/15)', colors: ['#FF69B4', '#FFDAB9'] },
  { name: "👨‍👧‍👦 Father's Day", schedule: 'range(06/15-06/20)', colors: ['#1E90FF', '#4682B4'] },
  { name: '🗽 Independence Day', schedule: 'range(07/01-07/05)', colors: ['#FF0000', '#FFFFFF', '#0000FF'] },
  { name: '🧹 Labor Day', schedule: 'range(09/01-09/07)', colors: ['#FFD700', '#4682B4'] },
  { name: '🎃 Halloween', schedule: 'range(10/01-10/31)', colors: ['#FFA500', '#000000'] },
  { name: '🦃 Thanksgiving', schedule: 'range(11/01-11/30)', colors: ['#FFA500', '#8B4513'] },
  { name: '🎄 Christmas', schedule: 'range(12/01-12/31)', colors: ['#FF0000', '#00FF00'] },
]

type BorderTabProps = {
  hasUnsavedBorderChanges: boolean
  saving: boolean
  runningBorderReplacer: boolean
  borderWidth: number
  borderMode: 'incremental' | 'full'
  newColor: string
  borderColors: string[]
  holidaySchedules: HolidaySchedule[]
  skipRunOutsideHoliday: boolean
  removeBorders: boolean
  seasonMode: 'inherit' | 'remove' | 'colors'
  seasonColors: string[]
  seasonWidth: number
  newSeasonColor: string
  onSaveSettings: () => void
  onRunBorderReplacer: (dryRun: boolean) => void
  onSetBorderWidth: (width: number) => void
  onSetBorderMode: (mode: 'incremental' | 'full') => void
  onSetNewColor: (color: string) => void
  onAddBorderColor: () => void
  onRemoveBorderColor: (color: string) => void
  onSetRemoveBorders: (value: boolean) => void
  onSetSkipRunOutsideHoliday: (value: boolean) => void
  onAddHolidaySchedule: (holiday: HolidaySchedule) => void
  onRemoveHolidaySchedule: (name: string) => void
  onSetSeasonMode: (mode: 'inherit' | 'remove' | 'colors') => void
  onSetSeasonWidth: (width: number) => void
  onSetNewSeasonColor: (color: string) => void
  onAddSeasonBorderColor: () => void
  onRemoveSeasonBorderColor: (color: string) => void
}

function BorderTab({
  hasUnsavedBorderChanges,
  saving,
  runningBorderReplacer,
  borderWidth,
  borderMode,
  newColor,
  borderColors,
  holidaySchedules,
  skipRunOutsideHoliday,
  removeBorders,
  seasonMode,
  seasonColors,
  seasonWidth,
  newSeasonColor,
  onSaveSettings,
  onRunBorderReplacer,
  onSetBorderWidth,
  onSetBorderMode,
  onSetNewColor,
  onAddBorderColor,
  onRemoveBorderColor,
  onSetRemoveBorders,
  onSetSkipRunOutsideHoliday,
  onAddHolidaySchedule,
  onRemoveHolidaySchedule,
  onSetSeasonMode,
  onSetSeasonWidth,
  onSetNewSeasonColor,
  onAddSeasonBorderColor,
  onRemoveSeasonBorderColor,
}: BorderTabProps) {
  const navigate = useNavigate()

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const [selectedPresetName, setSelectedPresetName] = useState('')
  const [editingHolidayName, setEditingHolidayName] = useState<string | null>(null)
  const [editFromMonth, setEditFromMonth] = useState('01')
  const [editFromDay, setEditFromDay] = useState('01')
  const [editToMonth, setEditToMonth] = useState('01')
  const [editToDay, setEditToDay] = useState('01')
  const [editHolidayColors, setEditHolidayColors] = useState<string[]>([])
  const [editNewColor, setEditNewColor] = useState('#000000')
  const [customHolidayName, setCustomHolidayName] = useState('')
  const [customFromMonth, setCustomFromMonth] = useState('01')
  const [customFromDay, setCustomFromDay] = useState('01')
  const [customToMonth, setCustomToMonth] = useState('01')
  const [customToDay, setCustomToDay] = useState('01')
  const [customHolidayColors, setCustomHolidayColors] = useState<string[]>([])
  const [customNewColor, setCustomNewColor] = useState('#000000')

  const editFromDayOptions = useMemo(() => {
    const month = MONTHS.find((item) => item.value === editFromMonth)
    const count = month?.days ?? 31
    return Array.from({ length: count }, (_, index) => String(index + 1).padStart(2, '0'))
  }, [editFromMonth])

  const editToDayOptions = useMemo(() => {
    const month = MONTHS.find((item) => item.value === editToMonth)
    const count = month?.days ?? 31
    return Array.from({ length: count }, (_, index) => String(index + 1).padStart(2, '0'))
  }, [editToMonth])

  const customFromDayOptions = useMemo(() => {
    const month = MONTHS.find((item) => item.value === customFromMonth)
    const count = month?.days ?? 31
    return Array.from({ length: count }, (_, index) => String(index + 1).padStart(2, '0'))
  }, [customFromMonth])

  const customToDayOptions = useMemo(() => {
    const month = MONTHS.find((item) => item.value === customToMonth)
    const count = month?.days ?? 31
    return Array.from({ length: count }, (_, index) => String(index + 1).padStart(2, '0'))
  }, [customToMonth])

  const buildScheduleRange = (fromMonth: string, fromDay: string, toMonth: string, toDay: string) => {
    return `range(${fromMonth}/${fromDay}-${toMonth}/${toDay})`
  }

  const parseScheduleRange = (schedule: string) => {
    const fallback = { fromMonth: '01', fromDay: '01', toMonth: '01', toDay: '01' }
    const match = schedule.match(/^range\((\d{2})\/(\d{2})-(\d{2})\/(\d{2})\)$/)
    if (!match) return fallback
    return {
      fromMonth: match[1],
      fromDay: match[2],
      toMonth: match[3],
      toDay: match[4],
    }
  }

  const handleAddHolidayPreset = () => {
    const preset = HOLIDAY_PRESETS.find((item) => item.name === selectedPresetName)
    if (!preset) return
    onAddHolidaySchedule(preset)
  }

  const handleStartEditHoliday = (holiday: HolidaySchedule) => {
    const parsed = parseScheduleRange(holiday.schedule)
    setEditFromMonth(parsed.fromMonth)
    setEditFromDay(parsed.fromDay)
    setEditToMonth(parsed.toMonth)
    setEditToDay(parsed.toDay)
    setEditHolidayColors(holiday.colors.length > 0 ? [...holiday.colors] : [...borderColors])
    setEditNewColor('#000000')
    setEditingHolidayName(holiday.name)
  }

  const handleSaveEditHoliday = (holiday: HolidaySchedule) => {
    const colorsToSave = editHolidayColors.length > 0 ? editHolidayColors : holiday.colors
    if (colorsToSave.length === 0) {
      return
    }

    onAddHolidaySchedule({
      ...holiday,
      schedule: buildScheduleRange(editFromMonth, editFromDay, editToMonth, editToDay),
      colors: colorsToSave,
    })
    setEditingHolidayName(null)
  }

  const handleAddEditHolidayColor = () => {
    if (!editNewColor || editHolidayColors.includes(editNewColor)) {
      return
    }
    setEditHolidayColors((prev) => [...prev, editNewColor])
    setEditNewColor('#000000')
  }

  const handleRemoveEditHolidayColor = (color: string) => {
    setEditHolidayColors((prev) => prev.filter((item) => item !== color))
  }

  const handleAddCustomHoliday = () => {
    const name = customHolidayName.trim()
    if (!name) return

    const effectiveColors = customHolidayColors.length > 0 ? customHolidayColors : borderColors
    if (effectiveColors.length === 0) return

    onAddHolidaySchedule({
      name,
      schedule: buildScheduleRange(customFromMonth, customFromDay, customToMonth, customToDay),
      colors: effectiveColors,
    })

    setCustomHolidayName('')
    setCustomHolidayColors([])
    setCustomNewColor('#000000')
    setCustomFromMonth('01')
    setCustomFromDay('01')
    setCustomToMonth('01')
    setCustomToDay('01')
  }

  const handleAddCustomHolidayColor = () => {
    if (!customNewColor || customHolidayColors.includes(customNewColor)) {
      return
    }
    setCustomHolidayColors((prev) => [...prev, customNewColor])
    setCustomNewColor('#000000')
  }

  const handleRemoveCustomHolidayColor = (color: string) => {
    setCustomHolidayColors((prev) => prev.filter((item) => item !== color))
  }

  return (
    <>
      <Toolbar title="Border Replacer" description="Apply or remove borders from poster images">
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={openSchedulingSettings}>
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={openNotificationSettings}>
            Discord
          </button>
        </div>
        <button
          className={`btn-toolbar ${hasUnsavedBorderChanges ? 'btn-unsaved' : ''}`}
          onClick={onSaveSettings}
          disabled={!hasUnsavedBorderChanges || saving}
          title={hasUnsavedBorderChanges ? 'Save changes' : 'No changes to save'}
        >
          <Save size={16} />
          {saving ? 'Saving...' : 'Save Settings'}
        </button>
        <button className="btn-toolbar" onClick={() => onRunBorderReplacer(true)} disabled={runningBorderReplacer} title="Dry run border replacer">
          <Eye size={16} />
          Dry Run
        </button>
        <button className="btn-toolbar btn-primary" onClick={() => onRunBorderReplacer(false)} disabled={runningBorderReplacer}>
          <Play size={16} />
          {runningBorderReplacer ? 'Running...' : 'Run Border Replacer'}
        </button>
      </Toolbar>

      <div className="settings-section">
        <h2>Border Configuration</h2>
        <p className="section-description">Configure border colors and dimensions. Leave colors empty to remove borders instead of adding them.</p>

        <div className="settings-grid">
          <div className="field-group">
            <label>Incremental Mode</label>
            <div className="toggle-field">
              <label className="toggle-switch">
                <input type="checkbox" checked={borderMode === 'incremental'} onChange={(e) => onSetBorderMode(e.target.checked ? 'incremental' : 'full')} />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">{borderMode === 'incremental' ? 'Incremental (Changed Items Only)' : 'Full (All Items)'}</span>
            </div>
            <small>
              When enabled, only processes items that have changed since last run (faster).
              When disabled, processes all items regardless of changes.
              <br />
              <em>Applies to all execution paths: workflow, auto-run after Poster Renamer, and standalone runs.</em>
            </small>
          </div>

          <div className="field-group">
            <label>Border Width (pixels)</label>
            <input
              type="number"
              value={borderWidth}
              onChange={(e) => onSetBorderWidth(parseInt(e.target.value) || 26)}
              min="1"
              max="200"
              style={{ maxWidth: '120px' }}
            />
            <small>Recommended: 26px (DAPS standard)</small>
          </div>

          <div className="field-group">
            <label>Remove Borders</label>
            <div className="toggle-field">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={removeBorders}
                  onChange={(e) => onSetRemoveBorders(e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">
                {removeBorders
                  ? 'Enabled (border removal mode)'
                  : 'Disabled (border replacement mode)'}
              </span>
            </div>
            <small>
              When enabled, posters are processed in remove-borders mode. Color and holiday color settings are hidden.
            </small>
          </div>

          {!removeBorders && (
          <div className="field-group">
            <label>Border Colors</label>
            <small style={{ marginBottom: '1rem', display: 'block' }}>
              Add one or more hex colors. Colors will cycle through posters.
              Leave empty to remove borders instead.
            </small>

            <div className="color-input-row">
              <input type="color" value={newColor} onChange={(e) => onSetNewColor(e.target.value)} className="color-picker" />
              <input type="text" value={newColor} onChange={(e) => onSetNewColor(e.target.value)} placeholder="#000000" />
              <button className="btn-secondary" onClick={onAddBorderColor} disabled={!newColor || borderColors.includes(newColor)}>
                Add Color
              </button>
            </div>

            {borderColors.length > 0 ? (
              <div className="color-list">
                {borderColors.map((color, index) => (
                  <div key={index} className="color-item">
                    <div className="color-preview" style={{ background: color }} />
                    <span className="color-value">{color}</span>
                    <button className="btn-remove-color" onClick={() => onRemoveBorderColor(color)} title="Remove color">
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-config">
                <p>No colors added. Add at least one color, or enable Remove Borders mode.</p>
              </div>
            )}
          </div>
          )}

          <div className="field-group">
            <label>Season Poster Borders</label>
            <small style={{ marginBottom: '1rem', display: 'block' }}>
              Control how borders are applied to TV season posters (files named like <code>Season01.jpg</code>).
              Overrides the main poster setting for season files only.
            </small>

            <div className="season-mode-options">
              <label className="radio-label">
                <input
                  type="radio"
                  name="seasonMode"
                  value="inherit"
                  checked={seasonMode === 'inherit'}
                  onChange={() => onSetSeasonMode('inherit')}
                />
                <span>Same as main posters</span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  name="seasonMode"
                  value="remove"
                  checked={seasonMode === 'remove'}
                  onChange={() => onSetSeasonMode('remove')}
                />
                <span>Remove borders</span>
              </label>
              <label className="radio-label">
                <input
                  type="radio"
                  name="seasonMode"
                  value="colors"
                  checked={seasonMode === 'colors'}
                  onChange={() => onSetSeasonMode('colors')}
                />
                <span>Custom colors</span>
              </label>
            </div>

            {seasonMode !== 'inherit' && (
              <div style={{ marginTop: '1rem' }}>
                <label>Season Border Width (pixels)</label>
                <input
                  type="number"
                  value={seasonWidth}
                  onChange={(e) => onSetSeasonWidth(parseInt(e.target.value) || 26)}
                  min="1"
                  max="200"
                  style={{ maxWidth: '120px' }}
                />
                <small style={{ display: 'block' }}>
                  Border width applied to season posters. Independent of the main poster width above.
                </small>
              </div>
            )}

            {seasonMode === 'colors' && (
              <div style={{ marginTop: '1rem' }}>
                <div className="color-input-row">
                  <input
                    type="color"
                    value={newSeasonColor}
                    onChange={(e) => onSetNewSeasonColor(e.target.value)}
                    className="color-picker"
                  />
                  <input
                    type="text"
                    value={newSeasonColor}
                    onChange={(e) => onSetNewSeasonColor(e.target.value)}
                    placeholder="#000000"
                  />
                  <button
                    className="btn-secondary"
                    onClick={onAddSeasonBorderColor}
                    disabled={!newSeasonColor || seasonColors.includes(newSeasonColor)}
                  >
                    Add Color
                  </button>
                </div>

                {seasonColors.length > 0 ? (
                  <div className="color-list">
                    {seasonColors.map((color, index) => (
                      <div key={index} className="color-item">
                        <div className="color-preview" style={{ background: color }} />
                        <span className="color-value">{color}</span>
                        <button
                          className="btn-remove-color"
                          onClick={() => onRemoveSeasonBorderColor(color)}
                          title="Remove color"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-config">
                    <p>No season colors added. Add at least one color for season posters.</p>
                  </div>
                )}
              </div>
            )}
          </div>

          {!removeBorders && (
          <div className="field-group">
            <label>Holiday Preset Schedules</label>
            <small style={{ marginBottom: '1rem', display: 'block' }}>
              Add preset holiday date ranges and color palettes. If a holiday is active, these colors override the default border colors.
            </small>

            <div className="holiday-preset-row">
              <select
                value={selectedPresetName}
                onChange={(e) => setSelectedPresetName(e.target.value)}
              >
                <option value="">Select holiday preset...</option>
                {HOLIDAY_PRESETS.map((preset) => (
                  <option key={preset.name} value={preset.name}>{preset.name}</option>
                ))}
              </select>
              <button
                className="btn-secondary"
                onClick={handleAddHolidayPreset}
                disabled={!selectedPresetName}
              >
                Add Preset
              </button>
            </div>

            <div className="custom-holiday-form">
              <label>Custom Holiday/Schedule</label>
              <input
                type="text"
                value={customHolidayName}
                onChange={(e) => setCustomHolidayName(e.target.value)}
                placeholder="Holiday name"
              />

              <div className="custom-holiday-range">
                <span>From</span>
                <select value={customFromMonth} onChange={(e) => setCustomFromMonth(e.target.value)}>
                  {MONTHS.map((month) => (
                    <option key={`custom-from-month-${month.value}`} value={month.value}>{month.label}</option>
                  ))}
                </select>
                <select value={customFromDay} onChange={(e) => setCustomFromDay(e.target.value)}>
                  {customFromDayOptions.map((day) => (
                    <option key={`custom-from-day-${day}`} value={day}>{day}</option>
                  ))}
                </select>

                <span>To</span>
                <select value={customToMonth} onChange={(e) => setCustomToMonth(e.target.value)}>
                  {MONTHS.map((month) => (
                    <option key={`custom-to-month-${month.value}`} value={month.value}>{month.label}</option>
                  ))}
                </select>
                <select value={customToDay} onChange={(e) => setCustomToDay(e.target.value)}>
                  {customToDayOptions.map((day) => (
                    <option key={`custom-to-day-${day}`} value={day}>{day}</option>
                  ))}
                </select>
              </div>

              <div className="custom-holiday-colors">
                <div className="custom-holiday-colors-input">
                  <input type="color" value={customNewColor} onChange={(e) => setCustomNewColor(e.target.value)} />
                  <input type="text" value={customNewColor} onChange={(e) => setCustomNewColor(e.target.value)} placeholder="#000000" />
                  <button
                    className="btn-secondary holiday-action-btn"
                    onClick={handleAddCustomHolidayColor}
                    disabled={!customNewColor || customHolidayColors.includes(customNewColor)}
                  >
                    Add Color
                  </button>
                </div>

                {customHolidayColors.length > 0 ? (
                  <div className="custom-holiday-color-list">
                    {customHolidayColors.map((color) => (
                      <div key={`custom-color-${color}`} className="custom-holiday-color-item">
                        <span className="holiday-color-swatch" style={{ background: color }} />
                        <span className="holiday-edit-color-value">{color}</span>
                        <button
                          className="btn-remove-color"
                          onClick={() => handleRemoveCustomHolidayColor(color)}
                          title="Remove color"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <small className="holiday-edit-empty">No custom colors set. Border Colors will be used.</small>
                )}
              </div>

              <button
                className="btn-secondary"
                onClick={handleAddCustomHoliday}
                disabled={!customHolidayName.trim() || (borderColors.length === 0 && customHolidayColors.length === 0)}
              >
                Add Custom Holiday
              </button>
            </div>

            {holidaySchedules.length > 0 ? (
              <div className="holiday-schedule-list">
                {holidaySchedules.map((holiday) => (
                  <div key={holiday.name} className="holiday-schedule-item">
                    <div className="holiday-schedule-header">
                      <div>
                        <div className="holiday-name">{holiday.name}</div>
                        <div className="holiday-range">{holiday.schedule}</div>
                      </div>
                      <div className="holiday-actions">
                        <button
                          className="btn-secondary holiday-action-btn"
                          onClick={() => handleStartEditHoliday(holiday)}
                          title="Edit holiday date range"
                        >
                          Edit
                        </button>
                        <button
                          className="btn-remove-color"
                          onClick={() => onRemoveHolidaySchedule(holiday.name)}
                          title="Remove holiday schedule"
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </div>

                    {editingHolidayName === holiday.name && (
                      <div className="holiday-edit-panel">
                        <div className="holiday-edit-row">
                          <span>From</span>
                          <select value={editFromMonth} onChange={(e) => setEditFromMonth(e.target.value)}>
                            {MONTHS.map((month) => (
                              <option key={`edit-from-month-${month.value}`} value={month.value}>{month.label}</option>
                            ))}
                          </select>
                          <select value={editFromDay} onChange={(e) => setEditFromDay(e.target.value)}>
                            {editFromDayOptions.map((day) => (
                              <option key={`edit-from-day-${day}`} value={day}>{day}</option>
                            ))}
                          </select>
                          <span>To</span>
                          <select value={editToMonth} onChange={(e) => setEditToMonth(e.target.value)}>
                            {MONTHS.map((month) => (
                              <option key={`edit-to-month-${month.value}`} value={month.value}>{month.label}</option>
                            ))}
                          </select>
                          <select value={editToDay} onChange={(e) => setEditToDay(e.target.value)}>
                            {editToDayOptions.map((day) => (
                              <option key={`edit-to-day-${day}`} value={day}>{day}</option>
                            ))}
                          </select>
                        </div>

                        <div className="holiday-edit-colors">
                          <div className="holiday-edit-colors-input">
                            <input type="color" value={editNewColor} onChange={(e) => setEditNewColor(e.target.value)} />
                            <input type="text" value={editNewColor} onChange={(e) => setEditNewColor(e.target.value)} placeholder="#000000" />
                            <button
                              className="btn-secondary holiday-action-btn"
                              onClick={handleAddEditHolidayColor}
                              disabled={!editNewColor || editHolidayColors.includes(editNewColor)}
                            >
                              Add Color
                            </button>
                          </div>

                          {editHolidayColors.length > 0 ? (
                            <div className="holiday-edit-color-list">
                              {editHolidayColors.map((color) => (
                                <div key={`${holiday.name}-edit-${color}`} className="holiday-edit-color-item">
                                  <span className="holiday-color-swatch" style={{ background: color }} />
                                  <span className="holiday-edit-color-value">{color}</span>
                                  <button
                                    className="btn-remove-color"
                                    onClick={() => handleRemoveEditHolidayColor(color)}
                                    title="Remove color"
                                  >
                                    <Trash2 size={14} />
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : (
                            <small className="holiday-edit-empty">Add at least one color before saving.</small>
                          )}
                        </div>

                        <div className="holiday-edit-actions">
                          <button
                            className="btn-secondary holiday-action-btn"
                            onClick={() => handleSaveEditHoliday(holiday)}
                            disabled={editHolidayColors.length === 0}
                          >
                            Save
                          </button>
                          <button className="btn-secondary holiday-action-btn" onClick={() => setEditingHolidayName(null)}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    )}

                    <div className="holiday-color-row">
                      {holiday.colors.map((color, index) => (
                        <span key={`${holiday.name}-${index}`} className="holiday-color-swatch" style={{ background: color }} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty-config">
                <p>No holiday schedules configured yet</p>
              </div>
            )}
          </div>
          )}

          {!removeBorders && (
          <div className="field-group">
            <label>Skip Outside Holiday Schedules</label>
            <div className="toggle-field">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={skipRunOutsideHoliday}
                  onChange={(e) => onSetSkipRunOutsideHoliday(e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">
                {skipRunOutsideHoliday
                  ? 'Enabled (Run only during configured holiday schedules)'
                  : 'Disabled (Always run border replacement/removal)'}
              </span>
            </div>
            <small>
              Applies everywhere border replacer runs: Workflow, Border tab manual runs, and auto-run after Poster Renamer.
              Outside holiday ranges, files are copied unchanged from tmp to final destination.
            </small>
          </div>
          )}
        </div>
      </div>
    </>
  )
}

export default BorderTab
