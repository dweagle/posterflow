import { useState, useEffect, useRef, useMemo, useCallback, memo } from 'react'
import LogPane, { type LogPaneHandle } from '../components/LogPane'
import { getLogs, getLogHistory, searchLogs, clearLogs, LogEntry, getJobLogs, getJobLogContent, downloadJobLog, connectJobLogLiveWS, JobLogs as JobLogsData, JobLogFile } from '../api/client'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import { Trash2, RefreshCw, Download, FileText, ChevronDown, ChevronUp, Monitor, Briefcase, Radio, Search, X } from 'lucide-react'
import './Logs.css'

const LOGS_TAB_STORAGE_KEY = 'posterflow.logs.activeTab'
type LogsTab = 'system' | 'job'

const isLogsTab = (value: string): value is LogsTab => {
  return ['system', 'job'].includes(value)
}

// Entries carry a stable id so appends don't re-render every existing row.
type DisplayLogEntry = LogEntry & { id: number }

type HistoryStatus = 'idle' | 'loading' | 'done'

const LogsHistoryHeader = ({ status }: { status: HistoryStatus }) => {
  if (status === 'loading') return <div className="logs-history-note">Loading older logs…</div>
  if (status === 'done') return <div className="logs-history-note">— beginning of log file —</div>
  return null
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

// Text with every case-insensitive occurrence of `term` wrapped in a highlight mark.
const HighlightedText = memo(({ text, term }: { text: string; term: string }) => {
  if (!term) return <>{text}</>
  const lower = text.toLowerCase()
  const needle = term.toLowerCase()
  const parts: React.ReactNode[] = []
  let pos = 0
  for (let hit = lower.indexOf(needle, pos); hit !== -1; hit = lower.indexOf(needle, pos)) {
    if (hit > pos) parts.push(text.slice(pos, hit))
    parts.push(<mark key={hit} className="log-match">{text.slice(hit, hit + needle.length)}</mark>)
    pos = hit + needle.length
  }
  parts.push(text.slice(pos))
  return <>{parts}</>
})

// One raw job-log line with the 42ch hanging indent (timestamp 17 + " | " + level 7 +
// " | " + tag block 12), optional term highlighting, and focused-match tint.
const VirtualLogLine = memo(({ line, term, active }: { line: string; term?: string; active?: boolean }) => (
  <div className={`log-hang-line${active ? ' log-hang-line-active' : ''}`}>
    {line === '' ? '\u00A0' : term ? <HighlightedText text={line} term={term} /> : line}
  </div>
))

const LogRow = memo(({ log }: { log: DisplayLogEntry }) => (
  <div className="log-entry">
    <span className="log-timestamp">{log.timestamp}</span>
    <span className="log-level" style={{ color: getLevelColor(log.level) }}>
      {log.level}
    </span>
    <span className="log-message">{log.message}</span>
  </div>
))

// A whole-file search result row: the term highlighted, the focused match tinted.
const SearchLogRow = memo(({ log, term, active }: { log: DisplayLogEntry; term: string; active: boolean }) => (
  <div className={`log-entry${active ? ' log-entry-active-match' : ''}`}>
    <span className="log-timestamp">{log.timestamp}</span>
    <span className="log-level" style={{ color: getLevelColor(log.level) }}>
      {log.level}
    </span>
    <span className="log-message"><HighlightedText text={log.message} term={term} /></span>
  </div>
))

function Logs() {
  const [activeTab, setActiveTab] = useState<LogsTab>(() => {
    const savedTab = localStorage.getItem(LOGS_TAB_STORAGE_KEY)
    if (savedTab && isLogsTab(savedTab)) {
      return savedTab
    }
    return 'system'
  })
  
  const [logs, setLogs] = useState<DisplayLogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<string>('all')
  // Search is whole-file only (Enter / button) — typing never filters the live list.
  const [searchInput, setSearchInput] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [showConfirm, setShowConfirm] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const nextLogIdRef = useRef(0)
  const isMountedRef = useRef<boolean>(true)
  const logPaneRef = useRef<LogPaneHandle | null>(null)
  const [followLatest, setFollowLatest] = useState(true)
  const followLatestRef = useRef(true)
  const handlePinnedChange = useCallback((pinned: boolean) => {
    followLatestRef.current = pinned
    setFollowLatest(pinned)
  }, [])
  const jumpToLatest = useCallback(() => logPaneRef.current?.jumpToLatest(), [])
  // Older-history paging: byte cursor from the WS backlog frame; ids grow downward so
  // prepended rows keep stable keys distinct from live ones.
  const historyCursorRef = useRef<number | null>(null)
  const historyLoadingRef = useRef(false)
  const historyIdRef = useRef(-1)
  const [historyStatus, setHistoryStatus] = useState<HistoryStatus>('idle')
  // Whole-file search (Enter in the search box); non-null replaces the live list.
  const [fileSearch, setFileSearch] = useState<{ term: string; entries: DisplayLogEntry[]; total: number; truncated: boolean } | null>(null)
  const [fileSearchLoading, setFileSearchLoading] = useState(false)
  // Which result row is focused while cycling with the ▲▼ buttons.
  const [fileMatchIdx, setFileMatchIdx] = useState(0)
  const searchPaneRef = useRef<LogPaneHandle | null>(null)
  
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
    artwork_pull: [],
  })
  const [jobLogsLoading, setJobLogsLoading] = useState(false)
  const [selectedLog, setSelectedLog] = useState<{ type: string; file: JobLogFile } | null>(null)
  const [logContent, setLogContent] = useState<string>('')
  const [loadingContent, setLoadingContent] = useState(false)
  // Find-in-file for the job log viewer (client-side; the whole file is already loaded).
  const [jobSearch, setJobSearch] = useState('')
  const [jobMatchIdx, setJobMatchIdx] = useState(-1)
  const jobPaneRef = useRef<LogPaneHandle | null>(null)
  const [collapsedSections, setCollapsedSections] = useState<{ [key: string]: boolean }>({
    sync_one: true,
    sync_all: true,
    plex_upload: true,
    workflow: false,  // Workflow section open by default
    poster_renamer: true,
    border_replacer: true,
    unmatched_assets: true,
    idarr: true,
    artwork_pull: true,
  })

  // Live-tail state
  const [liveJobType, setLiveJobType] = useState<string | null>(null)
  const [liveContent, setLiveContent] = useState<string>('')
  const [liveConnected, setLiveConnected] = useState(false)
  const liveWsRef = useRef<WebSocket | null>(null)
  const livePaneRef = useRef<LogPaneHandle | null>(null)
  const [liveFollowLatest, setLiveFollowLatest] = useState(true)
  const jumpLiveToLatest = useCallback(() => livePaneRef.current?.jumpToLatest(), [])
  
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
          artwork_pull: true,
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
      historyCursorRef.current = null // REST mode carries no history cursor
      setHistoryStatus('idle')
      setLogs(data.map(e => ({ ...e, id: nextLogIdRef.current++ })))
    }, 'Error fetching logs:')
    if (loading) {
      setLoading(false)
    }
  }

  // Scrolled to the top: pull the previous window of the log file in front of the list.
  const loadOlderHistory = async () => {
    const cursor = historyCursorRef.current
    if (!cursor || historyLoadingRef.current) return
    historyLoadingRef.current = true
    setHistoryStatus('loading')
    try {
      const page = await getLogHistory(cursor)
      historyCursorRef.current = page.cursor > 0 ? page.cursor : null
      const stamped = page.entries.map(e => ({ ...e, id: historyIdRef.current-- }))
      if (stamped.length > 0) {
        setLogs(prev => [...stamped, ...prev])
      }
      setHistoryStatus(page.cursor > 0 ? 'idle' : 'done')
    } catch (error) {
      console.error('Error loading older logs:', error)
      setHistoryStatus('idle')
    } finally {
      historyLoadingRef.current = false
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
      if (wsRef.current !== ws) return // superseded before opening
      // The server replays the recent backlog on every connect; start from a clean
      // slate so a reconnect can't append older lines after newer ones.
      historyCursorRef.current = null
      setHistoryStatus('idle')
      setLogs([])
      setLoading(false)
    }

    // Entries apply the moment a frame arrives — no buffering or pacing. The server
    // already coalesces each ~0.5s of new lines into one batch frame. The 20k head-trim
    // only runs while following the tail, so loaded history isn't eaten out from under
    // a reader who scrolled up.
    const appendLogEntries = (entries: LogEntry[]) => {
      if (entries.length === 0) return
      const stamped = entries.map(e => ({ ...e, id: nextLogIdRef.current++ }))
      setLogs(prev => {
        const items = [...prev, ...stamped]
        return items.length > 20000 && followLatestRef.current ? items.slice(items.length - 20000) : items
      })
    }

    const isLogEntry = (m: Partial<LogEntry>): m is LogEntry =>
      typeof m.timestamp === 'string' && typeof m.level === 'string' && typeof m.message === 'string'

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return // superseded — never mix streams from a dying socket
      try {
        const message = JSON.parse(event.data) as Partial<LogEntry> & {
          type?: string
          heartbeat?: number
          entries?: LogEntry[]
          history_cursor?: number
        }

        if (message.type === 'heartbeat' || typeof message.heartbeat === 'number') {
          return
        }

        if (message.type === 'batch' && Array.isArray(message.entries)) {
          if (typeof message.history_cursor === 'number') {
            historyCursorRef.current = message.history_cursor > 0 ? message.history_cursor : null
            setHistoryStatus(message.history_cursor > 0 ? 'idle' : 'done')
          }
          appendLogEntries(message.entries.filter(isLogEntry))
          return
        }

        if (isLogEntry(message)) {
          appendLogEntries([message])
        }
      } catch (error) {
        console.error('Error parsing log message:', error)
      }
    }

    ws.onerror = (error) => {
      console.error('[Logs] WebSocket error:', error)
    }

    ws.onclose = (event) => {
      // An old socket's close fires AFTER its replacement is stored — only clear the
      // ref (and consider reconnecting) if this socket is still the current one.
      if (wsRef.current !== ws) return
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
      historyCursorRef.current = null
      setHistoryStatus('idle')
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
          artwork_pull: data.artwork_pull ?? [],
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
    // Re-pin in case a previous session was left detached (no-op on first open).
    livePaneRef.current?.jumpToLatest()
    setLiveFollowLatest(true)
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

  const liveLines = useMemo(() => (liveContent || 'Waiting for log output...').split('\n'), [liveContent])

  // Job log viewer: split once, find the matching line numbers for the find bar.
  const jobLines = useMemo(() => logContent.split('\n'), [logContent])
  const jobMatchLines = useMemo(() => {
    const term = jobSearch.trim().toLowerCase()
    if (term.length < 2) return []
    const out: number[] = []
    jobLines.forEach((line, i) => {
      if (line.toLowerCase().includes(term)) out.push(i)
    })
    return out
  }, [jobLines, jobSearch])

  const handleJobSearchChange = (value: string) => {
    setJobSearch(value)
    setJobMatchIdx(-1)
  }

  // Opening a different file resets the find bar.
  useEffect(() => {
    setJobSearch('')
    setJobMatchIdx(-1)
  }, [selectedLog])

  const cycleJobMatch = (direction: 1 | -1) => {
    if (jobMatchLines.length === 0) return
    const next = jobMatchIdx === -1
      ? (direction === 1 ? 0 : jobMatchLines.length - 1)
      : (jobMatchIdx + direction + jobMatchLines.length) % jobMatchLines.length
    setJobMatchIdx(next)
    jobPaneRef.current?.scrollToItem(jobMatchLines[next])
  }

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

  const filteredLogs = useMemo(
    () => logs.filter(log => filter === 'all' || log.level === filter),
    [logs, filter]
  )

  // Whole-file results still honor the level filter buttons.
  const fileSearchFiltered = useMemo(
    () => (fileSearch ? fileSearch.entries.filter(log => filter === 'all' || log.level === filter) : null),
    [fileSearch, filter]
  )

  // Every result row IS a match, so cycling steps row to row (with wraparound).
  const fileMatchClamped = fileSearchFiltered ? Math.min(fileMatchIdx, Math.max(0, fileSearchFiltered.length - 1)) : 0
  const cycleFileMatch = (direction: 1 | -1) => {
    if (!fileSearchFiltered || fileSearchFiltered.length === 0) return
    const next = (fileMatchClamped + direction + fileSearchFiltered.length) % fileSearchFiltered.length
    setFileMatchIdx(next)
    searchPaneRef.current?.scrollToItem(fileSearchFiltered[next].id)
  }

  const runFileSearch = async () => {
    const term = searchInput.trim()
    if (term.length < 2 || fileSearchLoading) return
    setFileSearchLoading(true)
    try {
      const result = await searchLogs(term)
      setFileSearch({
        term,
        entries: result.entries.map((e, i) => ({ ...e, id: i })),
        total: result.total,
        truncated: result.truncated,
      })
      setFileMatchIdx(Math.max(0, result.entries.length - 1)) // start at the newest match
    } catch (error) {
      console.error('Error searching log file:', error)
      showToast('Log file search failed', 'error')
    } finally {
      setFileSearchLoading(false)
    }
  }

  const handleSearchChange = (value: string) => {
    setSearchInput(value)
    if (fileSearch) setFileSearch(null) // edited term — results are stale, back to the live view
  }

  const clearSearch = () => {
    setSearchInput('')
    setFileSearch(null)
  }

  return (
    <div className={`page-container logs-page${activeTab === 'system' ? ' logs-page-fill' : ''}`}>
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
          <div className="logs-search-row">
            <div className="logs-search">
              <div className="logs-search-box">
                <input
                  type="text"
                  placeholder="Search the log file…"
                  value={searchInput}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') runFileSearch()
                    if (e.key === 'Escape') clearSearch()
                  }}
                />
                {(searchInput.trim() !== '' || fileSearch) && (
                  <button className="logs-search-clear-inner" onClick={clearSearch} title="Clear search">
                    <X size={14} />
                  </button>
                )}
              </div>
              <button className="btn-log-search" onClick={runFileSearch} disabled={fileSearchLoading || searchInput.trim().length < 2}>
                <Search size={15} />
                Search
              </button>
              <span className="logs-search-meta">
                {fileSearch && fileSearchFiltered && (
                  <span className="logs-match-nav" title={`${fileSearch.total.toLocaleString()} matches in file${fileSearch.truncated ? `, showing last ${fileSearch.entries.length.toLocaleString()}` : ''}`}>
                    <button className="btn-match-nav" onClick={() => cycleFileMatch(-1)} title="Previous (older) match">
                      <ChevronUp size={14} />
                    </button>
                    <button className="btn-match-nav" onClick={() => cycleFileMatch(1)} title="Next (newer) match">
                      <ChevronDown size={14} />
                    </button>
                    <span className="logs-match-counter">
                      {fileSearchFiltered.length === 0 ? '0 / 0' : `${(fileMatchClamped + 1).toLocaleString()} / ${fileSearchFiltered.length.toLocaleString()}`}
                      {` (${fileSearch.total.toLocaleString()})`}
                    </span>
                  </span>
                )}
              </span>
            </div>
          </div>

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
            <div className="logs-container">
              {loading ? (
                <div className="logs-loading">Loading logs...</div>
              ) : fileSearchFiltered ? (
                fileSearchFiltered.length === 0 ? (
                  <div className="no-logs">No matches in the log file</div>
                ) : (
                  <LogPane
                    key="file-search"
                    ref={searchPaneRef}
                    className="logs-list"
                    items={fileSearchFiltered}
                    itemKey={(log) => log.id}
                    renderItem={(log, index) => (
                      <SearchLogRow log={log} term={fileSearch?.term ?? ''} active={index === fileMatchClamped} />
                    )}
                    initialBottom
                  />
                )
              ) : filteredLogs.length === 0 ? (
                <div className="no-logs">No logs found</div>
              ) : filter !== 'all' ? (
                // Level-filtered view: static list on its own keyed instance, no follow.
                <LogPane
                  key="filtered"
                  className="logs-list"
                  items={filteredLogs}
                  itemKey={(log) => log.id}
                  renderItem={(log) => <LogRow log={log} />}
                  initialBottom
                />
              ) : (
                <LogPane
                  key="live"
                  ref={logPaneRef}
                  className="logs-list"
                  items={filteredLogs}
                  itemKey={(log) => log.id}
                  renderItem={(log) => <LogRow log={log} />}
                  follow
                  onPinnedChange={handlePinnedChange}
                  onStartReached={loadOlderHistory}
                  header={<LogsHistoryHeader status={historyStatus} />}
                />
              )}
            </div>

            {!fileSearch && !followLatest && logs.length > 0 && (
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                  <span>Asset Renamer</span>
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                <h2 onClick={() => toggleSection('artwork_pull')}>
                  <span>Artwork Pull</span>
                  <div className="section-header-actions">
                    <button
                      className={`live-btn ${liveJobType === 'artwork_pull' ? 'active' : ''}`}
                      onClick={(e) => { e.stopPropagation(); liveJobType === 'artwork_pull' ? stopLiveTail() : startLiveTail('artwork_pull') }}
                      title={liveJobType === 'artwork_pull' ? 'Stop live view' : 'Live tail log'}
                    >
                      <Radio size={10} />{liveJobType === 'artwork_pull' ? 'Stop' : 'Live'}
                    </button>
                    <ChevronDown size={18} className={`collapse-icon ${collapsedSections['artwork_pull'] ? 'collapsed' : ''}`} />
                  </div>
                </h2>
                {!collapsedSections['artwork_pull'] && (
                  <div className="log-section-content">
                    {jobLogs.artwork_pull.length === 0 ? (
                      <p className="no-logs">No Artwork Pull logs yet</p>
                    ) : (
                      <div className="log-list">
                        {jobLogs.artwork_pull.map((file) => (
                          <div
                            key={file.name}
                            className={`log-item ${selectedLog?.file.name === file.name ? 'active' : ''}`}
                            onClick={() => viewLog('artwork_pull', file)}
                          >
                            <div className="log-item-content">
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
                              <span className="log-size">{formatFileSize(file.size)}</span>
                            </div>
                            <button
                              className="btn-download-mini"
                              onClick={(e) => {
                                e.stopPropagation()
                                handleDownload('artwork_pull', file.name)
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
                              <span className="log-name">{file.job_id != null ? `Job #${file.job_id}` : file.name}</span>
                              {file.started_at && <span className="log-date">{file.started_at}</span>}
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
                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                      {!liveFollowLatest && (
                        <button className="btn-jump-latest" onClick={jumpLiveToLatest} title="Resume following the latest output">
                          <ChevronDown size={16} /> Jump to latest
                        </button>
                      )}
                      <button className="btn-close-live" onClick={stopLiveTail} title="Close live view">
                        <X size={16} /> Close
                      </button>
                    </div>
                  </div>
                  <div className="log-viewer-content live-viewer-content">
                    <LogPane
                      ref={livePaneRef}
                      className="log-hang"
                      items={liveLines}
                      itemKey={(_line, index) => index}
                      renderItem={(line) => <VirtualLogLine line={line} />}
                      follow
                      onPinnedChange={setLiveFollowLatest}
                    />
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
                    <div className="log-viewer-find">
                      <div className="logs-search-box">
                        <input
                          type="text"
                          placeholder="Find in file…"
                          value={jobSearch}
                          onChange={(e) => handleJobSearchChange(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') cycleJobMatch(e.shiftKey ? -1 : 1)
                            if (e.key === 'Escape') handleJobSearchChange('')
                          }}
                        />
                        {jobSearch !== '' && (
                          <button className="logs-search-clear-inner" onClick={() => handleJobSearchChange('')} title="Clear">
                            <X size={12} />
                          </button>
                        )}
                      </div>
                      {jobSearch.trim().length >= 2 && (
                        <>
                          <button className="btn-match-nav" onClick={() => cycleJobMatch(-1)} title="Previous match">
                            <ChevronUp size={14} />
                          </button>
                          <button className="btn-match-nav" onClick={() => cycleJobMatch(1)} title="Next match">
                            <ChevronDown size={14} />
                          </button>
                          <span className="logs-match-counter">
                            {jobMatchLines.length === 0 ? '0 / 0'
                              : `${jobMatchIdx === -1 ? '–' : jobMatchIdx + 1} / ${jobMatchLines.length.toLocaleString()}`}
                          </span>
                        </>
                      )}
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
                    ) : !logContent ? (
                      <div className="no-logs">Log file is empty</div>
                    ) : (
                      <LogPane
                        ref={jobPaneRef}
                        className="log-hang"
                        items={jobLines}
                        itemKey={(_line, index) => index}
                        renderItem={(line, index) => (
                          <VirtualLogLine
                            line={line}
                            term={jobSearch.trim().length >= 2 ? jobSearch.trim() : undefined}
                            active={jobMatchIdx !== -1 && jobMatchLines[jobMatchIdx] === index}
                          />
                        )}
                      />
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
