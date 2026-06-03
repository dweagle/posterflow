import { MutableRefObject, useState } from 'react'
import { getSettings, saveBulkSettings } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface OriginalBorderSettings {
  colors: string[]
  width: number
  mode: 'incremental' | 'full'
  autoRunBorder: boolean
  holidaySchedules: BorderHolidaySchedule[]
  skipRunOutsideHoliday: boolean
  removeBorders: boolean
  seasonMode: 'inherit' | 'remove' | 'colors'
  seasonColors: string[]
}

export interface BorderHolidaySchedule {
  name: string
  schedule: string
  colors: string[]
}

interface UsePosterManagerBorderParams {
  originalBorderSettingsRef: MutableRefObject<OriginalBorderSettings | null>
  setHasUnsavedBorderChanges: (value: boolean) => void
  setSaving: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
}

export const usePosterManagerBorder = ({
  originalBorderSettingsRef,
  setHasUnsavedBorderChanges,
  setSaving,
  showToast,
}: UsePosterManagerBorderParams) => {
  const [borderColors, setBorderColors] = useState<string[]>([])
  const [borderWidth, setBorderWidth] = useState(26)
  const [borderMode, setBorderMode] = useState<'incremental' | 'full'>('incremental')
  const [newColor, setNewColor] = useState('#000000')
  const [autoRunBorder, setAutoRunBorder] = useState(false)
  const [holidaySchedules, setHolidaySchedules] = useState<BorderHolidaySchedule[]>([])
  const [skipRunOutsideHoliday, setSkipRunOutsideHoliday] = useState(false)
  const [removeBorders, setRemoveBorders] = useState(false)
  const [seasonMode, setSeasonMode] = useState<'inherit' | 'remove' | 'colors'>('inherit')
  const [seasonColors, setSeasonColors] = useState<string[]>([])
  const [newSeasonColor, setNewSeasonColor] = useState('#000000')

  const fetchBorderSettings = async () => {
    try {
      const settings = await getSettings()

      const colorsStr = settings['border_replacer_colors'] || '[]'
      let colors: string[] = []
      try {
        colors = JSON.parse(colorsStr)
        colors = Array.isArray(colors) ? colors : []
        setBorderColors(colors)
      } catch {
        setBorderColors([])
      }

      const widthStr = settings['border_replacer_width'] || '26'
      const width = parseInt(widthStr) || 26
      setBorderWidth(width)

      const modeStr = settings['border_replacer_mode'] || 'incremental'
      const mode = modeStr === 'full' ? 'full' : 'incremental'
      setBorderMode(mode)

      const autoRunStr = settings['auto_run_border'] || 'false'
      const autoRun = autoRunStr.toLowerCase() === 'true'
      setAutoRunBorder(autoRun)

      const holidaysStr = settings['border_replacer_holidays'] || '[]'
      let holidays: BorderHolidaySchedule[] = []
      try {
        const parsed = JSON.parse(holidaysStr)
        if (Array.isArray(parsed)) {
          holidays = parsed
            .filter((item: unknown): item is Record<string, unknown> => !!item && typeof item === 'object')
            .map((item) => ({
              name: String(item.name || '').trim(),
              schedule: String(item.schedule || '').trim(),
              colors: Array.isArray(item.colors)
                ? item.colors.map((color) => String(color).trim()).filter(Boolean)
                : Array.isArray(item.color)
                  ? item.color.map((color) => String(color).trim()).filter(Boolean)
                  : [],
            }))
            .filter((item) => item.name && item.schedule)
        }
      } catch {
        holidays = []
      }
      setHolidaySchedules(holidays)

      const skipRunStr = settings['border_replacer_skip_non_holiday'] || 'false'
      const skipRun = skipRunStr.toLowerCase() === 'true'
      setSkipRunOutsideHoliday(skipRun)

      const removeBordersStr = settings['border_replacer_remove_borders'] || 'false'
      const removeBordersEnabled = removeBordersStr.toLowerCase() === 'true'
      setRemoveBorders(removeBordersEnabled)

      const seasonModeStr = settings['border_replacer_season_mode'] || 'inherit'
      const resolvedSeasonMode = (['inherit', 'remove', 'colors'].includes(seasonModeStr)
        ? seasonModeStr
        : 'inherit') as 'inherit' | 'remove' | 'colors'
      setSeasonMode(resolvedSeasonMode)

      const seasonColorsStr = settings['border_replacer_season_colors'] || '[]'
      let loadedSeasonColors: string[] = []
      try {
        loadedSeasonColors = JSON.parse(seasonColorsStr)
        loadedSeasonColors = Array.isArray(loadedSeasonColors) ? loadedSeasonColors : []
        setSeasonColors(loadedSeasonColors)
      } catch {
        setSeasonColors([])
      }

      originalBorderSettingsRef.current = {
        colors: [...colors],
        width,
        mode,
        autoRunBorder: autoRun,
        holidaySchedules: holidays,
        skipRunOutsideHoliday: skipRun,
        removeBorders: removeBordersEnabled,
        seasonMode: resolvedSeasonMode,
        seasonColors: [...loadedSeasonColors],
      }
    } catch (error) {
      console.error('Error fetching border settings:', error)
    }
  }

  const saveBorderSettings = async () => {
    const hasHolidayColors = holidaySchedules.some(
      (holiday) => Array.isArray(holiday.colors) && holiday.colors.some((color) => color.trim().length > 0)
    )
    const isConfigured = removeBorders || borderColors.length > 0 || hasHolidayColors

    if (!isConfigured) {
      showToast(
        'Border Replacer configuration is incomplete. Add a border color, add holiday colors, or enable Remove Borders.',
        'error'
      )
      return
    }

    try {
      setSaving(true)
      await saveBulkSettings({
        border_replacer_colors: JSON.stringify(borderColors),
        border_replacer_width: borderWidth.toString(),
        border_replacer_mode: borderMode,
        auto_run_border: autoRunBorder ? 'true' : 'false',
        border_replacer_holidays: JSON.stringify(holidaySchedules),
        border_replacer_skip_non_holiday: skipRunOutsideHoliday ? 'true' : 'false',
        border_replacer_remove_borders: removeBorders ? 'true' : 'false',
        border_replacer_season_mode: seasonMode,
        border_replacer_season_colors: JSON.stringify(seasonColors),
      })

      originalBorderSettingsRef.current = {
        colors: [...borderColors],
        width: borderWidth,
        mode: borderMode,
        autoRunBorder,
        holidaySchedules,
        skipRunOutsideHoliday,
        removeBorders,
        seasonMode,
        seasonColors: [...seasonColors],
      }
      setHasUnsavedBorderChanges(false)
      showToast('Border settings saved successfully', 'success')
    } catch (error) {
      console.error('Error saving border settings:', error)
      showToast('Failed to save border settings', 'error')
    } finally {
      setSaving(false)
    }
  }

  const addBorderColor = () => {
    if (newColor && !borderColors.includes(newColor)) {
      setBorderColors([...borderColors, newColor])
      setNewColor('#000000')
    }
  }

  const removeBorderColor = (color: string) => {
    setBorderColors(borderColors.filter(c => c !== color))
  }

  const addSeasonBorderColor = () => {
    if (newSeasonColor && !seasonColors.includes(newSeasonColor)) {
      setSeasonColors([...seasonColors, newSeasonColor])
      setNewSeasonColor('#000000')
    }
  }

  const removeSeasonBorderColor = (color: string) => {
    setSeasonColors(seasonColors.filter(c => c !== color))
  }

  const addHolidaySchedule = (holiday: BorderHolidaySchedule) => {
    const name = holiday.name.trim()
    const schedule = holiday.schedule.trim()
    const colors = holiday.colors.map((color) => color.trim()).filter(Boolean)
    if (!name || !schedule || colors.length === 0) {
      return
    }

    setHolidaySchedules((prev) => {
      const filtered = prev.filter((item) => item.name.toLowerCase() !== name.toLowerCase())
      return [...filtered, { name, schedule, colors }]
    })
  }

  const removeHolidaySchedule = (name: string) => {
    setHolidaySchedules((prev) => prev.filter((item) => item.name !== name))
  }

  const resetBorderSettingsToOriginal = () => {
    if (!originalBorderSettingsRef.current) return

    const original = originalBorderSettingsRef.current
    setBorderColors([...original.colors])
    setBorderWidth(original.width)
    setBorderMode(original.mode)
    setAutoRunBorder(original.autoRunBorder)
    setHolidaySchedules([...original.holidaySchedules])
    setSkipRunOutsideHoliday(original.skipRunOutsideHoliday)
    setRemoveBorders(original.removeBorders)
    setSeasonMode(original.seasonMode)
    setSeasonColors([...original.seasonColors])
    setHasUnsavedBorderChanges(false)
  }

  return {
    borderColors,
    borderWidth,
    borderMode,
    newColor,
    autoRunBorder,
    holidaySchedules,
    skipRunOutsideHoliday,
    removeBorders,
    seasonMode,
    seasonColors,
    newSeasonColor,
    setBorderWidth,
    setBorderMode,
    setNewColor,
    setAutoRunBorder,
    setSkipRunOutsideHoliday,
    setRemoveBorders,
    setSeasonMode,
    setNewSeasonColor,
    fetchBorderSettings,
    saveBorderSettings,
    resetBorderSettingsToOriginal,
    addBorderColor,
    removeBorderColor,
    addSeasonBorderColor,
    removeSeasonBorderColor,
    addHolidaySchedule,
    removeHolidaySchedule,
  }
}
