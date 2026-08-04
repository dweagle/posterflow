import { useEffect, useState } from 'react'
import { RefreshCw, X } from 'lucide-react'
import { NEW_VERSION_EVENT } from '../api/http'
import './UpdateBanner.css'

const SNOOZE_KEY = 'posterflow.update.snooze'
const SNOOZE_MS = 24 * 60 * 60 * 1000

// Dismiss is a snooze, not a mute: an always-open tab gets re-prompted after
// 24h (or immediately if the server moved to yet another version).
export function shouldShowUpdateBanner(serverVersion: string, snoozeRaw: string | null, now: number): boolean {
  if (!serverVersion) return false
  if (!snoozeRaw) return true
  try {
    const snooze = JSON.parse(snoozeRaw) as { version?: string; until?: number }
    return snooze.version !== serverVersion || typeof snooze.until !== 'number' || now >= snooze.until
  } catch {
    return true
  }
}

function UpdateBanner() {
  const [serverVersion, setServerVersion] = useState<string | null>(null)

  useEffect(() => {
    const onNewVersion = (e: Event) => {
      const version = (e as CustomEvent<string>).detail
      if (shouldShowUpdateBanner(version, localStorage.getItem(SNOOZE_KEY), Date.now())) {
        setServerVersion(version)
      }
    }
    window.addEventListener(NEW_VERSION_EVENT, onNewVersion)
    return () => window.removeEventListener(NEW_VERSION_EVENT, onNewVersion)
  }, [])

  if (!serverVersion) return null

  const dismiss = () => {
    localStorage.setItem(SNOOZE_KEY, JSON.stringify({ version: serverVersion, until: Date.now() + SNOOZE_MS }))
    setServerVersion(null)
  }

  return (
    <div className="update-banner" role="status">
      <span className="update-banner-text">
        A new version of Posterflow is available ({serverVersion})
      </span>
      <button className="update-banner-refresh" onClick={() => window.location.reload()}>
        <RefreshCw size={14} />
        Refresh
      </button>
      <button
        className="update-banner-dismiss"
        onClick={dismiss}
        aria-label="Remind me tomorrow"
        title="Remind me tomorrow"
      >
        <X size={16} />
      </button>
    </div>
  )
}

export default UpdateBanner
