import { getData, postData } from './http'

// Backup and Restore
export const downloadBackup = async (): Promise<Blob> => {
  return getData('/api/backup/', {
    responseType: 'blob'
  })
}

export const uploadBackup = async (file: File): Promise<{ message: string; restored_files: Record<string, boolean> }> => {
  const formData = new FormData()
  formData.append('file', file)
  return postData('/api/backup/', formData, {
    params: { confirm: true },
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

// Database Maintenance
export interface DatabaseStats {
  total_records: number
  orphaned_count: number
  by_location: Record<string, number>
  by_drive: Record<string, number>
  would_delete: number
  sample_records?: Array<{
    id: number
    file_name: string
    file_path: string
    drive_id: string
    drive_name: string
    reason?: string
    downloaded_at: string | null
    last_processed: string | null
  }>
  orphaned_records?: Array<{
    id: number
    file_name: string
    file_path: string
    drive_id: string
    drive_name: string
    reason?: string
    downloaded_at: string | null
    last_processed: string | null
  }>
}

export const getDatabaseStats = async (): Promise<DatabaseStats> => {
  return getData('/api/database/cleanup/preview?full=true')
}

export const cleanupDatabase = async (): Promise<{ deleted: number; message: string }> => {
  return postData('/api/database/cleanup/execute?confirm=true')
}
