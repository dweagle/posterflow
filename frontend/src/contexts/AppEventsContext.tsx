import { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react'
import { getUnmatchedStats, getMakerIdarrPendingCount, getMakerMonitorNeededCount, getCommunityRequestCount, getWebSocketUrl, Job, UnmatchedStats } from '../api/client'

interface JobUpdate {
  id: number
  job_type: string
  status: string
}

interface JobMessage {
  jobs?: JobUpdate[]
}

interface AppEventsContextType {
  unmatchedStats: UnmatchedStats | null
  unmatchedCount: number
  idarrPendingCount: number
  makerMonitorNeededCount: number
  communityRequestCount: number
  jobs: Job[]
  refreshStats: () => Promise<void>
  refreshIdarrPendingCount: () => Promise<void>
  refreshMakerMonitorNeededCount: () => Promise<void>
  refreshCommunityRequestCount: () => Promise<void>
}

const AppEventsContext = createContext<AppEventsContextType | undefined>(undefined)

export function AppEventsProvider({ children }: { children: ReactNode }) {
  const [unmatchedStats, setUnmatchedStats] = useState<UnmatchedStats | null>(null)
  const [unmatchedCount, setUnmatchedCount] = useState<number>(0)
  const [idarrPendingCount, setIdarrPendingCount] = useState<number>(0)
  const [makerMonitorNeededCount, setMakerMonitorNeededCount] = useState<number>(0)
  const [communityRequestCount, setCommunityRequestCount] = useState<number>(0)
  const [jobs, setJobs] = useState<Job[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastJobStatusRef = useRef<{ [key: string]: string }>({})
  const isMountedRef = useRef<boolean>(true)
  // Timestamp of the last real user interaction — gates the community poll so it
  // only runs while the app is actually being used.
  const lastActivityRef = useRef<number>(Date.now())

  const refreshStats = async () => {
    try {
      const data = await getUnmatchedStats()
      setUnmatchedStats(data)
      const newCount = data.summary.grand_total.unmatched
      setUnmatchedCount(newCount)
    } catch (error) {
      console.error('[AppEventsContext] Failed to refresh stats:', error)
    }
  }

  const refreshIdarrPendingCount = async () => {
    try {
      const data = await getMakerIdarrPendingCount()
      setIdarrPendingCount(data.count)
    } catch {
      // silent - sidebar badge is best-effort
    }
  }

  const refreshMakerMonitorNeededCount = async () => {
    try {
      const data = await getMakerMonitorNeededCount()
      setMakerMonitorNeededCount(data.count)
    } catch {
      // silent - sidebar badge is best-effort
    }
  }

  const refreshCommunityRequestCount = async () => {
    try {
      const data = await getCommunityRequestCount()
      setCommunityRequestCount(data.count)
    } catch {
      // silent - sidebar badge is best-effort
    }
  }

  const connectWebSocket = () => {
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }

    const wsUrl = getWebSocketUrl()
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as JobMessage
        if (data.jobs) {
          setJobs(data.jobs as Job[])

          // Check if any unmatched detection or workflow job just completed
          data.jobs.forEach((job) => {
            const jobKey = `${job.job_type}_${job.id}`
            const previousStatus = lastJobStatusRef.current[jobKey]
            
            // Detect transition to a terminal status
            // Watch for both standalone "Unmatched Detection" and "Poster Workflow" (which may include unmatched detection)
            const isTerminal = job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled'
            if ((job.job_type === 'Unmatched Detection' || job.job_type === 'Poster Workflow') &&
                isTerminal &&
                previousStatus !== job.status) {
              // Refresh stats when unmatched detection finishes (success, failure, or stop)
              // so the UI never shows stale cached data after a run ends
              refreshStats()
            }

            // Refresh IDarr pending count when an IDarr job ends
            if (job.job_type === 'idarr' &&
                isTerminal &&
                previousStatus !== job.status) {
              void refreshIdarrPendingCount()
            }

            // Refresh maker monitor needed count when a monitor scan ends
            if (job.job_type === 'maker_monitor' &&
                isTerminal &&
                previousStatus !== job.status) {
              void refreshMakerMonitorNeededCount()
            }

            // Update tracked status
            lastJobStatusRef.current[jobKey] = job.status
          })
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error)
      }
    }

    ws.onerror = () => {
      // Silent error - will reconnect
    }

    ws.onclose = (event) => {
      if (wsRef.current !== ws) {
        return
      }

      wsRef.current = null
      // Only reconnect if not a clean close (code 1000) and component is still mounted
      if (event.code !== 1000 && isMountedRef.current) {
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 2000)
      }
    }
  }

  useEffect(() => {
    // Initial fetch
    refreshStats()
    void refreshIdarrPendingCount()
    void refreshMakerMonitorNeededCount()
    void refreshCommunityRequestCount()
    
    // Poll community request count every 60 seconds so new requests from
    // external users are reflected without a full page reload — but only while
    // the user is actively using the app. If they've been idle (no interaction)
    // past the active window, skip the poll so an open-but-unused app stops
    // hitting the server.
    const ACTIVE_WINDOW = 90_000  // treated as "in use" for 90s after any interaction
    const communityPollInterval = setInterval(() => {
      if (Date.now() - lastActivityRef.current < ACTIVE_WINDOW) {
        void refreshCommunityRequestCount()
      }
    }, 60_000)

    // Track real user interaction. When they come back after being idle, refresh
    // immediately so the badge is current without waiting for the next poll.
    const markActivity = () => {
      const wasIdle = Date.now() - lastActivityRef.current >= ACTIVE_WINDOW
      lastActivityRef.current = Date.now()
      if (wasIdle) void refreshCommunityRequestCount()
    }
    const ACTIVITY_EVENTS = ['mousedown', 'mousemove', 'keydown', 'scroll', 'touchstart']
    ACTIVITY_EVENTS.forEach((evt) => window.addEventListener(evt, markActivity, { passive: true }))

    // Connect to WebSocket for job updates
    const connectTimeout = setTimeout(() => {
      connectWebSocket()
    }, 100)

    return () => {
      isMountedRef.current = false
      clearInterval(communityPollInterval)
      clearTimeout(connectTimeout)
      ACTIVITY_EVENTS.forEach((evt) => window.removeEventListener(evt, markActivity))
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [])

  return (
    <AppEventsContext.Provider value={{ unmatchedStats, unmatchedCount, idarrPendingCount, makerMonitorNeededCount, communityRequestCount, jobs, refreshStats, refreshIdarrPendingCount, refreshMakerMonitorNeededCount, refreshCommunityRequestCount }}>
      {children}
    </AppEventsContext.Provider>
  )
}

export function useAppEvents() {
  const context = useContext(AppEventsContext)
  if (context === undefined) {
    throw new Error('useAppEvents must be used within an AppEventsProvider')
  }
  return context
}
