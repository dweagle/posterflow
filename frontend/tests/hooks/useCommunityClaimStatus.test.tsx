import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { CommunityClaimStatusProvider, useCommunityClaimStatus } from '../../src/hooks/useCommunityClaimStatus'
import type { ClaimIndexRow } from '../../src/api/community'

/**
 * getStatus reports per poster style — { CL2K: 'fulfilled', MM2K: 'in_progress' } —
 * deliberately NOT filtered by the user's own drives: the badge spells out every
 * style so there's no confusion about which one a fulfillment was for.
 * Style-less legacy rows land under ''; getOverlap stays style-blind.
 */

const mocks = vi.hoisted(() => ({
  getClaimIndex: vi.fn(),
}))

vi.mock('../../src/api/community', () => ({
  getClaimIndex: mocks.getClaimIndex,
  getFulfilledRequestStyles: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
  cleanup()
})

const request = (overrides: Partial<ClaimIndexRow>): ClaimIndexRow => ({
  tmdb_id: 1352874,
  tvdb_id: null,
  media_type: 'movie',
  title: 'The Crucifix: Blood of the Exorcist',
  year: 2025,
  status: 'fulfilled',
  style_tags: ['CL2K Style'],
  ...overrides,
})

const crucifix = { tmdb_id: 1352874, media_type: 'movie', title: 'The Crucifix: Blood of the Exorcist', year: 2025 }

async function renderWithData(index: { requests?: ClaimIndexRow[]; list_items?: ClaimIndexRow[] }) {
  mocks.getClaimIndex.mockResolvedValue({ requests: [], list_items: [], ...index })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <CommunityClaimStatusProvider>{children}</CommunityClaimStatusProvider>
  )
  const rendered = renderHook(() => useCommunityClaimStatus(), { wrapper })
  await waitFor(() => expect(rendered.result.current.loaded).toBe(true))
  return rendered
}

describe('useCommunityClaimStatus per-style statuses', () => {
  it('reports the style a fulfillment was made in', async () => {
    const { result } = await renderWithData({ requests: [request({})] })
    expect(result.current.getStatus(crucifix)).toEqual({ CL2K: 'fulfilled' })
  })

  it('reports both styles independently', async () => {
    const { result } = await renderWithData({
      requests: [request({}), request({ status: 'in_progress', style_tags: ['MM2K Style'] })],
    })
    expect(result.current.getStatus(crucifix)).toEqual({ CL2K: 'fulfilled', MM2K: 'in_progress' })
  })

  it('lets fulfilled beat in_progress within one style, not across styles', async () => {
    const { result } = await renderWithData({
      requests: [request({ status: 'in_progress' }), request({})],
    })
    expect(result.current.getStatus(crucifix)).toEqual({ CL2K: 'fulfilled' })
  })

  it('buckets style-less legacy rows under the unknown style', async () => {
    const { result } = await renderWithData({ requests: [request({ style_tags: null })] })
    expect(result.current.getStatus(crucifix)).toEqual({ '': 'fulfilled' })
  })

  it('reads a list item single style_tag (bare label) the same way', async () => {
    const { result } = await renderWithData({
      list_items: [request({ status: 'in_progress', style_tags: undefined, style_tag: 'CL2K' })],
    })
    expect(result.current.getStatus(crucifix)).toEqual({ CL2K: 'in_progress' })
  })

  it('returns null when nothing matches', async () => {
    const { result } = await renderWithData({ requests: [request({ tmdb_id: 999, title: 'Other', year: 2000 })] })
    expect(result.current.getStatus(crucifix)).toBeNull()
  })

  it('keeps getOverlap style-blind for the community board chips', async () => {
    const { result } = await renderWithData({ requests: [request({ status: 'pending' })] })
    expect(result.current.getOverlap(crucifix)).toEqual({ onList: false, requested: true })
    // pending rows never produce a status badge, only overlap chips
    expect(result.current.getStatus(crucifix)).toBeNull()
  })
})
