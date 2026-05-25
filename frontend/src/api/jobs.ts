import { API_URL, getData, postData } from './http'

export interface Job {
  id: number
  job_type: string
  status: string
  progress: number
  message: string | null
  error: string | null
  started_at: string
  completed_at: string | null
}

// Jobs functions
export const getJobs = async (): Promise<Job[]> => {
  return getData('/api/jobs/')
}

export const getJob = async (jobId: number): Promise<Job> => {
  return getData(`/api/jobs/${jobId}`)
}

export const startSync = async (driveId: number): Promise<Job> => {
  return postData('/api/jobs/sync', { drive_id: driveId })
}

export const startSyncAll = async (): Promise<Job> => {
  return postData('/api/jobs/sync-all')
}

export const startIdarr = async (
  dryRun: boolean = false,
  syncTargetIndex?: number,
  sourceFilenames?: string[],
  syncAfterRun: boolean = false,
): Promise<Job> => {
  return postData('/api/jobs/idarr', {
    dry_run: dryRun,
    sync_target_index: syncTargetIndex,
    source_filenames: Array.isArray(sourceFilenames) && sourceFilenames.length > 0 ? sourceFilenames : undefined,
    sync_after_run: syncAfterRun,
  })
}

export const startIdarrSync = async (
  personalDriveId?: string,
  options?: { source_dir?: string; sync_target_index?: number }
): Promise<Job> => {
  return postData('/api/jobs/idarr-sync', {
    personal_drive_id: personalDriveId,
    source_dir: options?.source_dir,
    sync_target_index: options?.sync_target_index,
  })
}

// Job Logs functions
export interface JobLogFile {
  name: string
  path: string
  size: number
  modified: number
  job_id: number | null
  started_at: string | null
}

export interface JobLogs {
  sync_one: JobLogFile[]
  sync_all: JobLogFile[]
  plex_upload: JobLogFile[]
  workflow: JobLogFile[]
  poster_renamer: JobLogFile[]
  border_replacer: JobLogFile[]
  unmatched_assets: JobLogFile[]
  idarr: JobLogFile[]
}

export const getJobLogs = async (): Promise<JobLogs> => {
  return getData('/api/job-logs/')
}

export const getJobLogContent = async (jobType: string, filename: string): Promise<{ content: string; filename: string }> => {
  return getData(`/api/job-logs/${jobType}/${filename}`)
}

export const downloadJobLog = (jobType: string, filename: string): string => {
  return `${API_URL}/api/job-logs/${jobType}/${filename}/download`
}

export const connectJobLogLiveWS = (jobType: string): WebSocket => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${protocol}//${window.location.hostname}:${window.location.port}/api/job-logs/${jobType}/live`
  return new WebSocket(wsUrl)
}
