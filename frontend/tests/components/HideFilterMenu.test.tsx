import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import HideFilterMenu from '../../src/components/community/HideFilterMenu'
import { NO_HIDE_FILTERS } from '../../src/components/community/hideFilters'

afterEach(() => {
  cleanup()
})

describe('HideFilterMenu', () => {
  it('opens a checklist and reports a toggled filter merged into the current choices', async () => {
    const onChange = vi.fn()
    render(<HideFilterMenu value={{ ...NO_HIDE_FILTERS, mm2k: true }} onChange={onChange} />)
    expect(screen.queryByLabelText('Anime')).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /hide/i }))
    await userEvent.click(screen.getByLabelText('Anime'))

    expect(onChange).toHaveBeenCalledWith({ mm2k: true, anime: true, nonEnglish: false })
  })

  it('counts only the options it shows and omits an app-wide hidden style', async () => {
    render(<HideFilterMenu value={{ mm2k: true, anime: true, nonEnglish: false }} onChange={() => {}} omit={['mm2k']} />)
    const btn = screen.getByRole('button', { name: /hide/i })
    expect(btn.className).toContain('active')
    expect(btn.textContent).toContain('1')

    await userEvent.click(btn)
    expect(screen.queryByLabelText('MM2K')).toBeNull()
    expect((screen.getByLabelText('Anime') as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText('Non-English') as HTMLInputElement).checked).toBe(false)
  })

  it('stays plain with nothing hidden', () => {
    render(<HideFilterMenu value={NO_HIDE_FILTERS} onChange={() => {}} />)
    const btn = screen.getByRole('button', { name: /hide/i })
    expect(btn.className).not.toContain('active')
    expect(btn.textContent).toBe('Hide')
  })

  it('closes on Escape and on a click outside', async () => {
    render(<HideFilterMenu value={NO_HIDE_FILTERS} onChange={() => {}} />)
    const btn = screen.getByRole('button', { name: /hide/i })

    await userEvent.click(btn)
    expect(screen.getByLabelText('Anime')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByLabelText('Anime')).toBeNull()

    await userEvent.click(btn)
    expect(screen.getByLabelText('Anime')).toBeTruthy()
    fireEvent.mouseDown(document.body)
    expect(screen.queryByLabelText('Anime')).toBeNull()
  })
})
