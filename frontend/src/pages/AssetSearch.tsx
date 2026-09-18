import { useCallback, useEffect, useState } from 'react'
import DriveSearchPanel from '../components/DriveSearchPanel'
import OverrideTargetModal from '../components/poster-manager/OverrideTargetModal'
import { PosterOverride, getPosterOverrides } from '../api/posterManager'
import { DrivePosterPick, pinnedFileBadges } from '../utils/posterOverrideTarget'
import './AssetSearch.css'

function AssetSearch() {
  const [pick, setPick] = useState<DrivePosterPick | null>(null)
  const [overrides, setOverrides] = useState<PosterOverride[]>([])
  const loadOverrides = useCallback(() => {
    getPosterOverrides().then(setOverrides).catch(() => {})
  }, [])
  useEffect(() => {
    loadOverrides()
  }, [loadOverrides])

  return (
    <div className="page-container">
      <div className="poster-search-header">
        <h1>Asset Search</h1>
        <p>
          Search the synced Google Drive poster and artwork folders and preview what each drive offers.
          Use any result for a library item and the next rename places that file instead of the
          priority pick.
        </p>
      </div>

      <DriveSearchPanel
        autoFocus
        enableSlashFocus
        onUse={setPick}
        useLabel="Use for…"
        useTitle="Pin this file to a library item (overrides drive priority)"
        fileBadges={pinnedFileBadges(overrides)}
      />

      {pick && <OverrideTargetModal pick={pick} onClose={() => setPick(null)} onSaved={loadOverrides} />}
    </div>
  )
}

export default AssetSearch
