import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Maximize2, Minimize2, Play, Plus, Eraser, RefreshCw, RotateCcw, Trash2, Undo2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import Toolbar from '../components/Toolbar'
import ConfirmDialog from '../components/ConfirmDialog'
import OverlayPreview from '../components/overlays/OverlayPreview'
import PlacementControls from '../components/overlays/PlacementControls'
import OptionFields, { InfoIcon, Toggle } from '../components/overlays/OptionFields'
import ListPicker from '../components/overlays/ListPicker'
import LibrarySelectGrid from '../components/poster-manager/LibrarySelectGrid'
import type { PlexLibraryConfig } from '../api/client'
import { getApiErrorMessage, getJob } from '../api/client'
import {
  getOverlayFonts,
  getOverlayTypes,
  getOverlaysConfig,
  runOverlays,
  saveOverlaysConfig,
  type OverlayFont,
  type OverlayPlacement,
  type OverlayDefinition,
  type OverlayTypeConfig,
  type OverlayTypeInfo,
  type OverlaysConfig,
} from '../api/overlays'
import { getTools, runTool } from '../api/tools'
import { optionsMatch } from '../utils/optionsMatch'
import { useToast } from '../components/Toast'
import './Overlays.css'
import SaveActions from '../components/SaveActions'

const SELECTED_TYPE_KEY = 'posterflow.overlays.selectedType'
const FIRST_ROW_CARDS = 3  // the settings grid is three cards wide

// First-row slots left for a type's own sections after the shared cards (the Preview/Position
// stack, Text, Backdrop); sections beyond them are the second row's cards.
const freeFirstRowSlots = (t: OverlayTypeInfo): number =>
  Math.max(0, FIRST_ROW_CARDS - 1 - (t.hidden_placement.includes('text') ? 0 : 1) - (t.hidden_placement.includes('backdrop') ? 0 : 1))

// Which overlay of a type applies to a library: the one naming it, else one set to all libraries.
const overlayFor = (t: OverlayTypeConfig | undefined, library?: string): OverlayDefinition | undefined =>
  (library ? t?.overlays.find((o) => o.enabled && !o.all_libraries && o.libraries.includes(library)) : undefined) ?? t?.overlays.find((o) => o.enabled && o.all_libraries)

function Overlays() {
  const { showToast } = useToast()
  const [types, setTypes] = useState<OverlayTypeInfo[]>([])
  const [fonts, setFonts] = useState<OverlayFont[]>([])
  const [config, setConfig] = useState<OverlaysConfig | null>(null)
  const [savedJson, setSavedJson] = useState('')
  const [hasTmdbKey, setHasTmdbKey] = useState(true)
  const [libraryConfigs, setLibraryConfigs] = useState<PlexLibraryConfig[]>([])
  const [badgedCount, setBadgedCount] = useState(0)
  const [showLibraries, setShowLibraries] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [selectedType, setSelectedType] = useState<string>(() => {
    try { return localStorage.getItem(SELECTED_TYPE_KEY) || '' } catch { return '' }
  })
  const [variantByType, setVariantByType] = useState<Record<string, string>>({})
  const [assetVersion, setAssetVersion] = useState(0)  // bumped when an image file changes, so the preview refetches
  // Per type: which of its overlays is being edited.
  const [overlayByType, setOverlayByType] = useState<Record<string, number>>({})
  const [largePreview, setLargePreview] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [showServerResetConfirm, setShowServerResetConfirm] = useState(false)
  const [maintenanceRunning, setMaintenanceRunning] = useState(false)
  const [watchedJobs, setWatchedJobs] = useState<number[]>([])  // jobs started here that have not finished (a reset starts one per server type)
  const watchTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  const mounted = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [typesResp, cfgResp, fontsResp] = await Promise.all([getOverlayTypes(), getOverlaysConfig(), getOverlayFonts().catch(() => ({ fonts: [] }))])
      setTypes(typesResp.types)
      setConfig(cfgResp.config)
      setSavedJson(JSON.stringify(cfgResp.config))
      setHasTmdbKey(cfgResp.has_tmdb_key)
      setLibraryConfigs(cfgResp.library_configs ?? [])
      setBadgedCount(cfgResp.badged_count ?? 0)
      setFonts(fontsResp.fonts)
      setSelectedType((prev) => (typesResp.types.some((t) => t.key === prev) ? prev : typesResp.types[0]?.key ?? ''))
    } catch (err) {
      setLoadError(getApiErrorMessage(err, 'Failed to load overlay settings'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    try { if (selectedType) localStorage.setItem(SELECTED_TYPE_KEY, selectedType) } catch { /* ignore */ }
  }, [selectedType])

  const hasUnsaved = config !== null && JSON.stringify(config) !== savedJson
  const current = useMemo(() => types.find((t) => t.key === selectedType) ?? null, [types, selectedType])
  const typeCfg = current && config ? config.types[current.key] : null
  const overlayIndex = current && typeCfg && typeCfg.overlays.length ? Math.min(overlayByType[current.key] ?? 0, typeCfg.overlays.length - 1) : -1
  const currentCfg = typeCfg && overlayIndex >= 0 ? typeCfg.overlays[overlayIndex] : null
  // Another overlay places this badge (Audio on the Resolution line): its own Position and Backdrop are not in use.
  const placementLock = current?.placement_lock && currentCfg && optionsMatch(current.placement_lock.when, currentCfg.options) ? current.placement_lock.note : undefined
  const enabledCount = config ? Object.values(config.types).filter((t) => t.enabled).length : 0
  const tmdbNeeded = Boolean(config) && types.some((t) => t.needs.includes('tmdb') && config?.types[t.key]?.enabled)

  const updateType = (key: string, patch: Partial<OverlaysConfig['types'][string]>) =>
    setConfig((prev) => (prev ? { ...prev, types: { ...prev.types, [key]: { ...prev.types[key], ...patch } } } : prev))
  // Edits land on the overlay of the type that is being edited.
  const updateOverlay = (key: string, patch: Partial<OverlayDefinition>) =>
    setConfig((prev) => {
      if (!prev) return prev
      const t = prev.types[key]
      const index = Math.min(overlayByType[key] ?? 0, t.overlays.length - 1)
      if (index < 0) return prev
      return { ...prev, types: { ...prev.types, [key]: { ...t, overlays: t.overlays.map((o, i) => (i === index ? { ...o, ...patch } : o)) } } }
    })
  // `at` names another overlay of the type than the one being edited (the preview's layers).
  const updatePlacement = (key: string, patch: Partial<OverlayPlacement>, at?: number) =>
    setConfig((prev) => {
      if (!prev) return prev
      const t = prev.types[key]
      const index = Math.min(at ?? overlayByType[key] ?? 0, t.overlays.length - 1)
      if (index < 0) return prev
      return { ...prev, types: { ...prev.types, [key]: { ...t, overlays: t.overlays.map((o, i) => (i === index ? { ...o, placement: { ...o.placement, ...patch } } : o)) } } }
    })
  const updateOptions = (key: string, options: Record<string, unknown>) => updateOverlay(key, { options })
  // Every enabled movie/show library, as "instance:key". An empty selection means all of them.
  const allLibraryKeys = useMemo(
    () => libraryConfigs.flatMap((c) => c.libraries.filter((l) => l.enabled).map((l) => `${c.instance_name}:${l.key}`)),
    [libraryConfigs],
  )
  // What the grid shows as ticked: everything while "All" is on, else the explicit list.
  const selectedLibraries = useMemo(
    () => new Set(config?.all_libraries ? allLibraryKeys : config?.libraries ?? []),
    [config, allLibraryKeys],
  )
  // Unticking one library drops "All" and keeps the rest ticked; ticking the last missing
  // one turns "All" back on so libraries enabled later are included too.
  const toggleLibrary = (fullKey: string) =>
    setConfig((prev) => {
      if (!prev) return prev
      const set = new Set(prev.all_libraries ? allLibraryKeys : prev.libraries)
      if (set.has(fullKey)) set.delete(fullKey); else set.add(fullKey)
      const all = allLibraryKeys.length > 0 && allLibraryKeys.every((k) => set.has(k))
      return { ...prev, all_libraries: all, libraries: all ? [] : Array.from(set).sort() }
    })
  const setAllLibraries = (on: boolean) => setConfig((prev) => (prev ? { ...prev, all_libraries: on, libraries: [] } : prev))

  const handleSave = async () => {
    if (!config) return
    setSaving(true)
    try {
      const result = await saveOverlaysConfig(config)
      setConfig(result.config)
      setSavedJson(JSON.stringify(result.config))
      if (typeof result.badged_count === 'number') setBadgedCount(result.badged_count)
      showToast('Overlay settings saved', 'success')
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to save overlay settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  // Drop every unsaved edit and go back to what the server has.
  const handleCancel = () => {
    if (!savedJson) return
    setConfig(JSON.parse(savedJson))
  }

  // Save + Cancel as a pair, shown at the head and the foot of the overlay card.
  const saveCancel = (
    <>
      <SaveActions dirty={hasUnsaved} saving={saving} disabled={!config} onSave={handleSave} onCancel={handleCancel} />
    </>
  )

  // Refresh the badged count (and the rest of the config response) once a job we started
  // has finished, so the buttons reflect what is on the servers now.
  const refreshCounts = useCallback(async () => {
    try {
      const cfgResp = await getOverlaysConfig()
      setBadgedCount(cfgResp.badged_count ?? 0)
      setLibraryConfigs(cfgResp.library_configs ?? [])
    } catch { /* keep what we have */ }
  }, [])
  const watchJob = useCallback((jobId: number, label: string) => {
    setWatchedJobs((prev) => (prev.includes(jobId) ? prev : [...prev, jobId]))
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        if (!mounted.current) return  // the page is gone: nothing to update, no more polling
        if (['completed', 'failed', 'cancelled'].includes(job.status)) {
          delete watchTimers.current[jobId]
          setWatchedJobs((prev) => prev.filter((id) => id !== jobId))
          await refreshCounts()
          showToast(job.status === 'completed' ? `${label} finished: ${job.message ?? 'done'}` : `${label} ${job.status}${job.error ? `: ${job.error}` : ''}`, job.status === 'completed' ? 'success' : 'error')
          return
        }
      } catch { /* try again */ }
      if (mounted.current) watchTimers.current[jobId] = setTimeout(poll, 3000)
    }
    void poll()
  }, [refreshCounts, showToast])
  useEffect(() => () => { mounted.current = false; Object.values(watchTimers.current).forEach(clearTimeout) }, [])
  const watching = watchedJobs.length > 0

  const handleRun = async (mode: 'incremental' | 'full' | 'rebuild' | 'restore') => {
    setRunning(true)
    try {
      const result = await runOverlays({ mode })
      const label = mode === 'restore' ? 'Remove Overlays' : mode === 'rebuild' ? 'Rebuild All' : 'Overlays Run'
      showToast(`${label} started (Job ID: ${result.job_id})`, 'success')
      watchJob(result.job_id, label)
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to start overlays run'), 'error')
    } finally {
      setRunning(false)
    }
  }

  const handleCleanup = async () => {
    setMaintenanceRunning(true)
    try {
      const result = await runTool('plex_cleanup', { settings: { kinds: ['poster'] } })
      showToast(`Plex Cleanup started (Job ID: ${result.job_id})`, 'success')
      watchJob(result.job_id, 'Plex Cleanup')
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to start Plex cleanup'), 'error')
    } finally {
      setMaintenanceRunning(false)
    }
  }
  const handleServerReset = async () => {
    setShowServerResetConfirm(false)
    setMaintenanceRunning(true)
    try {
      // One reset tool per media server type; run the ones that have a server configured.
      const { groups } = await getTools()
      const resets = groups.flatMap((g) => g.tools).filter((t) => t.id.endsWith('_reset_posters') && t.available)
      if (resets.length === 0) showToast('No media server is configured to reset posters on', 'error')
      for (const tool of resets) {
        const result = await runTool(tool.id, { confirm: true, settings: { kinds: ['poster'] } })
        showToast(`${tool.label} started (Job ID: ${result.job_id})`, 'success')
        watchJob(result.job_id, tool.label)
      }
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to start the poster reset'), 'error')
    } finally {
      setMaintenanceRunning(false)
    }
  }

  const resetCurrentType = () => {
    if (!current) return
    updateOverlay(current.key, { placement: { ...current.default_placement }, options: { ...current.default_options } })
    setShowResetConfirm(false)
    showToast(`${currentCfg?.name || current.label} reset to defaults (not saved yet)`, 'info')
  }

  const variants = useMemo(() => {
    if (!current || !currentCfg) return current?.variants ?? []
    // The list follows the options being edited: a variant standing for a table row shows only
    // while that row is on and sorts by the row's priority; one tied to an option is relabeled
    // or dropped as that option changes. Otherwise the backend's order is kept.
    const row = (v: { group?: string; item?: string }) =>
      v.group && v.item ? ((currentCfg.options[v.group] as Record<string, { enabled?: boolean; priority?: number }> | undefined)?.[v.item]) : undefined
    const kept = current.variants
      .filter((v) => !(v.group && v.item) || row(v)?.enabled !== false)
      .flatMap((v) => {
        if (!v.option) return [v]
        const label = v.labels?.[String(currentCfg.options[v.option] ?? '')]
        return label ? [{ ...v, label }] : []
      })
    const priority = (v: { group?: string; item?: string }) => {
      const p = row(v)?.priority
      return typeof p === 'number' ? p : Number.NEGATIVE_INFINITY
    }
    return kept.some((v) => priority(v) !== Number.NEGATIVE_INFINITY)
      ? kept.map((v, i) => ({ v, i })).sort((a, b) => priority(b.v) - priority(a.v) || a.i - b.i).map((x) => x.v)
      : kept
  }, [current, currentCfg])
  const variant = current ? (variantByType[current.key] && variants.some((v) => v.key === variantByType[current.key]) ? variantByType[current.key] : variants[0]?.key ?? '') : ''
  // Every OTHER enabled type, in the overlay it would use for the libraries being edited here,
  // so the preview shows the whole composition.
  const otherLayers = useMemo(() => {
    if (!config || !current) return { layers: [], overlayAt: {} as Record<string, number> }
    const mine = config.types[current.key]
    const edited = mine?.overlays[Math.min(overlayByType[current.key] ?? 0, Math.max(0, (mine?.overlays.length ?? 1) - 1))]
    const anchor = edited && !edited.all_libraries ? edited.libraries[0] : undefined
    const overlayAt: Record<string, number> = {}  // which overlay of each type is drawn, so a drag on it lands there
    const layers = types
      .filter((t) => t.key !== current.key && config.types[t.key]?.enabled)
      .flatMap((t) => {
        const cfg = overlayFor(config.types[t.key], anchor)
        if (!cfg) return []
        overlayAt[t.key] = config.types[t.key].overlays.indexOf(cfg)
        const chosen = variantByType[t.key]
        const v = chosen && t.variants.some((x) => x.key === chosen) ? chosen : t.variants[0]?.key ?? ''
        return [{ type: t.key, variant: v, placement: cfg.placement, options: cfg.options }]
      })
    return { layers, overlayAt }
  }, [config, current, types, variantByType, overlayByType])
  const typeLabels = useMemo(() => Object.fromEntries(types.map((t) => [t.key, t.label])), [types])

  // ── the overlays of the selected type ──
  const selectOverlay = (key: string, index: number) => setOverlayByType((prev) => ({ ...prev, [key]: index }))
  // The libraries overlays cover (the selection at the top), with titles for the picker; only
  // the kind this type badges (a movies-only type lists no TV library, and the other way round).
  const fits = (libraryType: string) =>
    !current || (libraryType === 'movie' ? current.targets.includes('movie') : current.targets.includes('show') || current.targets.includes('season'))
  const coveredLibraries = libraryConfigs.flatMap((c) =>
    c.libraries
      .filter((l) => l.enabled && selectedLibraries.has(`${c.instance_name}:${l.key}`) && fits(l.type))
      .map((l) => ({ fullKey: `${c.instance_name}:${l.key}`, title: l.title, instance: c.instance_name })),
  )
  const libraryKinds = !current ? 'movie or TV' : current.targets.includes('movie') ? (current.targets.includes('show') ? 'movie or TV' : 'movie') : 'TV'
  const overlayName = (o: OverlayDefinition, index: number) => o.name.trim() || `Overlay ${index + 1}`
  // Which overlay of the selected type names a library (-1 = none; an all-libraries overlay never does).
  const namedBy = (fullKey: string) => (typeCfg?.overlays ?? []).findIndex((o) => !o.all_libraries && o.libraries.includes(fullKey))
  const addOverlay = (key: string) => {
    const info = types.find((t) => t.key === key)
    if (!info) return
    const count = config?.types[key]?.overlays.length ?? 0
    const fresh: OverlayDefinition = {
      name: count === 0 ? info.label : `${info.label} ${count + 1}`,
      enabled: true,
      all_libraries: true,
      libraries: [],
      placement: { ...info.default_placement },
      options: JSON.parse(JSON.stringify(info.default_options)) as Record<string, unknown>,
    }
    setConfig((prev) => (prev ? { ...prev, types: { ...prev.types, [key]: { ...prev.types[key], overlays: [...prev.types[key].overlays, fresh] } } } : prev))
    setOverlayByType((prev) => ({ ...prev, [key]: count }))
  }
  const removeOverlay = (key: string) => {
    if (overlayIndex < 0) return
    setConfig((prev) => (prev ? { ...prev, types: { ...prev.types, [key]: { ...prev.types[key], overlays: prev.types[key].overlays.filter((_, i) => i !== overlayIndex) } } } : prev))
    setOverlayByType((prev) => ({ ...prev, [key]: Math.max(0, overlayIndex - 1) }))
  }
  const toggleOverlayLibrary = (key: string, fullKey: string) => {
    const libs = currentCfg?.libraries ?? []
    updateOverlay(key, { libraries: libs.includes(fullKey) ? libs.filter((k) => k !== fullKey) : [...libs, fullKey].sort() })
  }

  return (
    <div className="page-container overlays-page">
      <div className="page-header overlays-header">
        <h1>Overlays</h1>
        <p>Status and rating badges applied straight onto the posters on your media servers.</p>
      </div>

      <Toolbar
        title="Overlays"
        description={
          <>
            Status badges applied directly to the posters on your media servers. Each overlay type is its own
            module; preview and position it here. Badges go on as <strong>Asset Upload</strong> pushes posters (workflow,
            webhooks, single uploads); stale ones are refreshed by the next upload run, a schedule, or Apply Changes. Badges are composed from your poster files in
            the destination folder, so nothing is downloaded and removing overlays uploads the clean posters again. Items without a
            Posterflow poster are left alone, unless you switch on <strong>Badge items with no Posterflow poster</strong>: those are
            badged on the poster the media server already has, and removing overlays puts the server&rsquo;s own poster back.
          </>
        }
      >
        <button
          className="btn-toolbar"
          onClick={handleCleanup}
          disabled={maintenanceRunning || !config || hasUnsaved}
          title="Run the Clean Up Plex uploads tool: superseded uploaded posters are removed from the Plex bundles (needs the Plex Metadata folders set on the Tools page)"
        >
          <Eraser size={16} /> Clean Up Plex
        </button>
        <button
          className="btn-toolbar btn-danger"
          onClick={() => setShowServerResetConfirm(true)}
          disabled={maintenanceRunning || !config || hasUnsaved}
          title="Put every poster in every enabled library back to the server's own default"
        >
          <RotateCcw size={16} /> Reset Posters
        </button>
        {config && config.enabled && enabledCount > 0 && (
          <>
            <button
              className="btn-toolbar btn-primary"
              onClick={() => handleRun('incremental')}
              disabled={running || hasUnsaved || watching}
              title={hasUnsaved ? 'Save first. Runs use the saved settings.' : 'Badge new items and re-badge any whose status, score or poster changed. Items already current are skipped. Changed settings re-badge everything.'}
            >
              <Play size={16} /> Apply Changes
            </button>
            <button
              className="btn-toolbar"
              onClick={() => handleRun('rebuild')}
              disabled={running || hasUnsaved || watching}
              title={hasUnsaved ? 'Save first. Runs use the saved settings.' : 'Re-compose every badge from its poster file and upload it again, even when nothing changed, and ask TMDB again for every title instead of using the cache. Use it when posters on the servers were changed behind Posterflow\u2019s back, or when TMDB had something wrong.'}
            >
              <RefreshCw size={16} /> Rebuild All
            </button>
          </>
        )}
        <button
          className="btn-toolbar"
          onClick={() => handleRun('restore')}
          disabled={running || !config || hasUnsaved || badgedCount === 0 || watching}
          title={hasUnsaved ? 'Save first. Runs use the saved settings.' : badgedCount === 0 ? 'No item carries a badge' : `Upload the clean poster again for every badged item${config?.enabled ? ' (the next run re-applies badges while overlays are on)' : ''}`}
        >
          <Undo2 size={16} /> {watching ? 'Running…' : badgedCount > 0 ? `Remove All (${badgedCount})` : 'Remove All'}
        </button>
        <SaveActions dirty={hasUnsaved} saving={saving} disabled={!config} onSave={handleSave} onCancel={handleCancel} />
      </Toolbar>

      {loadError && <div className="overlays-error">{loadError}</div>}
      {loading && !config && <div className="overlays-loading">Loading overlay settings…</div>}

      {config && (
        <>
          <section className="overlays-global" aria-label="Overlay settings">
            <div className="overlays-global-main">
              <div className="overlays-global-row">
                <div className="overlays-global-item">
                  <Toggle checked={config.enabled} onChange={(v) => setConfig({ ...config, enabled: v })} label={config.enabled ? 'Overlays are ON' : 'Overlays are OFF'} />
                </div>
                <div className="overlays-global-item">
                  <button type="button" className="overlays-libraries-toggle" onClick={() => setShowLibraries((v) => !v)} aria-expanded={showLibraries}>
                    Libraries: {config.all_libraries ? `all (${allLibraryKeys.length})` : selectedLibraries.size === 0 ? 'none' : `${selectedLibraries.size} of ${allLibraryKeys.length}`}
                    {showLibraries ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                  {badgedCount > 0 && <span className="overlays-badged-count">{badgedCount} item{badgedCount === 1 ? '' : 's'} badged</span>}
                </div>
              </div>
              <div className="overlays-global-row">
                <div className="overlays-global-item overlays-server-posters">
                  <Toggle checked={config.badge_server_posters === true} onChange={(v) => setConfig({ ...config, badge_server_posters: v })} label="Badge items with no Posterflow poster" />
                  <InfoIcon help="Items without a Posterflow poster are badged on the poster your media server already has. Nothing is saved. Removing overlays puts the server's own poster back." />
                </div>
                <div className="overlays-global-item overlays-server-posters">
                  <Toggle checked={config.prune_plex_uploads !== false} onChange={(v) => setConfig({ ...config, prune_plex_uploads: v })} label="Clean up Plex uploads after badging" />
                  <InfoIcon help="After a badge goes up, the superseded uploads are removed from the item's Plex bundle. Needs the Plex Metadata folder set on the Tools page. With no folder set, nothing is touched." />
                </div>
              </div>
            </div>
            <div className="overlays-global-item overlays-global-hint">
              {config.enabled
                ? 'Badges are drawn from your poster files and go straight onto the posters on your media servers: at upload time, on schedules, or with Apply Changes. Your files stay clean on disk, and every run re-checks TMDB for shows still airing or upcoming.'
                : 'Nothing is applied while off. Items that still carry a badge keep it until you click Remove Overlays (or an upload run or schedule), which uploads their clean posters again.'}
            </div>
            <div className="overlays-global-item">
              {hasTmdbKey ? (
                <span className="overlays-source ok" title="Show status and air dates come from TMDB and are refreshed on every run for shows that are still airing or upcoming"><CheckCircle2 size={14} /> TMDB key set</span>
              ) : tmdbNeeded ? (
                <span className="overlays-source warn"><AlertTriangle size={14} /> Add a TMDB API key in <Link to="/settings">Settings</Link> or posters get no badge</span>
              ) : (
                <span className="overlays-source"><AlertTriangle size={14} /> No TMDB key yet. Status badges need one.</span>
              )}
            </div>
          </section>

          {showLibraries && (
            <section className="overlays-libraries" aria-label="Libraries">
              <label className="checkbox-label overlays-libraries-all">
                <input type="checkbox" checked={config.all_libraries} onChange={(e) => setAllLibraries(e.target.checked)} />
                All enabled movie and TV libraries
              </label>
              {allLibraryKeys.length === 0 ? (
                <p className="overlays-empty">No media-server libraries are enabled yet. Set them up under Settings, Media servers.</p>
              ) : (
                <LibrarySelectGrid libraryConfigs={libraryConfigs} selected={selectedLibraries} onToggle={(_i, _k, fullKey) => toggleLibrary(fullKey)} />
              )}
              <small className="overlays-libraries-hint">Untick a library to leave it alone. "All" also covers libraries you enable later.</small>
            </section>
          )}

          <section className="overlays-types" aria-labelledby="overlays-types-title">
            <div className="overlays-types-head">
              <h2 id="overlays-types-title">Overlay Types</h2>
              <span className="overlays-types-hint">Pick a type to edit it. The switch turns it on or off.</span>
            </div>
            <div className="overlays-tabs pf-subtabs" role="tablist" aria-label="Overlay types">
              {types.map((t) => {
                const on = Boolean(config.types[t.key]?.enabled)
                return (
                  <div key={t.key} className={`overlays-tab ${selectedType === t.key ? 'active' : ''} ${on ? 'on' : ''}`}>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={selectedType === t.key}
                      onClick={() => setSelectedType(t.key)}
                    >
                      {t.label}
                    </button>
                    <Toggle checked={on} onChange={(v) => updateType(t.key, { enabled: v })} label={`${t.label} ${on ? 'on' : 'off'}`} visibleLabel={false} />
                  </div>
                )
              })}
              {types.length === 0 && !loading && <span className="overlays-empty">No overlay types installed.</span>}
            </div>
          </section>

          {current && typeCfg && (
            <section className="overlays-editor" role="tabpanel" aria-label={current.label}>
              <div className="overlays-editor-head">
                <div className="overlays-editor-title">
                  <h2>{current.label}</h2>
                  <span className="overlays-editor-desc">{current.description}</span>
                </div>
                <div className="overlays-editor-actions">
                  {currentCfg && (
                    <button type="button" className="btn-toolbar btn-toolbar-link" onClick={() => setShowResetConfirm(true)}>
                      <RotateCcw size={14} /> Reset to Defaults
                    </button>
                  )}
                  {saveCancel}
                </div>
              </div>
              <div className="overlays-defs">
                <div className="overlays-def-tabs">
                  {typeCfg.overlays.length > 0 && (
                    <div className="overlays-def-tablist pf-subtabs" role="tablist" aria-label={`${current.label} overlays`}>
                      {typeCfg.overlays.map((o, i) => (
                        <div key={i} className={`overlays-tab ${overlayIndex === i ? 'active' : ''} ${o.enabled ? 'on' : ''}`}>
                          <button type="button" role="tab" aria-selected={overlayIndex === i} onClick={() => selectOverlay(current.key, i)}>
                            {overlayName(o, i)}
                          </button>
                          <Toggle
                            checked={o.enabled}
                            onChange={(v) => setConfig((prev) => (prev ? { ...prev, types: { ...prev.types, [current.key]: { ...prev.types[current.key], overlays: prev.types[current.key].overlays.map((x, j) => (j === i ? { ...x, enabled: v } : x)) } } } : prev))}
                            label={`${overlayName(o, i)} ${o.enabled ? 'on' : 'off'}`}
                            visibleLabel={false}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  <button type="button" className="btn-toolbar btn-toolbar-link overlays-def-add" onClick={() => addOverlay(current.key)} title="Add an overlay of this type: name it and pick the libraries it applies to">
                    <Plus size={14} /> Add Overlay
                  </button>
                </div>
                {currentCfg ? (
                  <div className="overlays-def-panel" role="group" aria-label="Overlay name and libraries">
                    {/* The rows (name and hint, then the library boxes) beside a Remove button that
                        stays in the card's bottom-right corner whatever the rows do. */}
                    <div className="overlays-def-main">
                    <div className="overlays-def-row">
                      <label className="overlays-def-name">
                        Name
                        <input type="text" value={currentCfg.name} maxLength={60} placeholder={current.label} onChange={(e) => updateOverlay(current.key, { name: e.target.value })} />
                      </label>
                      <span className="overlays-def-hint">
                        {coveredLibraries.length === 0
                          ? (selectedLibraries.size === 0 ? 'No libraries are selected at the top of the page.' : `No ${libraryKinds} library is selected at the top of the page.`)
                          : currentCfg.all_libraries ? 'Applies to every selected library that no other overlay of this type names.' : 'Applies to the ticked libraries only.'}
                      </span>
                    </div>
                    <div className="overlays-def-label">Libraries</div>
                    <div className="overlays-def-row overlays-def-libraries">
                      <label className="overlays-def-pick overlays-def-all">
                        <input type="checkbox" checked={currentCfg.all_libraries} onChange={(e) => updateOverlay(current.key, { all_libraries: e.target.checked })} />
                        All libraries
                      </label>
                      {coveredLibraries.map((lib) => {
                        const owner = namedBy(lib.fullKey)
                        const taken = owner >= 0 && owner !== overlayIndex
                        // With All libraries on, the boxes only show what this overlay covers; they are picked when it is off.
                        const covered = currentCfg.all_libraries ? !taken : currentCfg.libraries.includes(lib.fullKey)
                        const locked = taken || currentCfg.all_libraries
                        return (
                          <label key={lib.fullKey} className={`overlays-def-pick ${locked ? 'disabled' : ''}`} title={taken ? `Used by ${overlayName(typeCfg.overlays[owner], owner)}` : currentCfg.all_libraries ? 'Untick All libraries to pick libraries' : undefined}>
                            <input type="checkbox" checked={covered} disabled={locked} onChange={() => toggleOverlayLibrary(current.key, lib.fullKey)} />
                            {lib.title}
                            <small>{lib.instance}</small>
                          </label>
                        )
                      })}
                    </div>
                    </div>
                    <button type="button" className="btn-toolbar btn-toolbar-link overlays-def-remove" onClick={() => removeOverlay(current.key)}>
                      <Trash2 size={14} /> Remove Overlay
                    </button>
                  </div>
                ) : (
                  <p className="overlays-empty">No {current.label} overlays yet. Add one, name it and pick the libraries it applies to.</p>
                )}
              </div>
              {currentCfg && (
                <>
              <div className={`overlays-editor-body ${largePreview ? 'large' : ''}`}>
                <OverlayPreview
                  type={current.key}
                  variant={variant}
                  placement={currentCfg.placement}
                  options={currentCfg.options}
                  onPlacementChange={(patch) => updatePlacement(current.key, patch)}
                  enabled={typeCfg.enabled && currentCfg.enabled}
                  large={largePreview}
                  others={otherLayers.layers}
                  labels={typeLabels}
                  onLayerPlacementChange={(key, patch) => updatePlacement(key, patch, otherLayers.overlayAt[key])}
                  refreshKey={assetVersion}
                />
                <div className="overlay-panels">
                  {/* One grid cell: the preview card stacked on the Position card (which fills the rest of the row's height). */}
                  <div className="overlay-panel-stack">
                    <div className="overlay-panel overlay-preview-panel">
                      <h3>Preview</h3>
                      <div className="overlay-preview-panel-row">
                        <div className="field-group overlay-preview-state">
                          {/* One picker per type, drawing its own list: the browser's menu for a long list (Resolution has 39 states) can run out of room on a small screen and shut. */}
                          <ListPicker
                            key={current.key}
                            id="ov-preview-state"
                            ariaLabel="Preview State"
                            title="Preview state"
                            value={variant}
                            options={variants.map((v) => ({ value: v.key, label: v.label }))}
                            onChange={(key) => setVariantByType((prev) => ({ ...prev, [current.key]: key }))}
                          />
                        </div>
                        <button type="button" className="btn-toolbar btn-toolbar-link btn-xs overlays-preview-size" onClick={() => setLargePreview((v) => !v)}>
                          {largePreview ? <Minimize2 size={12} /> : <Maximize2 size={12} />} {largePreview ? 'Smaller Preview' : 'Larger Preview'}
                        </button>
                      </div>
                    </div>
                    <PlacementControls part="position" placement={currentCfg.placement} fonts={fonts} hidden={current.hidden_placement} lockedNote={placementLock} onChange={(patch) => updatePlacement(current.key, patch)} />
                  </div>
                  {/* Then Text, the type's sections that fit the first row, Backdrop, and the rest as the second row's fixed-height cards. */}
                  <PlacementControls part="text" placement={currentCfg.placement} fonts={fonts} hidden={current.hidden_placement} onChange={(patch) => updatePlacement(current.key, patch)} />
                  {/* Keyed by type: a type's cards never start from another type's field state (a picked badge, a half-typed number). */}
                  <OptionFields key={`own-${current.key}`} render="fields" part="own" cardsFirst={freeFirstRowSlots(current)} schema={current.schema} options={currentCfg.options} onChange={(options) => updateOptions(current.key, options)} idPrefix={`ov-${current.key}`} typeKey={current.key} onAssetsChanged={() => setAssetVersion((v) => v + 1)} />
                  <PlacementControls part="backdrop" placement={currentCfg.placement} fonts={fonts} hidden={current.hidden_placement} lockedNote={placementLock} onChange={(patch) => updatePlacement(current.key, patch)} />
                  <OptionFields key={`merged-${current.key}`} render="fields" part="merged" cardsFirst={freeFirstRowSlots(current)} schema={current.schema} options={currentCfg.options} onChange={(options) => updateOptions(current.key, options)} idPrefix={`ov-${current.key}`} typeKey={current.key} onAssetsChanged={() => setAssetVersion((v) => v + 1)} />
                </div>
              </div>
              {current.schema.some((f) => f.kind === 'states') && (
                <section className="overlays-states-section">
                  <OptionFields key={`states-${current.key}`} render="states" schema={current.schema} options={currentCfg.options} onChange={(options) => updateOptions(current.key, options)} idPrefix={`ov-${current.key}`} backdropEnabled={currentCfg.placement.back_enabled !== false} defaults={current.default_options} />
                </section>
              )}
                </>
              )}
              <div className="overlays-editor-foot">
                {saveCancel}
              </div>
            </section>
          )}
        </>
      )}

      <ConfirmDialog
        isOpen={showServerResetConfirm}
        title="Reset Posters to the Server Defaults?"
        message="Every movie, show, season and collection in every enabled library, not only the ones overlays cover, goes back to the poster Plex or Jellyfin fetches for itself. Badges and the posters Posterflow uploaded are discarded, and their upload records are dropped so the next upload run pushes your posters again, badged where overlays apply. This cannot be undone."
        confirmText="Yes, reset them"
        variant="danger"
        onConfirm={handleServerReset}
        onCancel={() => setShowServerResetConfirm(false)}
      />

      <ConfirmDialog
        isOpen={showResetConfirm}
        title="Reset Overlay to Defaults?"
        message={`Position, style and options for “${current?.label ?? ''}” go back to their defaults. Nothing is saved until you click Save.`}
        confirmText="Reset"
        variant="warning"
        onConfirm={resetCurrentType}
        onCancel={() => setShowResetConfirm(false)}
      />
    </div>
  )
}

export default Overlays
