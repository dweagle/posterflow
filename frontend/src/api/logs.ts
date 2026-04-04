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
