import { getData, postData, putData, deleteData } from './http'

// Stats functions
export interface Stats {
  total_posters: number
  subscribed_posters: number
  posters_by_type: {
    cl2k: number
    mm2k: number
    custom: number
  }
  drives: {
    total: number
    subscribed: number
    synced: number
    by_type: {
      cl2k: number
      mm2k: number
      custom: number
    }
  }
  subscribed_drives_by_type: {
    cl2k: number
    mm2k: number
    custom: number
  }
}

export const getStats = async (): Promise<Stats> => {
  return getData('/api/stats/')
}

export interface RecentSyncedPoster {
  id: number
  file_name: string
  drive_id: string
  drive_name: string
  downloaded_at: string | null
  image_url: string
}

export interface RecentSyncedPosterResponse {
  items: RecentSyncedPoster[]
  count: number
}

export const getRecentSyncedPosters = async (limit: number = 100): Promise<RecentSyncedPosterResponse> => {
  return getData(`/api/stats/recent-posters?limit=${limit}`)
}

export interface PosterActivityStats {
  posters_matched_today: number
  posters_renamed_today: number
  posters_renamed_week: number
  posters_borders_replaced_today: number
  synced_new_today: number
  synced_replaced_today: number
  synced_deleted_today: number
  synced_new_week: number
  synced_replaced_week: number
  synced_deleted_week: number
  synced_new_month: number
  synced_replaced_month: number
  synced_deleted_month: number
}

export const getPosterActivityStats = async (): Promise<PosterActivityStats> => {
  return getData('/api/stats/poster-daily-activity')
}

// Schedule types
export interface Schedule {
  id: number
  job_type: string
  name: string
  enabled: boolean
  drive_id: number | null  // If set, sync only this drive; if null, check drive_group
  drive_group: string | null  // 'CL2K', 'MM2K', or 'Custom' - sync all drives of this type
  schedule_type: string  // 'disabled', 'hourly', 'daily', 'weekly', 'cron'
  schedule_value: string | null
  job_config: string | null  // JSON string for job-specific config (e.g. {"sync_after_run": true} for idarr)
  last_run: string | null
  next_run: string | null
}

export const getSchedules = async (jobType?: string): Promise<Schedule[]> => {
  const params = jobType ? `?job_type=${jobType}` : ''
  return getData(`/api/schedules/${params}`)
}

export const createSchedule = async (data: Partial<Schedule>): Promise<Schedule> => {
  return postData('/api/schedules/', data)
}

export const updateSchedule = async (
  scheduleId: number,
  scheduleUpdate: Partial<Schedule>
): Promise<Schedule> => {
  return putData(`/api/schedules/${scheduleId}`, scheduleUpdate)
}

export const deleteSchedule = async (scheduleId: number): Promise<void> => {
  await deleteData<void>(`/api/schedules/${scheduleId}`)
}
