import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { getApiErrorMessage, ignoreAndUploadMakerIdarrPending, type MakerIdarrRunOutcome } from '../api/client'
import {
  IDARR_QUICK_ADD_NOTICE_EVENT,
  describeOutcomeReason,
  describeOutcomeStatus,
  type IdarrQuickAddNotice as Notice,
} from '../utils/idarrTargetedRun'
import { useToast } from './Toast'
import './IdarrQuickAddNotice.css'

type RowAction =
  | { state: 'working' }
  | { state: 'done'; uploadJobId: number | null }
  | { state: 'error'; message: string }

/**
 * Global popup for quick-add drops that didn't make it to the drive. Mounted once in the
 * layout so the sidebar drop zone, the community cards and the IDarr page all report the
 * same way — a maker who drops a poster into a request otherwise has nothing telling them
 * it went pending and is still sitting in the local folder.
 */
function IdarrQuickAddNoticeHost() {
  const [notice, setNotice] = useState<Notice | null>(null)
  const [rowActions, setRowActions] = useState<Record<string, RowAction>>({})
  const navigate = useNavigate()
  const { showToast } = useToast()

  useEffect(() => {
    const onNotice = (event: Event) => {
      const detail = (event as CustomEvent<Notice>).detail
      if (detail) {
        setNotice(detail)
        setRowActions({})
      }
    }
    window.addEventListener(IDARR_QUICK_ADD_NOTICE_EVENT, onNotice)
    return () => window.removeEventListener(IDARR_QUICK_ADD_NOTICE_EVENT, onNotice)
  }, [])

  if (!notice) return null

  const close = () => setNotice(null)
  const count = notice.problems.length
  const pendingCount = notice.problems.filter((row) => row.status === 'pending' || row.status === 'conflict').length

  const heading = notice.error
    ? 'IDarr run failed'
    : count === 1
      ? 'A dropped file needs a manual match'
      : `${count} dropped files need a manual match`

  const summary = notice.error
    ? notice.error
    : pendingCount > 0
      ? notice.autoUpload
        ? `Waiting in IDarr's pending matches — not uploaded to the drive. Resolve the match on the IDarr page and it will go up on the next run.`
        : `Waiting in IDarr's pending matches. Resolve the match on the IDarr page before the next drive sync.`
      : 'These files stayed local.'

  const doneText = (uploadJobId: number | null) =>
    notice.autoUpload
      ? `Added to the ignore list · uploading to the drive unchanged${uploadJobId ? ` (job ${uploadJobId})` : ''}`
      : 'Added to the ignore list · goes up unchanged with the next drive sync'

  // A drop the maker already knows will never match: ignore it here instead of on the IDarr page.
  const ignoreRow = async (row: MakerIdarrRunOutcome) => {
    const key = row.asset_key
    if (!key) return
    setRowActions((prev) => ({ ...prev, [key]: { state: 'working' } }))
    try {
      const response = await ignoreAndUploadMakerIdarrPending({
        asset_key: key,
        relative_path: row.relative_path || row.final_filename,
        sync_target_index: notice.syncTargetIndex,
        upload: notice.autoUpload,
      })
      // Nothing else to deal with: drop the popup and confirm with a toast instead.
      if (notice.problems.length === 1) {
        close()
        showToast(doneText(response.upload_job_id), 'success')
        return
      }
      setRowActions((prev) => ({ ...prev, [key]: { state: 'done', uploadJobId: response.upload_job_id } }))
    } catch (error) {
      const message = getApiErrorMessage(error, 'Failed to ignore this file')
      setRowActions((prev) => ({ ...prev, [key]: { state: 'error', message } }))
    }
  }

  const ignoreLabel = notice.autoUpload ? 'Upload as-is & ignore' : 'Ignore'
  const ignoreHint = notice.autoUpload
    ? 'Add this title to the IDarr ignore list and upload the file to the drive under its current name'
    : 'Add this title to the IDarr ignore list; the file stays in the sync folder and goes up unchanged with the next drive sync'

  return (
    <div className="idarr-notice-overlay" onClick={(event) => { if (event.target === event.currentTarget) close() }}>
      <div className="idarr-notice-dialog" role="alertdialog" aria-labelledby="idarr-notice-title">
        <div className="idarr-notice-header">
          <AlertTriangle size={22} />
          <h3 id="idarr-notice-title">{heading}</h3>
          <button className="idarr-notice-close" onClick={close} aria-label="Close">×</button>
        </div>

        <div className="idarr-notice-body">
          <p className="idarr-notice-summary">{summary}</p>

          {count > 0 && (
            <ul className="idarr-notice-list">
              {notice.problems.map((row) => {
                const action = row.asset_key ? rowActions[row.asset_key] : undefined
                const ignored = action?.state === 'done'
                const canIgnore = row.status === 'pending' && Boolean(row.asset_key) && !ignored
                return (
                  <li key={`${row.source_filename}-${row.status}`} className="idarr-notice-item">
                    <div className="idarr-notice-item-top">
                      <span className="idarr-notice-filename" title={row.source_filename}>{row.source_filename}</span>
                      <span className={`idarr-notice-pill idarr-notice-pill-${ignored ? 'ignored' : row.status}`}>
                        {ignored ? 'Ignored' : describeOutcomeStatus(row.status)}
                      </span>
                    </div>
                    <div className="idarr-notice-reason">{describeOutcomeReason(row)}</div>
                    {canIgnore && (
                      <div className="idarr-notice-item-actions">
                        <button
                          className="idarr-notice-btn-row"
                          onClick={() => { void ignoreRow(row) }}
                          disabled={action?.state === 'working'}
                          title={ignoreHint}
                        >
                          {action?.state === 'working' ? 'Working…' : ignoreLabel}
                        </button>
                        {action?.state === 'error' && (
                          <span className="idarr-notice-item-status idarr-notice-item-status-error">{action.message}</span>
                        )}
                      </div>
                    )}
                    {action?.state === 'done' && (
                      <div className="idarr-notice-item-status">{doneText(action.uploadJobId)}</div>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          {notice.uploadedCount > 0 && (
            <p className="idarr-notice-uploaded">
              {notice.uploadedCount} other file{notice.uploadedCount === 1 ? '' : 's'} uploaded to the drive.
            </p>
          )}
        </div>

        <div className="idarr-notice-footer">
          <button className="idarr-notice-btn-secondary" onClick={close}>Got it</button>
          <button
            className="idarr-notice-btn-primary"
            onClick={() => { close(); navigate('/IDarr') }}
          >
            Open IDarr
          </button>
        </div>
      </div>
    </div>
  )
}

export default IdarrQuickAddNoticeHost
