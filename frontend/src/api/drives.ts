import { getData, postData, patchData, deleteData } from './http'

export type DriveStyleType = 'CL2K' | 'MM2K' | 'Custom'

export interface Drive {
  id: number
  name: string
  drive_id: string
  style_type: DriveStyleType
  subscribed: boolean
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
}

export const getDrives = async (): Promise<Drive[]> => {
  return getData('/api/drives/')
}

export const subscribeDrive = async (driveId: number): Promise<DriveSubscriptionResponse> => {
  return postData(`/api/drives/${driveId}/subscribe`)
}

export const unsubscribeDrive = async (driveId: number): Promise<DriveSubscriptionResponse> => {
  return postData(`/api/drives/${driveId}/unsubscribe`)
}

export const updateDrive = async (driveId: number, updates: { priority?: number; custom_path?: string; style_type?: DriveStyleType; subscribed?: boolean; drive_id?: string }) => {
  return patchData(`/api/drives/${driveId}`, updates)
}

export const createCustomDrive = async (drive: {
  name: string
  drive_id?: string
  style_type?: DriveStyleType
  custom_path?: string
  priority?: number
  subscribed?: boolean
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
