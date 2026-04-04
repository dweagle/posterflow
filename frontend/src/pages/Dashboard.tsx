import { useState, useEffect, useRef } from 'react'
import { getStats, Stats, getSchedules, Schedule, getDrives, Drive, getUnmatchedStats, UnmatchedStats, runFlow, runBorderReplacer, startUnmatchedDetection, startPosterRename, getPosterConfig, getApiErrorMessage, getRecentSyncedPosters, RecentSyncedPoster, getMakerIdarrConfig, MakerIdarrSyncTarget, getPosterActivityStats, PosterActivityStats, formatJobType } from '../api/client'
import { useNavigate } from 'react-router-dom'
import { Play, Waves, AlertCircle, FolderSync, ChevronLeft, ChevronRight, ListOrdered, RefreshCw } from 'lucide-react'
import { useToast } from '../components/Toast'
import { useUnmatched } from '../contexts/UnmatchedContext'
import './Dashboard.css'

function Dashboard() {
  const SETTINGS_TAB_STORAGE_KEY = 'posterflow.settings.activeTab'
  const navigate = useNavigate()
  const { jobs } = useUnmatched()
  const [stats, setStats] = useState<Stats | null>(null)
  const [unmatchedStats, setUnmatchedStats] = useState<UnmatchedStats | null>(null)
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [recentPosters, setRecentPosters] = useState<RecentSyncedPoster[]>([])
  const [recentPosterIndex, setRecentPosterIndex] = useState(0)
  const [drives, setDrives] = useState<Drive[]>([])
  const [idarrTargets, setIdarrTargets] = useState<MakerIdarrSyncTarget[]>([])
  const [activityStats, setActivityStats] = useState<PosterActivityStats | null>(null)
  const [flowRunning, setFlowRunning] = useState(false)
  const [borderRunning, setBorderRunning] = useState(false)
  const [unmatchedRunning, setUnmatchedRunning] = useState(false)
  const [renameRunning, setRenameRunning] = useState(false)
  const [queuePopoverOpen, setQueuePopoverOpen] = useState(false)
  const [displayJobProgress, setDisplayJobProgress] = useState(0)
  const lastDisplayJobIdRef = useRef<number | null>(null)
  const { showToast } = useToast()

  const formatPercent = (percent: number): string => {
    if (percent === 100) return '100.0'
    if (percent >= 99.95) return percent.toFixed(2)
    return percent.toFixed(1)
  }

  useEffect(() => {
    fetchStats()
    fetchUnmatchedStats()
    fetchSchedules()
    fetchDrives()
    fetchIdarrTargets()
    fetchRecentPosters()
    fetchActivityStats()
    return undefined
  }, [])

  const runWithLoadingState = async (
    setLoadingState: (value: boolean) => void,
    action: () => Promise<void>
  ) => {
    setLoadingState(true)
    try {
      await action()
    } finally {
      setLoadingState(false)
    }
  }

  const fetchWithLogging = async (action: () => Promise<void>, errorMessage: string) => {
    try {
      await action()
    } catch (error) {
      console.error(errorMessage, error)
    }
  }

  const fetchStats = async () => {
    await fetchWithLogging(async () => {
      const data = await getStats()
      setStats(data)
    }, 'Error fetching stats:')
  }

  const fetchUnmatchedStats = async () => {
    await fetchWithLogging(async () => {
      const data = await getUnmatchedStats()
      setUnmatchedStats(data)
    }, 'Error fetching unmatched stats:')
  }

  const fetchSchedules = async () => {
    await fetchWithLogging(async () => {
      const data = await getSchedules()
      setSchedules(data)
    }, 'Error fetching schedules:')
  }

  const fetchDrives = async () => {
    await fetchWithLogging(async () => {
      const data = await getDrives()
      setDrives(data)
    }, 'Error fetching drives:')
  }

  const fetchIdarrTargets = async () => {
    await fetchWithLogging(async () => {
      const config = await getMakerIdarrConfig()
      const targets = Array.isArray(config.sync_targets) ? config.sync_targets : []
      setIdarrTargets(targets)
    }, 'Error fetching IDarr targets:')
  }

  const fetchRecentPosters = async () => {
    await fetchWithLogging(async () => {
      const data = await getRecentSyncedPosters(25)
      setRecentPosters(data.items)
      setRecentPosterIndex(0)
    }, 'Error fetching recent posters:')
  }

  const fetchActivityStats = async () => {
    await fetchWithLogging(async () => {
      const data = await getPosterActivityStats()
      setActivityStats(data)
    }, 'Error fetching activity stats:')
  }

  const handleRunFlow = async () => {
    await runWithLoadingState(setFlowRunning, async () => {
      try {
        showToast('Starting workflow...', 'info')
        const result = await runFlow()
        if (result.success && result.job_id) {
          showToast('Workflow started! Check Job Logs for progress.', 'success')
        }
      } catch (error) {
        console.error('Error running flow:', error)
        showToast(getApiErrorMessage(error, 'Failed to start workflow'), 'error')
      }
    })
  }

  const handleRunBorderReplacer = async () => {
    await runWithLoadingState(setBorderRunning, async () => {
      try {
        const result = await runBorderReplacer()
        if (result.success) {
          showToast(`Border replacer started (Job ID: ${result.job_id})`, 'success')
        }
      } catch (error) {
        console.error('Error running border replacer:', error)
        showToast(getApiErrorMessage(error, 'Failed to start border replacer'), 'error')
      }
    })
  }

  const handleRunUnmatched = async () => {
    await runWithLoadingState(setUnmatchedRunning, async () => {
      try {
        const result = await startUnmatchedDetection()
        if (result.job_id) {
          showToast('Unmatched detection started!', 'success')
        }
      } catch (error) {
        console.error('Error starting unmatched detection:', error)
        showToast(getApiErrorMessage(error, 'Failed to start detection'), 'error')
      }
    })
  }

  const handleRunPosterRename = async () => {
    await runWithLoadingState(setRenameRunning, async () => {
      try {
        const config = await getPosterConfig()
        const result = await startPosterRename(config)
        if (result.job_id) {
          showToast('Poster Renamer started!', 'success')
        }
      } catch (error) {
        console.error('Error starting Poster Renamer:', error)
        showToast(getApiErrorMessage(error, 'Failed to start Poster Renamer'), 'error')
      }
    })
  }

  const formatNextRun = (nextRun: string | null) => {
    if (!nextRun) return 'Not scheduled'
    try {
      const date = new Date(nextRun)
      return date.toLocaleString('en-US', {
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
      })
    } catch {
      return 'Invalid date'
    }
  }

  const formatTimeValue = (value: string) => {
    const normalized = value.trim()
    if (!normalized) return ''

    const match = normalized.match(/^(\d{1,2}):(\d{2})$/)
    if (!match) {
      return normalized
    }

    const hour = Number(match[1])
    const minute = Number(match[2])

    if (Number.isNaN(hour) || Number.isNaN(minute) || hour < 0 || hour > 23 || minute < 0 || minute > 59) {
      return normalized
    }

    const meridiem = hour >= 12 ? 'PM' : 'AM'
    const hour12 = hour % 12 || 12
    return `${hour12}:${String(minute).padStart(2, '0')} ${meridiem}`
  }

  const formatTimes = (value: string) => {
    return value
      .split(',')
      .map(time => formatTimeValue(time))
      .filter(Boolean)
      .join(', ')
  }

  const getScheduleCadence = (schedule: Schedule) => {
    if (!schedule.enabled) {
      return 'Disabled'
    }

    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    const scheduleValue = schedule.schedule_value || ''

    switch (schedule.schedule_type) {
      case 'hourly': {
        const minute = scheduleValue || '0'
        const paddedMinute = minute.padStart(2, '0')
        return `Every hour at :${paddedMinute}`
      }
      case 'daily': {
        if (!scheduleValue) return 'Daily'
        return `Daily at ${formatTimeValue(scheduleValue)}`
      }
      case 'multiple_daily': {
        if (!scheduleValue) return 'Daily'
        return `Daily at ${formatTimes(scheduleValue)}`
      }
      case 'weekly': {
        if (!scheduleValue) return 'Weekly'
        const parts = scheduleValue.split(':')
        const dayIndex = Number(parts[0])
        const dayName = Number.isNaN(dayIndex) ? parts[0] : (dayNames[dayIndex] || parts[0])
        const times = formatTimes(parts.slice(1).join(':'))
        return times ? `${dayName} at ${times}` : `${dayName}`
      }
      case 'multiple_days': {
        if (!scheduleValue) return 'Multiple days'
        const daySchedules = scheduleValue
          .split('|')
          .map(segment => segment.trim())
          .filter(Boolean)
          .map(segment => {
            const parts = segment.split(':')
            const dayIndex = Number(parts[0])
            const dayName = Number.isNaN(dayIndex) ? parts[0] : (dayNames[dayIndex] || parts[0])
            const times = formatTimes(parts.slice(1).join(':'))
            return times ? `${dayName} ${times}` : dayName
          })

        return daySchedules.length > 0 ? daySchedules.join(' · ') : 'Multiple days'
      }
      case 'monthly': {
        if (!scheduleValue) return 'Monthly'
        const parts = scheduleValue.split(':')
        const dayOfMonth = parts[0]
        const times = formatTimes(parts.slice(1).join(':'))
        return times ? `Day ${dayOfMonth} at ${times}` : `Day ${dayOfMonth}`
      }
      case 'cron':
        return scheduleValue ? `Cron: ${scheduleValue}` : 'Cron schedule'
      default:
        return scheduleValue || 'Not configured'
    }
  }

  const getScheduleTaskLabel = (jobType: string) => formatJobType(jobType)

  const isSyncSchedule = (jobType: string) => jobType === 'gdrive_sync' || jobType === 'sync'
  const isIdarrSchedule = (jobType: string) => jobType === 'idarr'

  const getDriveScope = (driveId: number | null, driveGroup: string | null) => {
    if (driveId !== null) {
      const drive = drives.find(d => d.id === driveId)
      if (drive) {
        return drive.name
      }
      if (driveGroup) {
        return `All ${driveGroup}`
      }
      return 'All Subscribed Drives'
    }
    if (driveGroup) return `All ${driveGroup}`
    return 'All Subscribed Drives'
  }

  const getIdarrScope = (driveGroup: string | null) => {
    const rawScope = String(driveGroup || '').trim()
    if (!rawScope.startsWith('idarr_target_')) {
      return 'IDarr Default Target'
    }

    const index = Number(rawScope.replace('idarr_target_', ''))
    if (Number.isNaN(index) || index < 0) {
      return 'IDarr Default Target'
    }

    const target = idarrTargets[index]
    if (!target) {
      return `Target ${index + 1}`
    }

    const label = String(target.label || '').trim()
    return label || `Target ${index + 1}`
  }

  const getScheduleScope = (schedule: Schedule) => {
    if (isSyncSchedule(schedule.job_type)) {
      return getDriveScope(schedule.drive_id, schedule.drive_group)
    }
    if (isIdarrSchedule(schedule.job_type)) {
      return getIdarrScope(schedule.drive_group)
    }
    return null
  }

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'running': return '#2196f3'
      case 'pending': return '#ff9800'
      case 'completed': return '#4caf50'
      case 'failed': return '#f44336'
      default: return '#ccc'
    }
  }

  const truncateText = (value: string, maxLength: number) => {
    const normalized = String(value || '')
    if (normalized.length <= maxLength) {
      return normalized
    }
    return `${normalized.slice(0, maxLength)}…`
  }

  const sortedSchedules = [...schedules].sort((a, b) => {
    const aTime = a.next_run ? new Date(a.next_run).getTime() : Number.POSITIVE_INFINITY
    const bTime = b.next_run ? new Date(b.next_run).getTime() : Number.POSITIVE_INFINITY
    return aTime - bTime
  })

  const currentRecentPoster = recentPosters.length > 0
    ? recentPosters[((recentPosterIndex % recentPosters.length) + recentPosters.length) % recentPosters.length]
    : null

  const getRecentPosterImageUrl = (poster: RecentSyncedPoster) => {
    const versionToken = poster.downloaded_at || String((poster as { file_mtime?: number }).file_mtime || poster.id)
    return `${poster.image_url}?v=${encodeURIComponent(versionToken)}`
  }

  const goToPreviousRecentPoster = () => {
    if (recentPosters.length === 0) return
    setRecentPosterIndex((prev) => (prev - 1 + recentPosters.length) % recentPosters.length)
  }

  const goToNextRecentPoster = () => {
    if (recentPosters.length === 0) return
    setRecentPosterIndex((prev) => (prev + 1) % recentPosters.length)
  }

  const runningJobs = jobs
    .filter(job => job.status === 'running')
    .sort((a, b) => b.id - a.id)
  const queuedJobs = jobs
    .filter(job => job.status === 'pending')
    .sort((a, b) => a.id - b.id)
  const displayJob = runningJobs[0] || queuedJobs[0] || null

  useEffect(() => {
    if (!displayJob || displayJob.status !== 'running') {
      lastDisplayJobIdRef.current = null
      setDisplayJobProgress(0)
      return
    }

    const incomingProgress = Math.max(0, Math.min(100, Number(displayJob.progress || 0)))

    if (lastDisplayJobIdRef.current !== displayJob.id) {
      lastDisplayJobIdRef.current = displayJob.id
      setDisplayJobProgress(incomingProgress)
      return
    }

    setDisplayJobProgress(prev => Math.max(prev, incomingProgress))
  }, [displayJob?.id, displayJob?.status, displayJob?.progress])

  const driveCountsByType = drives.reduce(
    (acc, drive) => {
      const normalizedStyleType = String(drive.style_type || '').toUpperCase()
      const driveType = drive.is_custom
        ? 'custom'
        : normalizedStyleType === 'CL2K'
          ? 'cl2k'
          : normalizedStyleType === 'MM2K'
            ? 'mm2k'
            : 'custom'

      if (drive.subscribed) {
        acc.subscribed[driveType] += 1
        if (drive.last_synced) {
          acc.synced[driveType] += 1
        }
      }

      return acc
    },
    {
      subscribed: { cl2k: 0, mm2k: 0, custom: 0 },
      synced: { cl2k: 0, mm2k: 0, custom: 0 },
    }
  )

  const openSchedulingSettings = () => {
    localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, 'scheduling')
    navigate('/settings')
  }

  return (
    <div className="page-container dashboard">
      <h1>Dashboard</h1>

      <div className="quick-actions-bar">
        <div className="quick-actions-header">
          <h2>Quick Actions</h2>
        </div>
        <div className="quick-actions-buttons">
          <button className="quick-action-btn" onClick={handleRunFlow} disabled={flowRunning}>
            <Waves size={18} className="icon-workflow" />
            <span className="action-title">Run Workflow</span>
          </button>
          <button className="quick-action-btn" onClick={handleRunPosterRename} disabled={renameRunning}>
            <FolderSync size={18} className="icon-rename" />
            <span className="action-title">Poster Renamer</span>
          </button>
          <button className="quick-action-btn" onClick={handleRunBorderReplacer} disabled={borderRunning}>
            <Play size={18} className="icon-border" />
            <span className="action-title">Border Replacer</span>
          </button>
          <button className="quick-action-btn" onClick={handleRunUnmatched} disabled={unmatchedRunning}>
            <AlertCircle size={18} className="icon-unmatched" />
            <span className="action-title">Detect Unmatched</span>
          </button>
        </div>
      </div>

      {unmatchedStats && unmatchedStats.summary && unmatchedStats.last_run && (
        <div className="poster-coverage-card">
          <div className="card-header">
            <div className="coverage-header-left">
              <h2>Poster Coverage</h2>
              <p className="last-checked">Last checked: {new Date(unmatchedStats.last_run).toLocaleString()}</p>
            </div>
            <div className="coverage-header-center">
              <div className="coverage-total-inline">
                <div className="total-percent">{formatPercent(unmatchedStats.summary.grand_total.percent_complete)}%</div>
                <div className="total-stats">
                  <span>{unmatchedStats.summary.grand_total.total - unmatchedStats.summary.grand_total.unmatched} / {unmatchedStats.summary.grand_total.total} items</span>
                </div>
              </div>
            </div>
            <div className="coverage-header-right">
              <button
                className="view-details-link"
                onClick={() => navigate('/poster-manager', { state: { activeTab: 'unmatched' } })}
              >
                View Details →
              </button>
            </div>
          </div>
          <div className="coverage-grid">
            {unmatchedStats.summary.movies && unmatchedStats.summary.movies.total > 0 && (
              <div className="coverage-item">
                <div className="coverage-label">Movies</div>
                <div className="coverage-bar"><div className="coverage-fill" style={{ width: `${unmatchedStats.summary.movies.percent_complete}%` }} /></div>
                <div className="coverage-stats">
                  <span className="matched">{unmatchedStats.summary.movies.total - unmatchedStats.summary.movies.unmatched} with posters</span>
                  <span className="missing">{unmatchedStats.summary.movies.unmatched} missing</span>
                </div>
              </div>
            )}
            {unmatchedStats.summary.series && unmatchedStats.summary.series.total > 0 && (
              <div className="coverage-item">
                <div className="coverage-label">Series</div>
                <div className="coverage-bar"><div className="coverage-fill" style={{ width: `${unmatchedStats.summary.series.percent_complete}%` }} /></div>
                <div className="coverage-stats">
                  <span className="matched">{unmatchedStats.summary.series.total - unmatchedStats.summary.series.unmatched} with posters</span>
                  <span className="missing">{unmatchedStats.summary.series.unmatched} missing</span>
                </div>
              </div>
            )}
            {unmatchedStats.summary.seasons && unmatchedStats.summary.seasons.total > 0 && (
              <div className="coverage-item">
                <div className="coverage-label">Seasons</div>
                <div className="coverage-bar"><div className="coverage-fill" style={{ width: `${unmatchedStats.summary.seasons.percent_complete}%` }} /></div>
                <div className="coverage-stats">
                  <span className="matched">{unmatchedStats.summary.seasons.total - unmatchedStats.summary.seasons.unmatched} with posters</span>
                  <span className="missing">{unmatchedStats.summary.seasons.unmatched} missing</span>
                </div>
              </div>
            )}
            {unmatchedStats.summary.collections && unmatchedStats.summary.collections.total > 0 && (
              <div className="coverage-item">
                <div className="coverage-label">Collections</div>
                <div className="coverage-bar"><div className="coverage-fill" style={{ width: `${unmatchedStats.summary.collections.percent_complete}%` }} /></div>
                <div className="coverage-stats">
                  <span className="matched">{unmatchedStats.summary.collections.total - unmatchedStats.summary.collections.unmatched} with posters</span>
                  <span className="missing">{unmatchedStats.summary.collections.unmatched} missing</span>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="dashboard-panels-grid">
        <div className="stat-card recent-posters-card panel-recent">
          <div className="recent-posters-card-header">
            <h3>Recently Synced Posters</h3>
            <button
              className="recent-posters-refresh-btn"
              onClick={fetchRecentPosters}
              aria-label="Refresh recently synced posters"
              title="Refresh"
            >
              <RefreshCw size={14} />
            </button>
          </div>
          <div className="stat-details recent-posters-details">
            {currentRecentPoster ? (
              <>
                <div className="recent-poster-carousel">
                  <button type="button" className="recent-poster-nav" onClick={goToPreviousRecentPoster} aria-label="Previous poster">
                    <ChevronLeft size={18} />
                  </button>
                  <div className="recent-poster-image-wrap">
                    <img src={getRecentPosterImageUrl(currentRecentPoster)} alt={currentRecentPoster.file_name} className="recent-poster-image" />
                  </div>
                  <button type="button" className="recent-poster-nav" onClick={goToNextRecentPoster} aria-label="Next poster">
                    <ChevronRight size={18} />
                  </button>
                </div>
                <div className="recent-poster-drive">
                  Added in: <strong>{currentRecentPoster.drive_name}</strong>
                </div>
                {recentPosters.length > 1 && (
                  <div className="recent-poster-position">
                    {recentPosterIndex + 1} / {recentPosters.length}
                  </div>
                )}
              </>
            ) : (
              <div className="stat-item">
                <span style={{ color: '#888', fontStyle: 'italic' }}>No recently synced posters</span>
              </div>
            )}
          </div>
        </div>

        <div className="right-column">
          <div className="right-top-cards">
            <div className="stat-card schedule-stat-card">
              <div className="schedule-card-header-row">
                <div className="schedule-card-title-inline">
                  <h3>Scheduled Tasks:</h3>
                  <span className="schedule-count-inline">{schedules.length}</span>
                </div>
                <button type="button" className="schedule-settings-link" onClick={openSchedulingSettings}>
                  Manage
                </button>
              </div>
              <div className="stat-details schedule-stat-details">
                <div className="stat-row schedule-summary-row">
                  <span className="stat-label">Enabled:</span>
                  <span className="stat-number">{schedules.filter(s => s.enabled).length} / {schedules.length}</span>
                </div>
                <div className="stat-breakdown schedule-breakdown-scroll">
                  {schedules.length > 0 ? (
                    sortedSchedules.map((schedule, index) => (
                      <div key={schedule.id} className="stat-item schedule-item">
                        <div className="schedule-main">
                          <span className="schedule-inline" title={`${schedule.name} · ${getScheduleTaskLabel(schedule.job_type)}${getScheduleScope(schedule) ? ` · ${getScheduleScope(schedule)}` : ''} · ${getScheduleCadence(schedule)} · ${index === 0 && schedule.next_run ? 'Next up · ' : ''}${formatNextRun(schedule.next_run)}`}>
                            <span className="schedule-primary-line">
                              <span className="schedule-name-inline">{truncateText(schedule.name, 22)}</span>
                              <span className="schedule-separator"> · </span>
                              <span className="schedule-task-inline">{getScheduleTaskLabel(schedule.job_type)}</span>
                              {getScheduleScope(schedule)
                                ? (
                                  <>
                                    <span className="schedule-separator"> · </span>
                                    <span className="schedule-scope-inline">{getScheduleScope(schedule)}</span>
                                  </>
                                )
                                : ''}
                            </span>
                            <span className="schedule-detail-line">
                              <span className="schedule-cadence-inline">{getScheduleCadence(schedule)}</span>
                              <span className="schedule-separator"> · </span>
                              <span className="schedule-time-inline">{index === 0 && schedule.next_run ? 'Next up · ' : ''}{formatNextRun(schedule.next_run)}</span>
                            </span>
                          </span>
                        </div>
                        <span className={`schedule-badge ${schedule.enabled ? 'enabled' : 'disabled'}`}>
                          {schedule.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="stat-item">
                      <span style={{ color: '#888', fontStyle: 'italic' }}>No schedules configured</span>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="stat-card poster-stats-card">
              <h3>Poster Stats</h3>
              <div className="stat-details">
                <div className="combined-columns">
                  <div className="combined-section">
                    <div className="stat-row">
                      <span className="stat-label">Synced Drives:</span>
                      <span className="stat-number">{stats?.drives.subscribed || 0} / {stats?.drives.total || 0}</span>
                    </div>
                    <div className="stat-breakdown">
                      <div className="stat-item"><span className="stat-badge cl2k">CL2K</span><span>{driveCountsByType.synced.cl2k} / {driveCountsByType.subscribed.cl2k}</span></div>
                      <div className="stat-item"><span className="stat-badge mm2k">MM2K</span><span>{driveCountsByType.synced.mm2k} / {driveCountsByType.subscribed.mm2k}</span></div>
                      <div className="stat-item"><span className="stat-badge custom">Custom</span><span>{driveCountsByType.synced.custom} / {driveCountsByType.subscribed.custom}</span></div>
                    </div>
                  </div>
                  <div className="combined-section combined-section-right">
                    <div className="stat-row">
                      <span className="stat-label">Posters:</span>
                      <span className="stat-number">{stats?.subscribed_posters || 0}</span>
                    </div>
                    <div className="stat-breakdown">
                      <div className="stat-item"><span className="stat-badge cl2k">CL2K</span><span>{stats?.posters_by_type.cl2k || 0} </span></div>
                      <div className="stat-item"><span className="stat-badge mm2k">MM2K</span><span>{stats?.posters_by_type.mm2k || 0} </span></div>
                      <div className="stat-item"><span className="stat-badge custom">Custom</span><span>{stats?.posters_by_type.custom || 0} </span></div>
                    </div>
                  </div>
                </div>

                <div className="stat-section-divider" />

                <div className="activity-stats-section">
                  <div className="activity-stats-row activity-stats-header">
                    <span className="activity-stat-label" />
                    <span className="activity-stat-col-head">Today</span>
                    <span className="activity-stat-col-head">Week</span>
                    <span className="activity-stat-col-head">Month</span>
                  </div>
                  <div className="activity-stats-row">
                    <span className="activity-stat-label">New Synced</span>
                    <span className="activity-stat-value">{activityStats?.synced_new_today ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_new_week ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_new_month ?? '—'}</span>
                  </div>
                  <div className="activity-stats-row">
                    <span className="activity-stat-label">Replaced</span>
                    <span className="activity-stat-value">{activityStats?.synced_replaced_today ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_replaced_week ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_replaced_month ?? '—'}</span>
                  </div>
                  <div className="activity-stats-row">
                    <span className="activity-stat-label">Deleted</span>
                    <span className="activity-stat-value">{activityStats?.synced_deleted_today ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_deleted_week ?? '—'}</span>
                    <span className="activity-stat-value">{activityStats?.synced_deleted_month ?? '—'}</span>
                  </div>
                </div>
              </div>
            </div>
            </div>

          <div className="active-jobs-bar right-active-jobs">
          <div className="active-jobs-header">
              <h2>Active Jobs</h2>
              <div className="active-jobs-summary">
                <div className={`active-slot-indicator ${runningJobs.length > 0 ? 'busy' : 'idle'}`}>
                  <span className="slot-label">Slot</span>
                  <span className="slot-value">{runningJobs.length > 0 ? '1/1 Busy' : '0/1 Idle'}</span>
                </div>
                <div
                  className="queue-popover-wrap"
                  onMouseEnter={() => setQueuePopoverOpen(true)}
                  onMouseLeave={() => setQueuePopoverOpen(false)}
                >
                  <button
                    type="button"
                    className={`queued-count-indicator queue-trigger ${queuedJobs.length === 0 ? 'empty' : ''}`}
                    onClick={() => setQueuePopoverOpen(prev => !prev)}
                    aria-label="Show queued jobs"
                  >
                    <ListOrdered size={14} />
                    <span>{queuedJobs.length} queued</span>
                  </button>
                  {queuePopoverOpen && (
                    <div className="queue-popover">
                      <div className="queue-popover-title">Queued Jobs</div>
                      {queuedJobs.length === 0 ? (
                        <div className="queue-empty">No queued jobs</div>
                      ) : (
                        <ul className="queue-list">
                          {queuedJobs.map((job, index) => (
                            <li key={job.id} className="queue-item">
                              <span className="queue-index">#{index + 1}</span>
                              <div className="queue-content">
                                <span className="queue-type">{formatJobType(job.job_type)}</span>
                                <span className="queue-message">{job.message || 'Waiting for available slot...'}</span>
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {displayJob ? (
              <div className="jobs-list">
                <div key={displayJob.id} className={`job-item ${displayJob.status === 'pending' ? 'queued' : 'running'}`}>
                  <div className="job-header">
                    <span className="job-type">{formatJobType(displayJob.job_type).toUpperCase()}</span>
                    <span className="job-status" style={{ color: getStatusColor(displayJob.status) }}>
                      {displayJob.status === 'pending' ? 'queued' : displayJob.status}
                    </span>
                  </div>
                  <div className="job-message">{displayJob.message}</div>
                  {displayJob.status === 'pending' ? (
                    <div className="job-progress queued-indicator">
                      <span className="queue-icon">⏳</span>
                      <span className="progress-text">Waiting for available slot...</span>
                    </div>
                  ) : (
                    <div className="job-progress">
                      <div className="progress-bar" style={{ width: `${displayJobProgress}%` }} />
                      <span className="progress-text">{displayJobProgress}%</span>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="no-active-jobs">No active jobs</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default Dashboard