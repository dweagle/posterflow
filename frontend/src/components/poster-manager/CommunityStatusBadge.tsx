import { Check } from 'lucide-react'
import { type ClaimStatus } from '../../hooks/useCommunityClaimStatus'

/**
 * Shows whether an item is already being worked on (orange) or done (green)
 * in the community, so the user doesn't request/list it again. Renders nothing
 * when there's no matching claim/fulfillment. `iconOnly` drops the text label
 * (used on compact rows), keeping just the colored checkmark + hover hint.
 */
export default function CommunityStatusBadge({ status, iconOnly = false }: { status: ClaimStatus | null; iconOnly?: boolean }) {
  if (!status) return null
  const fulfilled = status === 'fulfilled'
  return (
    <span
      className={`community-claim-badge${fulfilled ? ' community-claim-fulfilled' : ' community-claim-in-progress'}${iconOnly ? ' community-claim-icon-only' : ''}`}
      title={fulfilled
        ? 'Already made for the community — no need to request it again'
        : 'Claimed — a maker is working on it; no need to request it again'}
    >
      <Check size={11} />
      {!iconOnly && <span>{fulfilled ? 'Made' : 'In progress'}</span>}
    </span>
  )
}
