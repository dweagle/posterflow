import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import CommunityStatusBadge from '../../src/components/poster-manager/CommunityStatusBadge'

/**
 * Check-only chips: color class carries the style (blue CL2K / green MM2K /
 * gray unknown, amber when claimed), the tooltip spells it out — no text.
 */

afterEach(cleanup)

describe('CommunityStatusBadge', () => {
  it('renders one check per style with its color class and tooltip', () => {
    const { container } = render(<CommunityStatusBadge status={{ CL2K: 'fulfilled', MM2K: 'in_progress' }} />)
    const chips = container.querySelectorAll('.community-claim-badge')
    expect(chips).toHaveLength(2)
    expect(chips[0].className).toContain('community-claim-cl2k')
    expect(chips[0].className).toContain('community-claim-fulfilled')
    expect(chips[0].getAttribute('title')).toContain('Already made in CL2K')
    expect(chips[1].className).toContain('community-claim-mm2k')
    expect(chips[1].className).toContain('community-claim-in-progress')
    expect(chips[1].getAttribute('title')).toContain('Claimed in MM2K')
  })

  it('never renders text — the check and tooltip say it all', () => {
    const { container } = render(<CommunityStatusBadge status={{ CL2K: 'fulfilled', MM2K: 'fulfilled', '': 'fulfilled' }} />)
    expect(container.textContent).toBe('')
    const chips = container.querySelectorAll('.community-claim-badge')
    expect(chips).toHaveLength(3)
    expect(chips[2].className).toContain('community-claim-unknown')
    expect(chips[2].getAttribute('title')).toContain('style unknown')
  })

  it('renders nothing without a status', () => {
    const { container } = render(<CommunityStatusBadge status={null} />)
    expect(container.innerHTML).toBe('')
  })
})
