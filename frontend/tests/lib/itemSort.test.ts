import { describe, it, expect } from 'vitest'
import { sortItems, type SortableItem } from '../../src/components/poster-manager/itemSort'

const item = (title: string, extra: Partial<SortableItem> = {}): SortableItem => ({
  title,
  year: null,
  type: 'movie',
  seasonCount: 0,
  ...extra,
})

describe('date sorts', () => {
  const items = [
    item('Newest', { added: '2026-08-01T04:00:00Z', releaseDate: '2026-07-15T00:00:00Z' }),
    item('No Dates'),
    item('Oldest', { added: '2024-01-05T04:00:00Z', releaseDate: '2023-12-01T00:00:00Z' }),
    item('Middle', { added: '2025-03-10T04:00:00Z', releaseDate: '2025-02-20T00:00:00Z' }),
  ]

  it('sorts by added ascending with dateless items last', () => {
    const sorted = sortItems(items, { group: 'all', field: 'added', dir: 'asc' })
    expect(sorted.map((i) => i.title)).toEqual(['Oldest', 'Middle', 'Newest', 'No Dates'])
  })

  it('sorts by added descending with dateless items still last', () => {
    const sorted = sortItems(items, { group: 'all', field: 'added', dir: 'desc' })
    expect(sorted.map((i) => i.title)).toEqual(['Newest', 'Middle', 'Oldest', 'No Dates'])
  })

  it('sorts by release date independently of added', () => {
    const sorted = sortItems(items, { group: 'all', field: 'released', dir: 'asc' })
    expect(sorted.map((i) => i.title)).toEqual(['Oldest', 'Middle', 'Newest', 'No Dates'])
  })

  it('tiebreaks equal dates by title', () => {
    const tied = [
      item('Beta', { added: '2025-01-01T00:00:00Z' }),
      item('Alpha', { added: '2025-01-01T00:00:00Z' }),
    ]
    const sorted = sortItems(tied, { group: 'all', field: 'added', dir: 'desc' })
    expect(sorted.map((i) => i.title)).toEqual(['Alpha', 'Beta'])
  })
})
