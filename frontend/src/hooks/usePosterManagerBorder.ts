import { MutableRefObject, useState } from 'react'
import { getSettings, saveBulkSettings } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

// Parse an int, falling back only when the value isn't a number at all — a saved
// 0 (e.g. inner-glow opacity 0) must survive, so `|| fallback` would be wrong.
const parseIntOr = (value: unknown, fallback: number): number => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const n = parseInt(String(value ?? ''), 10)
  return Number.isNaN(n) ? fallback : n
}

export type BorderStyle = 'solid' | 'gradient' | 'image' | 'remove'
export type GradientDirection = 'vertical' | 'horizontal' | 'diagonal'
export type InnerEffect = 'none' | 'glow' | 'fade'
export type SeasonMode = 'inherit' | 'remove' | 'custom'

// Plex label/genre/collection border rules.
export type RuleMatchType = 'label' | 'genre' | 'collection'
export type RuleMode = 'include' | 'except' | 'skip'
export const RULE_RUN_TYPES = ['workflow', 'webhook', 'manual', 'autorun', 'scheduled'] as const
export type RuleRunType = (typeof RULE_RUN_TYPES)[number]

// A single rule: match items by a Plex label/genre/collection, then either apply
// this style to matches (include), to non-matches (except), or leave matches alone (skip).
export interface PlexBorderRule {
  name: string
  match: RuleMatchType
  value: string
  mode: RuleMode
  colors: string[]
  style: SeasonStyle
}

// Full independent style for season posters (used when seasonMode === 'custom').
// Solid colors come from the shared seasonColors; this holds everything else.
export interface SeasonStyle {
  style: BorderStyle
  overlayImage: string
  removeExisting: boolean
  gradientColors: string[]
  gradientDirection: GradientDirection
  innerEffect: InnerEffect
  innerColor: string
  innerOpacity: number
  innerWidth: number
  fadeWidth: number
}

// Default border-style options — the single source of truth for the main border,
// season custom style, new holidays, and resetBorderSettingsToDefaults, so no
// default can drift between the initial state and a reset.
const DEFAULT_BORDER_STYLE: SeasonStyle = {
  style: 'solid',
  overlayImage: '',
  removeExisting: false,
  gradientColors: [],
  gradientDirection: 'vertical',
  innerEffect: 'none',
  innerColor: '#000000',
  innerOpacity: 70,
  innerWidth: 8,
  fadeWidth: 8,
}

// The season/holiday code refers to these same defaults by their SeasonStyle name.
export const DEFAULT_SEASON_STYLE: SeasonStyle = DEFAULT_BORDER_STYLE

export interface OriginalBorderSettings {
  colors: string[]
  width: number
  bandWidth: number
  mode: 'incremental' | 'full'
  autoRunBorder: boolean
  autoRunCleanup: boolean
  cleanupDeleteUnknown: boolean
  holidaySchedules: BorderHolidaySchedule[]
  removeBorders: boolean
  seasonMode: SeasonMode
  seasonColors: string[]
  seasonWidth: number
  seasonStyle: SeasonStyle
  borderStyle: BorderStyle
  overlayImage: string
  gradientColors: string[]
  gradientDirection: GradientDirection
  overlayRemoveExisting: boolean
  innerEffect: InnerEffect
  innerColor: string
  innerOpacity: number
  innerWidth: number
  fadeWidth: number
  plexRules: PlexBorderRule[]
  ruleRunTypes: RuleRunType[]
  ruleLibraries: string[]
}

export interface BorderHolidaySchedule {
  name: string
  schedule: string
  colors: string[]
  style: SeasonStyle
}

interface UsePosterManagerBorderParams {
  originalBorderSettingsRef: MutableRefObject<OriginalBorderSettings | null>
  setHasUnsavedBorderChanges: (value: boolean) => void
  setSaving: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
}

// SeasonStyle <-> the snake_case shape the backend reads (for season + holidays).
const styleToSnake = (s: SeasonStyle) => ({
  style: s.style,
  overlay_image: s.overlayImage,
  remove_existing: s.removeExisting,
  gradient_colors: s.gradientColors,
  gradient_direction: s.gradientDirection,
  inner_effect: s.innerEffect,
  inner_color: s.innerColor,
  inner_opacity: s.innerOpacity,
  inner_width: s.innerWidth,
  fade_width: s.fadeWidth,
})

const snakeToStyle = (raw: Record<string, unknown> | undefined, legacyBorderImage?: string): SeasonStyle => {
  const r = raw || {}
  const num = (v: unknown, d: number) => parseIntOr(v, d)
  const styleVal = String(r.style || (legacyBorderImage ? 'image' : 'solid'))
  return {
    style: (['solid', 'gradient', 'image', 'remove'].includes(styleVal) ? styleVal : 'solid') as BorderStyle,
    overlayImage: String(r.overlay_image || legacyBorderImage || ''),
    removeExisting: r.remove_existing === true || String(r.remove_existing).toLowerCase() === 'true',
    gradientColors: Array.isArray(r.gradient_colors) ? r.gradient_colors.map((c) => String(c)) : [],
    gradientDirection: (['vertical', 'horizontal', 'diagonal'].includes(String(r.gradient_direction))
      ? String(r.gradient_direction)
      : 'vertical') as GradientDirection,
    innerEffect: (['none', 'glow', 'fade'].includes(String(r.inner_effect)) ? String(r.inner_effect) : 'none') as InnerEffect,
    innerColor: String(r.inner_color || '#000000'),
    innerOpacity: num(r.inner_opacity, 70),
    innerWidth: num(r.inner_width, 8),
    fadeWidth: num(r.fade_width, 8),
  }
}

// Serialize holidays for the backend (each holiday carries a full snake_case style).
const serializeHolidays = (holidays: BorderHolidaySchedule[]) =>
  holidays.map((holiday) => ({
    name: holiday.name,
    schedule: holiday.schedule,
    colors: holiday.colors,
    style: styleToSnake(holiday.style),
  }))

// Serialize Plex rules for the backend (same snake_case style as holidays).
const serializePlexRules = (rules: PlexBorderRule[]) =>
  rules.map((rule) => ({
    name: rule.name,
    match: rule.match,
    value: rule.value,
    mode: rule.mode,
    colors: rule.colors,
    style: styleToSnake(rule.style),
  }))

// Parse the stored border_replacer_plex_rules JSON into typed rules.
const parsePlexRules = (raw: string | undefined): PlexBorderRule[] => {
  try {
    const parsed = JSON.parse(raw || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((item: unknown): item is Record<string, unknown> => !!item && typeof item === 'object')
      .map((item) => ({
        name: String(item.name || '').trim(),
        match: (['label', 'genre', 'collection'].includes(String(item.match)) ? String(item.match) : 'label') as RuleMatchType,
        value: String(item.value || '').trim(),
        mode: (['include', 'except', 'skip'].includes(String(item.mode)) ? String(item.mode) : 'include') as RuleMode,
        colors: Array.isArray(item.colors) ? item.colors.map((c) => String(c).trim()).filter(Boolean) : [],
        style: snakeToStyle(item.style as Record<string, unknown> | undefined),
      }))
      .filter((rule) => rule.value)
  } catch {
    return []
  }
}

export const usePosterManagerBorder = ({
  originalBorderSettingsRef,
  setHasUnsavedBorderChanges,
  setSaving,
  showToast,
}: UsePosterManagerBorderParams) => {
  const [borderColors, setBorderColors] = useState<string[]>([])
  const [borderWidth, setBorderWidth] = useState(26)
  // Separate width for the ADDED band (Remove Borders off); the top width is removal-only.
  const [bandWidth, setBandWidth] = useState(26)
  const [borderMode, setBorderMode] = useState<'incremental' | 'full'>('incremental')
  const [newColor, setNewColor] = useState('#ffffff')
  const [autoRunBorder, setAutoRunBorder] = useState(false)
  const [autoRunCleanup, setAutoRunCleanup] = useState(true)
  const [cleanupDeleteUnknown, setCleanupDeleteUnknown] = useState(false)
  const [borderSettingsLoaded, setBorderSettingsLoaded] = useState(false)
  const [holidaySchedules, setHolidaySchedules] = useState<BorderHolidaySchedule[]>([])
  const [removeBorders, setRemoveBorders] = useState(false)
  const [seasonMode, setSeasonMode] = useState<SeasonMode>('inherit')
  const [seasonColors, setSeasonColors] = useState<string[]>([])
  const [seasonWidth, setSeasonWidth] = useState(26)
  const [seasonStyle, setSeasonStyle] = useState<SeasonStyle>(DEFAULT_SEASON_STYLE)
  const [borderStyle, setBorderStyle] = useState<BorderStyle>(DEFAULT_BORDER_STYLE.style)
  const [overlayImage, setOverlayImage] = useState(DEFAULT_BORDER_STYLE.overlayImage)
  const [overlayRemoveExisting, setOverlayRemoveExisting] = useState(DEFAULT_BORDER_STYLE.removeExisting)
  const [gradientColors, setGradientColors] = useState<string[]>([...DEFAULT_BORDER_STYLE.gradientColors])
  const [gradientDirection, setGradientDirection] = useState<GradientDirection>(DEFAULT_BORDER_STYLE.gradientDirection)
  const [newGradientColor, setNewGradientColor] = useState('#ffffff')
  const [innerEffect, setInnerEffect] = useState<InnerEffect>(DEFAULT_BORDER_STYLE.innerEffect)
  const [innerColor, setInnerColor] = useState(DEFAULT_BORDER_STYLE.innerColor)
  const [innerOpacity, setInnerOpacity] = useState(DEFAULT_BORDER_STYLE.innerOpacity)
  const [innerWidth, setInnerWidth] = useState(DEFAULT_BORDER_STYLE.innerWidth)
  const [fadeWidth, setFadeWidth] = useState(DEFAULT_BORDER_STYLE.fadeWidth)
  const [plexRules, setPlexRules] = useState<PlexBorderRule[]>([])
  const [ruleRunTypes, setRuleRunTypes] = useState<RuleRunType[]>([...RULE_RUN_TYPES])
  const [ruleLibraries, setRuleLibraries] = useState<Set<string>>(new Set())

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
      // Band width falls back to the (removal) width when unset, preserving old configs.
      const loadedBandWidth = parseIntOr(settings['border_replacer_band_width'], width)
      setBandWidth(loadedBandWidth)

      const modeStr = settings['border_replacer_mode'] || 'incremental'
      const mode = modeStr === 'full' ? 'full' : 'incremental'
      setBorderMode(mode)

      const autoRunStr = settings['auto_run_border'] || 'false'
      const autoRun = autoRunStr.toLowerCase() === 'true'
      setAutoRunBorder(autoRun)

      const autoRunCleanupVal = (settings['auto_run_cleanup'] || 'true').toLowerCase() === 'true'
      setAutoRunCleanup(autoRunCleanupVal)
      const cleanupDeleteUnknownVal = (settings['cleanup_delete_unknown'] || 'false').toLowerCase() === 'true'
      setCleanupDeleteUnknown(cleanupDeleteUnknownVal)

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
              style: snakeToStyle(
                item.style as Record<string, unknown> | undefined,
                String(item.border_image || '').trim() || undefined,
              ),
            }))
            .filter((item) => item.name && item.schedule)
        }
      } catch {
        holidays = []
      }
      setHolidaySchedules(holidays)


      const removeBordersStr = settings['border_replacer_remove_borders'] || 'false'
      const removeBordersEnabled = removeBordersStr.toLowerCase() === 'true'
      setRemoveBorders(removeBordersEnabled)

      const seasonModeStr = settings['border_replacer_season_mode'] || 'inherit'
      // Legacy "colors" mode is equivalent to a custom solid style.
      const resolvedSeasonMode: SeasonMode =
        seasonModeStr === 'remove'
          ? 'remove'
          : seasonModeStr === 'custom' || seasonModeStr === 'colors'
            ? 'custom'
            : 'inherit'
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

      const seasonWidthStr = settings['border_replacer_season_width'] || String(width)
      const loadedSeasonWidth = parseInt(seasonWidthStr) || width
      setSeasonWidth(loadedSeasonWidth)

      const loadedSeasonStyle: SeasonStyle = { ...DEFAULT_SEASON_STYLE }
      const seasonStyleStr = settings['border_replacer_season_style'] || ''
      loadedSeasonStyle.style = (['solid', 'gradient', 'image'].includes(seasonStyleStr)
        ? seasonStyleStr
        : 'solid') as BorderStyle
      loadedSeasonStyle.overlayImage = settings['border_replacer_season_overlay_image'] || ''
      loadedSeasonStyle.removeExisting = (settings['border_replacer_season_overlay_remove_existing'] || 'false').toLowerCase() === 'true'
      try {
        const parsed = JSON.parse(settings['border_replacer_season_gradient_colors'] || '[]')
        loadedSeasonStyle.gradientColors = Array.isArray(parsed) ? parsed : []
      } catch {
        loadedSeasonStyle.gradientColors = []
      }
      const seasonGradDir = settings['border_replacer_season_gradient_direction'] || 'vertical'
      loadedSeasonStyle.gradientDirection = (['vertical', 'horizontal', 'diagonal'].includes(seasonGradDir)
        ? seasonGradDir
        : 'vertical') as GradientDirection
      const seasonInner = settings['border_replacer_season_inner_effect'] || 'none'
      loadedSeasonStyle.innerEffect = (['none', 'glow', 'fade'].includes(seasonInner) ? seasonInner : 'none') as InnerEffect
      loadedSeasonStyle.innerColor = settings['border_replacer_season_inner_color'] || '#000000'
      loadedSeasonStyle.innerOpacity = parseIntOr(settings['border_replacer_season_inner_opacity'], 70)
      loadedSeasonStyle.innerWidth = parseIntOr(settings['border_replacer_season_inner_width'], 8)
      loadedSeasonStyle.fadeWidth = parseIntOr(settings['border_replacer_season_fade_width'], 8)
      setSeasonStyle(loadedSeasonStyle)

      const styleStr = settings['border_replacer_style'] || 'solid'
      const resolvedStyle = (['solid', 'gradient', 'image', 'remove'].includes(styleStr) ? styleStr : 'solid') as BorderStyle
      setBorderStyle(resolvedStyle)

      const loadedOverlayImage = settings['border_replacer_overlay_image'] || ''
      setOverlayImage(loadedOverlayImage)

      const loadedOverlayRemoveExisting = (settings['border_replacer_overlay_remove_existing'] || 'false').toLowerCase() === 'true'
      setOverlayRemoveExisting(loadedOverlayRemoveExisting)

      const gradientColorsStr = settings['border_replacer_gradient_colors'] || '[]'
      let loadedGradientColors: string[] = []
      try {
        loadedGradientColors = JSON.parse(gradientColorsStr)
        loadedGradientColors = Array.isArray(loadedGradientColors) ? loadedGradientColors : []
      } catch {
        loadedGradientColors = []
      }
      setGradientColors(loadedGradientColors)

      const gradientDirStr = settings['border_replacer_gradient_direction'] || 'vertical'
      const resolvedGradientDir = (['vertical', 'horizontal', 'diagonal'].includes(gradientDirStr)
        ? gradientDirStr
        : 'vertical') as GradientDirection
      setGradientDirection(resolvedGradientDir)

      const innerEffectStr = settings['border_replacer_inner_effect'] || 'none'
      const resolvedInnerEffect = (['none', 'glow', 'fade'].includes(innerEffectStr)
        ? innerEffectStr
        : 'none') as InnerEffect
      setInnerEffect(resolvedInnerEffect)

      const loadedInnerColor = settings['border_replacer_inner_color'] || '#000000'
      setInnerColor(loadedInnerColor)
      const loadedInnerOpacity = parseIntOr(settings['border_replacer_inner_opacity'], 70)
      setInnerOpacity(loadedInnerOpacity)
      const loadedInnerWidth = parseIntOr(settings['border_replacer_inner_width'], 8)
      setInnerWidth(loadedInnerWidth)
      const loadedFadeWidth = parseIntOr(settings['border_replacer_fade_width'], 8)
      setFadeWidth(loadedFadeWidth)

      const loadedPlexRules = parsePlexRules(settings['border_replacer_plex_rules'])
      setPlexRules(loadedPlexRules)

      // Absent setting (never saved) defaults to all run types on; an explicitly
      // saved empty string is respected as "all off".
      const runTypesRaw = settings['border_replacer_rule_run_types']
      const loadedRuleRunTypes: RuleRunType[] =
        runTypesRaw === undefined || runTypesRaw === null
          ? [...RULE_RUN_TYPES]
          : runTypesRaw
              .split(',')
              .map((t) => t.trim())
              .filter((t): t is RuleRunType => (RULE_RUN_TYPES as readonly string[]).includes(t))
      setRuleRunTypes(loadedRuleRunTypes)

      let loadedRuleLibraries: string[] = []
      try {
        const parsed = JSON.parse(settings['border_replacer_rule_libraries'] || '[]')
        loadedRuleLibraries = Array.isArray(parsed) ? parsed.map((x) => String(x)) : []
      } catch {
        loadedRuleLibraries = []
      }
      setRuleLibraries(new Set(loadedRuleLibraries))

      originalBorderSettingsRef.current = {
        colors: [...colors],
        width,
        bandWidth: loadedBandWidth,
        mode,
        autoRunBorder: autoRun,
        autoRunCleanup: autoRunCleanupVal,
        cleanupDeleteUnknown: cleanupDeleteUnknownVal,
        holidaySchedules: holidays,
        removeBorders: removeBordersEnabled,
        seasonMode: resolvedSeasonMode,
        seasonColors: [...loadedSeasonColors],
        seasonWidth: loadedSeasonWidth,
        seasonStyle: { ...loadedSeasonStyle },
        borderStyle: resolvedStyle,
        overlayImage: loadedOverlayImage,
        overlayRemoveExisting: loadedOverlayRemoveExisting,
        gradientColors: [...loadedGradientColors],
        gradientDirection: resolvedGradientDir,
        innerEffect: resolvedInnerEffect,
        innerColor: loadedInnerColor,
        innerOpacity: loadedInnerOpacity,
        innerWidth: loadedInnerWidth,
        fadeWidth: loadedFadeWidth,
        plexRules: loadedPlexRules,
        ruleRunTypes: loadedRuleRunTypes,
        ruleLibraries: loadedRuleLibraries,
      }
    } catch (error) {
      console.error('Error fetching border settings:', error)
    } finally {
      setBorderSettingsLoaded(true)
    }
  }

  const saveBorderSettings = async () => {
    // No "must be configured" guard: an empty config is valid and the border
    // replacer falls back to removing borders when no color/style is set.
    try {
      setSaving(true)
      await saveBulkSettings({
        border_replacer_colors: JSON.stringify(borderColors),
        border_replacer_width: borderWidth.toString(),
        border_replacer_band_width: bandWidth.toString(),
        border_replacer_mode: borderMode,
        auto_run_border: autoRunBorder ? 'true' : 'false',
        auto_run_cleanup: autoRunCleanup ? 'true' : 'false',
        cleanup_delete_unknown: cleanupDeleteUnknown ? 'true' : 'false',
        border_replacer_holidays: JSON.stringify(serializeHolidays(holidaySchedules)),
        border_replacer_remove_borders: removeBorders ? 'true' : 'false',
        border_replacer_season_mode: seasonMode,
        border_replacer_season_colors: JSON.stringify(seasonColors),
        border_replacer_season_width: seasonWidth.toString(),
        border_replacer_season_style: seasonStyle.style,
        border_replacer_season_overlay_image: seasonStyle.overlayImage,
        border_replacer_season_overlay_remove_existing: seasonStyle.removeExisting ? 'true' : 'false',
        border_replacer_season_gradient_colors: JSON.stringify(seasonStyle.gradientColors),
        border_replacer_season_gradient_direction: seasonStyle.gradientDirection,
        border_replacer_season_inner_effect: seasonStyle.innerEffect,
        border_replacer_season_inner_color: seasonStyle.innerColor,
        border_replacer_season_inner_opacity: seasonStyle.innerOpacity.toString(),
        border_replacer_season_inner_width: seasonStyle.innerWidth.toString(),
        border_replacer_season_fade_width: seasonStyle.fadeWidth.toString(),
        border_replacer_style: borderStyle,
        border_replacer_overlay_image: overlayImage,
        border_replacer_overlay_remove_existing: overlayRemoveExisting ? 'true' : 'false',
        border_replacer_gradient_colors: JSON.stringify(gradientColors),
        border_replacer_gradient_direction: gradientDirection,
        border_replacer_inner_effect: innerEffect,
        border_replacer_inner_color: innerColor,
        border_replacer_inner_opacity: innerOpacity.toString(),
        border_replacer_inner_width: innerWidth.toString(),
        border_replacer_fade_width: fadeWidth.toString(),
        border_replacer_plex_rules: JSON.stringify(serializePlexRules(plexRules)),
        border_replacer_rule_run_types: ruleRunTypes.join(','),
        border_replacer_rule_libraries: JSON.stringify(Array.from(ruleLibraries)),
      })

      originalBorderSettingsRef.current = {
        colors: [...borderColors],
        width: borderWidth,
        bandWidth,
        mode: borderMode,
        autoRunBorder,
        autoRunCleanup,
        cleanupDeleteUnknown,
        holidaySchedules,
        removeBorders,
        seasonMode,
        seasonColors: [...seasonColors],
        seasonWidth,
        seasonStyle: { ...seasonStyle },
        borderStyle,
        overlayImage,
        overlayRemoveExisting,
        gradientColors: [...gradientColors],
        gradientDirection,
        innerEffect,
        innerColor,
        innerOpacity,
        innerWidth,
        fadeWidth,
        plexRules,
        ruleRunTypes,
        ruleLibraries: Array.from(ruleLibraries),
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
      setNewColor('#ffffff')
    }
  }

  const removeBorderColor = (color: string) => {
    setBorderColors(borderColors.filter(c => c !== color))
  }

  const addGradientColor = () => {
    if (newGradientColor && !gradientColors.includes(newGradientColor)) {
      setGradientColors([...gradientColors, newGradientColor])
      setNewGradientColor('#ffffff')
    }
  }

  const removeGradientColor = (color: string) => {
    setGradientColors(gradientColors.filter(c => c !== color))
  }

  const addHolidaySchedule = (holiday: BorderHolidaySchedule) => {
    const name = holiday.name.trim()
    const schedule = holiday.schedule.trim()
    const colors = holiday.colors.map((color) => color.trim()).filter(Boolean)
    const style = holiday.style
    const hasRenderable =
      (style.style === 'image' && style.overlayImage.trim().length > 0) ||
      (style.style === 'gradient' && style.gradientColors.length > 0)
    // A holiday is valid with solid colors, or a gradient/image style.
    if (!name || !schedule || (colors.length === 0 && !hasRenderable)) {
      return
    }

    setHolidaySchedules((prev) => {
      const filtered = prev.filter((item) => item.name.toLowerCase() !== name.toLowerCase())
      return [...filtered, { name, schedule, colors, style }]
    })
  }

  const removeHolidaySchedule = (name: string) => {
    setHolidaySchedules((prev) => prev.filter((item) => item.name !== name))
  }

  const toggleRuleRunType = (type: RuleRunType) => {
    setRuleRunTypes((prev) => (prev.includes(type) ? prev.filter((t) => t !== type) : [...prev, type]))
  }

  const toggleRuleLibrary = (fullKey: string) => {
    setRuleLibraries((prev) => {
      const next = new Set(prev)
      if (next.has(fullKey)) next.delete(fullKey)
      else next.add(fullKey)
      return next
    })
  }

  const resetBorderSettingsToOriginal = () => {
    if (!originalBorderSettingsRef.current) return

    const original = originalBorderSettingsRef.current
    setBorderColors([...original.colors])
    setBorderWidth(original.width)
    setBandWidth(original.bandWidth)
    setBorderMode(original.mode)
    setAutoRunBorder(original.autoRunBorder)
    setAutoRunCleanup(original.autoRunCleanup)
    setCleanupDeleteUnknown(original.cleanupDeleteUnknown)
    setHolidaySchedules([...original.holidaySchedules])
    setRemoveBorders(original.removeBorders)
    setSeasonMode(original.seasonMode)
    setSeasonColors([...original.seasonColors])
    setSeasonWidth(original.seasonWidth)
    setSeasonStyle({ ...original.seasonStyle })
    setBorderStyle(original.borderStyle)
    setOverlayImage(original.overlayImage)
    setOverlayRemoveExisting(original.overlayRemoveExisting)
    setGradientColors([...original.gradientColors])
    setGradientDirection(original.gradientDirection)
    setInnerEffect(original.innerEffect)
    setInnerColor(original.innerColor)
    setInnerOpacity(original.innerOpacity)
    setInnerWidth(original.innerWidth)
    setFadeWidth(original.fadeWidth)
    setPlexRules([...original.plexRules])
    setRuleRunTypes([...original.ruleRunTypes])
    setRuleLibraries(new Set(original.ruleLibraries))
    setHasUnsavedBorderChanges(false)
  }

  // Reset the Border tab's on-page settings to factory defaults. Leaves auto-run
  // settings alone (they live on other tabs) and does not persist — the change
  // detector flags the Save button so the user chooses whether to keep it.
  const resetBorderSettingsToDefaults = () => {
    setBorderColors([])
    setBorderWidth(26)
    setBandWidth(26)
    setBorderMode('incremental')
    setNewColor('#ffffff')
    setHolidaySchedules([])
    setRemoveBorders(false)
    setSeasonMode('inherit')
    setSeasonColors([])
    setSeasonWidth(26)
    setSeasonStyle({ ...DEFAULT_BORDER_STYLE })
    setBorderStyle(DEFAULT_BORDER_STYLE.style)
    setOverlayImage(DEFAULT_BORDER_STYLE.overlayImage)
    setOverlayRemoveExisting(DEFAULT_BORDER_STYLE.removeExisting)
    setGradientColors([...DEFAULT_BORDER_STYLE.gradientColors])
    setGradientDirection(DEFAULT_BORDER_STYLE.gradientDirection)
    setNewGradientColor('#ffffff')
    setInnerEffect(DEFAULT_BORDER_STYLE.innerEffect)
    setInnerColor(DEFAULT_BORDER_STYLE.innerColor)
    setInnerOpacity(DEFAULT_BORDER_STYLE.innerOpacity)
    setInnerWidth(DEFAULT_BORDER_STYLE.innerWidth)
    setFadeWidth(DEFAULT_BORDER_STYLE.fadeWidth)
    setPlexRules([])
    setRuleRunTypes([...RULE_RUN_TYPES])
    setRuleLibraries(new Set())
  }

  return {
    borderColors,
    borderWidth,
    bandWidth,
    borderMode,
    newColor,
    autoRunBorder,
    autoRunCleanup,
    cleanupDeleteUnknown,
    borderSettingsLoaded,
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
    setBorderWidth,
    setBandWidth,
    setBorderMode,
    setNewColor,
    setAutoRunBorder,
    setAutoRunCleanup,
    setCleanupDeleteUnknown,
    setRemoveBorders,
    setSeasonMode,
    setSeasonWidth,
    setSeasonColors,
    setSeasonStyle,
    setBorderStyle,
    setOverlayImage,
    setOverlayRemoveExisting,
    setGradientDirection,
    setNewGradientColor,
    setInnerEffect,
    setInnerColor,
    setInnerOpacity,
    setInnerWidth,
    setFadeWidth,
    setPlexRules,
    toggleRuleRunType,
    toggleRuleLibrary,
    fetchBorderSettings,
    saveBorderSettings,
    resetBorderSettingsToOriginal,
    resetBorderSettingsToDefaults,
    addBorderColor,
    removeBorderColor,
    addGradientColor,
    removeGradientColor,
    addHolidaySchedule,
    removeHolidaySchedule,
  }
}
