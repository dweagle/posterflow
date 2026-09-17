import { useCallback, useMemo } from 'react'
import { useIdarrSyncTarget, getSyncTargetStorageValue, resolveSyncTargetIndex } from '../../hooks/useIdarrSyncTarget'

export type AssetScope = { index: number; value: string; label: string }

/** The IDarr artwork scopes (sync targets flagged is_asset_drive) + the shared selection,
 * plus the poster scopes (neither artwork nor PSD) that can be browsed for artwork gaps.
 * `selected.index` is the index into the FULL sync_targets list (what the API expects). */
export function useArtworkScopes() {
  const { targets, storedValue, loaded: scopesLoaded, setSelectedValue } = useIdarrSyncTarget()

  const { scopes, posterScopes } = useMemo(() => {
    const indexed = targets.map((t, index) => ({ t, index }))
    const toScope = ({ t, index }: (typeof indexed)[number]): AssetScope => ({
      index, value: getSyncTargetStorageValue(t), label: t.label || t.source_dir || `Scope ${index + 1}`,
    })
    return {
      scopes: indexed.filter(({ t }) => Boolean(t.is_asset_drive)).map(toScope),
      posterScopes: indexed.filter(({ t }) => !t.is_asset_drive && !t.is_psd_drive).map(toScope),
    }
  }, [targets])

  // The shared selection may point at a non-artwork drive; fall back to the first artwork scope.
  const selected = useMemo(() => {
    const storedIndex = resolveSyncTargetIndex(targets, storedValue)
    return scopes.find((s) => s.index === storedIndex) ?? scopes[0] ?? null
  }, [targets, scopes, storedValue])
  const selectedValue = selected?.value ?? ''

  const onSelectScope = useCallback((value: string) => {
    setSelectedValue(value)
  }, [setSelectedValue])

  return { scopes, posterScopes, selectedValue, selected, onSelectScope, scopesLoaded }
}
