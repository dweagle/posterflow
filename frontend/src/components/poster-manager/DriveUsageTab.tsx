import { useCallback, useEffect, useState } from 'react'
import { Pin } from 'lucide-react'
import Toolbar from '../Toolbar'
import PriorityScopeSelector, { type PriorityScope } from './PriorityScopeSelector'
import { TITLE, DESCRIPTION } from './PriorityHeader'
import DriveUsagePanel from './DriveUsagePanel'
import PosterOverridesModal from './PosterOverridesModal'
import { PosterStyleStats, getPosterOverrides } from '../../api/posterManager'
import { useArtworkUsageData, usePosterUsageData } from '../../hooks/usePosterUsageData'

type DriveUsageTabProps = {
  scope: PriorityScope
  onScopeChange: (scope: PriorityScope) => void
  styleStats: PosterStyleStats | null
}

// Drive Usage view of the Drive Priority tab: both last-rename reports side by side.
function DriveUsageTab({ scope, onScopeChange, styleStats }: DriveUsageTabProps) {
  const poster = usePosterUsageData(styleStats)
  const artwork = useArtworkUsageData(styleStats)
  const [showOverrides, setShowOverrides] = useState(false)
  const [overrideCount, setOverrideCount] = useState<number | null>(null)

  const refreshOverrideCount = useCallback(() => {
    getPosterOverrides().then((o) => setOverrideCount(o.length)).catch(() => {})
  }, [])
  useEffect(refreshOverrideCount, [])

  const driveInfo = new Map(
    [...poster.driveUsage, ...artwork.driveUsage].map((d) => [d.drive_id, { name: d.name, style: d.style }])
  )

  return (
    <>
      <Toolbar title={TITLE} description={DESCRIPTION} />
      <PriorityScopeSelector value={scope} onChange={onScopeChange} />

      <div className="priority-tab">
        <div className="drive-usage-intro">
          <div>
            <h3 className="drive-usage-intro-title">Drive Usage Report</h3>
            <p className="drive-usage-intro-text">
              Where every poster and artwork file placed by the last rename came from, and what
              each drive could have supplied. Open a drive to browse its posters, compare
              versions across drives side by side, and pin a specific drive's poster to
              override the priority order.
            </p>
          </div>
          <button className="drive-usage-overrides-btn" onClick={() => setShowOverrides(true)}>
            <Pin size={15} />
            Overrides
            {overrideCount != null && <span className="drive-usage-overrides-count">{overrideCount}</span>}
          </button>
        </div>

        {poster.driveUsage.length === 0 && artwork.driveUsage.length === 0 && (
          <p className="drive-usage-hint">
            No drive usage recorded yet - the reports populate on the next rename run.
          </p>
        )}
        <div className="drive-usage-tab-grid">
          <DriveUsagePanel
            usage={poster.driveUsage}
            itemsForDrive={poster.itemsForDrive}
            outrankedForDrive={poster.outrankedForDrive}
            compareForItem={poster.compareForItem}
            availableCountFor={poster.availableCountFor}
            onOverridesChange={refreshOverrideCount}
            title="Last Rename - Poster Drive Usage"
            collapsible={false}
          />
          <DriveUsagePanel
            usage={artwork.driveUsage}
            itemsForDrive={artwork.itemsForDrive}
            outrankedForDrive={artwork.outrankedForDrive}
            compareForItem={artwork.compareForItem}
            availableCountFor={artwork.availableCountFor}
            onOverridesChange={refreshOverrideCount}
            overrideDomain="artwork"
            noun="artwork file"
            filePrefix="artwork-usage"
            title="Last Rename - Artwork Drive Usage"
            collapsible={false}
          />
        </div>
      </div>

      {showOverrides && (
        <PosterOverridesModal
          driveInfo={driveInfo}
          onClose={() => {
            setShowOverrides(false)
            refreshOverrideCount()
          }}
        />
      )}
    </>
  )
}

export default DriveUsageTab
