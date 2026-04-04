import { createContext, useContext, useState, useEffect, useRef, ReactNode } from 'react'
import { getUnmatchedStats, getWebSocketUrl, Job, UnmatchedStats } from '../api/client'

interface JobUpdate {
  id: number
  job_type: string
  status: string
}

interface JobMessage {
  jobs?: JobUpdate[]
}

interface UnmatchedContextType {
  unmatchedStats: UnmatchedStats | null
  unmatchedCount: number
  jobs: Job[]
  refreshStats: () => Promise<void>
}

const UnmatchedContext = createContext<UnmatchedContextType | undefined>(undefined)

export function UnmatchedProvider({ children }: { children: ReactNode }) {
  const [unmatchedStats, setUnmatchedStats] = useState<UnmatchedStats | null>(null)
  const [unmatchedCount, setUnmatchedCount] = useState<number>(0)
  const [jobs, setJobs] = useState<Job[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastJobStatusRef = useRef<{ [key: string]: string }>({})
  const isMountedRef = useRef<boolean>(true)

  const refreshStats = async () => {
    try {
      const data = await getUnmatchedStats()
      setUnmatchedStats(data)
      const newCount = data.summary.grand_total.unmatched
      setUnmatchedCount(newCount)
    } catch (error) {
      console.error('[UnmatchedContext] Failed to refresh stats:', error)
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
            
            // Detect transition to completed status
            // Watch for both standalone "Unmatched Detection" and "Poster Workflow" (which may include unmatched detection)
            if ((job.job_type === 'Unmatched Detection' || job.job_type === 'Poster Workflow') && 
                job.status === 'completed' && 
                previousStatus !== 'completed') {
              // Refresh stats when unmatched detection completes (standalone or as part of workflow)
              refreshStats()
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
    
    // Connect to WebSocket for job updates
    const connectTimeout = setTimeout(() => {
      connectWebSocket()
    }, 100)

    return () => {
      isMountedRef.current = false
      clearTimeout(connectTimeout)
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
    <UnmatchedContext.Provider value={{ unmatchedStats, unmatchedCount, jobs, refreshStats }}>
      {children}
    </UnmatchedContext.Provider>
  )
}

export function useUnmatched() {
  const context = useContext(UnmatchedContext)
  if (context === undefined) {
    throw new Error('useUnmatched must be used within an UnmatchedProvider')
  }
  return context
}
