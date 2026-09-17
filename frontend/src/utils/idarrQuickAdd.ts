import { getMakerIdarrConfig, uploadMakerIdarrFiles, startIdarr, type MakerIdarrConfig } from '../api/client'
import { notifyIdarrTargetedRun } from './idarrTargetedRun'

export interface IdarrQuickAddResult {
  uploadedCount: number
  skippedCount: number
  /** Targeted auto-rename job, when quick add auto-rename is on and something was uploaded. */
  jobId: number | null
  /** Set when the upload worked but the auto-rename run could not be started. */
  autoRenameError: unknown
}

// One quick-add path for every drop target (sidebar, community cards, maker cards): upload into
// the sync target's folder, then start the targeted run when auto-rename is on. Callers toast.
export async function quickAddFilesToIdarr(syncTargetIndex: number, files: File[], config?: MakerIdarrConfig): Promise<IdarrQuickAddResult> {
  const resolvedConfig = config ?? await getMakerIdarrConfig()
  const response = await uploadMakerIdarrFiles(syncTargetIndex, files)
  let jobId: number | null = null
  let autoRenameError: unknown = null
  if (resolvedConfig.auto_rename_quick_add && response.uploaded_count > 0) {
    try {
      const job = await startIdarr(false, syncTargetIndex, response.uploaded, resolvedConfig.auto_upload_quick_add)
      jobId = job.id
      void notifyIdarrTargetedRun(job.id, Boolean(resolvedConfig.auto_upload_quick_add), syncTargetIndex)
    } catch (error) {
      autoRenameError = error
    }
  }
  return { uploadedCount: response.uploaded_count, skippedCount: response.skipped_count, jobId, autoRenameError }
}
