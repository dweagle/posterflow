import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RuleEditor from '../../src/components/overlays/RuleEditor'
import type { OverlayRule, OverlayRuleField } from '../../src/api/overlays'
import { pick, picker, pickerOptions } from '../utils/listPicker'
import { searchBox, searchCommit, searchOptions, searchPick, searchType } from '../utils/searchPicker'

const mockGetRuleValues = vi.fn()

vi.mock('../../src/api/overlays', () => ({
  getRuleValues: (...args: unknown[]) => mockGetRuleValues(...args),
}))

const HAS = [{ value: 'is', label: 'is' }, { value: 'is_not', label: 'is not' }]

const specs: OverlayRuleField[] = [
  { key: 'title', label: 'Title', group: 'Item', kind: 'text', ops: HAS, options: [], multi: true, help: 'The title as your media server has it.' },
  { key: 'label', label: 'Label or tag', group: 'Media server', kind: 'tag', ops: HAS, options: [], multi: true, suggest: 'list' },
  { key: 'radarr_tag', label: 'Radarr tag', group: 'Radarr and Sonarr', kind: 'tag', ops: HAS, options: [], multi: true, suggest: 'list' },
  { key: 'sonarr_tag', label: 'Sonarr tag', group: 'Radarr and Sonarr', kind: 'tag', ops: HAS, options: [], multi: true, suggest: 'list' },
  { key: 'original_language', label: 'Original language', group: 'TMDB', kind: 'text', ops: HAS, options: [], multi: true, suggest: 'list' },
  { key: 'tmdb_keyword', label: 'TMDB keyword', group: 'TMDB', kind: 'tag', ops: HAS, options: [], multi: true, suggest: 'search', placeholder: 'based on novel or book' },
  { key: 'released', label: 'Released', group: 'Item', kind: 'days', options: [],
    ops: [{ value: 'within', label: 'in the last' }, { value: 'older', label: 'more than' }, { value: 'before', label: 'before' }, { value: 'after', label: 'after' }, { value: 'regex', label: 'matches (regex)' }] },
  { key: 'tmdb_popular', label: 'Popular', group: 'TMDB charts', kind: 'number', ops: [{ value: 'lte', label: 'is in the top' }, { value: 'gt', label: 'is not in the top' }], options: [], start: 20, region: true },
  { key: 'resolution', label: 'Resolution', group: 'Video and audio', kind: 'choice', ops: HAS, multi: true, options: [{ value: '4k', label: '4K' }, { value: '1080p', label: '1080p' }, { value: '720p', label: '720p' }] },
  { key: 'tmdb_rating', label: 'TMDB score', group: 'TMDB', kind: 'number', ops: [{ value: 'gte', label: 'is at least' }, { value: 'lte', label: 'is at most' }], options: [], step: 0.1 },
  { key: 'list', label: 'MDBList list', group: 'Lists', kind: 'tag', ops: [{ value: 'is', label: 'has the item' }, { value: 'is_not', label: 'does not have it' }], options: [], multi: true,
    placeholder: 'https://mdblist.com/lists/name/list', pattern: '^https://mdblist\\.com/lists/[\\w.~-]+(/[\\w.~-]+){1,2}$' },
]
const modes = [{ value: 'all', label: 'All of them' }, { value: 'any', label: 'Any of them' }]

// The editor holding its conditions the way a badge row does.
function Harness({ start, startMode = 'all', scope, onChange }: { start: OverlayRule[]; startMode?: string; scope?: string[]; onChange: (rules: OverlayRule[], mode: string) => void }) {
  const [rules, setRules] = useState(start)
  const [mode, setMode] = useState(startMode)
  return (
    <RuleEditor
      title="Conditions: Netflix"
      help="Which movies and shows get this badge."
      specs={specs}
      modes={modes}
      rules={rules}
      mode={mode}
      onRulesChange={(next) => { setRules(next); onChange(next, mode) }}
      onModeChange={(next) => { setMode(next); onChange(rules, next) }}
      idPrefix="ov-rules"
      scope={scope}
      info={(help) => <span aria-label={help} />}
    />
  )
}

const setup = (start: OverlayRule[] = [], scope?: string[]) => {
  const onChange = vi.fn()
  render(<Harness start={start} scope={scope} onChange={onChange} />)
  return { rules: () => onChange.mock.calls.at(-1)?.[0] as OverlayRule[], mode: () => onChange.mock.calls.at(-1)?.[1] as string, onChange }
}

// The chips of a condition, as they read: the name, and beside it where the value is from.
const chips = (condition: number): string[] => {
  const row = screen.getAllByRole('row')[condition]
  return Array.from(row.querySelectorAll('.overlay-rule-chip')).map((chip) =>
    [chip.querySelector('.overlay-rule-chip-name')?.textContent, chip.querySelector('.overlay-rule-chip-note')?.textContent].filter(Boolean).join(' | '))
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetRuleValues.mockResolvedValue({ field: '', values: [], unreachable: [] })
})

afterEach(() => {
  cleanup()
})

describe('RuleEditor', () => {
  it('starts empty and adds a condition on the first field', () => {
    const { rules } = setup()
    expect(screen.getByRole('heading', { name: 'Conditions: Netflix' })).toBeTruthy()
    expect(screen.queryByRole('table')).toBeNull()
    expect(screen.queryByLabelText('Must match')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Add Condition/ }))
    expect(rules()).toEqual([{ field: 'title', op: 'is', value: '' }])
    expect(pickerOptions('Condition 1')).toEqual(['Title', 'Label or tag', 'Radarr tag', 'Sonarr tag', 'Original language', 'TMDB keyword', 'Released', 'Popular', 'Resolution', 'TMDB score', 'MDBList list'])
    expect(mockGetRuleValues).not.toHaveBeenCalled()  // a typed field asks for nothing
    expect(screen.getByLabelText('The title as your media server has it.')).toBeTruthy()  // the field's help beside its picker
    searchCommit('Condition 1 value', 'Heat')
    pick('Condition 1 comparison', 'is not')
    expect(rules()).toEqual([{ field: 'title', op: 'is_not', value: 'Heat' }])
    expect(chips(1)).toEqual(['Heat'])
  })

  it('keeps what is typed as a value when the box is left, without an Enter', () => {
    const { rules } = setup([{ field: 'title', op: 'is', value: '' }])
    const box = searchBox('Condition 1 value')
    fireEvent.focus(box)
    fireEvent.change(box, { target: { value: '  Heat  ' } })
    expect(rules).toBeDefined()
    fireEvent.blur(box)
    expect(rules()).toEqual([{ field: 'title', op: 'is', value: 'Heat' }])
    expect(searchBox('Condition 1 value').value).toBe('')  // the box is free for another value
  })

  it('holds several values as chips: any one of them will do', () => {
    const { rules } = setup([{ field: 'title', op: 'is', value: 'Heat' }, { field: 'released', op: 'within', value: 90 }])
    expect(chips(1)).toEqual(['Heat'])
    expect(searchBox('Condition 1 value').placeholder).toBe('Add another')
    // The box stays on top; the chips line up under it.
    const cell = searchBox('Condition 1 value').closest('.overlay-rule-values') as HTMLElement
    expect(Array.from(cell.children).map((child) => child.className.split(' ')[0])).toEqual(['search-picker', 'overlay-rule-chips'])
    searchCommit('Condition 1 value', 'Jaws')
    searchCommit('Condition 1 value', 'Speed')
    searchCommit('Condition 1 value', 'jaws')  // said twice: once is enough
    expect(rules()[0]).toEqual({ field: 'title', op: 'is', value: 'Heat', also: [{ value: 'Jaws' }, { value: 'Speed' }] })
    expect(chips(1)).toEqual(['Heat', 'Jaws', 'Speed'])
    pick('Condition 1 comparison', 'is not')  // the comparison is the condition's: every value keeps its place
    expect(rules()[0]).toEqual({ field: 'title', op: 'is_not', value: 'Heat', also: [{ value: 'Jaws' }, { value: 'Speed' }] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Heat from condition 1' }))
    expect(rules()[0]).toEqual({ field: 'title', op: 'is_not', value: 'Jaws', also: [{ value: 'Speed' }] })  // the next value leads
    fireEvent.keyDown(searchBox('Condition 1 value'), { key: 'Backspace' })  // a key in the box never takes a chip away
    expect(rules()[0]).toEqual({ field: 'title', op: 'is_not', value: 'Jaws', also: [{ value: 'Speed' }] })
    fireEvent.click(screen.getByRole('button', { name: 'Remove Speed from condition 1' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove Jaws from condition 1' }))
    expect(rules()[0]).toEqual({ field: 'title', op: 'is_not', value: '' })
    expect(chips(1)).toEqual([])
    expect(searchBox('Condition 1 value').closest('.overlay-rule-values')?.querySelector('.overlay-rule-chips')).toBeNull()  // no empty row of chips
    // A number of days is one number: a plain box, no chips.
    expect(screen.getByLabelText('Condition 2 value').getAttribute('role')).toBeNull()
  })

  it('gives each kind of field its own value control and starts over when the field changes', () => {
    const { rules } = setup([{ field: 'title', op: 'is_not', value: 'Heat', also: [{ value: 'Jaws' }] }])
    pick('Condition 1', 'Released')
    expect(rules()).toEqual([{ field: 'released', op: 'within', value: 90 }])  // the old values do not ride along
    expect(pickerOptions('Condition 1 comparison')).toEqual(['in the last', 'more than', 'before', 'after', 'matches (regex)'])
    const row = () => screen.getAllByRole('row')[1]
    expect(within(row()).getByText('days')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Condition 1 value'), { target: { value: '30' } })
    pick('Condition 1 comparison', 'more than')
    expect(rules()).toEqual([{ field: 'released', op: 'older', value: 30 }])
    expect(within(row()).getByText('days ago')).toBeTruthy()
    // One of a fixed set: the set is the list, and a pick is a chip.
    pick('Condition 1', 'Resolution')
    expect(rules()).toEqual([{ field: 'resolution', op: 'is', value: '' }])
    expect(chips(1)).toEqual([])  // nothing is picked for you: the condition starts with no value
    expect(searchOptions('Condition 1 value')).toEqual(['4K', '1080p', '720p'])
    searchPick('Condition 1 value', '4K')
    expect(rules()).toEqual([{ field: 'resolution', op: 'is', value: '4k' }])
    expect(chips(1)).toEqual(['4K'])
    expect(searchOptions('Condition 1 value')).toEqual(['1080p', '720p'])  // what is a chip already is not offered again
    searchPick('Condition 1 value', '1080p')
    expect(rules()).toEqual([{ field: 'resolution', op: 'is', value: '4k', also: [{ value: '1080p' }] }])
    searchCommit('Condition 1 value', '720P')  // typed: the entry of that name
    searchCommit('Condition 1 value', '8K')  // not one of the set: nothing
    expect(rules()[0].also).toEqual([{ value: '1080p' }, { value: '720p' }])
    pick('Condition 1', 'TMDB score')
    fireEvent.change(screen.getByLabelText('Condition 1 value'), { target: { value: '7.5' } })
    expect(rules()).toEqual([{ field: 'tmdb_rating', op: 'gte', value: 7.5 }])
  })

  it('compares a date field with a number of days or with a date', () => {
    const { rules } = setup([{ field: 'released', op: 'within', value: 90 }])
    pick('Condition 1 comparison', 'before')
    const picked = rules()[0]
    expect(picked.op).toBe('before')
    expect(String(picked.value)).toMatch(/^\d{4}-\d{2}-\d{2}$/)  // a date to start from: today
    const box = screen.getByLabelText('Condition 1 value') as HTMLInputElement
    expect(box.type).toBe('date')
    fireEvent.change(box, { target: { value: '2026-07-01' } })
    pick('Condition 1 comparison', 'after')  // still a date: it stays
    expect(rules()).toEqual([{ field: 'released', op: 'after', value: '2026-07-01' }])
    pick('Condition 1 comparison', 'more than')  // back to counting days
    expect(rules()).toEqual([{ field: 'released', op: 'older', value: 90 }])
    expect((screen.getByLabelText('Condition 1 value') as HTMLInputElement).type).toBe('number')
    // A pattern is text of its own: it starts empty, and is read against month/day/year.
    pick('Condition 1 comparison', 'matches (regex)')
    expect(rules()).toEqual([{ field: 'released', op: 'regex', value: '' }])
    const pattern = screen.getByLabelText('Condition 1 value') as HTMLInputElement
    expect(pattern.type).toBe('text')
    expect(pattern.getAttribute('role')).toBeNull()  // a plain box: no list, no chips
    expect(within(screen.getAllByRole('row')[1]).getByText('of month/day/year')).toBeTruthy()
    fireEvent.change(pattern, { target: { value: '^12/25' } })
    expect(rules()).toEqual([{ field: 'released', op: 'regex', value: '^12/25' }])
    pick('Condition 1 comparison', 'in the last')  // a pattern is no number of days
    expect(rules()).toEqual([{ field: 'released', op: 'within', value: 90 }])
    pick('Condition 1 comparison', 'matches (regex)')
    pick('Condition 1 comparison', 'before')  // nor a date
    expect(String(rules()[0].value)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    // A chart starts at a top that means something.
    pick('Condition 1', 'Popular')
    expect(rules()).toEqual([{ field: 'tmdb_popular', op: 'lte', value: 20 }])
    expect(pickerOptions('Condition 1 comparison')).toEqual(['is in the top', 'is not in the top'])
  })

  it('lets a chart name a region, picked from the countries or typed as two letters', async () => {
    mockGetRuleValues.mockResolvedValue({ field: 'country', unreachable: [], values: [
      { value: 'GB', label: 'United Kingdom', note: 'GB' }, { value: 'US', label: 'United States of America', note: 'US' }] })
    const { rules } = setup([{ field: 'tmdb_popular', op: 'lte', value: 20 }, { field: 'released', op: 'within', value: 90 }])
    expect(screen.queryByLabelText('Condition 2 region')).toBeNull()  // only a chart has regions
    await waitFor(() => expect(searchOptions('Condition 1 region')).toEqual(['United Kingdom (GB)', 'United States of America (US)']))
    expect(mockGetRuleValues).toHaveBeenCalledWith('country', { libraries: [] })
    expect(searchBox('Condition 1 region').placeholder).toBe('any region')
    searchPick('Condition 1 region', 'United Kingdom (GB)')
    expect(rules()[0]).toEqual({ field: 'tmdb_popular', op: 'lte', value: 20, region: 'GB' })
    expect(searchBox('Condition 1 region').value).toBe('GB')
    searchType('Condition 1 region', 'us-')
    expect(rules()[0].region).toBe('US')  // two letters, in capitals
    searchType('Condition 1 region', '')
    expect(rules()[0].region).toBe('')  // empty again: TMDB's own chart
    fireEvent.change(screen.getByLabelText('Condition 1 value'), { target: { value: '50' } })
    expect(rules()[0]).toEqual({ field: 'tmdb_popular', op: 'lte', value: 50, region: '' })
    pick('Condition 1', 'Title')
    expect(rules()[0]).toEqual({ field: 'title', op: 'is', value: '' })  // another field: no region rides along
  })

  it('asks whether all or any must hold once there are two conditions, and removes one', () => {
    const { rules, mode } = setup([{ field: 'title', op: 'is', value: 'Heat' }, { field: 'resolution', op: 'is', value: '4k' }])
    expect(picker('Must match').dataset.value).toBe('all')
    pick('Must match', 'Any of them')
    expect(mode()).toBe('any')
    fireEvent.click(screen.getByRole('button', { name: 'Remove condition 1' }))
    expect(rules()).toEqual([{ field: 'resolution', op: 'is', value: '4k' }])
    expect(screen.queryByLabelText('Must match')).toBeNull()
    expect(picker('Condition 1').dataset.value).toBe('resolution')
  })

  it('offers each instance’s own tags, Radarr’s for a Radarr tag and Sonarr’s for a Sonarr tag', async () => {
    mockGetRuleValues.mockImplementation(async (field: string) => (field === 'radarr_tag'
      ? { field, values: [{ value: '4k-only', instance: 'Radarr', label: '4k-only', note: 'Radarr' }, { value: 'kids', instance: 'Radarr', label: 'kids', note: 'Radarr' }, { value: 'kids', instance: 'Radarr 4k', label: 'kids', note: 'Radarr 4k' }], unreachable: ['Radarr HD'] }
      : { field, values: [{ value: 'anime', instance: 'Sonarr', label: 'anime', note: 'Sonarr' }, { value: 'kids', instance: 'Sonarr', label: 'kids', note: 'Sonarr' }], unreachable: [] }))
    const { rules } = setup([{ field: 'radarr_tag', op: 'is', value: '' }, { field: 'radarr_tag', op: 'is', value: 'kids', instance: 'Radarr 4k' }, { field: 'sonarr_tag', op: 'is', value: 'kids', instance: 'Sonarr' }])
    await waitFor(() => expect(searchOptions('Condition 1 value')).toEqual(['4k-only (Radarr)', 'kids (Radarr)', 'kids (Radarr 4k)']))
    await waitFor(() => expect(searchOptions('Condition 3 value')).toEqual(['anime (Sonarr)']))  // its own kids is a chip already
    expect(mockGetRuleValues.mock.calls.map((c) => c[0]).sort()).toEqual(['radarr_tag', 'sonarr_tag'])  // once per field, not once per condition
    expect(searchBox('Condition 1 value').title).toBe('Not answering: Radarr HD')
    expect(chips(2)).toEqual(['kids | Radarr 4k'])  // the instance's own kids, the instance beside the name
    expect(chips(3)).toEqual(['kids | Sonarr'])
    searchPick('Condition 1 value', 'kids (Radarr)')
    expect(rules()[0]).toEqual({ field: 'radarr_tag', op: 'is', value: 'kids', instance: 'Radarr' })
    searchPick('Condition 2 value', '4k-only (Radarr)')
    expect(rules()[1]).toEqual({ field: 'radarr_tag', op: 'is', value: 'kids', instance: 'Radarr 4k', also: [{ value: '4k-only', instance: 'Radarr' }] })
    expect(chips(2)).toEqual(['kids | Radarr 4k', '4k-only | Radarr'])
    // Another field starts clean: neither the instance nor the further values ride along.
    pick('Condition 2', 'Title')
    expect(rules()[1]).toEqual({ field: 'title', op: 'is', value: '' })
  })

  it('keeps a tag that is gone as it is, and a typed tag means every instance', async () => {
    mockGetRuleValues.mockResolvedValue({ field: 'radarr_tag', values: [{ value: 'kids', instance: 'Radarr', label: 'kids', note: 'Radarr' }, { value: 'kids', instance: 'Radarr 4k', label: 'kids', note: 'Radarr 4k' },
      { value: 'uhd', instance: 'Radarr 4k', label: 'uhd', note: 'Radarr 4k' }], unreachable: [] })
    const { rules } = setup([{ field: 'radarr_tag', op: 'is', value: 'Gone', instance: 'Radarr 4k' }])
    await waitFor(() => expect(searchOptions('Condition 1 value')).toHaveLength(3))
    expect(chips(1)).toEqual(['Gone | Radarr 4k'])  // not among them, still shown with its instance
    expect(searchType('Condition 1 value', 'ki')).toEqual(['kids (Radarr)', 'kids (Radarr 4k)'])
    expect(rules).toBeDefined()
    fireEvent.keyDown(searchBox('Condition 1 value'), { key: 'Enter' })
    expect(rules()[0].also).toEqual([{ value: 'ki', instance: '' }])  // typed: no one instance's tag
    searchCommit('Condition 1 value', 'kids')  // two instances have a kids: typed still means every instance
    searchCommit('Condition 1 value', 'UHD')  // only one has it: that one's, as if picked
    expect(rules()[0].also).toEqual([{ value: 'ki', instance: '' }, { value: 'kids', instance: '' }, { value: 'uhd', instance: 'Radarr 4k' }])
  })

  it('leaves the value to be typed when there is nothing to pick from', async () => {
    mockGetRuleValues.mockRejectedValue(new Error('down'))
    const { rules } = setup([{ field: 'sonarr_tag', op: 'is', value: '' }])
    await waitFor(() => expect(mockGetRuleValues).toHaveBeenCalledTimes(1))
    expect(searchType('Condition 1 value', 'kids')).toEqual([])
    fireEvent.keyDown(searchBox('Condition 1 value'), { key: 'Enter' })
    expect(rules()).toEqual([{ field: 'sonarr_tag', op: 'is', value: 'kids' }])
  })

  it('offers what the overlay’s libraries hold, server by server, and asks again when they change', async () => {
    mockGetRuleValues.mockImplementation(async (_field: string, ask: { libraries?: string[] }) => ({
      field: 'label', unreachable: [], values: (ask.libraries ?? []).includes('Jelly:9')
        ? [{ value: 'anime', instance: 'Jelly', label: 'anime', note: 'Jelly' }]
        : [{ value: 'Kids', instance: 'Plex', label: 'Kids', note: 'Plex · 12' }, { value: 'Pixar', instance: 'Plex', label: 'Pixar', note: 'Plex · 31' }, { value: 'Pixar', instance: 'Jelly', label: 'Pixar', note: 'Jelly' }] }))
    const onChange = vi.fn()
    const rules = () => onChange.mock.calls.at(-1)?.[0]
    const view = render(<Harness start={[{ field: 'label', op: 'is', value: '' }]} scope={['Plex:1', 'Jelly:8']} onChange={onChange} />)
    await waitFor(() => expect(searchOptions('Condition 1 value')).toEqual(['Kids (Plex · 12)', 'Pixar (Plex · 31)', 'Pixar (Jelly)']))
    expect(mockGetRuleValues).toHaveBeenLastCalledWith('label', { libraries: ['Plex:1', 'Jelly:8'] })
    // In the list the name stands at the left and where it is from at the right, each in its own part.
    fireEvent.focus(searchBox('Condition 1 value'))
    const first = screen.getAllByRole('option')[0]
    expect(first.querySelector('.search-picker-name')?.textContent).toBe('Kids')
    expect(first.querySelector('.search-picker-note')?.textContent).toBe('Plex · 12')
    fireEvent.blur(searchBox('Condition 1 value'))
    expect(searchType('Condition 1 value', 'jelly')).toEqual(['Pixar (Jelly)'])  // typing a server's name lists its entries
    searchPick('Condition 1 value', 'Pixar (Jelly)')
    expect(rules()).toEqual([{ field: 'label', op: 'is', value: 'Pixar', instance: 'Jelly' }])  // that server's Pixar, not the other one's
    expect(chips(1)).toEqual(['Pixar | Jelly'])  // the chip names the server; the count is the list's
    searchPick('Condition 1 value', 'Pixar (Plex · 31)')
    searchCommit('Condition 1 value', 'brand new')
    expect(rules()).toEqual([{ field: 'label', op: 'is', value: 'Pixar', instance: 'Jelly', also: [{ value: 'Pixar', instance: 'Plex' }, { value: 'brand new', instance: '' }] }])  // typed: any server's posters
    expect(chips(1)).toEqual(['Pixar | Jelly', 'Pixar | Plex', 'brand new'])
    expect(mockGetRuleValues).toHaveBeenCalledTimes(1)  // one list serves every value of the field
    view.rerender(<Harness start={[{ field: 'label', op: 'is', value: '' }]} scope={['Jelly:9']} onChange={onChange} />)
    await waitFor(() => expect(searchOptions('Condition 1 value')).toEqual(['anime (Jelly)']))
    expect(mockGetRuleValues).toHaveBeenLastCalledWith('label', { libraries: ['Jelly:9'] })
  })

  it('shows a language by its name and keeps its code', async () => {
    mockGetRuleValues.mockResolvedValue({ field: 'original_language', values: [{ value: 'en', label: 'English', note: 'en' }, { value: 'ja', label: 'Japanese', note: 'ja' }], unreachable: [] })
    const { rules } = setup([{ field: 'original_language', op: 'is', value: 'ja' }])
    await waitFor(() => expect(chips(1)).toEqual(['Japanese | ja']))
    expect(searchType('Condition 1 value', 'engl')).toEqual(['English (en)'])
    searchPick('Condition 1 value', 'English (en)')
    expect(rules()).toEqual([{ field: 'original_language', op: 'is', value: 'ja', also: [{ value: 'en' }] }])
    fireEvent.click(screen.getByRole('button', { name: 'Remove Japanese (ja) from condition 1' }))
    searchCommit('Condition 1 value', 'japanese')  // the name typed out: its code is kept
    expect(rules()).toEqual([{ field: 'original_language', op: 'is', value: 'en', also: [{ value: 'ja' }] }])
  })

  it('searches TMDB for keywords as you type', async () => {
    mockGetRuleValues.mockImplementation(async (_field: string, ask: { q?: string }) => ({
      field: 'tmdb_keyword', unreachable: [], values: ask.q === 'based' ? [{ value: 'based on novel or book', label: 'based on novel or book' }, { value: 'based on comic', label: 'based on comic' }] : [] }))
    const { rules, onChange } = setup([{ field: 'tmdb_keyword', op: 'is', value: '' }])
    expect(mockGetRuleValues).not.toHaveBeenCalled()  // nothing to list before something is typed
    expect(searchBox('Condition 1 value').placeholder).toBe('based on novel or book')
    searchType('Condition 1 value', 'based')
    expect(onChange).not.toHaveBeenCalled()  // what is being typed is not a value yet
    await waitFor(() => expect(mockGetRuleValues).toHaveBeenCalledWith('tmdb_keyword', { q: 'based' }))
    await waitFor(() => expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['based on novel or book', 'based on comic']))
    searchPick('Condition 1 value', 'based on comic')
    expect(rules()).toEqual([{ field: 'tmdb_keyword', op: 'is', value: 'based on comic' }])
    expect(chips(1)).toEqual(['based on comic'])
  })

  it('marks a list address that is not one', () => {
    const { rules } = setup([{ field: 'list', op: 'is', value: 'https://trakt.tv/users/me/lists/x' }])
    const chip = () => screen.getAllByRole('row')[1].querySelectorAll('.overlay-rule-chip')
    expect(chip()[0].className).toContain('invalid')
    expect(chip()[0].getAttribute('title')).toBe('Use an address like https://mdblist.com/lists/name/list')
    searchCommit('Condition 1 value', 'https://mdblist.com/lists/me/top-10')
    expect(chip()[1].className).not.toContain('invalid')
    expect(rules()).toEqual([{ field: 'list', op: 'is', value: 'https://trakt.tv/users/me/lists/x', also: [{ value: 'https://mdblist.com/lists/me/top-10' }] }])  // several lists in one condition
  })
})
