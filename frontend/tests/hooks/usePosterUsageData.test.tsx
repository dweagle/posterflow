import { describe, expect, it } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useArtworkUsageData, usePosterUsageData } from '../../src/hooks/usePosterUsageData'
import type { FallbackItem, PosterStatsItems, PosterStyleStats } from '../../src/api/posterManager'

const item = (title: string, drive_id: string, file = `${title}.jpg`): FallbackItem =>
  ({ title, year: 2020, type: 'movie', drive_id, file })

const summary: PosterStyleStats = {
  style_counts: { CL2K: 2 },
  drive_usage: [{ drive_id: 'd1', name: 'Drive 1', count: 1 }, { drive_id: 'd2', name: 'Drive 2', count: 1 }],
  artwork_drive_usage: [{ drive_id: 'a1', name: 'Art 1', count: 1 }],
}

const items: PosterStatsItems = {
  style_fallbacks: { MM2K: [item('Alpha', 'd1'), item('Beta', 'd2')] },
  drive_outranked: { d2: [item('Alpha', 'd2', 'alpha-alt.jpg')] },
  artwork_drive_items: { a1: [item('Logo', 'a1')] },
  artwork_drive_outranked: {},
}

describe('usePosterUsageData', () => {
  it('shows the drive summary before the item lists arrive, then fills them in', () => {
    const before = renderHook(() => usePosterUsageData(summary, null)).result.current
    expect(before.driveUsage.map((d) => d.drive_id)).toEqual(['d1', 'd2'])
    expect(before.itemsForDrive('d1')).toEqual([])

    const after = renderHook(() => usePosterUsageData(summary, items)).result.current
    expect(after.itemsForDrive('d1').map((it) => it.title)).toEqual(['Alpha'])
    expect(after.outrankedForDrive('d2').map((it) => it.file)).toEqual(['alpha-alt.jpg'])
    expect(after.compareForItem(item('Alpha', 'd1'), { season: null, slot: null }).map((c) => c.drive_id)).toEqual(['d1', 'd2'])
  })

  it('reads artwork lists from the items payload', () => {
    const art = renderHook(() => useArtworkUsageData(summary, items)).result.current
    expect(art.driveUsage[0].drive_id).toBe('a1')
    expect(art.itemsForDrive('a1').map((it) => it.title)).toEqual(['Logo'])
  })
})
