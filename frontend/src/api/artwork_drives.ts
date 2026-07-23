import { getData, postData, patchData, deleteData } from './http'

// Artwork drives hold logos / backdrops / square art. Unlike poster drives they have
// no style_type (no MM2K/CL2K), but they DO carry a priority so users can order
// them across makers.
export interface ArtworkDrive {
  id: number
  name: string
  display_name: string | null
  drive_id: string
  description: string | null
  subscribed: boolean
  sync_enabled: boolean
  priority: number
  custom_path: string | null
  is_custom: boolean
  is_deprecated: boolean
  last_synced: string | null
  sync_file_count: number
  last_files_transferred: number
  artwork_count: number
  logo_count: number
  background_count: number
  squareart_count: number
}

export const getArtworkDrives = async (): Promise<ArtworkDrive[]> => {
  return getData('/api/artwork-drives/')
}

export interface ArtworkDriveSubscriptionResponse {
  message?: string
  added_to_priority?: boolean
  scan_job_id?: number | null
}

export const subscribeArtworkDrive = async (driveId: number, addToPriority = false): Promise<ArtworkDriveSubscriptionResponse> => {
  return postData(`/api/artwork-drives/${driveId}/subscribe?add_to_priority=${addToPriority}`)
}

export const unsubscribeArtworkDrive = async (driveId: number) => {
  return postData(`/api/artwork-drives/${driveId}/unsubscribe`)
}

export const updateArtworkDrive = async (
  driveId: number,
  updates: { priority?: number; custom_path?: string | null; subscribed?: boolean; sync_enabled?: boolean; drive_id?: string }
) => {
  return patchData(`/api/artwork-drives/${driveId}`, updates)
}

export const createCustomArtworkDrive = async (drive: {
  name: string
  drive_id?: string
  custom_path?: string
  priority?: number
  subscribed?: boolean
  sync_enabled?: boolean
}) => {
  return postData('/api/artwork-drives/custom', drive)
}

export const deleteArtworkDrive = async (driveId: number, deleteFiles: boolean = false) => {
  return deleteData(`/api/artwork-drives/${driveId}?delete_files=${deleteFiles}&confirm=true`)
}

export const reloadArtworkDrives = async (): Promise<{
  success: boolean
  added?: number
  updated?: number
  deprecated?: number
  reactivated?: number
  error?: string
}> => {
  return postData('/api/artwork-drives/reload')
}

export const syncArtworkDrive = async (driveId: number): Promise<{ job_id: number; message: string }> => {
  return postData(`/api/artwork-drives/${driveId}/sync`)
}

export const syncAllArtworkDrives = async (): Promise<{ job_id: number; message: string }> => {
  return postData('/api/artwork-drives/sync-all')
}

export interface ArtworkDrivePriority {
  drive_ids: number[]
}

export const getArtworkDrivePriority = async (): Promise<ArtworkDrivePriority> => {
  return getData('/api/artwork-drives/priority')
}

export const saveArtworkDrivePriority = async (driveIds: number[]): Promise<{ success: boolean }> => {
  return postData('/api/artwork-drives/priority', { drive_ids: driveIds })
}
