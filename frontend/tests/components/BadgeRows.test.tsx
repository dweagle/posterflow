import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OptionFields from '../../src/components/overlays/OptionFields'
import type { OverlayBadgeRow, OverlayField } from '../../src/api/overlays'
import { pick } from '../utils/listPicker'

const mockFetch = vi.fn()

vi.mock('../../src/api/overlays', async () => ({
  ...(await vi.importActual<typeof import('../../src/api/overlays')>('../../src/api/overlays')),
  fetchCustomBadge: (...args: unknown[]) => mockFetch(...args),
  uploadCustomBadge: vi.fn(),
}))

const HAS = [{ value: 'is', label: 'is' }, { value: 'is_not', label: 'is not' }]

const schema: OverlayField[] = [
  { kind: 'select', key: 'badge', label: 'Badge', section: 'Badge', options: [{ value: 'text', label: 'Text' }, { value: 'image', label: 'Image' }] },
  {
    kind: 'badges', key: 'badges', label: 'Badges', kind_option: 'badge', max: 3, text_limit: 120, text_help: 'It can name values such as <<runtime>>.', help: 'Each badge has its own wording or image and its own conditions.',
    modes: [{ value: 'all', label: 'All of them' }, { value: 'any', label: 'Any of them' }],
    fields: [
      { key: 'label', label: 'Label or tag', group: 'Media server', kind: 'tag', ops: HAS, options: [] },
      { key: 'radarr_tag', label: 'Radarr tag', group: 'Radarr and Sonarr', kind: 'tag', ops: HAS, options: [] },
      { key: 'tmdb_popular', label: 'Popular', group: 'TMDB charts', kind: 'number', ops: [{ value: 'lte', label: 'is in the top' }], options: [], region: true },
      { key: 'released', label: 'Released', group: 'Item', kind: 'days', options: [],
        ops: [{ value: 'within', label: 'in the last' }, { value: 'older', label: 'more than' }, { value: 'before', label: 'before' }, { value: 'regex', label: 'matches (regex)' }] },
      { key: 'resolution', label: 'Resolution', group: 'Video and audio', kind: 'choice', ops: HAS, options: [{ value: '4k', label: '4K' }] },
    ],
  },
]

const row = (id: string, name: string, extra: Partial<OverlayBadgeRow> = {}): OverlayBadgeRow =>
  ({ id, name, enabled: true, text: name.toUpperCase(), image: '', weight: 0, match: 'all', rules: [], ...extra })

type Options = { badge: string; badges: OverlayBadgeRow[] }

// The table inside the page's own options renderer, holding the options like the page does.
function Harness({ start, onChange, onVariant }: { start: Options; onChange: (options: Options) => void; onVariant?: (id: string) => void }) {
  const [options, setOptions] = useState<Record<string, unknown>>(start)
  return <OptionFields render="states" schema={schema} options={options} idPrefix="ov-custom" onVariantChange={onVariant} onChange={(next) => { setOptions(next); onChange(next as Options) }} />
}

const setup = (start: Options) => {
  const onChange = vi.fn()
  const onVariant = vi.fn()
  render(<Harness start={start} onChange={onChange} onVariant={onVariant} />)
  return { rows: () => (onChange.mock.calls.at(-1)?.[0] as Options).badges, onVariant }
}

describe('BadgeRows', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:badge'), revokeObjectURL: vi.fn() })
    mockFetch.mockResolvedValue(new Blob(['png']))
  })

  afterEach(() => {
    cleanup()
  })

  it('lists the badges with their wording and weight, and sums up each one’s conditions', () => {
    const { rows } = setup({ badge: 'text', badges: [
      row('0000000a', 'Netflix', { weight: 60, rules: [{ field: 'label', op: 'is', value: 'Kids' }] }),
      row('0000000b', 'Disney', { rules: [{ field: 'released', op: 'within', value: 90 }, { field: 'resolution', op: 'is', value: '4k' }], match: 'any' }),
      row('0000000c', 'Everything', { enabled: false }),
    ] })
    expect(screen.getByRole('heading', { name: 'Badges' })).toBeTruthy()
    // The Weight column says whose weight it is: the badge's, not the overlay's.
    expect(within(screen.getByRole('columnheader', { name: /^Weight/ })).getByLabelText(/weight of this one badge inside this overlay.*overlay has a weight of its own beside Group/)).toBeTruthy()
    const textHead = screen.getByRole('columnheader', { name: /^Text/ })
    expect(within(textHead).getByLabelText('It can name values such as <<runtime>>.')).toBeTruthy()  // the variables a wording takes
    expect((screen.getByLabelText('Netflix text') as HTMLInputElement).maxLength).toBe(120)
    expect((screen.getByLabelText('Badge 1 name') as HTMLInputElement).value).toBe('Netflix')
    expect((screen.getByLabelText('Netflix text') as HTMLInputElement).value).toBe('NETFLIX')
    expect((screen.getByLabelText('Netflix weight') as HTMLInputElement).value).toBe('60')
    expect(screen.getByRole('button', { name: 'Netflix conditions: Label or tag is Kids' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Disney conditions: 2 conditions, any' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Everything conditions: Every poster' })).toBeTruthy()
    expect(screen.getByLabelText('Everything off')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Disney text'), { target: { value: 'D+' } })
    fireEvent.change(screen.getByLabelText('Disney weight'), { target: { value: '50' } })
    fireEvent.click(screen.getByLabelText('Everything off'))
    expect(rows().map((r) => [r.name, r.text, r.weight, r.enabled])).toEqual([['Netflix', 'NETFLIX', 60, true], ['Disney', 'D+', 50, true], ['Everything', 'EVERYTHING', 0, true]])
    expect(screen.getByRole('button', { name: /Add Badge/ })).toHaveProperty('disabled', true)  // the type's limit
  })

  it('says which instance a tag is in, and which server a label is on', () => {
    setup({ badge: 'text', badges: [
      row('0000000d', 'UHD kids', { rules: [{ field: 'radarr_tag', op: 'is', value: 'kids', instance: 'Radarr 4k' }] }),
      row('0000000e', 'Pixar', { rules: [{ field: 'label', op: 'is', value: 'Pixar', instance: 'Plex' }] }),
    ] })
    expect(screen.getByRole('button', { name: 'UHD kids conditions: Radarr tag is kids (Radarr 4k)' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Pixar conditions: Label or tag is Pixar (Plex)' })).toBeTruthy()
  })

  it('names a chart\u2019s region where it has one', () => {
    setup({ badge: 'text', badges: [
      row('00000013', 'UK hits', { rules: [{ field: 'tmdb_popular', op: 'lte', value: 20, region: 'GB' }] }),
      row('00000014', 'Hits', { rules: [{ field: 'tmdb_popular', op: 'lte', value: 50 }] }),
    ] })
    expect(screen.getByRole('button', { name: 'UK hits conditions: Popular is in the top 20 in GB' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Hits conditions: Popular is in the top 50' })).toBeTruthy()
  })

  it('reads a date as it is and a number of days as days', () => {
    setup({ badge: 'text', badges: [
      row('00000011', 'Old', { rules: [{ field: 'released', op: 'before', value: '2020-01-01' }] }),
      row('00000012', 'New', { rules: [{ field: 'released', op: 'older', value: 30 }] }),
      row('00000013', 'Xmas', { rules: [{ field: 'released', op: 'regex', value: '^12/25' }] }),
    ] })
    expect(screen.getByRole('button', { name: 'Old conditions: Released before 2020-01-01' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'New conditions: Released more than 30 days ago' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Xmas conditions: Released matches (regex) ^12/25' })).toBeTruthy()  // a pattern is no number of days
  })

  it('reads a condition with several values as this or that', () => {
    setup({ badge: 'text', badges: [
      row('0000000f', 'Family', { rules: [{ field: 'label', op: 'is', value: 'Kids', instance: 'Plex', also: [{ value: 'Pixar', instance: 'Jelly' }, { value: 'Disney' }] }] }),
      row('00000010', 'Sharp', { rules: [{ field: 'resolution', op: 'is_not', value: '4k', also: [{ value: '4k' }] }] }),
    ] })
    expect(screen.getByRole('button', { name: 'Family conditions: Label or tag is Kids (Plex) or Pixar (Jelly) or Disney' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sharp conditions: Resolution is not 4K or 4K' })).toBeTruthy()
  })

  it('explains under the open badge how its conditions work', () => {
    setup({ badge: 'text', badges: [row('0000000a', 'Netflix')] })
    const help = screen.getByRole('heading', { name: 'Conditions: Netflix' }).nextElementSibling?.textContent ?? ''
    for (const part of ['With no condition, every poster this overlay covers gets it.', 'type one and press Enter', 'any one of them is enough',
      'none of them may fit', 'only matches there', 'Must match says whether all or any']) expect(help).toContain(part)
    expect(help).not.toMatch(/\u2014|Kometa/)  // the page's copy rules: no long dashes, no other tool's name
  })

  it('opens one badge’s conditions at a time and edits them there', () => {
    const { rows, onVariant } = setup({ badge: 'text', badges: [row('0000000a', 'Netflix'), row('0000000b', 'Disney')] })
    // The first badge is open to start with.
    expect(screen.getByRole('heading', { name: 'Conditions: Netflix' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Netflix conditions/ }).getAttribute('aria-expanded')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /^Disney conditions/ }))
    expect(onVariant).toHaveBeenLastCalledWith('0000000b')  // the preview follows the open badge
    expect(screen.getByRole('heading', { name: 'Conditions: Disney' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Add Condition/ }))
    pick('Condition 1', 'Released')
    fireEvent.change(screen.getByLabelText('Condition 1 value'), { target: { value: '45' } })
    expect(rows()[1].rules).toEqual([{ field: 'released', op: 'within', value: 45 }])
    expect(rows()[0].rules).toEqual([])
    expect(screen.getByRole('button', { name: 'Disney conditions: Released in the last 45 days' })).toBeTruthy()
    // Typing in another row opens that one.
    fireEvent.focus(screen.getByLabelText('Netflix weight'))
    expect(screen.getByRole('heading', { name: 'Conditions: Netflix' })).toBeTruthy()
    expect(onVariant).toHaveBeenLastCalledWith('0000000a')
    // So does a click anywhere in a row, on no control at all.
    const disneyRow = screen.getByLabelText('Badge 2 name').closest('tr') as HTMLElement
    fireEvent.click(within(disneyRow).getAllByRole('cell')[0])
    expect(screen.getByRole('heading', { name: 'Conditions: Disney' })).toBeTruthy()
    expect(onVariant).toHaveBeenLastCalledWith('0000000b')
  })

  it('lets a badge have a size of its own: a wording\u2019s text size, a picture\u2019s width', () => {
    const { rows } = setup({ badge: 'text', badges: [row('0000000a', 'Netflix', { size: 96 }), row('0000000b', 'Disney')] })
    expect(within(screen.getByRole('columnheader', { name: /^Size/ })).getByLabelText(/own text size.*Leave it at 0 to use the Size from the Text card/)).toBeTruthy()
    expect((screen.getByLabelText('Netflix size') as HTMLInputElement).value).toBe('96')
    expect((screen.getByLabelText('Disney size') as HTMLInputElement).value).toBe('0')  // none of its own: the overlay's
    fireEvent.change(screen.getByLabelText('Disney size'), { target: { value: '60' } })
    expect(rows().map((r) => r.size)).toEqual([96, 60])
    cleanup()
    setup({ badge: 'image', badges: [row('0000000c', 'Ribbon', { size: 300 })] })
    expect(within(screen.getByRole('columnheader', { name: /^Width/ })).getByLabelText(/own width.*Leave it at 0 to use the overlay.s Image width/)).toBeTruthy()
    expect((screen.getByLabelText('Ribbon width') as HTMLInputElement).value).toBe('300')
  })

  it('adds a badge with an id of its own, and removes one but never the last', () => {
    const harness = setup({ badge: 'text', badges: [row('0000000a', 'Netflix')] })
    const { rows } = harness
    expect(screen.getByRole('button', { name: 'Remove Netflix' })).toHaveProperty('disabled', true)
    fireEvent.click(screen.getByRole('button', { name: /Add Badge/ }))
    expect(rows()).toHaveLength(2)
    expect(rows()[1]).toMatchObject({ name: 'Badge 2', enabled: true, text: '', image: '', size: 0, weight: 0, match: 'all', rules: [] })
    expect(rows()[1].id).toMatch(/^[0-9a-f]{8}$/)
    expect(rows()[1].id).not.toBe('0000000a')
    expect(screen.getByRole('heading', { name: 'Conditions: Badge 2' })).toBeTruthy()  // the new badge is the open one
    const { onVariant } = harness
    onVariant.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Remove Badge 2' }))
    expect(rows().map((r) => r.name)).toEqual(['Netflix'])
    expect(screen.getByRole('heading', { name: 'Conditions: Netflix' })).toBeTruthy()
    expect(onVariant.mock.calls).toEqual([['0000000a']])  // the click that removed it did not open the badge that is gone
  })

  it('draws a picture and an upload button per badge for an image overlay', async () => {
    setup({ badge: 'image', badges: [row('0000000a', 'Netflix', { image: 'abcdef012345' }), row('0000000b', 'Disney')] })
    expect(screen.getByRole('columnheader', { name: 'Image' })).toBeTruthy()
    expect(screen.queryByLabelText('Netflix text')).toBeNull()
    expect(await screen.findByAltText('Netflix image')).toBeTruthy()
    expect(mockFetch).toHaveBeenCalledWith('abcdef012345')
    const rowsOnPage = within(screen.getAllByRole('table')[0]).getAllByRole('row')
    expect(within(rowsOnPage[1]).getByRole('button', { name: 'Replace Netflix image' })).toBeTruthy()
    expect(within(rowsOnPage[2]).getByRole('button', { name: 'Upload Disney image' })).toBeTruthy()
  })

  it('is drawn with the tables, never as a settings card', () => {
    render(<OptionFields render="fields" schema={schema} options={{ badge: 'text', badges: [row('0000000a', 'Netflix')] }} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Badge')).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Badges' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Add Badge/ })).toBeNull()
  })
})
