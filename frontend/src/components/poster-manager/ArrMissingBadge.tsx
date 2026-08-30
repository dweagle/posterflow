/**
 * Small red "M" badge for items a source tracks but that have no downloaded
 * file/episodes yet. Names the item's actual source when known: an arr, or the
 * media server library it came from. Renders nothing unless `available` is
 * explicitly false (undefined = unknown / collections).
 */
export default function ArrMissingBadge({
  available,
  source,
  instance,
}: {
  available?: boolean | null
  source?: string | null
  instance?: string | null
}) {
  if (available !== false) return null
  const fromMediaServer = source === 'plex' || source === 'jellyfin'
  const title = fromMediaServer
    ? `Missing — ${instance || 'your media server'} reports no downloaded file/episodes for this item yet`
    : source === 'manual'
      ? 'Missing — this manual entry has no downloaded file/episodes recorded'
      : `Missing in Arr — ${instance || 'Sonarr/Radarr'} tracks this but hasn't downloaded it yet`
  return (
    <span className="arr-missing-badge" title={title}>
      M
    </span>
  )
}
