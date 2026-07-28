import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Eye, Info, Play, RotateCcw, Save, Trash2, Upload } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import Toolbar from '../Toolbar'
import BorderStyleControls, { BorderStyleValue, OverlaySelect, StyleValuePreview } from './BorderStyleControls'
import PlexRulesSection from './PlexRulesSection'
import NumberField from './NumberField'
import ConfirmDialog from '../ConfirmDialog'
import type { BorderStyle, GradientDirection, InnerEffect, PlexBorderRule, RuleRunType, SeasonMode, SeasonStyle } from '../../hooks/usePosterManagerBorder'
import { DEFAULT_SEASON_STYLE } from '../../hooks/usePosterManagerBorder'
import {
  BorderOverlay,
  deleteBorderOverlay,
  listBorderOverlays,
  uploadBorderOverlay,
} from '../../api/posterManager'

type HolidayPreset = {
  name: string
  schedule: string
  colors: string[]
}

type HolidaySchedule = {
  name: string
  schedule: string
  colors: string[]
  style: SeasonStyle
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
  settingsLoaded: boolean
  borderWidth: number
  bandWidth: number
  borderMode: 'incremental' | 'full'
  newColor: string
  borderColors: string[]
  holidaySchedules: HolidaySchedule[]
  removeBorders: boolean
  seasonMode: SeasonMode
  seasonColors: string[]
  seasonWidth: number
  seasonStyle: SeasonStyle
  borderStyle: BorderStyle
  overlayImage: string
  overlayRemoveExisting: boolean
  gradientColors: string[]
  gradientDirection: GradientDirection
  newGradientColor: string
  innerEffect: InnerEffect
  innerColor: string
  innerOpacity: number
  innerWidth: number
  fadeWidth: number
  plexRules: PlexBorderRule[]
  ruleRunTypes: RuleRunType[]
  ruleLibraries: Set<string>
  onSetPlexRules: (updater: (prev: PlexBorderRule[]) => PlexBorderRule[]) => void
  onToggleRuleRunType: (type: RuleRunType) => void
  onToggleRuleLibrary: (fullKey: string) => void
  onSaveSettings: () => void
  onResetBorderSettings: () => void
  onRunBorderReplacer: (dryRun: boolean) => void
  onSetBorderWidth: (width: number) => void
  onSetBandWidth: (width: number) => void
  onSetBorderMode: (mode: 'incremental' | 'full') => void
  onSetNewColor: (color: string) => void
  onAddBorderColor: () => void
  onRemoveBorderColor: (color: string) => void
  onSetRemoveBorders: (value: boolean) => void
  onAddHolidaySchedule: (holiday: HolidaySchedule) => void
  onRemoveHolidaySchedule: (name: string) => void
  onSetSeasonMode: (mode: SeasonMode) => void
  onSetSeasonWidth: (width: number) => void
  onSetSeasonColors: (colors: string[]) => void
  onSetSeasonStyle: (updater: (prev: SeasonStyle) => SeasonStyle) => void
  onSetBorderStyle: (style: BorderStyle) => void
  onSetOverlayImage: (name: string) => void
  onSetOverlayRemoveExisting: (value: boolean) => void
  onSetGradientDirection: (direction: GradientDirection) => void
  onSetNewGradientColor: (color: string) => void
  onAddGradientColor: () => void
  onRemoveGradientColor: (color: string) => void
  onSetInnerEffect: (effect: InnerEffect) => void
  onSetInnerColor: (color: string) => void
  onSetInnerOpacity: (value: number) => void
  onSetInnerWidth: (value: number) => void
  onSetFadeWidth: (value: number) => void
}

function BorderTab({
  hasUnsavedBorderChanges,
  saving,
  runningBorderReplacer,
  settingsLoaded,
  borderWidth,
  bandWidth,
  borderMode,
  newColor,
  borderColors,
  holidaySchedules,
  removeBorders,
  seasonMode,
  seasonColors,
  seasonWidth,
  seasonStyle,
  borderStyle,
  overlayImage,
  overlayRemoveExisting,
  gradientColors,
  gradientDirection,
  newGradientColor,
  innerEffect,
  innerColor,
  innerOpacity,
  innerWidth,
  fadeWidth,
  plexRules,
  ruleRunTypes,
  ruleLibraries,
  onSetPlexRules,
  onToggleRuleRunType,
  onToggleRuleLibrary,
  onSaveSettings,
  onResetBorderSettings,
  onRunBorderReplacer,
  onSetBorderWidth,
  onSetBandWidth,
  onSetBorderMode,
  onSetNewColor,
  onAddBorderColor,
  onRemoveBorderColor,
  onSetRemoveBorders,
  onAddHolidaySchedule,
  onRemoveHolidaySchedule,
  onSetSeasonMode,
  onSetSeasonWidth,
  onSetSeasonColors,
  onSetSeasonStyle,
  onSetBorderStyle,
  onSetOverlayImage,
  onSetOverlayRemoveExisting,
  onSetGradientDirection,
  onSetNewGradientColor,
  onAddGradientColor,
  onRemoveGradientColor,
  onSetInnerEffect,
  onSetInnerColor,
  onSetInnerOpacity,
  onSetInnerWidth,
  onSetFadeWidth,
}: BorderTabProps) {
  const navigate = useNavigate()

  const [overlays, setOverlays] = useState<BorderOverlay[]>([])
  const [overlayError, setOverlayError] = useState('')
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const refreshOverlays = useCallback(async () => {
    try {
      const result = await listBorderOverlays()
      setOverlays(result.overlays || [])
    } catch {
      setOverlays([])
    }
  }, [])

  useEffect(() => {
    refreshOverlays()
  }, [refreshOverlays])

  const handleUploadOverlay = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setOverlayError('')
    try {
      const result = await uploadBorderOverlay(file)
      await refreshOverlays()
      onSetOverlayImage(result.name)
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setOverlayError(detail || 'Failed to upload overlay. Must be a 1000x1500 PNG with transparency.')
    }
  }

  const handleDeleteOverlay = async (name: string) => {
    setOverlayError('')
    try {
      await deleteBorderOverlay(name)
      if (overlayImage === name) onSetOverlayImage('')
      await refreshOverlays()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setOverlayError(detail || 'Failed to delete overlay.')
    }
  }

  // Live preview: render the sample poster with the current border settings.
  // The main border style assembled into the shared BorderStyleValue shape (for the preview).
  const mainStyleValue: BorderStyleValue = {
    style: borderStyle,
    colors: borderColors,
    gradientColors,
    gradientDirection,
    overlayImage,
    removeExisting: overlayRemoveExisting,
    innerEffect,
    innerColor,
    innerOpacity,
    innerWidth,
    fadeWidth,
  }

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
  const [editHolidayStyle, setEditHolidayStyle] = useState<SeasonStyle>(DEFAULT_SEASON_STYLE)
  const [customHolidayName, setCustomHolidayName] = useState('')
  const [customFromMonth, setCustomFromMonth] = useState('01')
  const [customFromDay, setCustomFromDay] = useState('01')
  const [customToMonth, setCustomToMonth] = useState('01')
  const [customToDay, setCustomToDay] = useState('01')
  const [customHolidayColors, setCustomHolidayColors] = useState<string[]>([])
  const [customHolidayStyle, setCustomHolidayStyle] = useState<SeasonStyle>(DEFAULT_SEASON_STYLE)

  // Route a BorderStyleControls patch into a (colors, style) state pair.
  // Switching to gradient seeds the gradient with the existing solid colors so an
  // already-configured palette (holiday preset, season, custom) isn't retyped.
  const applyStylePatch = (
    patch: Partial<BorderStyleValue>,
    currentColors: string[],
    setColors: (c: string[]) => void,
    setStyle: (updater: (prev: SeasonStyle) => SeasonStyle) => void,
  ) => {
    if (patch.colors !== undefined) setColors(patch.colors)
    const { colors, ...rest } = patch
    void colors
    if (Object.keys(rest).length > 0) {
      setStyle((prev) => {
        const next = { ...prev, ...rest }
        if (rest.style === 'gradient' && prev.style !== 'gradient' && next.gradientColors.length === 0) {
          next.gradientColors = [...currentColors]
        }
        return next
      })
    }
  }

  const holidayStyleHasRenderable = (colors: string[], style: SeasonStyle) =>
    colors.length > 0 ||
    (style.style === 'image' && style.overlayImage.trim().length > 0) ||
    (style.style === 'gradient' && style.gradientColors.length > 0)

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
    onAddHolidaySchedule({ ...preset, style: { ...DEFAULT_SEASON_STYLE } })
  }

  const handleStartEditHoliday = (holiday: HolidaySchedule) => {
    const parsed = parseScheduleRange(holiday.schedule)
    setEditFromMonth(parsed.fromMonth)
    setEditFromDay(parsed.fromDay)
    setEditToMonth(parsed.toMonth)
    setEditToDay(parsed.toDay)
    setEditHolidayColors(holiday.colors.length > 0 ? [...holiday.colors] : [...borderColors])
    setEditHolidayStyle({ ...holiday.style })
    setEditingHolidayName(holiday.name)
  }

  const handleSaveEditHoliday = (holiday: HolidaySchedule) => {
    if (!holidayStyleHasRenderable(editHolidayColors, editHolidayStyle)) return

    onAddHolidaySchedule({
      ...holiday,
      schedule: buildScheduleRange(editFromMonth, editFromDay, editToMonth, editToDay),
      colors: editHolidayColors,
      style: editHolidayStyle,
    })
    setEditingHolidayName(null)
  }

  // True when the in-progress edit differs from the saved holiday (drives the red Save button).
  const editHolidayHasChanges = (holiday: HolidaySchedule) => {
    const initialColors = holiday.colors.length > 0 ? holiday.colors : borderColors
    return (
      buildScheduleRange(editFromMonth, editFromDay, editToMonth, editToDay) !== holiday.schedule ||
      JSON.stringify(editHolidayColors) !== JSON.stringify(initialColors) ||
      JSON.stringify(editHolidayStyle) !== JSON.stringify(holiday.style)
    )
  }

  const handleAddCustomHoliday = () => {
    const name = customHolidayName.trim()
    if (!name) return

    const effectiveColors = customHolidayColors.length > 0 ? customHolidayColors : borderColors
    if (!holidayStyleHasRenderable(effectiveColors, customHolidayStyle)) return

    onAddHolidaySchedule({
      name,
      schedule: buildScheduleRange(customFromMonth, customFromDay, customToMonth, customToDay),
      colors: effectiveColors,
      style: customHolidayStyle,
    })

    setCustomHolidayName('')
    setCustomHolidayColors([])
    setCustomHolidayStyle({ ...DEFAULT_SEASON_STYLE })
    setCustomFromMonth('01')
    setCustomFromDay('01')
    setCustomToMonth('01')
    setCustomToDay('01')
  }

  const confirmResetToDefaults = () => {
    onResetBorderSettings()
    setEditingHolidayName(null)
    setShowResetConfirm(false)
  }

  // Reused in each section header so users can save without scrolling to the toolbar.
  const sectionSaveButton = (
    <button
      className={`btn-toolbar section-save-btn ${hasUnsavedBorderChanges ? 'btn-unsaved' : ''}`}
      onClick={onSaveSettings}
      disabled={!hasUnsavedBorderChanges || saving}
      title={hasUnsavedBorderChanges ? 'Save changes' : 'No changes to save'}
    >
      <Save size={16} />
      {saving ? 'Saving...' : 'Save Settings'}
    </button>
  )

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

      {!settingsLoaded ? (
        <div className="settings-section"><p className="section-description">Loading border settings…</p></div>
      ) : (
      <>
      <div className="settings-section border-settings-card">
        <div className="section-header-row">
          <h2>Border Settings</h2>
          <button
            className="btn-secondary"
            onClick={() => setShowResetConfirm(true)}
            title="Reset all border settings on this page to their defaults"
          >
            <RotateCcw size={16} /> Reset to Defaults
          </button>
        </div>

        <div className="settings-grid border-basic-row">
          <div className="field-group">
            <div className="field-label-row">
              <label>Incremental Mode</label>
              <span className="toolbar-info" tabIndex={0}>
                <Info size={14} />
                <div className="toolbar-tooltip">
                  When enabled, only processes items changed since the last run (faster); when disabled, processes all items regardless of changes.
                  Applies to all execution paths: workflow, auto-run after Asset Renamer, and standalone runs.
                </div>
              </span>
            </div>
            <div className="toggle-field">
              <label className="toggle-switch">
                <input type="checkbox" checked={borderMode === 'incremental'} onChange={(e) => onSetBorderMode(e.target.checked ? 'incremental' : 'full')} />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">{borderMode === 'incremental' ? 'Incremental (Changed Items Only)' : 'Full (All Items)'}</span>
            </div>
          </div>

          <div className="field-group">
            <div className="field-label-row">
              <label>Border Removal Width (pixels)</label>
              <span className="toolbar-info" tabIndex={0}>
                <Info size={14} />
                <div className="toolbar-tooltip">How many pixels to strip when removing borders (the Remove Borders toggle). The width of an ADDED border is set separately in the Border Style section below. Recommended: 26px (DAPS standard).</div>
              </span>
            </div>
            <NumberField value={borderWidth} onChange={onSetBorderWidth} fallback={26} min={1} max={200} style={{ maxWidth: '120px' }} />
          </div>

          <div className="field-group">
            <div className="field-label-row">
              <label>Remove Borders</label>
              <span className="toolbar-info" tabIndex={0}>
                <Info size={14} />
                <div className="toolbar-tooltip">When enabled, posters are processed in remove-borders mode. Color and holiday color settings are hidden.</div>
              </span>
            </div>
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
          </div>
        </div>
      </div>

      <div className="settings-section">
        <div className="section-header-row">
          <h2>Border Style &amp; Preview</h2>
          {sectionSaveButton}
        </div>
        {removeBorders ? (
          <p className="section-description">Border style options are unavailable in remove-borders mode.</p>
        ) : (
          <div className="border-style-layout">
            <div className="border-style-preview-col">
              <div className="border-style-preview-wrap">
                <span className="border-style-preview-label">Preview — click to enlarge</span>
                <StyleValuePreview value={mainStyleValue} borderWidth={bandWidth} />
              </div>
            </div>
            <div className="border-style-settings-col">
              <div className="settings-grid">

          {!removeBorders && (
          <div className="field-group">
            <div className="field-label-row">
              <label>Border Width</label>
              <span className="toolbar-info" tabIndex={0}>
                <Info size={14} />
                <div className="toolbar-tooltip">How thick the added border is (band, gradient, or frame edge). The width at the top of the page applies only to border removal.</div>
              </span>
            </div>
            <NumberField value={bandWidth} onChange={onSetBandWidth} fallback={26} min={1} max={200} style={{ maxWidth: '120px' }} />
          </div>
          )}

          {!removeBorders && (
          <div className="field-group">
            <label>Border Style</label>
            <small style={{ marginBottom: '0.75rem', display: 'block' }}>
              How the replaced border is rendered. <strong>Solid</strong> uses the Border Colors below;
              <strong> Gradient</strong> blends colors across the band; <strong>Image overlay</strong> composites a PNG frame you design;
              <strong> Remove borders</strong> strips the border — but unlike the Remove Borders toggle above, an active holiday still applies its border.
            </small>
            <select
              value={borderStyle}
              onChange={(e) => onSetBorderStyle(e.target.value as BorderStyle)}
              style={{ maxWidth: '240px' }}
            >
              <option value="solid">Solid color</option>
              <option value="gradient">Gradient band</option>
              <option value="image">Image overlay (frame)</option>
              <option value="remove">Remove borders</option>
            </select>

            {borderStyle === 'gradient' && (
              <div style={{ marginTop: '1rem' }}>
                <label>Gradient Colors</label>
                <small style={{ marginBottom: '0.5rem', display: 'block' }}>Two or more colors, blended in order across the border.</small>
                <div className="color-input-row">
                  <input type="color" value={newGradientColor} onChange={(e) => onSetNewGradientColor(e.target.value)} className="color-picker" />
                  <input type="text" value={newGradientColor} onChange={(e) => onSetNewGradientColor(e.target.value)} placeholder="#000000" />
                  <button className="btn-secondary" onClick={onAddGradientColor} disabled={!newGradientColor || gradientColors.includes(newGradientColor)}>
                    Add Color
                  </button>
                </div>
                {gradientColors.length > 0 ? (
                  <div className="color-list">
                    {gradientColors.map((color, index) => (
                      <div key={index} className="color-item">
                        <div className="color-preview" style={{ background: color }} />
                        <span className="color-value">{color}</span>
                        <button className="btn-remove-color" onClick={() => onRemoveGradientColor(color)} title="Remove color">
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-config"><p>Add at least two colors for a gradient.</p></div>
                )}
                <div style={{ marginTop: '0.75rem' }}>
                  <label>Direction</label>
                  <select
                    value={gradientDirection}
                    onChange={(e) => onSetGradientDirection(e.target.value as GradientDirection)}
                    style={{ maxWidth: '200px', display: 'block' }}
                  >
                    <option value="vertical">Vertical</option>
                    <option value="horizontal">Horizontal</option>
                    <option value="diagonal">Diagonal</option>
                  </select>
                </div>
              </div>
            )}

            {borderStyle === 'image' && (
              <div style={{ marginTop: '1rem' }}>
                <label>Border Frame Image</label>
                <small style={{ marginBottom: '0.5rem', display: 'block' }}>
                  Choose a 1000×1500 PNG frame (transparent center). Bundled presets and your uploads are listed.
                </small>
                <div className="border-overlay-row">
                  <OverlaySelect overlays={overlays} value={overlayImage} onChange={onSetOverlayImage} />
                  <input ref={fileInputRef} type="file" accept="image/png" style={{ display: 'none' }} onChange={handleUploadOverlay} />
                  <button className="btn-secondary" onClick={() => fileInputRef.current?.click()}>
                    <Upload size={16} /> Upload
                  </button>
                  {overlayImage && overlays.find((o) => o.name === overlayImage)?.source === 'user' && (
                    <button className="btn-remove-color" onClick={() => handleDeleteOverlay(overlayImage)} title="Delete uploaded overlay">
                      <Trash2 size={16} />
                    </button>
                  )}
                </div>
                {overlayError && <small style={{ color: 'var(--error, #e55)', display: 'block' }}>{overlayError}</small>}
              </div>
            )}

          </div>
          )}

          {!removeBorders && borderStyle === 'solid' && (
          <div className="field-group">
            <label>Border Colors</label>
            <small style={{ marginBottom: '1rem', display: 'block' }}>
              Add one or more hex colors. Colors will cycle through posters.
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

          {!removeBorders && borderStyle !== 'remove' && (
          <div className="field-group">
            <div className="toggle-field">
              <label className="toggle-switch">
                <input type="checkbox" checked={overlayRemoveExisting} onChange={(e) => onSetOverlayRemoveExisting(e.target.checked)} />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">Remove existing border first</span>
            </div>
            <small style={{ display: 'block' }}>
              Strip the existing border first — exactly like Remove Borders mode (top/left/right cropped, black title bar) — before applying the new border.
            </small>
          </div>
          )}
              </div>
            </div>
            <div className="border-style-settings-col">
              <div className="settings-grid">

          {!removeBorders && borderStyle !== 'remove' && (
          <div className="field-group">
            <label>Inner Edge Effect</label>
            <small style={{ marginBottom: '0.75rem', display: 'block' }}>
              Adds a soft transition just inside the border. Source posters already carry some baked-in inner glow,
              so keep these subtle. Applies on top of image-overlay frames too.
            </small>

            <div className="season-mode-options">
              <label className="radio-label">
                <input type="radio" name="innerEffect" checked={innerEffect === 'none'} onChange={() => onSetInnerEffect('none')} />
                <span>None</span>
              </label>
              <label className="radio-label">
                <input type="radio" name="innerEffect" checked={innerEffect === 'glow'} onChange={() => onSetInnerEffect('glow')} />
                <span>Dark inner glow</span>
              </label>
              <label className="radio-label">
                <input type="radio" name="innerEffect" checked={innerEffect === 'fade'} onChange={() => onSetInnerEffect('fade')} />
                <span>Border-color fade</span>
              </label>
            </div>

            {innerEffect === 'glow' && (
              <div style={{ marginTop: '1rem' }}>
                <label>Glow Color</label>
                <div className="color-input-row">
                  <input type="color" value={innerColor} onChange={(e) => onSetInnerColor(e.target.value)} className="color-picker" />
                  <input type="text" value={innerColor} onChange={(e) => onSetInnerColor(e.target.value)} placeholder="#000000" />
                </div>
                <div style={{ marginTop: '0.75rem' }}>
                  <label>Opacity: {innerOpacity}%</label>
                  <input
                    type="range"
                    min="0"
                    max="100"
                    value={innerOpacity}
                    onChange={(e) => onSetInnerOpacity(parseInt(e.target.value) || 0)}
                    style={{ display: 'block', maxWidth: '240px' }}
                  />
                  <small>Lower = lighter glow, higher = darker.</small>
                </div>
                <div style={{ marginTop: '0.5rem' }}>
                  <label>Glow Width (pixels)</label>
                  <NumberField value={innerWidth} onChange={onSetInnerWidth} fallback={1} min={1} max={200} style={{ maxWidth: '120px' }} />
                </div>
              </div>
            )}

            {innerEffect === 'fade' && (
              <div style={{ marginTop: '1rem' }}>
                <label>Fade Width (pixels)</label>
                <NumberField value={fadeWidth} onChange={onSetFadeWidth} fallback={1} min={1} max={200} style={{ maxWidth: '120px' }} />
                <small style={{ display: 'block' }}>The border color fades into the poster over this many pixels.</small>
              </div>
            )}
          </div>
          )}
              </div>
            </div>
          </div>
        )}
      </div>

      <div className="settings-section">
        <div className="section-header-row">
          <h2>Season Poster Borders</h2>
          {sectionSaveButton}
        </div>
          <div className="field-group">
            <small style={{ marginBottom: '0.5rem', display: 'block' }}>
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
                  value="custom"
                  checked={seasonMode === 'custom'}
                  onChange={() => onSetSeasonMode('custom')}
                />
                <span>Custom style</span>
              </label>
            </div>

            {seasonMode !== 'inherit' && (
              <div style={{ marginTop: '0.5rem' }}>
                <label>Season Border Width (pixels)</label>
                <NumberField value={seasonWidth} onChange={onSetSeasonWidth} fallback={26} min={1} max={200} style={{ maxWidth: '120px' }} />
                <small style={{ display: 'block' }}>
                  Border width applied to season posters. Independent of the main poster width above.
                </small>
              </div>
            )}

            {seasonMode === 'custom' && (
              <div style={{ marginTop: '1rem' }}>
                <BorderStyleControls
                  verbose
                  value={{ ...seasonStyle, colors: seasonColors }}
                  onChange={(patch) => applyStylePatch(patch, seasonColors, onSetSeasonColors, onSetSeasonStyle)}
                  overlays={overlays}
                  refreshOverlays={refreshOverlays}
                  idPrefix="season"
                  label="Season Border Style"
                  preview={<StyleValuePreview value={{ ...seasonStyle, colors: seasonColors }} borderWidth={seasonWidth} />}
                />
              </div>
            )}
          </div>
      </div>

      {!removeBorders && (
      <div className="settings-section">
        <div className="section-header-row">
          <h2>Holiday Schedules</h2>
          {sectionSaveButton}
        </div>
        <div className="settings-grid">

          {!removeBorders && (
          <div className="field-group">
            <label>Configured Holidays</label>
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
                        {editingHolidayName === holiday.name ? (
                          <>
                            <button
                              className={`btn-secondary holiday-action-btn ${editHolidayHasChanges(holiday) ? 'btn-unsaved' : ''}`}
                              onClick={() => handleSaveEditHoliday(holiday)}
                              disabled={!holidayStyleHasRenderable(editHolidayColors, editHolidayStyle) || !editHolidayHasChanges(holiday)}
                            >
                              Save
                            </button>
                            <button className="btn-secondary holiday-action-btn" onClick={() => setEditingHolidayName(null)}>
                              Cancel
                            </button>
                          </>
                        ) : (
                          <button
                            className="btn-secondary holiday-action-btn"
                            onClick={() => handleStartEditHoliday(holiday)}
                            title="Edit holiday date range"
                          >
                            Edit
                          </button>
                        )}
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

                        <BorderStyleControls
                          value={{ ...editHolidayStyle, colors: editHolidayColors }}
                          onChange={(patch) => applyStylePatch(patch, editHolidayColors, setEditHolidayColors, setEditHolidayStyle)}
                          overlays={overlays}
                          refreshOverlays={refreshOverlays}
                          idPrefix={`holiday-edit-${holiday.name}`}
                          label="Holiday Border Style"
                          preview={<StyleValuePreview value={{ ...editHolidayStyle, colors: editHolidayColors }} borderWidth={bandWidth} />}
                        />
                      </div>
                    )}

                    <div className="holiday-color-row">
                      {holiday.style.style !== 'image' && holiday.colors.map((color, index) => (
                        <span key={`${holiday.name}-${index}`} className="holiday-color-swatch" style={{ background: color }} />
                      ))}
                      {holiday.style.style !== 'solid' && (
                        <span className="holiday-frame-badge" title={`Style: ${holiday.style.style}`}>
                          {holiday.style.style === 'image' ? `🖼 ${holiday.style.overlayImage || 'frame'}` : '🎨 gradient'}
                        </span>
                      )}
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
              <div className="custom-holiday-name-row">
                <input
                  type="text"
                  value={customHolidayName}
                  onChange={(e) => setCustomHolidayName(e.target.value)}
                  placeholder="Holiday name"
                />
                <button
                  className="btn-secondary btn-add-holiday"
                  onClick={handleAddCustomHoliday}
                  disabled={!customHolidayName.trim() || !holidayStyleHasRenderable(customHolidayColors.length > 0 ? customHolidayColors : borderColors, customHolidayStyle)}
                >
                  Add Custom Holiday
                </button>
              </div>

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

              <BorderStyleControls
                value={{ ...customHolidayStyle, colors: customHolidayColors }}
                onChange={(patch) => applyStylePatch(patch, customHolidayColors.length > 0 ? customHolidayColors : borderColors, setCustomHolidayColors, setCustomHolidayStyle)}
                overlays={overlays}
                refreshOverlays={refreshOverlays}
                idPrefix="holiday-custom"
                label="Holiday Border Style"
                preview={<StyleValuePreview value={{ ...customHolidayStyle, colors: customHolidayColors }} borderWidth={bandWidth} />}
              />
            </div>
          </div>
          )}
        </div>
      </div>
      )}

      <PlexRulesSection
        rules={plexRules}
        runTypes={ruleRunTypes}
        libraries={ruleLibraries}
        borderWidth={bandWidth}
        overlays={overlays}
        refreshOverlays={refreshOverlays}
        sectionSaveButton={sectionSaveButton}
        onSetRules={onSetPlexRules}
        onToggleRunType={onToggleRuleRunType}
        onToggleLibrary={onToggleRuleLibrary}
      />
      </>
      )}

      <ConfirmDialog
        isOpen={showResetConfirm}
        title="Reset to Defaults"
        message="Reset all Border Replacer settings on this page to their defaults? This clears border colors, styles, inner effects, season settings, and holiday schedules. Nothing is saved until you click Save Settings."
        confirmText="Reset to Defaults"
        cancelText="Cancel"
        variant="danger"
        onConfirm={confirmResetToDefaults}
        onCancel={() => setShowResetConfirm(false)}
      />
    </>
  )
}

export default BorderTab
