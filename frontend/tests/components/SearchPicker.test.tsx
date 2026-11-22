import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SearchPicker, { type SearchOption } from '../../src/components/overlays/SearchPicker'
import { searchBox, searchOptions, searchPick, searchType } from '../utils/searchPicker'

const studios: SearchOption[] = ['Pixar', 'Walt Disney Pictures', 'Disney Television Animation', 'A24', 'Warner Bros.'].map((label, i) => ({ value: String(i), label }))

// The box holding its value the way a condition does: typing keeps the text, a pick keeps the entry's label.
function Harness({ options, start = '', searched = false, onType, onPick }: { options: SearchOption[]; start?: string; searched?: boolean; onType?: (t: string) => void; onPick?: (o: SearchOption) => void }) {
  const [text, setText] = useState(start)
  return <SearchPicker ariaLabel="Studio" text={text} options={options} searched={searched} placeholder="Pixar" onType={(t) => { setText(t); onType?.(t) }} onPick={(o) => { setText(o.label); onPick?.(o) }} />
}

afterEach(() => {
  cleanup()
})

describe('SearchPicker', () => {
  it('lists everything on a click and narrows the list as you type', () => {
    const onType = vi.fn()
    render(<Harness options={studios} onType={onType} />)
    expect(searchBox('Studio').placeholder).toBe('Pixar')
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(searchOptions('Studio')).toEqual(['Pixar', 'Walt Disney Pictures', 'Disney Television Animation', 'A24', 'Warner Bros.'])
    expect(onType).not.toHaveBeenCalled()  // looking changes nothing
    expect(searchType('Studio', 'disney')).toEqual(['Walt Disney Pictures', 'Disney Television Animation'])
    expect(onType).toHaveBeenLastCalledWith('disney')  // what was typed is the value until an entry is picked
    expect(searchType('Studio', 'zzz')).toEqual([])
    expect(screen.queryByRole('listbox')).toBeNull()  // nothing fits: no empty list
    expect(searchBox('Studio').value).toBe('zzz')  // and what was typed stays
  })

  it('takes a pick by click or by the keyboard', () => {
    const onPick = vi.fn()
    render(<Harness options={studios} onPick={onPick} />)
    searchPick('Studio', 'A24')
    expect(onPick).toHaveBeenLastCalledWith({ value: '3', label: 'A24' })
    expect(searchBox('Studio').value).toBe('A24')
    expect(screen.queryByRole('listbox')).toBeNull()
    const input = searchBox('Studio')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'dis' } })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant')).toMatch(/-1$/)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onPick).toHaveBeenLastCalledWith({ value: '2', label: 'Disney Television Animation' })
    fireEvent.focus(input)
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('draws a long list a part at a time, the next part as its end is scrolled to', () => {
    const many = Array.from({ length: 250 }, (_, i) => ({ value: String(i), label: `Studio ${i}` }))
    render(<Harness options={many} />)
    expect(searchOptions('Studio')).toHaveLength(100)
    fireEvent.focus(searchBox('Studio'))
    const list = screen.getByRole('listbox')
    const drawn = () => screen.getAllByRole('option').length
    // jsdom lays nothing out: the list is given a size, and scrolled by hand.
    const scrollTo = (top: number, height: number) => {
      Object.defineProperty(list, 'clientHeight', { value: 320, configurable: true })
      Object.defineProperty(list, 'scrollHeight', { value: height, configurable: true })
      list.scrollTop = top
      fireEvent.scroll(list)
    }
    scrollTo(900, 2600)  // part of the way down: nothing more yet
    expect(drawn()).toBe(100)
    scrollTo(2200, 2600)  // near the end
    expect(drawn()).toBe(200)
    scrollTo(4800, 5200)
    expect(drawn()).toBe(250)
    scrollTo(6180, 6500)  // the whole list is drawn: the end is the end
    expect(drawn()).toBe(250)
    expect(screen.queryByText(/Type to narrow/)).toBeNull()
    // Typing starts over from the top of what fits; so does opening the list again.
    expect(searchType('Studio', 'Studio 24')).toEqual(['Studio 24', 'Studio 240', 'Studio 241', 'Studio 242', 'Studio 243', 'Studio 244', 'Studio 245', 'Studio 246', 'Studio 247', 'Studio 248', 'Studio 249'])
    expect(searchType('Studio', 'Studio')).toHaveLength(100)
    fireEvent.blur(searchBox('Studio'))
    expect(searchOptions('Studio')).toHaveLength(100)
  })

  it('draws the next part when the arrow keys pass the last entry drawn', () => {
    const many = Array.from({ length: 130 }, (_, i) => ({ value: String(i), label: `Studio ${i}` }))
    const onPick = vi.fn()
    render(<Harness options={many} onPick={onPick} />)
    const input = searchBox('Studio')
    fireEvent.focus(input)
    for (let i = 0; i < 100; i += 1) fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant')).toMatch(/-99$/)
    expect(screen.getAllByRole('option')).toHaveLength(100)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')).toHaveLength(130)
    expect(input.getAttribute('aria-activedescendant')).toMatch(/-100$/)
    for (let i = 0; i < 60; i += 1) fireEvent.keyDown(input, { key: 'ArrowDown' })  // stops at the last one
    expect(input.getAttribute('aria-activedescendant')).toMatch(/-129$/)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onPick).toHaveBeenLastCalledWith({ value: '129', label: 'Studio 129' })
  })

  it('adds values to a list kept beside it: Enter or leaving the box keeps what was typed', () => {
    const onCommit = vi.fn()
    const onPick = vi.fn()
    const onType = vi.fn()
    render(<SearchPicker ariaLabel="Studio" text="" options={studios} onType={onType} onPick={onPick} onCommit={onCommit} />)
    const input = searchBox('Studio')
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '  My Studio ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onCommit).toHaveBeenLastCalledWith('My Studio')
    expect(searchBox('Studio').value).toBe('')  // free for the next one
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'Another' } })
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenLastCalledWith('Another')
    fireEvent.focus(input)
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledTimes(2)  // nothing typed, nothing added
    // A pick is the entry, not the letters typed to find it.
    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: 'pix' } })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onPick).toHaveBeenLastCalledWith({ value: '0', label: 'Pixar' })
    expect(onCommit).toHaveBeenCalledTimes(2)
  })

  it('shows where an entry is from beside its name, and finds entries by it', () => {
    const tags: SearchOption[] = [{ value: '0', label: 'kids', note: 'Radarr' }, { value: '1', label: 'kids', note: 'Radarr 4k' }, { value: '2', label: 'uhd', note: 'Radarr 4k' }]
    render(<Harness options={tags} />)
    expect(searchOptions('Studio')).toEqual(['kids (Radarr)', 'kids (Radarr 4k)', 'uhd (Radarr 4k)'])
    fireEvent.focus(searchBox('Studio'))
    const rows = screen.getAllByRole('option')
    expect(rows[1].querySelector('.search-picker-name')?.textContent).toBe('kids')
    expect(rows[1].querySelector('.search-picker-note')?.textContent).toBe('Radarr 4k')
    expect(searchType('Studio', '4k')).toEqual(['kids (Radarr 4k)', 'uhd (Radarr 4k)'])
  })

  it('lists searched matches as they are, without filtering them again', () => {
    render(<Harness options={[{ value: '0', label: 'based on novel or book' }, { value: '1', label: 'comic' }]} searched />)
    expect(searchType('Studio', 'based')).toEqual(['based on novel or book', 'comic'])
  })
})
