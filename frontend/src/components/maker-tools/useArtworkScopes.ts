import { useCallback, useEffect, useMemo, useState } from 'react'
import { getMakerIdarrConfig, type MakerIdarrSyncTarget } from '../../api/client'

const IDARR_SYNC_TARGET_STORAGE_KEY = 'posterflow.idarr.selectedSyncTarget'

// Same stable id IDarr / the community picker use, so the chosen scope stays in sync.
function syncTargetStorageValue(t: MakerIdarrSyncTarget): string {
  const token = String(t.scope_token || '').trim()
  if (token) return `scope:${token}`
  return `${String(t.personal_drive_id || '').trim()}::${String(t.source_dir || '').trim()}::${String(t.label || '').trim()}`
}

export type AssetScope = { index: number; value: string; label: string }

/** Load the IDarr artwork scopes (sync targets flagged is_asset_drive) + the shared selection.
 * `selected.index` is the index into the FULL sync_targets list (what the API expects). */
export function useArtworkScopes() {
  const [scopes, setScopes] = useState<AssetScope[]>([])
  const [selectedValue, setSelectedValue] = useState<string>('')
  const [scopesLoaded, setScopesLoaded] = useState(false)

  useEffect(() => {
    getMakerIdarrConfig()
      .then((config) => {
        const targets = Array.isArray(config.sync_targets) ? config.sync_targets : []
        const assetScopes: AssetScope[] = targets
          .map((t, index) => ({ t, index }))
          .filter(({ t }) => Boolean(t.is_asset_drive))
          .map(({ t, index }) => ({ index, value: syncTargetStorageValue(t), label: t.label || t.source_dir || `Scope ${index + 1}` }))
        setScopes(assetScopes)
        const stored = localStorage.getItem(IDARR_SYNC_TARGET_STORAGE_KEY)
        const match = assetScopes.find((s) => s.value === stored)
        setSelectedValue(match ? match.value : assetScopes[0]?.value ?? '')
      })
      .catch(() => {})
      .finally(() => setScopesLoaded(true))
  }, [])

  const selected = useMemo(() => scopes.find((s) => s.value === selectedValue) ?? null, [scopes, selectedValue])

  const onSelectScope = useCallback((value: string) => {
    setSelectedValue(value)
    localStorage.setItem(IDARR_SYNC_TARGET_STORAGE_KEY, value)
  }, [])

  return { scopes, selectedValue, selected, onSelectScope, scopesLoaded }
}
