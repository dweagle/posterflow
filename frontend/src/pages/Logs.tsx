import { useState, useEffect, useRef, useMemo } from 'react'
import { getLogs, clearLogs, LogEntry, getJobLogs, getJobLogContent, downloadJobLog, connectJobLogLiveWS, JobLogs as JobLogsData, JobLogFile } from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Trash2, RefreshCw, Download, FileText, ChevronDown, Monitor, Briefcase, Radio, X } from 'lucide-react'
import './Logs.css'

const LOGS_TAB_STORAGE_KEY = 'posterflow.logs.activeTab'
type LogsTab = 'system' | 'job'

const isLogsTab = (value: string): value is LogsTab => {
  return ['system', 'job'].includes(value)
}

function Logs() {
  const [activeTab, setActiveTab] = useState<LogsTab>(() => {
    const savedTab = localStorage.getItem(LOGS_TAB_STORAGE_KEY)
    if (savedTab && isLogsTab(savedTab)) {
      return savedTab
    }
    return 'system'
  })
  
  // System logs state
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('all')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [showConfirm, setShowConfirm] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const isMountedRef = useRef<boolean>(true)
  const logsContainerRef = useRef<HTMLDivElement | null>(null)
  const isProgrammaticScrollRef = useRef(false)
  const lastScrollTopRef = useRef(0)
  const userScrollActiveRef = useRef(false)
  const [followLatest, setFollowLatest] = useState(true)
  
  // Job logs state
  const [jobLogs, setJobLogs] = useState<JobLogsData>({ 
    sync_one: [], 
    sync_all: [], 
    plex_upload: [],
    workflow: [], 
    poster_renamer: [], 
    border_replacer: [], 
    unmatched_assets: [],
    idarr: [],
  })
  const [jobLogsLoading, setJobLogsLoading] = useState(false)
  const [selectedLog, setSelectedLog] = useState<{ type: string; file: JobLogFile } | null>(null)
  const [logContent, setLogContent] = useState<string>('')
  const [loadingContent, setLoadingContent] = useState(false)
  const [collapsedSections, setCollapsedSections] = useState<{ [key: string]: boolean }>({ 
    sync_one: true,
    sync_all: true, 
    plex_upload: true,
    workflow: false,  // Workflow section open by default
    poster_renamer: true, 
    border_replacer: true, 
    unmatched_assets: true,
    idarr: true,
  })

  // Live-tail state
  const [liveJobType, setLiveJobType] = useState<string | null>(null)
  const [liveContent, setLiveContent] = useState<string>('')
  const [liveConnected, setLiveConnected] = useState(false)
  const liveWsRef = useRef<WebSocket | null>(null)
  const liveContainerRef = useRef<HTMLDivElement | null>(null)
  
  const { showToast } = useToast()

  useEffect(() => {
    localStorage.setItem(LOGS_TAB_STORAGE_KEY, activeTab)
  }, [activeTab])

  const fetchWithLogging = async (action: () => Promise<void>, errorMessage: string) => {
    try {
      await action()
    } catch (error) {
      console.error(errorMessage, error)
    }
  }

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

  const toggleSection = (section: string) => {
    setCollapsedSections(prev => {
      const isCurrentlyCollapsed = prev[section]
      
      if (isCurrentlyCollapsed) {
        // Expanding this section - collapse all others
        return {
          sync_one: true,
          sync_all: true,
          plex_upload: true,
          workflow: true,
          poster_renamer: true,
          border_replacer: true,
          unmatched_assets: true,
          idarr: true,
          [section]: false
        }
      } else {
        // Collapsing this section
        return {
          ...prev,
          [section]: true
        }
      }
    })
  }

  // System logs functions
  const fetchLogs = async () => {
    await fetchWithLogging(async () => {
      const level = filter === 'all' ? undefined : filter
      const data = await getLogs(1000, level)
      setLogs(data)
    }, 'Error fetching logs:')
    if (loading) {
      setLoading(false)
    }
  }

  const connectLogWebSocket = () => {
    // Close existing connection if any
    if (wsRef.current) {
      wsRef.current.close()
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.hostname}:${window.location.port}/api/logs/ws`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setLoading(false)
    }

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data) as Partial<LogEntry> & {
          type?: string
          heartbeat?: number
        }

        if (message.type === 'heartbeat' || typeof message.heartbeat === 'number') {
          return
        }

        if (
          typeof message.timestamp !== 'string' ||
          typeof message.level !== 'string' ||
          typeof message.message !== 'string'
        ) {
          return
        }

        const logEntry: LogEntry = {
          timestamp: message.timestamp,
          level: message.level,
          message: message.message,
        }
        
        setLogs(prev => {
          const updated = [...prev, logEntry]
          return updated.length > 20000 ? updated.slice(-20000) : updated
        })
      } catch (error) {
        console.error('Error parsing log message:', error)
      }
    }

    ws.onerror = (error) => {
      console.error('[Logs] WebSocket error:', error)
    }

    ws.onclose = (event) => {
      wsRef.current = null
      // Only reconnect if auto-refresh is still enabled, on system tab, and component is mounted
      if (autoRefresh && activeTab === 'system' && event.code !== 1000 && isMountedRef.current) {
        reconnectTimeoutRef.current = setTimeout(connectLogWebSocket, 2000)
      }
    }
  }

  const disconnectLogWebSocket = () => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }

  const handleClearLogs = async () => {
    setShowConfirm(false)
    try {
      await clearLogs()
      setLogs([])
      showToast('Logs cleared successfully')
      // WebSocket will automatically detect file truncation and reset
      if (!autoRefresh) {
        fetchLogs() // Only fetch if not using WebSocket
      }
    } catch (error) {
      console.error('Error clearing logs:', error)
      showToast('Failed to clear logs', 'error')
    }
  }

  // Job logs functions
  const fetchJobLogs = async () => {
    await runWithLoadingState(setJobLogsLoading, async () => {
      try {
        const data = await getJobLogs()
        setJobLogs({
          ...data,
          poster_renamer: data.poster_renamer ?? [],
          idarr: data.idarr ?? [],
        })
      } catch (error) {
        console.error('Failed to fetch job logs:', error)
        showToast('Failed to load job logs', 'error')
      }
    })
  }

  const viewLog = async (jobType: string, file: JobLogFile) => {
    setSelectedLog({ type: jobType, file })
    await runWithLoadingState(setLoadingContent, async () => {
      try {
        const data = await getJobLogContent(jobType, file.name)
        setLogContent(data.content)
      } catch (error) {
        console.error('Failed to fetch log content:', error)
        showToast('Failed to load log content', 'error')
        setLogContent('Error loading log content')
      }
    })
  }

  const handleDownload = (jobType: string, filename: string) => {
    const url = downloadJobLog(jobType, filename)
    window.open(url, '_blank')
    showToast('Download started', 'success')
  }

  const stopLiveTail = () => {
    if (liveWsRef.current) {
      liveWsRef.current.close()
      liveWsRef.current = null
    }
    setLiveJobType(null)
    setLiveContent('')
    setLiveConnected(false)
  }

  const startLiveTail = (jobType: string) => {
    // Stop any existing live session
    if (liveWsRef.current) {
      liveWsRef.current.close()
      liveWsRef.current = null
    }
    setLiveContent('')
    setLiveConnected(false)
    setLiveJobType(jobType)
    // Clear selected static log so the viewer panel shows live content
    setSelectedLog(null)

    const ws = connectJobLogLiveWS(jobType)
    liveWsRef.current = ws

    ws.onopen = () => setLiveConnected(true)

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        if (msg.type === 'heartbeat') return
        if (msg.type === 'content' || msg.type === 'reset') {
          setLiveContent(msg.content ?? '')
        } else if (msg.type === 'append') {
          setLiveContent(prev => prev + (msg.content ?? ''))
        }
      } catch {
        // ignore parse errors
      }
    }

    ws.onerror = () => setLiveConnected(false)
    ws.onclose = () => setLiveConnected(false)
  }

  // Auto-scroll live viewer to bottom when content updates
  useEffect(() => {
    if (liveContainerRef.current) {
      liveContainerRef.current.scrollTop = liveContainerRef.current.scrollHeight
    }
  }, [liveContent])

  // Clean up live WS on unmount or tab change
  useEffect(() => {
    return () => {
      if (liveWsRef.current) {
        liveWsRef.current.close()
        liveWsRef.current = null
      }
    }
  }, [activeTab])

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const formatDate = (timestamp: number): string => {
    const date = new Date(timestamp * 1000)
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hours = String(date.getHours()).padStart(2, '0')
    const minutes = String(date.getMinutes()).padStart(2, '0')
    const seconds = String(date.getSeconds()).padStart(2, '0')
    return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`
  }

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'DEBUG': return '#64b5f6'
      case 'INFO': return '#4caf50'
      case 'WARNING': return '#ff9800'
      case 'ERROR': return '#f44336'
      default: return '#ccc'
    }
  }

  const checkIsNearBottom = (element: HTMLDivElement) => {
    const threshold = 160
    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight
    return distanceFromBottom <= threshold
  }

  const handleUserWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (event.deltaY < 0) {
      setFollowLatest(false)
      return
    }

    if (event.deltaY > 0 && logsContainerRef.current && checkIsNearBottom(logsContainerRef.current)) {
      setFollowLatest(true)
    }
  }

  const handleUserPointerDown = () => {
    userScrollActiveRef.current = true
  }

  const handleUserPointerUp = () => {
    userScrollActiveRef.current = false
  }

  const handleLogsScroll = () => {
    const container = logsContainerRef.current
    if (!container) return

    if (isProgrammaticScrollRef.current) {
      lastScrollTopRef.current = container.scrollTop
      return
    }

    const currentScrollTop = container.scrollTop
    const scrollDelta = currentScrollTop - lastScrollTopRef.current
    lastScrollTopRef.current = currentScrollTop

    const nearBottom = checkIsNearBottom(container)

    // Only disable follow mode when user intentionally scrolls up.
    if (!nearBottom && userScrollActiveRef.current && scrollDelta < -2) {
      setFollowLatest(false)
      return
    }

    // Re-enable follow mode when user intentionally scrolls back down to the bottom.
    if (nearBottom && scrollDelta > 2) {
      setFollowLatest(true)
    }
  }

  const jumpToLatest = () => {
    const container = logsContainerRef.current
    if (!container) return
    isProgrammaticScrollRef.current = true
    container.scrollTo({ top: container.scrollHeight, behavior: 'auto' })
    lastScrollTopRef.current = container.scrollTop
    requestAnimationFrame(() => {
      isProgrammaticScrollRef.current = false
    })
    setFollowLatest(true)
  }

  useEffect(() => {
    if (activeTab === 'system') {
      if (autoRefresh) {
        // Use WebSocket streaming when auto-refresh is on
        connectLogWebSocket()
      } else {
        // Use REST API when auto-refresh is off
        disconnectLogWebSocket()
        fetchLogs()
      }
    } else if (activeTab === 'job') {
      disconnectLogWebSocket()
      fetchJobLogs()
    }

    // Cleanup on unmount or tab change
    return () => {
      disconnectLogWebSocket()
    }
  }, [activeTab, autoRefresh])

  // Set mounted flag on mount/unmount
  useEffect(() => {
    isMountedRef.current = true
    return () => {
      isMountedRef.current = false
    }
  }, [activeTab, autoRefresh])

  // Handle filter changes without reconnecting WebSocket
  useEffect(() => {
    if (activeTab === 'system' && !autoRefresh) {
      // Only refetch when filter changes and not using WebSocket
      fetchLogs()
    }
    // If using WebSocket, filter is applied client-side in onmessage
  }, [filter])

  // Keep newest logs visible while follow-latest mode is enabled
  useEffect(() => {
    if (activeTab !== 'system' || !autoRefresh || !logsContainerRef.current) {
      return
    }
    if (followLatest) {
      isProgrammaticScrollRef.current = true
      logsContainerRef.current.scrollTop = logsContainerRef.current.scrollHeight
      lastScrollTopRef.current = logsContainerRef.current.scrollTop
      requestAnimationFrame(() => {
        isProgrammaticScrollRef.current = false
      })
    }
  }, [logs, activeTab, autoRefresh, followLatest])

  useEffect(() => {
    const handlePointerUp = () => {
      userScrollActiveRef.current = false
    }

    window.addEventListener('mouseup', handlePointerUp)
    window.addEventListener('touchend', handlePointerUp)

    return () => {
      window.removeEventListener('mouseup', handlePointerUp)
      window.removeEventListener('touchend', handlePointerUp)
    }
  }, [])

  const filteredLogs = useMemo(
    () => logs.filter(log => filter === 'all' || log.level === filter),
    [logs, filter]
  )

  return (
    <div className="page-container logs-page">
      <div className="logs-header">
        <h1>Logs</h1>
        <p>View system and job logs</p>
        <div className="tabs">
          <button 
            className={`tab ${activeTab === 'system' ? 'active' : ''}`}
            onClick={() => setActiveTab('system')}
          >
            <Monitor size={16} className="tab-icon icon-system" />
            System Logs
          </button>
          <button 
            className={`tab ${activeTab === 'job' ? 'active' : ''}`}
            onClick={() => setActiveTab('job')}
          >
            <Briefcase size={16} className="tab-icon icon-job" />
            Job Logs
          </button>
        </div>
      </div>

      {activeTab === 'system' ? (
        <>
          <div className="logs-controls">
            <div className="filter-buttons">
              <button 
                className={filter === 'all' ? 'active' : ''} 
                onClick={() => setFilter('all')}
              >
                All
              </button>
              <button 
                className={filter === 'INFO' ? 'active' : ''} 
                onClick={() => setFilter('INFO')}
              >
                Info
              </button>
              <button 
                className={filter === 'WARNING' ? 'active' : ''} 
                onClick={() => setFilter('WARNING')}
              >
                Warnings
              </button>
              <button 
                className={filter === 'ERROR' ? 'active' : ''} 
                onClick={() => setFilter('ERROR')}
              >
                Errors
              </button>
              <button 
                className={filter === 'DEBUG' ? 'active' : ''} 
                onClick={() => setFilter('DEBUG')}
              >
                Debug
              </button>
            </div>

            <div className="auto-refresh">
              <label>
                <input
                  type="checkbox"
                  checked={autoRefresh}
                  onChange={(e) => setAutoRefresh(e.target.checked)}
                />
                Auto-refresh
              </label>
            </div>

            <button className="btn-refresh" onClick={fetchLogs}>
              <RefreshCw size={16} />
              Refresh
            </button>

            <button className="btn-clear" onClick={() => setShowConfirm(true)}>
              <Trash2 size={16} />
              Clear Logs
            </button>
          </div>

          <ConfirmDialog
            isOpen={showConfirm}
            title="Clear All Logs"
            message="Are you sure you want to clear all logs? This action cannot be undone."
            confirmText="Clear Logs"
            cancelText="Cancel"
            variant="danger"
            onConfirm={handleClearLogs}
            onCancel={() => setShowConfirm(false)}
          />

          <div className="logs-window">
            <div
              className="logs-container"
              ref={logsContainerRef}
              onScroll={handleLogsScroll}
              onWheel={handleUserWheel}
              onMouseDown={handleUserPointerDown}
              onMouseUp={handleUserPointerUp}
              onTouchStart={handleUserPointerDown}
              onTouchEnd={handleUserPointerUp}
            >
              {loading ? (
                <div className="logs-loading">Loading logs...</div>
              ) : filteredLogs.length === 0 ? (
                <div className="no-logs">No logs found</div>
              ) : (
                <div className="logs-list">
                  {filteredLogs.map((log, index) => (
                    <div key={index} className="log-entry">
                      <span className="log-timestamp">{log.timestamp}</span>
                      <span 
                        className="log-level" 
                        style={{ color: getLevelColor(log.level) }}
                      >
                        {log.level}
                      </span>
                      <span className="log-message">{log.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {!followLatest && logs.length > 0 && (
              <button className="btn-jump-latest btn-jump-latest-floating" onClick={jumpToLatest}>
                <ChevronDown size={16} />
                Jump to latest
              </button>
            )}
          </div>
        </>
      ) : (
        <div className="job-logs-content">
          <div className="job-logs-header-controls">
            <button onClick={fetchJobLogs} disabled={jobLogsLoading} className="btn-refresh">
              <RefreshCw size={18} className={jobLogsLoading ? 'spinning' : ''} />
              Refresh
            </button>
          </div>

          <div className="job-logs-layout">
            <div className="log-files-panel">
              <div className="log-section">
                <h2 onClick={() => toggleSection('workflow')}>
                  <span>Workflow</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'workflow' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'workflow' ? stopLiveTail() : startLiveTail('workflow') }}
                      title={liveJobType === 'workflow' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'workflow' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['workflow'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['workflow'] && (
                  <div className="log-section-content">
                    {jobLogs.workflow.length === 0 ? (
                      <p className="no-logs">No workflow logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.workflow.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('workflow', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('workflow', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('sync_one')}>
                  <span>Single Drive Sync</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'sync_one' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'sync_one' ? stopLiveTail() : startLiveTail('sync_one') }}
                      title={liveJobType === 'sync_one' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'sync_one' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['sync_one'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['sync_one'] && (
                  <div className="log-section-content">
                    {jobLogs.sync_one.length === 0 ? (
                      <p className="no-logs">No sync logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.sync_one.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('sync_one', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('sync_one', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('sync_all')}>
                  <span>Sync All Drives</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'sync_all' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'sync_all' ? stopLiveTail() : startLiveTail('sync_all') }}
                      title={liveJobType === 'sync_all' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'sync_all' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['sync_all'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['sync_all'] && (
                  <div className="log-section-content">
                    {jobLogs.sync_all.length === 0 ? (
                      <p className="no-logs">No sync-all logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.sync_all.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('sync_all', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('sync_all', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('poster_renamer')}>
                  <span>Poster Renamer</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'poster_renamer' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'poster_renamer' ? stopLiveTail() : startLiveTail('poster_renamer') }}
                      title={liveJobType === 'poster_renamer' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'poster_renamer' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['poster_renamer'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['poster_renamer'] && (
                  <div className="log-section-content">
                    {jobLogs.poster_renamer.length === 0 ? (
                      <p className="no-logs">No rename logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.poster_renamer.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('poster_renamer', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('poster_renamer', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('border_replacer')}>
                  <span>Border Replacer</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'border_replacer' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'border_replacer' ? stopLiveTail() : startLiveTail('border_replacer') }}
                      title={liveJobType === 'border_replacer' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'border_replacer' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['border_replacer'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['border_replacer'] && (
                  <div className="log-section-content">
                    {jobLogs.border_replacer.length === 0 ? (
                      <p className="no-logs">No border replacer logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.border_replacer.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('border_replacer', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('border_replacer', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('unmatched_assets')}>
                  <span>Unmatched Assets</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'unmatched_assets' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'unmatched_assets' ? stopLiveTail() : startLiveTail('unmatched_assets') }}
                      title={liveJobType === 'unmatched_assets' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'unmatched_assets' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['unmatched_assets'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['unmatched_assets'] && (
                  <div className="log-section-content">
                    {jobLogs.unmatched_assets.length === 0 ? (
                      <p className="no-logs">No unmatched assets logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.unmatched_assets.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('unmatched_assets', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('unmatched_assets', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('idarr')}>
                  <span>IDarr</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'idarr' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'idarr' ? stopLiveTail() : startLiveTail('idarr') }}
                      title={liveJobType === 'idarr' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'idarr' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['idarr'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['idarr'] && (
                  <div className="log-section-content">
                    {jobLogs.idarr.length === 0 ? (
                      <p className="no-logs">No IDarr logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.idarr.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('idarr', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('idarr', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>

              <div className="log-section">
                <h2 onClick={() => toggleSection('plex_upload')}>
                  <span>Plex Upload</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'plex_upload' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'plex_upload' ? stopLiveTail() : startLiveTail('plex_upload') }}
                      title={liveJobType === 'plex_upload' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'plex_upload' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['plex_upload'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['plex_upload'] && (
                  <div className="log-section-content">
                    {jobLogs.plex_upload.length === 0 ? (
                      <p className="no-logs">No plex upload logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.plex_upload.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('plex_upload', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.name}</span>
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('plex_upload', file.name)
                              }}
                              title="Download log"
                            >
                              <Download size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div className="log-viewer-panel">
              {liveJobType ? (
                <>
                  <div className="log-viewer-header">
                    <div className="live-viewer-title">
                      <Radio size={16} className={`live-icon ${liveConnected ? 'live-active' : 'live-inactive'}`} />
                      <h3>{liveJobType.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())} — Live</h3>
                      <span className={`live-status-badge ${liveConnected ? 'connected' : 'disconnected'}`}>
                        {liveConnected ? 'Connected' : 'Disconnected'}
                      </span>
                    </div>
                    <button className="btn-close-live" onClick={stopLiveTail} title="Close live view">
                      <X size={16} /> Close
                    </button>
                  </div>
                  <div className="log-viewer-content live-viewer-content" ref={liveContainerRef}>
                    <pre>{liveContent || 'Waiting for log output...'}</pre>
                  </div>
                </>
              ) : !selectedLog ? (
                <div className="log-viewer-empty">
                  <FileText size={48} />
                  <p>Select a log file to view its contents</p>
                </div>
              ) : (
                <>
                  <div className="log-viewer-header">
                    <div>
                      <h3>{selectedLog.file.name}</h3>
                      <p className="log-viewer-meta">
                        {formatFileSize(selectedLog.file.size)} • {formatDate(selectedLog.file.modified)}
                      </p>
                    </div>
                    <button
                      className="btn-download"
                      onClick={() => handleDownload(selectedLog.type, selectedLog.file.name)}
                    >
                      <Download size={18} />
                      Download
                    </button>
                  </div>
                  <div className="log-viewer-content">
                    {loadingContent ? (
                      <div className="log-viewer-loading">
                        <RefreshCw size={24} className="spinning" />
                        <p>Loading log content...</p>
                      </div>
                    ) : (
                      <pre>{logContent || 'Log file is empty'}</pre>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default Logs
