import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Check, CircleHelp, Clapperboard, Clapperboard as MovieIcon, FolderOpen, Info, Monitor, Paintbrush, Play, Plus, Save, Search, SlidersHorizontal, Sparkles, Trash2, Tv } from 'lucide-react'
import {
  getApiErrorMessage,
  Drive,
  checkTmdbPosterAvailability,
  getDrives,
  getMakerMonitorConfig,
  getMakerMonitorLastResult,
  getSettings,
  MakerMonitorConfig,
  MakerMonitorRunResponse,
  PosterAvailability,
  runMakerMonitor,
  saveBulkSettings,
  saveMakerMonitorConfig,
  searchTmdb,
  TmdbSearchFilter,
  TmdbSearchResult,
} from '../api/client'
import TmdbItemCard, { derivePsdConfig } from '../components/maker-tools/TmdbItemCard'
import { useToast } from '../components/Toast'
import { useUnmatched } from '../contexts/UnmatchedContext'
import './MakerTools.css'

type ResultTab = string
type DiscoveryTab = 'series' | 'movies'
type MainTab = 'monitor' | 'tmdb-search'

const DEFAULT_MONITOR_CONFIG: MakerMonitorConfig = {
  tmdb_api_key: '',
  lookahead_days: 21,
  missing_retention_days: 2,
  drive_ids: [],
  enable_discovery: true,
  discovery_popularity: 1,
  discovery_vote_count: 0,
  discovery_max_results: 25,
  discovery_languages: ['en', 'ko', 'ja', 'zh', 'es'],
}

const cloneMonitorConfig = (value: MakerMonitorConfig): MakerMonitorConfig => ({
  tmdb_api_key: String(value.tmdb_api_key || ''),
  lookahead_days: Number(value.lookahead_days || 21),
  missing_retention_days: Number.isFinite(Number(value.missing_retention_days)) ? Math.max(0, Number(value.missing_retention_days)) : 2,
  drive_ids: Array.isArray(value.drive_ids)
    ? value.drive_ids.map((driveId) => Number(driveId)).filter((driveId) => Number.isFinite(driveId) && driveId > 0)
    : [],
  enable_discovery: Boolean(value.enable_discovery),
  discovery_popularity: Number.isFinite(Number(value.discovery_popularity)) ? Number(value.discovery_popularity) : 1,
  discovery_vote_count: Number.isFinite(Number(value.discovery_vote_count)) ? Number(value.discovery_vote_count) : 0,
  discovery_max_results: Number.isFinite(Number(value.discovery_max_results)) ? Number(value.discovery_max_results) : 25,
  discovery_languages: Array.isArray(value.discovery_languages)
    ? value.discovery_languages.map((item) => String(item).trim().toLowerCase()).filter(Boolean)
    : ['en', 'ko', 'ja', 'zh', 'es'],
})

function MakerTools() {
  const navigate = useNavigate()
  const location = useLocation()
  const [activeTab, setActiveTab] = useState<MainTab>('tmdb-search')
  const [drives, setDrives] = useState<Drive[]>([])
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [startedJobId, setStartedJobId] = useState<number | null>(null)
  const [config, setConfig] = useState<MakerMonitorConfig>(DEFAULT_MONITOR_CONFIG)
  const [modalConfig, setModalConfig] = useState<MakerMonitorConfig>(DEFAULT_MONITOR_CONFIG)
  const [showConfigModal, setShowConfigModal] = useState(false)
  const [result, setResult] = useState<MakerMonitorRunResponse | null>(null)
  const [resultTab, setResultTab] = useState<ResultTab>('')
  const [discoveryTab, setDiscoveryTab] = useState<DiscoveryTab>('series')
  const [modalDiscoveryLanguagesInput, setModalDiscoveryLanguagesInput] = useState('en, ko, ja, zh, es')
  const [tmdbQuery, setTmdbQuery] = useState('')
  const [tmdbFilter, setTmdbFilter] = useState<TmdbSearchFilter>('all')
  const [tmdbSearching, setTmdbSearching] = useState(false)
  const [tmdbResults, setTmdbResults] = useState<TmdbSearchResult[] | null>(null)
  const [tmdbError, setTmdbError] = useState<string | null>(null)
  const [tmdbHelpExpanded, setTmdbHelpExpanded] = useState(false)
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  // PSD export settings (used in config modal and passed to TmdbItemCard)
  const [psdExportFolder, setPsdExportFolder] = useState('')
  const [psdTemplatePath, setPsdTemplatePath] = useState('')
  const [psdOpenPhotopea, setPsdOpenPhotopea] = useState(false)
  const [psdPosterFitBorder, setPsdPosterFitBorder] = useState(false)
  const [psdImageExportFolder, setPsdImageExportFolder] = useState('')
  const [showPsdConfigModal, setShowPsdConfigModal] = useState(false)
  const { showToast } = useToast()

  const { jobs } = useUnmatched()
  const completionHandledRef = useRef(false)
  const prevIsMonitorJobActiveRef = useRef(false)
  const tmdbCacheRef = useRef<Map<string, TmdbSearchResult[]>>(new Map())

  const isMonitorJobActive = useMemo(() => {
    return jobs.some((job) => {
      if (job.job_type !== 'maker_monitor') {
        return false
      }
      return job.status === 'pending' || job.status === 'running'
    })
  }, [jobs])

  const startedMonitorJob = useMemo(() => {
    if (!startedJobId) {
      return null
    }
    return jobs.find((job) => job.id === startedJobId) || null
  }, [jobs, startedJobId])

  const applyMonitorResult = (monitorResult: MakerMonitorRunResponse) => {
    setResult(monitorResult)

    if (monitorResult.libraries.length > 0) {
      const first = monitorResult.libraries[0]
      setResultTab(`lib-${first.library_name}-${first.library_type}`)
    } else if ((monitorResult.discovery?.shows.length || 0) + (monitorResult.discovery?.movies.length || 0) > 0) {
      setResultTab('discovery')
    }
  }

  const refreshMonitorLastResult = async () => {
    const lastResult = await getMakerMonitorLastResult()
    if (lastResult && typeof lastResult === 'object' && 'libraries' in lastResult) {
      applyMonitorResult(lastResult as MakerMonitorRunResponse)
    }
  }

  useEffect(() => {
    const load = async () => {
      try {
        setLoading(true)
        const [fetched, driveList] = await Promise.all([
          getMakerMonitorConfig(),
          getDrives(),
        ])
        const normalizedConfig = cloneMonitorConfig(fetched)
        setConfig(normalizedConfig)
        setModalConfig(normalizedConfig)
        setModalDiscoveryLanguagesInput(normalizedConfig.discovery_languages.join(', '))
        setDrives(driveList)

        try {
          await refreshMonitorLastResult()
        } catch {
          // Non-blocking: page still works without persisted result
        }
      } catch (error) {
        showToast(getApiErrorMessage(error, 'Failed to load Maker Monitor config'), 'error')
      } finally {
        setLoading(false)
      }
    }
    void load()
  }, [showToast])

  useEffect(() => {
    getSettings().then((settings) => {
      const cfg = derivePsdConfig(settings)
      setPsdExportFolder(cfg.exportFolder)
      setPsdTemplatePath(cfg.templatePath)
      setPsdOpenPhotopea(cfg.openPhotopea)
      setPsdImageExportFolder(cfg.imageExportFolder)
      setPsdPosterFitBorder((settings.psd_poster_fit_border || '').trim().toLowerCase() === 'true')
    }).catch(() => {
      // Non-blocking: page still works with empty defaults
    })
  }, [])

  const addDriveSelection = () => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids.push(0)
      return next
    })
  }

  const removeDriveSelection = (index: number) => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids = next.drive_ids.filter((_, itemIndex) => itemIndex !== index)
      return next
    })
  }

  const updateDriveSelection = (index: number, selectedDriveId: number) => {
    setModalConfig((previous) => {
      const next = cloneMonitorConfig(previous)
      next.drive_ids[index] = selectedDriveId
      return next
    })
  }

  const openConfigModal = () => {
    const normalized = cloneMonitorConfig(config)
    setModalConfig(normalized)
    setModalDiscoveryLanguagesInput(normalized.discovery_languages.join(', '))
    setShowConfigModal(true)
  }

  const closeConfigModal = () => {
    setShowConfigModal(false)
  }

  const openPsdConfigModal = () => setShowPsdConfigModal(true)
  const closePsdConfigModal = () => setShowPsdConfigModal(false)

  const handleSavePsdConfig = async () => {
    try {
      setSaving(true)
      await saveBulkSettings({
        psd_export_folder: psdExportFolder.trim(),
        psd_template_path: psdTemplatePath.trim(),
        psd_open_photopea: String(psdOpenPhotopea),
        psd_poster_fit_border: String(psdPosterFitBorder),
        psd_image_export_folder: psdImageExportFolder.trim(),
      })
      showToast('PSD settings saved', 'success')
      setShowPsdConfigModal(false)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save PSD settings'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleSaveConfig = async () => {
    try {
      setSaving(true)
      const normalizedPayload = cloneMonitorConfig(modalConfig)
      const saved = await saveMakerMonitorConfig(normalizedPayload)
      const normalized = cloneMonitorConfig(saved)
      setConfig(normalized)
      setModalConfig(normalized)
      setModalDiscoveryLanguagesInput(normalized.discovery_languages.join(', '))
      setShowConfigModal(false)
      showToast('Monitor configuration saved', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to save monitor configuration'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleRunMonitor = async () => {
    if (isMonitorJobActive) {
      showToast('Monitor is already running', 'error')
      return
    }

    try {
      setRunning(true)
      completionHandledRef.current = false
      const response = await runMakerMonitor({ config, save_config: true })
      setStartedJobId(response.job_id)
      showToast(response.message || 'Monitor scan queued in background', 'success')
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to run monitor scan'), 'error')
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => {
    if (!startedMonitorJob || completionHandledRef.current) {
      return
    }

    if (startedMonitorJob.status === 'completed') {
      completionHandledRef.current = true
      void (async () => {
        try {
          await refreshMonitorLastResult()
        } catch {
          // non-blocking; preserve current UI result if fetch fails
        } finally {
          showToast('Monitor scan completed', 'success')
          setStartedJobId(null)
        }
      })()
      return
    }

    if (startedMonitorJob.status === 'failed') {
      completionHandledRef.current = true
      showToast(startedMonitorJob.error || 'Monitor scan failed', 'error')
      setStartedJobId(null)
    }
  }, [showToast, startedMonitorJob])

  useEffect(() => {
    const wasActive = prevIsMonitorJobActiveRef.current
    prevIsMonitorJobActiveRef.current = isMonitorJobActive

    if (wasActive && !isMonitorJobActive) {
      void refreshMonitorLastResult().catch(() => {})
    }
  }, [isMonitorJobActive])

  const sortedLibraryResults = useMemo(() => {
    if (!result) {
      return []
    }
    return [...result.libraries].sort((left, right) => left.library_name.localeCompare(right.library_name))
  }, [result])

  const availableDrives = useMemo(() => {
    return drives
      .filter((drive) => !drive.is_deprecated)
      .sort((left, right) => left.name.localeCompare(right.name))
  }, [drives])

  const selectedDriveIdSet = useMemo(() => {
    return new Set(modalConfig.drive_ids.filter((driveId) => driveId > 0))
  }, [modalConfig.drive_ids])

  const discoveryTotals = useMemo(() => {
    const shows = result?.discovery?.shows || []
    const movies = result?.discovery?.movies || []
    return {
      shows,
      movies,
      total: shows.length + movies.length,
    }
  }, [result])

  const activeResultTab = useMemo(() => {
    if (!result) {
      return ''
    }

    if (resultTab) {
      return resultTab
    }

    if (sortedLibraryResults.length > 0) {
      return `lib-${sortedLibraryResults[0].library_name}`
    }

    if (discoveryTotals.total > 0) {
      return 'discovery'
    }

    return ''
  }, [discoveryTotals.total, result, resultTab, sortedLibraryResults])

  const updateDiscoveryLanguages = (rawValue: string) => {
    setModalDiscoveryLanguagesInput(rawValue)
    const languages = rawValue
      .split(',')
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)

    setModalConfig((previous) => ({
      ...previous,
      discovery_languages: Array.from(new Set(languages)),
    }))
  }

  const handleTmdbSearch = async (queryOverride?: string, filterOverride?: TmdbSearchFilter) => {
    const query = (queryOverride ?? tmdbQuery).trim()
    if (!query) return
    const filter = filterOverride ?? tmdbFilter
    const cacheKey = `${filter}::${query.toLowerCase()}`
    const cached = tmdbCacheRef.current.get(cacheKey)
    if (cached) {
      setTmdbResults(cached)
      setTmdbError(null)
      void fetchPosterAvailability(cached)
      return
    }
    setTmdbSearching(true)
    setTmdbError(null)
    setTmdbResults(null)
    setPosterAvailability({})
    try {
      const results = await searchTmdb(query, filter)
      tmdbCacheRef.current.set(cacheKey, results)
      setTmdbResults(results)
      void fetchPosterAvailability(results)
    } catch (error) {
      setTmdbError(getApiErrorMessage(error, 'Search failed'))
    } finally {
      setTmdbSearching(false)
    }
  }

  const fetchPosterAvailability = async (results: TmdbSearchResult[]) => {
    if (results.length === 0) return
    try {
      const items = results.map((r) => ({ tmdb_id: r.tmdb_id, title: r.title, year: r.year, media_type: r.media_type }))
      const availability = await checkTmdbPosterAvailability(items)
      setPosterAvailability(availability)
    } catch {
      // Non-critical — silently ignore errors
    }
  }

  // Handle incoming navigation state from other pages (e.g. Poster Manager → Maker Tools search)
  useEffect(() => {
    const state = location.state as { tmdbSearch?: string } | null
    if (state?.tmdbSearch) {
      const query = state.tmdbSearch
      setTmdbQuery(query)
      setActiveTab('tmdb-search')
      void handleTmdbSearch(query)
      navigate(location.pathname, { replace: true, state: null })
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSearchOnTmdbTab = (name: string, date?: string) => {
    const year = date?.slice(0, 4) ?? ''
    const query = `${name} ${year}`.trim()
    setTmdbQuery(query)
    setActiveTab('tmdb-search')
    void handleTmdbSearch(query)
  }

  const handleTmdbFilterChange = (filter: TmdbSearchFilter) => {
    setTmdbFilter(filter)
    if (tmdbQuery.trim()) {
      void handleTmdbSearch(undefined, filter)
    }
  }

  const openSchedulingSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'scheduling')
    navigate('/settings')
  }

  const openNotificationSettings = () => {
    localStorage.setItem('posterflow.settings.activeTab', 'notifications')
    navigate('/settings')
  }

  const groupedDiscoveryItems = useMemo(() => {
    const source = discoveryTab === 'series' ? discoveryTotals.shows : discoveryTotals.movies
    const grouped = new Map<string, typeof source>()

    source
      .slice()
      .sort((left, right) => String(left.date || '').localeCompare(String(right.date || '')))
      .forEach((item) => {
        const language = String(item.language || 'EN').toUpperCase()
        const existing = grouped.get(language) || []
        existing.push(item)
        grouped.set(language, existing)
      })

    return grouped
  }, [discoveryTab, discoveryTotals.movies, discoveryTotals.shows])

  return (
    <div className="page-container maker-tools-page">
      <div className="maker-header">
        <h1>Maker Tools</h1>
        <p>Independent maker workflow tools and utilities.</p>
      </div>

      <div className="maker-tools-tabs" role="tablist" aria-label="Maker tools tabs">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'tmdb-search'}
          className={activeTab === 'tmdb-search' ? 'active' : ''}
          onClick={() => setActiveTab('tmdb-search')}
        >
          <Search size={16} /> TMDB Search
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'monitor'}
          className={activeTab === 'monitor' ? 'active' : ''}
          onClick={() => setActiveTab('monitor')}
        >
          <Monitor size={16} /> Monitor
        </button>
      </div>

      {activeTab === 'monitor' && (
      <div className="maker-tools-panel">
        <div className="toolbar">
          <div className="toolbar-title">
            <h2>Season Premieres Monitor</h2>
            <div className="toolbar-info">
              <Info size={16} />
              <div className="toolbar-tooltip">Tracks upcoming season premieres and highlights missing posters. Results appear below and persist after refresh.</div>
            </div>
          </div>
          <div className="action-buttons">
            <div className="btn-pair">
              <button className="btn-toolbar btn-toolbar-link" type="button" onClick={openSchedulingSettings} disabled={saving || loading || running || isMonitorJobActive}>
                Scheduling
              </button>
              <button className="btn-toolbar btn-toolbar-link" type="button" onClick={openNotificationSettings} disabled={saving || loading || running || isMonitorJobActive}>
                Discord
              </button>
            </div>
            <button className="btn-toolbar" type="button" onClick={openConfigModal} disabled={saving || loading || running || isMonitorJobActive}>
              <SlidersHorizontal size={16} /> Configure
            </button>
            <button className="btn-toolbar btn-primary" type="button" onClick={handleRunMonitor} disabled={saving || loading || running || isMonitorJobActive}>
              <Play size={16} /> {(running || isMonitorJobActive) ? 'Running...' : 'Run Monitor'}
            </button>
          </div>
        </div>

        {result && (
          <div className="maker-results">
            <p className="maker-range">Range: {result.range_start} → {result.range_end}</p>

            <div className="maker-result-tabs" role="tablist" aria-label="Monitor result tabs">
              {sortedLibraryResults.map((libraryResult) => {
                const tabKey = `lib-${libraryResult.library_name}-${libraryResult.library_type}`
                return (
                  <button
                    key={tabKey}
                    type="button"
                    className={activeResultTab === tabKey ? 'active' : ''}
                    onClick={() => setResultTab(tabKey)}
                  >
                    {libraryResult.library_name} ({libraryResult.library_type})
                  </button>
                )
              })}

              {discoveryTotals.total > 0 && (
                <button
                  type="button"
                  className={activeResultTab === 'discovery' ? 'active' : ''}
                  onClick={() => setResultTab('discovery')}
                >
                  <Sparkles size={15} /> New Releases
                </button>
              )}
            </div>

            {sortedLibraryResults.map((libraryResult) => {
              const tabKey = `lib-${libraryResult.library_name}-${libraryResult.library_type}`
              if (activeResultTab !== tabKey) {
                return null
              }

              const postersReady = Math.max(0, libraryResult.premieres_found - libraryResult.posters_needed)

              return (
                <div className="maker-result-panel" key={tabKey}>
                  <div className="maker-library-stats">
                    <div className="stat-card"><span>{libraryResult.total_scanned}</span><small>Unique Shows</small></div>
                    <div className="stat-card"><span>{libraryResult.premieres_found}</span><small>Premieres Found</small></div>
                    <div className="stat-card"><span>{libraryResult.posters_needed}</span><small>Posters Needed</small></div>
                    <div className="stat-card"><span>{postersReady}</span><small>Ready to Go</small></div>
                  </div>

                  <div className="maker-show-list full-width">
                    {libraryResult.shows.length === 0 && <p className="muted">No upcoming premieres found in this drive.</p>}

                    {libraryResult.shows
                      .slice()
                      .sort((left, right) => String(left.date || '').localeCompare(String(right.date || '')))
                      .map((show) => (
                        <div className={`maker-show-item ${show.poster_exists ? 'ready' : 'todo'}`} key={`${libraryResult.library_name}-${show.tmdb_id}-${show.season_number}`}>
                          <div className="maker-show-main">
                            <a href={show.homepage} target="_blank" rel="noreferrer">{show.name}{show.first_air_year ? <> <span className="tmdb-result-year">{show.first_air_year}</span></> : ''}</a>
                            <span>{show.season_number === 0 ? 'Specials' : `Season ${show.season_number}`} starts: {show.date}</span>
                          </div>
                          <div className="maker-badges">
                            <span className="badge badge-grey">Season Premiere</span>
                            <span className={`badge ${show.poster_exists ? 'badge-green' : 'badge-orange'}`}>
                              {show.poster_exists ? <Check size={13} /> : <Paintbrush size={13} />}
                              {show.poster_exists ? 'Poster Ready' : 'Needs Poster'}
                            </span>
                            <button
                              type="button"
                              className="maker-tmdb-search-btn"
                              title="Search TMDB tab"
                              onClick={() => handleSearchOnTmdbTab(show.name, show.first_air_year || undefined)}
                            >
                              <Search size={12} /> Maker
                            </button>
                          </div>
                        </div>
                      ))}
                  </div>
                </div>
              )
            })}

            {activeResultTab === 'discovery' && discoveryTotals.total > 0 && (
              <div className="maker-result-panel">
                <div className="maker-library-stats">
                  <div className="stat-card"><span>{discoveryTotals.shows.length}</span><small>New Series</small></div>
                  <div className="stat-card"><span>{discoveryTotals.movies.length}</span><small>New Movies</small></div>
                  <div className="stat-card"><span>{discoveryTotals.total}</span><small>Total Found</small></div>
                </div>

                <div className="maker-subtabs">
                  <button type="button" className={discoveryTab === 'series' ? 'active' : ''} onClick={() => setDiscoveryTab('series')}>
                    <Tv size={15} /> Series
                  </button>
                  <button type="button" className={discoveryTab === 'movies' ? 'active' : ''} onClick={() => setDiscoveryTab('movies')}>
                    <Clapperboard size={15} /> Movies
                  </button>
                </div>

                {Array.from(groupedDiscoveryItems.entries()).map(([language, items]) => (
                  <div className="maker-language-group" key={`${discoveryTab}-${language}`}>
                    <h4>{language}</h4>
                    <div className="maker-show-list full-width">
                      {items.map((item) => (
                        <div className={`maker-show-item ${item.statuses.some((status) => status.have || status.synced) ? 'ready' : 'todo'}`} key={`${discoveryTab}-${item.type}-${item.homepage}`}>
                          <div className="maker-show-main">
                            <div className="maker-show-title-row">
                              <a href={item.homepage} target="_blank" rel="noreferrer">{item.name}{item.date?.slice(0, 4) ? <> <span className="tmdb-result-year">{item.date.slice(0, 4)}</span></> : ''}</a>
                            </div>
                            <span>Release: {item.date || 'Unknown'} • Pop: {Math.round(Number(item.popularity || 0))}</span>
                          </div>

                          <div className="maker-badges wrap">
                            {(() => {
                              const hasAnyFound = item.statuses.some((status) => status.have || status.synced)

                              return item.statuses.map((status) => {
                                if (status.have) {
                                  const sourceLabel = status.have_sources.length > 0 ? ` (${status.have_sources.join(', ')})` : ''
                                  return <span className="badge badge-green" key={`${item.homepage}-${status.type}`}><Check size={13} /> {status.type}{sourceLabel}</span>
                                }
                                if (status.synced) {
                                  const sourceLabel = status.synced_sources.length > 0 ? ` (${status.synced_sources.join(', ')})` : ''
                                  return <span className="badge badge-blue" key={`${item.homepage}-${status.type}`}><Sparkles size={13} /> {status.type}{sourceLabel}</span>
                                }

                                if (hasAnyFound) {
                                  return <span className="badge badge-grey" key={`${item.homepage}-${status.type}`}>{status.type}</span>
                                }

                                return <span className="badge badge-orange" key={`${item.homepage}-${status.type}`}><Paintbrush size={13} /> {status.type}</span>
                              })
                            })()}
                            <button
                              type="button"
                              className="maker-tmdb-search-btn"
                              title="Search TMDB tab"
                              onClick={() => handleSearchOnTmdbTab(item.name, item.date)}
                            >
                              <Search size={12} /> Maker
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      )}

      {activeTab === 'tmdb-search' && (
        <div className="maker-tools-panel">
          <div className="toolbar">
            <div className="toolbar-title">
              <h2>TMDB Search/PSD Export</h2>
              <div className="toolbar-info">
                <Info size={16} />
                <div className="toolbar-tooltip">Search TMDB for movies, TV shows, and collections. Browse posters, logos, and backdrops, then export directly to PSD.</div>
              </div>
            </div>
            <div className="action-buttons">
              <button className="btn-toolbar" type="button" onClick={openPsdConfigModal}>
                <SlidersHorizontal size={16} /> Configure
              </button>
            </div>
          </div>
          <div className="tmdb-search-panel">
            <div className="tmdb-search-help">
              <button
                type="button"
                className="tmdb-search-help-toggle"
                onClick={() => setTmdbHelpExpanded((v) => !v)}
                aria-expanded={tmdbHelpExpanded}
              >
                <Info size={14} />
                <span>How to search &amp; export PSDs</span>
                <span className={`tmdb-help-chevron${tmdbHelpExpanded ? ' expanded' : ''}`}>›</span>
              </button>
              {tmdbHelpExpanded && (
                <div className="tmdb-search-help-body">
                  <p>Search TMDB for movies, TV shows, and collections to look up IDs and metadata.</p>
                  <ul>
                    <li><strong>By title</strong> — <code>The Office</code></li>
                    <li><strong>With year</strong> — <code>The Office (2005)</code> or <code>The Office 2005</code> (narrows results to that release year)</li>
                    <li><strong>Filter by type</strong> — use the All / Movies / TV Shows / Collections buttons below</li>
                    <li><strong>ID chips</strong> — click any TMDB / IMDB / TVDB chip to copy just the ID number</li>
                    <li><strong>Poster</strong> — click the poster thumbnail to enlarge it</li>
                  </ul>
                  <p>To build a PSD file from the search results:</p>
                  <ul>
                    <li><strong>Select images</strong> — click <strong>P</strong> on any poster to add it to your export, or <strong>L</strong> on a logo</li>
                    <li><strong>Backdrops</strong> — switch to the Backdrops tab and click <strong>B</strong> to add a backdrop as a background layer (fit to height, no crop)</li>
                    <li><strong>Logo</strong> — switch to the Logos tab and click <strong>L</strong> to add a logo; it will be placed at the bottom, converted to white, and sized automatically based on its shape and density:
                      <ul>
                        <li><strong>Short/wide logos</strong> — logos with a low projected height are given a wider target width so they don't appear too small</li>
                        <li><strong>Sparse logos</strong> — logos with thin strokes or lots of transparent space are sized up slightly so delicate details remain visible</li>
                        <li><strong>Dense logos</strong> — logos with heavily filled or solid artwork are sized down, with wider logos shrinking more than narrower ones, to avoid an overpowering block of white</li>
                        <li><strong>Hard limits</strong> — no logo will exceed 800px wide; tall logos are also capped in height to stay within the lower third of the canvas</li>
                      </ul>
                    </li>
                    <li><strong>Export PSD</strong> — two export buttons are always available in the gallery toolbar. Selecting images is optional — you can export a blank PSD or open an existing one without selecting anything:
                      <ul>
                        <li><strong>New Export</strong> — always creates a fresh PSD using your configured template (or the built-in default). With no images selected this gives you a blank template to start from. Any file in the export folder with the same name is overwritten from scratch.</li>
                        <li><strong>Use Existing PSD</strong> — opens an already-saved PSD from your export folder and injects any selected poster/logo/backdrop layers into it, preserving all your existing work (borders, text, effects). With no images selected it simply opens the existing file. It must be named <code>{'{'}title{'}'} ({'{'}year{'}'}).psd</code> and placed in the configured export folder. If no matching file is found, a prompt will guide you to either place the file there manually or upload it directly from your computer.</li>
                      </ul>
                    </li>
                    <li><strong>Open in Photopea</strong> — if the "Open in Photopea" toggle is enabled in Configure, the exported PSD will open directly in Photopea in a new tab instead of downloading</li>
                    <li><strong>Multiple posters</strong> — you can add more than one poster; each becomes its own layer in the PSD</li>
                  </ul>
                </div>
              )}
            </div>
            <div className="tmdb-search-bar">
              <Search size={18} className="tmdb-search-icon" />
              <input
                type="text"
                className="tmdb-search-input"
                placeholder="Search movies, TV shows, collections... add (year) to narrow results"
                value={tmdbQuery}
                onChange={(e) => setTmdbQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') void handleTmdbSearch() }}
                autoFocus
              />
              <button
                className="btn-toolbar btn-primary"
                type="button"
                onClick={() => void handleTmdbSearch()}
                disabled={tmdbSearching || !tmdbQuery.trim()}
              >
                {tmdbSearching ? 'Searching...' : 'Search'}
              </button>
            </div>

            <div className="tmdb-filter-bar">
              {(['all', 'movie', 'tv', 'collection'] as TmdbSearchFilter[]).map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`tmdb-filter-btn${tmdbFilter === f ? ' active' : ''}`}
                  onClick={() => handleTmdbFilterChange(f)}
                >
                  {f === 'all' && 'All'}
                  {f === 'movie' && <><MovieIcon size={13} /> Movies</>}
                  {f === 'tv' && <><Tv size={13} /> TV Shows</>}
                  {f === 'collection' && <><FolderOpen size={13} /> Collections</>}
                </button>
              ))}
            </div>

            <p className="tmdb-attribution">
              This product uses the TMDB API but is not endorsed or certified by TMDB.{' '}
              <a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer">themoviedb.org</a>
            </p>

            {tmdbError && <p className="tmdb-error">{tmdbError}</p>}

            {tmdbResults !== null && (
              tmdbResults.length === 0
                ? <p className="tmdb-empty">No results found.</p>
                : (
                  <div className="tmdb-results-grid">
                    {tmdbResults.map((item) => (
                      <TmdbItemCard
                        key={`${item.media_type}-${item.tmdb_id}`}
                        item={item}
                        posterAvailability={posterAvailability[item.tmdb_id]}
                        psdConfig={{ exportFolder: psdExportFolder, templatePath: psdTemplatePath, openPhotopea: psdOpenPhotopea, imageExportFolder: psdImageExportFolder }}
                      />
                    ))}
                  </div>
                )
            )}
          </div>
        </div>
      )}

      {showPsdConfigModal && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>TMDB Search/PSD Export Settings</h2>
              <button className="modal-close" onClick={closePsdConfigModal}>×</button>
            </div>
            <div className="modal-body">
              <div className="maker-card">
                <label>
                  PSD Export Folder
                  <small className="muted" style={{ display: 'block', margin: '0.25rem 0 0.5rem' }}>
                    Optional. When set, exported PSD files are saved here in addition to the browser download.
                    Must be an absolute container-side path (e.g. <code>/config/psd_exports</code>).
                    Leave blank for download-only.
                  </small>
                  <input
                    type="text"
                    value={psdExportFolder}
                    onChange={(e) => setPsdExportFolder(e.target.value)}
                    placeholder="/config/psd_exports"
                  />
                </label>
                <label>
                  Image Export Folder
                  <small className="muted" style={{ display: 'block', margin: '0.25rem 0 0.5rem' }}>
                    Optional. Where images exported from Photopea land — the in-box JPG button and
                    File→Export As / <code>Ctrl+Shift+Alt+S</code>. Set an absolute container-side path
                    (e.g. <code>/config/poster_jpgs</code>) to save there, or leave blank to download
                    images in your browser instead.
                  </small>
                  <input
                    type="text"
                    value={psdImageExportFolder}
                    onChange={(e) => setPsdImageExportFolder(e.target.value)}
                    placeholder="(leave blank to download)"
                  />
                </label>
                <label>
                  PSD Template File
                  <small className="muted" style={{ display: 'block', margin: '0.25rem 0 0.5rem' }}>
                    Override the bundled default PSD template with your own. Provide an absolute
                    container-side path to a <code>.psd</code> file (e.g. <code>/config/my_template.psd</code>).
                    The poster image is injected into the <strong>POSTER</strong> group and the logo into the <strong>LOGO</strong> group.
                    Leave blank to use the built-in default template.
                  </small>
                  <input
                    type="text"
                    value={psdTemplatePath}
                    onChange={(e) => setPsdTemplatePath(e.target.value)}
                    placeholder="/config/template.psd"
                  />
                </label>
                <div className="maker-setting-row">
                  <div>
                    <span style={{ fontWeight: 500 }}>Fit Poster Inside Border</span>
                    <small className="muted" style={{ display: 'block', marginTop: '0.2rem' }}>
                      Default export fills the full canvas. Enable this to scale posters to the canvas width
                      minus a 25px border on each side, preserve aspect ratio, and align the poster 25px
                      from the top edge.
                    </small>
                  </div>
                  <label className="toggle-switch" style={{ flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={psdPosterFitBorder}
                      onChange={(e) => setPsdPosterFitBorder(e.target.checked)}
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>
                <div className="maker-setting-row">
                  <div>
                    <span style={{ fontWeight: 500 }}>Open in Photopea</span>
                    <small className="muted" style={{ display: 'block', marginTop: '0.2rem' }}>
                      When enabled, exported PSD files automatically open in{' '}
                      <a href="https://www.photopea.com" target="_blank" rel="noopener noreferrer" style={{ color: '#64b5f6' }}>Photopea</a>{' '}
                      in a new tab. Requires PosterFlow to be accessible over HTTPS. If no export folder
                      is configured, files are saved temporarily to <code>/config/psd_cache</code>.
                    </small>
                  </div>
                  <label className="toggle-switch" style={{ flexShrink: 0 }}>
                    <input
                      type="checkbox"
                      checked={psdOpenPhotopea}
                      onChange={(e) => setPsdOpenPhotopea(e.target.checked)}
                    />
                    <span className="toggle-slider" />
                  </label>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={closePsdConfigModal} disabled={saving}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleSavePsdConfig} disabled={saving} style={{ justifyContent: 'center' }}>
                <Save size={16} /> {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showConfigModal && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Monitor Configuration</h2>
              <button className="modal-close" onClick={closeConfigModal}>×</button>
            </div>
            <div className="modal-body">
              <div className="maker-grid">
                <div className="maker-card">
                  <h3>General</h3>
                  <p style={{ margin: '0.25rem 0 0.75rem', fontSize: '0.8rem', color: '#888' }}>
                    TMDB API key is managed in{' '}
                    <a
                      href="/settings"
                      onClick={(e) => { e.preventDefault(); localStorage.setItem('posterflow.settings.activeTab', 'basic'); navigate('/settings') }}
                      style={{ color: '#64b5f6' }}
                    >
                      Settings → General → API Keys
                    </a>
                  </p>
                  <label>
                    Lookahead Days
                    <input
                      type="number"
                      min={1}
                      value={Number.isFinite(modalConfig.lookahead_days) ? modalConfig.lookahead_days : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            lookahead_days: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          lookahead_days: Number.isFinite(nextValue) && nextValue > 0 ? nextValue : previous.lookahead_days,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Missing Retention Days
                    <input
                      type="number"
                      min={0}
                      value={Number.isFinite(modalConfig.missing_retention_days) ? modalConfig.missing_retention_days : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            missing_retention_days: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          missing_retention_days: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.missing_retention_days,
                        }))
                      }}
                    />
                    <small className="muted">Keeps missing season premieres visible from previous runs for this many days. 0 = disable carryover.</small>
                  </label>
                  <label className="maker-checkbox-row">
                    <input
                      type="checkbox"
                      checked={Boolean(modalConfig.enable_discovery)}
                      onChange={(event) => setModalConfig((previous) => ({ ...previous, enable_discovery: event.target.checked }))}
                    />
                    <span>
                      Enable New Releases discovery
                      <small className="muted" style={{ display: 'block', marginTop: '2px' }}>Monitor upcoming movie and TV show releases. Adds a tab alongside monitored drives for browsing new and upcoming TMDB titles.</small>
                    </span>
                  </label>
                </div>

                <div className="maker-card">
                  <div className="maker-card-header-row">
                    <h3>Monitor Drives</h3>
                    <button className="btn-toolbar" type="button" onClick={addDriveSelection}>
                      <Plus size={16} /> Add Drive
                    </button>
                  </div>
                  <div className="maker-list">
                    {modalConfig.drive_ids.length === 0 && <p className="muted">No monitor drives selected.</p>}
                    {modalConfig.drive_ids.map((driveId, index) => {
                      const disabledIds = new Set(selectedDriveIdSet)
                      if (driveId > 0) {
                        disabledIds.delete(driveId)
                      }

                      return (
                        <div className="maker-list-item maker-drive-item" key={`drive-${index}`}>
                          <select
                            value={driveId > 0 ? String(driveId) : ''}
                            onChange={(event) => updateDriveSelection(index, Number(event.target.value))}
                          >
                            <option value="">Select a synced drive...</option>
                            {availableDrives.map((drive) => (
                              <option key={drive.id} value={drive.id} disabled={disabledIds.has(drive.id)}>
                                {drive.display_name || drive.name} ({drive.style_type})
                              </option>
                            ))}
                          </select>
                          <button className="btn-toolbar btn-danger" type="button" onClick={() => removeDriveSelection(index)}>
                            <Trash2 size={16} />
                          </button>
                        </div>
                      )
                    })}
                    {availableDrives.length === 0 && (
                      <p className="muted">No drives available.</p>
                    )}
                  </div>
                </div>

                <div className="maker-card">
                  <h3>Discovery</h3>
                  <label>
                    Minimum Popularity
                    <input
                      type="number"
                      min={0}
                      step="0.1"
                      value={Number.isFinite(modalConfig.discovery_popularity) ? modalConfig.discovery_popularity : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_popularity: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_popularity: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.discovery_popularity,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Minimum Vote Count
                    <input
                      type="number"
                      min={0}
                      value={Number.isFinite(modalConfig.discovery_vote_count) ? modalConfig.discovery_vote_count : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_vote_count: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_vote_count: Number.isFinite(nextValue) && nextValue >= 0 ? nextValue : previous.discovery_vote_count,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    Max Results Per Language
                    <input
                      type="number"
                      min={1}
                      value={Number.isFinite(modalConfig.discovery_max_results) ? modalConfig.discovery_max_results : ''}
                      onChange={(event) => {
                        const rawValue = event.target.value
                        if (rawValue.trim() === '') {
                          setModalConfig((previous) => ({
                            ...previous,
                            discovery_max_results: Number.NaN,
                          }))
                          return
                        }

                        const nextValue = Number(rawValue)
                        setModalConfig((previous) => ({
                          ...previous,
                          discovery_max_results: Number.isFinite(nextValue) && nextValue > 0 ? nextValue : previous.discovery_max_results,
                        }))
                      }}
                    />
                  </label>
                  <label>
                    <span className="maker-label-row">
                      Languages (comma-separated)
                      <button
                        type="button"
                        className="maker-help-button"
                        aria-label="Available language codes: en (English), ko (Korean), ja (Japanese), zh (Chinese), es (Spanish), fr (French), de (German), it (Italian), ru (Russian), hi (Hindi), th (Thai)"
                        title="Available: en (English), ko (Korean), ja (Japanese), zh (Chinese), es (Spanish), fr (French), de (German), it (Italian), ru (Russian), hi (Hindi), th (Thai)"
                      >
                        <CircleHelp size={14} />
                      </button>
                    </span>
                    <input
                      type="text"
                      value={modalDiscoveryLanguagesInput}
                      onChange={(event) => updateDiscoveryLanguages(event.target.value)}
                      placeholder="en, ko, ja, zh, es"
                    />
                  </label>
                </div>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={closeConfigModal} disabled={saving}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleSaveConfig} disabled={saving || loading} style={{ justifyContent: 'center' }}>
                <Save size={16} /> {saving ? 'Saving...' : 'Save Configuration'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default MakerTools
