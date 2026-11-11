import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import BorderStyleControls, { type BorderStyleValue } from '../../src/components/poster-manager/BorderStyleControls'
import { DEFAULT_SEASON_STYLE, snakeToStyle, styleToSnake } from '../../src/hooks/usePosterManagerBorder'

vi.mock('../../src/api/posterManager', () => ({
  deleteBorderOverlay: vi.fn(),
  fetchBorderPreview: vi.fn(),
  uploadBorderOverlay: vi.fn(),
}))

const value = (patch: Partial<BorderStyleValue> = {}): BorderStyleValue => ({ ...DEFAULT_SEASON_STYLE, colors: ['#ff0000'], ...patch })

const mount = (current: BorderStyleValue, mainWidth?: number) => {
  const onChange = vi.fn()
  render(<BorderStyleControls value={current} onChange={onChange} overlays={[]} refreshOverlays={() => {}} idPrefix="t" mainWidth={mainWidth} />)
  return onChange
}

afterEach(cleanup)

describe('a border width of its own', () => {
  it('is offered only where a main width is given', () => {
    mount(value())
    expect(screen.queryByText('Own border width')).toBeNull()
  })

  it('starts from the main width when switched on', async () => {
    const onChange = mount(value(), 26)
    expect(screen.getByText('Uses the main Border Width (26 px).')).toBeTruthy()
    expect(screen.queryByLabelText('Border Width (pixels)')).toBeNull()
    await userEvent.click(screen.getByText('Own border width').parentElement!.querySelector('input')!)
    expect(onChange).toHaveBeenCalledWith({ bandWidth: 26 })
  })

  it('edits the width, and goes back to the main width when switched off', async () => {
    const onChange = mount(value({ bandWidth: 40 }), 26)
    const field = screen.getByLabelText('Border Width (pixels)') as HTMLInputElement
    expect(field.value).toBe('40')
    fireEvent.change(field, { target: { value: '55' } })
    expect(onChange).toHaveBeenCalledWith({ bandWidth: 55 })
    await userEvent.click(screen.getByText('Own border width').parentElement!.querySelector('input')!)
    expect(onChange).toHaveBeenLastCalledWith({ bandWidth: null })
  })

  it('says that a frame image is not resized by it', () => {
    mount(value({ style: 'image', overlayImage: 'frame.png' }), 26)
    expect(screen.getByText(/A frame image is not resized/)).toBeTruthy()
    cleanup()
    mount(value(), 26)
    expect(screen.queryByText(/A frame image is not resized/)).toBeNull()
  })

  it('is stored with the style, and a style saved without one uses the main width', () => {
    expect(styleToSnake({ ...DEFAULT_SEASON_STYLE, bandWidth: 40 }).band_width).toBe(40)
    expect(styleToSnake(DEFAULT_SEASON_STYLE).band_width).toBeNull()
    expect(snakeToStyle({ style: 'solid', band_width: 40 }).bandWidth).toBe(40)
    expect(snakeToStyle({ style: 'solid', band_width: '40' }).bandWidth).toBe(40)
    for (const stored of [undefined, null, 0, '', 'wide']) {
      expect(snakeToStyle({ style: 'solid', band_width: stored }).bandWidth).toBeNull()
    }
    expect(snakeToStyle({ style: 'solid' }).bandWidth).toBeNull()
  })
})
