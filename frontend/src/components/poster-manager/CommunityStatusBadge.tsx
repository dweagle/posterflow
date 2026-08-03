import { Check } from 'lucide-react'
import { type ClaimStyle, type StyleClaimStatus } from '../../hooks/useCommunityClaimStatus'

const STYLE_ORDER: ClaimStyle[] = ['CL2K', 'MM2K', '']

/**
 * Community status checks, one per poster style. Color carries the meaning:
 * blue = made in CL2K, green = made in MM2K, gray = made in an unknown style
 * (legacy rows), amber = a maker has it claimed in that style. The tooltip
 * spells it out. Renders nothing when there's no matching claim/fulfillment.
 */
export default function CommunityStatusBadge({ status }: { status: StyleClaimStatus | null }) {
  if (!status) return null
  return (
    <>
      {STYLE_ORDER.filter((style) => status[style]).map((style) => {
        const fulfilled = status[style] === 'fulfilled'
        const inStyle = style ? ` in ${style}` : ' (style unknown)'
        return (
          <span
            key={style || 'unknown'}
            className={`community-claim-badge community-claim-${style ? style.toLowerCase() : 'unknown'}${fulfilled ? ' community-claim-fulfilled' : ' community-claim-in-progress'}`}
            title={fulfilled
              ? `Already made${inStyle} for the community; no need to request again\nIt may not have been uploaded by the maker yet or synced to your drive.`
              : `Claimed${inStyle} — a maker is working on it; no need to request it again`}
          >
            <Check size={11} />
          </span>
        )
      })}
    </>
  )
}
