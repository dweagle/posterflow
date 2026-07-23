import client, { getData, postData } from './http'

export interface LogEntry {
  timestamp: string
  level: string
  message: string
}

// Logs functions
export const getLogs = async (lines: number = 100, level?: string): Promise<LogEntry[]> => {
  const params: { lines: number; level?: string } = { lines }
  if (level) params.level = level

  return getData('/api/logs/', { params })
}

export interface LogHistoryPage {
  entries: LogEntry[]
  cursor: number
}

// Older log entries ending at byte `cursor` (from the WS backlog frame or a previous page).
// A returned cursor of 0 means the start of the file was reached.
export const getLogHistory = async (cursor: number): Promise<LogHistoryPage> => {
  return getData('/api/logs/history', { params: { end: cursor } })
}

export interface LogSearchResult {
  entries: LogEntry[]
  total: number
  truncated: boolean
}

// Case-insensitive search over the WHOLE log file (newest `limit` matches kept).
export const searchLogs = async (q: string): Promise<LogSearchResult> => {
  return getData('/api/logs/search', { params: { q } })
}

export const getDebugStatus = async (): Promise<{ debug_enabled: boolean }> => {
  return getData('/api/logs/debug-status')
}

export const toggleDebug = async (enable: boolean) => {
  return postData('/api/logs/debug-toggle', null, {
    params: { enable }
  })
}

export const clearLogs = async (): Promise<void> => {
  await client.post('/api/logs/clear', null, {
    params: { confirm: true }
  })
}
