import { getMakerIdarrRunResult, type MakerIdarrRunOutcome } from '../api/client'

export const IDARR_QUICK_ADD_NOTICE_EVENT = 'idarr-quick-add-notice'

export interface IdarrQuickAddNotice {
  jobId: number
  problems: MakerIdarrRunOutcome[]
  uploadedCount: number
  autoUpload: boolean
  error: string
}

// A targeted run can sit queued behind a long full run, so keep watching for a while.
const POLL_INTERVAL_MS = 3000
const POLL_TIMEOUT_MS = 15 * 60 * 1000

// Anything that did not end up on the drive is worth interrupting the maker for.
const PROBLEM_STATUSES = new Set(['pending', 'conflict', 'upload_failed', 'missing'])

const REASON_LABELS: Record<string, string> = {
  no_match: 'No TMDB match found',
  ambiguous: 'Several TMDB matches — needs a pick',
  low_confidence_alternate: 'Only a low-confidence alternate title matched',
  review_required_low_confidence_alternate: 'Low-confidence match held for review',
  tmdb_removed: 'The TMDB entry no longer exists',
  rename_conflict: 'Another file already uses the corrected name',
  rename_target_exists: 'A file with the corrected name is already there and is not older — pick which to keep',
  intra_batch_filename_conflict: 'Two dropped files map to the same corrected name',
  filename_too_long: 'The corrected filename is too long',
  not_scanned: "The file wasn't picked up by the scan",
}

export function describeOutcomeReason(outcome: MakerIdarrRunOutcome): string {
  if (outcome.status === 'upload_failed') return outcome.reason || 'Upload to the drive failed'
  const key = String(outcome.reason || '').trim().toLowerCase()
  return REASON_LABELS[key] || outcome.reason || 'Needs a manual match'
}

export function describeOutcomeStatus(status: string): string {
  if (status === 'pending') return 'Pending match'
  if (status === 'conflict') return 'Name conflict'
  if (status === 'upload_failed') return 'Upload failed'
  if (status === 'missing') return 'Not processed'
  return status
}

/**
 * Wait for a targeted IDarr run to finish, then report the files that need the maker's
 * attention. A maker dropping a poster into a request is nowhere near the IDarr page, so
 * without this nothing tells them the file went pending and is still sitting locally.
 * Resolves null when everything landed cleanly.
 */
export async function waitForIdarrTargetedRun(
  jobId: number,
  autoUpload: boolean,
): Promise<IdarrQuickAddNotice | null> {
  const deadline = Date.now() + POLL_TIMEOUT_MS

  while (Date.now() < deadline) {
    let result
    try {
      result = await getMakerIdarrRunResult(jobId)
    } catch {
      return null
    }

    if (result.finished) {
      const problems = result.outcomes.filter((outcome) => PROBLEM_STATUSES.has(outcome.status))
      const error = result.status === 'failed' ? result.error || 'The IDarr run failed' : ''
      if (!problems.length && !error) return null
      return {
        jobId,
        problems,
        uploadedCount: result.outcomes.filter((outcome) => outcome.status === 'uploaded').length,
        autoUpload,
        error,
      }
    }

    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }

  return null
}

/**
 * Fire-and-forget version used by every drop target: watches the run in the background and
 * raises the popup handled by IdarrQuickAddNoticeHost if anything needs the maker.
 */
export async function notifyIdarrTargetedRun(jobId: number, autoUpload: boolean): Promise<void> {
  const notice = await waitForIdarrTargetedRun(jobId, autoUpload)
  if (!notice) return
  window.dispatchEvent(new CustomEvent<IdarrQuickAddNotice>(IDARR_QUICK_ADD_NOTICE_EVENT, { detail: notice }))
}
