/**
 * Small red "M" badge for items that Sonarr/Radarr track but haven't downloaded
 * (no file/episodes yet). Hover reveals what it means. Renders nothing unless
 * `available` is explicitly false (undefined = unknown / collections).
 */
export default function ArrMissingBadge({ available }: { available?: boolean | null }) {
  if (available !== false) return null
  return (
    <span
      className="arr-missing-badge"
      title="Missing in Arr — tracked in Sonarr/Radarr but not downloaded yet (the file isn't in your library)"
    >
      M
    </span>
  )
}
