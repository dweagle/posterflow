import { CloudOff } from 'lucide-react'

/** Stands in for a drive card's icon when Google Drive sync is switched off. */
function DriveSyncOffIcon() {
  return (
    <div className="drive-icon" role="img" title="Google Drive sync is off. Sync jobs only scan the local folder.">
      <CloudOff size={18} color="var(--color-warning)" />
    </div>
  )
}

export default DriveSyncOffIcon
