import { getData, postData, patchData, deleteData } from './http'

export type DriveStyleType = 'CL2K' | 'MM2K' | 'Custom'

export interface Drive {
  id: number
  name: string
  display_name: string | null
  drive_id: string
  style_type: DriveStyleType
  description: string | null
  subscribed: boolean
  sync_enabled: boolean
  priority: number
  custom_path: string | null
  is_custom: boolean
  is_deprecated: boolean
  last_synced: string | null
  poster_count: number
  sync_file_count: number
}

export interface DriveSubscriptionResponse {
  message: string
  drive: Drive
  removed_from_priority?: boolean
  restored_to_priority?: boolean
  added_to_priority?: boolean
  scan_job_id?: number | null
  files_deleted?: boolean
  poster_records_deleted?: number
}

export const getDrives = async (): Promise<Drive[]> => {
  return getData('/api/drives/')
}

export const subscribeDrive = async (driveId: number, addToPriority = false): Promise<DriveSubscriptionResponse> => {
  return postData(`/api/drives/${driveId}/subscribe?add_to_priority=${addToPriority}`)
}

export const unsubscribeDrive = async (driveId: number, deleteFiles: boolean = false): Promise<DriveSubscriptionResponse> => {
  return postData(`/api/drives/${driveId}/unsubscribe?delete_files=${deleteFiles}`)
}

export const updateDrive = async (driveId: number, updates: { priority?: number; custom_path?: string | null; style_type?: DriveStyleType; subscribed?: boolean; sync_enabled?: boolean; drive_id?: string }) => {
  return patchData(`/api/drives/${driveId}`, updates)
}

export const createCustomDrive = async (drive: {
  name: string
  drive_id?: string
  style_type?: DriveStyleType
  custom_path?: string
  priority?: number
  subscribed?: boolean
  sync_enabled?: boolean
}) => {
  return postData('/api/drives/custom', drive)
}

export const deleteDrive = async (driveId: number, deleteFiles: boolean = false) => {
  return deleteData(`/api/drives/${driveId}?delete_files=${deleteFiles}&confirm=true`)
}

export const reloadDrives = async (): Promise<{
  success: boolean
  added?: number
  updated?: number
  deprecated?: number
  reactivated?: number
  error?: string
}> => {
  return postData('/api/drives/reload')
}

// Preset drives that recently joined the community list (poster + artwork). The sidebar
// badge counts the unseen ones; the GDrives page tags all of them until they expire.
export interface NewDriveEntry {
  id: number
  drive_id: string
  name: string
  display_name: string | null
  style_type: DriveStyleType | null
  added_at: string
  seen: boolean
}

export interface NewDrives {
  poster: NewDriveEntry[]
  artwork: NewDriveEntry[]
  unseen_count: number
}

export const EMPTY_NEW_DRIVES: NewDrives = { poster: [], artwork: [], unseen_count: 0 }

export const getNewDrives = async (): Promise<NewDrives> => {
  return getData('/api/drives/new')
}

export const markNewDrivesSeen = async (): Promise<void> => {
  await postData('/api/drives/new/seen')
}

export const dismissNewDrives = async (): Promise<void> => {
  await postData('/api/drives/new/dismiss')
}
