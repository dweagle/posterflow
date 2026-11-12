import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ListPicker from '../../src/components/overlays/ListPicker'

const options = ['4K DV HDR10+', '4K DV', '4K HDR', '4K', '1080p', 'HDR'].map((label) => ({ value: label.toLowerCase().replace(/[^a-z0-9]/g, ''), label }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const rect = (top: number, bottom: number) => ({ top, bottom, left: 0, right: 200, width: 200, height: bottom - top, x: 0, y: top, toJSON: () => ({}) }) as DOMRect

describe('ListPicker', () => {
  it('shows the chosen entry, opens its own list and picks with a click', () => {
    const onChange = vi.fn()
    render(<ListPicker ariaLabel="Preview State" value="4k" options={options} onChange={onChange} />)
    const button = screen.getByRole('combobox', { name: 'Preview State' })
    expect(button.textContent).toBe('4K')
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('listbox')).toBeNull()  // nothing in the page until it is opened

    fireEvent.click(button)
    expect(button.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(options.map((o) => o.label))
    expect(screen.getByRole('option', { name: '4K' }).getAttribute('aria-selected')).toBe('true')
    fireEvent.click(screen.getByRole('option', { name: '1080p' }))
    expect(onChange).toHaveBeenCalledWith('1080p')
    expect(screen.queryByRole('listbox')).toBeNull()  // choosing closes it

    // Picking the entry already chosen changes nothing; a press outside just closes the list.
    fireEvent.click(button)
    fireEvent.click(screen.getByRole('option', { name: '4K' }))
    expect(onChange).toHaveBeenCalledTimes(1)
    fireEvent.click(button)
    fireEvent.mouseDown(document.body)
    expect(screen.queryByRole('listbox')).toBeNull()
  })

  it('works from the keyboard: arrows, Home and End, typing, Enter and Escape', () => {
    const onChange = vi.fn()
    render(<ListPicker ariaLabel="Preview State" value="4kdv" options={options} onChange={onChange} />)
    const button = screen.getByRole('combobox')
    const active = () => document.getElementById(button.getAttribute('aria-activedescendant') ?? '')?.textContent
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    expect(active()).toBe('4K DV')  // opens on the chosen entry
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    expect(active()).toBe('4K HDR')
    fireEvent.keyDown(button, { key: 'End' })
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    expect(active()).toBe('HDR')  // stops at the last entry
    fireEvent.keyDown(button, { key: 'Home' })
    expect(active()).toBe('4K DV HDR10+')
    fireEvent.keyDown(button, { key: '1' })
    expect(active()).toBe('1080p')  // typing jumps to the entry that starts with it
    fireEvent.keyDown(button, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('1080p')
    expect(screen.queryByRole('listbox')).toBeNull()

    fireEvent.keyDown(button, { key: 'Enter' })
    expect(screen.getByRole('listbox')).toBeTruthy()
    fireEvent.keyDown(button, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('fits the list to the room there is, opening upward when there is more room above', () => {
    Object.defineProperty(window, 'innerHeight', { value: 700, configurable: true })
    const many = Array.from({ length: 39 }, (_, i) => ({ value: `v${i}`, label: `State ${i}` }))
    render(<ListPicker ariaLabel="Preview State" value="v0" options={many} onChange={vi.fn()} />)
    const button = screen.getByRole('combobox')
    const root = button.parentElement as HTMLElement

    // Near the top of a 700px window: 600px below, far more than the list is allowed to take.
    vi.spyOn(root, 'getBoundingClientRect').mockReturnValue(rect(60, 86))
    fireEvent.click(button)
    let list = screen.getByRole('listbox')
    expect(list.className).not.toContain('above')
    expect(list.style.maxHeight).toBe('320px')  // capped: it scrolls inside rather than fill the screen
    fireEvent.click(button)

    // Near the bottom: 100px below, 550px above. It opens upward.
    vi.spyOn(root, 'getBoundingClientRect').mockReturnValue(rect(562, 588))
    fireEvent.click(button)
    list = screen.getByRole('listbox')
    expect(list.className).toContain('above')
    expect(list.style.maxHeight).toBe('320px')
    fireEvent.click(button)

    // A short window with little room either way: the list shrinks to what is there and scrolls.
    Object.defineProperty(window, 'innerHeight', { value: 300, configurable: true })
    vi.spyOn(root, 'getBoundingClientRect').mockReturnValue(rect(100, 126))
    fireEvent.click(button)
    list = screen.getByRole('listbox')
    expect(list.className).not.toContain('above')  // 162px below beats 88px above
    expect(list.style.maxHeight).toBe('162px')
  })

  it('cannot be opened while disabled or empty', () => {
    const view = render(<ListPicker ariaLabel="Badge" value="" options={[]} onChange={vi.fn()} />)
    const button = screen.getByRole('combobox') as HTMLButtonElement
    fireEvent.click(button)
    fireEvent.keyDown(button, { key: 'ArrowDown' })
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(button.textContent).toBe('')
    view.rerender(<ListPicker ariaLabel="Badge" value="4k" options={options} onChange={vi.fn()} disabled />)
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(screen.queryByRole('listbox')).toBeNull()
  })
})
