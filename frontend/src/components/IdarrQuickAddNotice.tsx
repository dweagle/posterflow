import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import {
  IDARR_QUICK_ADD_NOTICE_EVENT,
  describeOutcomeReason,
  describeOutcomeStatus,
  type IdarrQuickAddNotice as Notice,
} from '../utils/idarrTargetedRun'
import './IdarrQuickAddNotice.css'

/**
 * Global popup for quick-add drops that didn't make it to the drive. Mounted once in the
 * layout so the sidebar drop zone, the community cards and the IDarr page all report the
 * same way — a maker who drops a poster into a request otherwise has nothing telling them
 * it went pending and is still sitting in the local folder.
 */
function IdarrQuickAddNoticeHost() {
  const [notice, setNotice] = useState<Notice | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    const onNotice = (event: Event) => {
      const detail = (event as CustomEvent<Notice>).detail
      if (detail) setNotice(detail)
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
              {notice.problems.map((row) => (
                <li key={`${row.source_filename}-${row.status}`} className="idarr-notice-item">
                  <div className="idarr-notice-item-top">
                    <span className="idarr-notice-filename" title={row.source_filename}>{row.source_filename}</span>
                    <span className={`idarr-notice-pill idarr-notice-pill-${row.status}`}>
                      {describeOutcomeStatus(row.status)}
                    </span>
                  </div>
                  <div className="idarr-notice-reason">{describeOutcomeReason(row)}</div>
                </li>
              ))}
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
