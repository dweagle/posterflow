import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import OverlayPreview from '../../src/components/overlays/OverlayPreview'
import type { OverlayPlacement } from '../../src/api/overlays'

const mockFetchOverlayPreview = vi.fn()

vi.mock('../../src/api/overlays', () => ({
  fetchOverlayPreview: (...args: unknown[]) => mockFetchOverlayPreview(...args),
}))

const placement: OverlayPlacement = {
  horizontal_align: 'center',
  horizontal_offset: 0,
  vertical_align: 'top',
  vertical_offset: 30,
  font: 'RobotoCondensed-Variable',
  font_weight: 'SemiBold',
  font_size: 48,
  font_color: '#FFFFFF',
  letter_spacing: 6,
  stroke_width: 0,
  stroke_color: '#000000',
  back_enabled: true,
  back_style: 'solid',
  back_color: '#000000B3',
  back_padding: 22,
  back_radius: 30,
  back_line_color: '#00000000',
  back_line_width: 0,
  back_min_width: 0,
  back_max_width: 0,
  gradient_start: '#000000D9',
  gradient_mode: 'linear',
  gradient_angle: 90,
  gradient_spread: 60,
  gradient_band_offset: 0,
  gradient_band_height: 400,
  gradient_band_left: 0,
  gradient_band_right: 0,
  gradient_band_curve: 'linear',
}

const result = (text: string) => ({ blob: new Blob([text]), rect: { x: 300, y: 35, w: 400, h: 80 }, text, layers: [] })
const settle = () => new Promise((r) => setTimeout(r, 450))  // past the 300 ms render debounce

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('URL', { ...URL, createObjectURL: (blob: Blob) => `blob:${blob.size}`, revokeObjectURL: () => undefined })
  mockFetchOverlayPreview.mockImplementation(({ type }: { type: string }) => Promise.resolve(result(type.toUpperCase())))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('OverlayPreview render cache', () => {
  it('shows a render it already has instead of asking the server again', async () => {
    const options = { a: 1 }
    const view = render(<OverlayPreview type="status" variant="airing" placement={placement} options={options} onPlacementChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('img')).toHaveProperty('alt', 'Overlay preview: STATUS'))
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(1)

    view.rerender(<OverlayPreview type="rating" variant="airing" placement={placement} options={options} onPlacementChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByRole('img')).toHaveProperty('alt', 'Overlay preview: RATING'))
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(2)

    // Back to the first type with nothing changed: the earlier render, at once, no request.
    view.rerender(<OverlayPreview type="status" variant="airing" placement={placement} options={options} onPlacementChange={vi.fn()} />)
    expect(screen.getByRole('img')).toHaveProperty('alt', 'Overlay preview: STATUS')
    expect(screen.queryByText('Rendering…')).toBeNull()
    await settle()
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(2)

    // A real change renders again; so does a changed asset version with the same settings.
    view.rerender(<OverlayPreview type="status" variant="airing" placement={placement} options={{ a: 2 }} onPlacementChange={vi.fn()} />)
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(3))
    view.rerender(<OverlayPreview type="status" variant="airing" placement={placement} options={{ a: 2 }} onPlacementChange={vi.fn()} refreshKey={1} />)
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(4))
  })

  it('keeps only the most recent renders', async () => {
    const view = render(<OverlayPreview type="status" variant="airing" placement={placement} options={{}} onPlacementChange={vi.fn()} />)
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(1))
    for (let i = 1; i <= 8; i++) {
      view.rerender(<OverlayPreview type="status" variant={`v${i}`} placement={placement} options={{}} onPlacementChange={vi.fn()} />)
      await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(1 + i))
    }
    // The first render (nine back) was evicted: it is fetched again. v1 (eight back) is still there.
    view.rerender(<OverlayPreview type="status" variant="airing" placement={placement} options={{}} onPlacementChange={vi.fn()} />)
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(10))
    view.rerender(<OverlayPreview type="status" variant="v2" placement={placement} options={{}} onPlacementChange={vi.fn()} />)
    await settle()
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(10)
  })
})

describe('OverlayPreview handles on the other overlays', () => {
  it('drags and nudges another layer\'s badge, changing that overlay and not the edited one', async () => {
    Object.defineProperty(HTMLImageElement.prototype, 'clientWidth', { configurable: true, get: () => 500 })
    const onPlacementChange = vi.fn()
    const onLayerPlacementChange = vi.fn()
    const rating = { ...placement, horizontal_align: 'right' as const, horizontal_offset: 35, vertical_align: 'bottom' as const, vertical_offset: 35 }
    mockFetchOverlayPreview.mockResolvedValue({ blob: new Blob(['x']), rect: { x: 300, y: 35, w: 400, h: 80 }, text: 'AIRING', layers: [{ type: 'rating', rect: { x: 665, y: 1365, w: 300, h: 100 } }] })
    render(
      <OverlayPreview
        type="status" variant="airing" placement={placement} options={{}} onPlacementChange={onPlacementChange}
        others={[{ type: 'rating', variant: 'top', placement: rating, options: {} }]} labels={{ rating: 'Rating' }} onLayerPlacementChange={onLayerPlacementChange}
      />,
    )
    const handle = await screen.findByRole('slider', { name: 'Rating position (drag or use arrow keys)' })
    fireEvent.load(screen.getByAltText(/Overlay preview/))
    expect(screen.getByRole('slider', { name: /Badge position/ })).toBeTruthy()  // the edited overlay keeps its own handle
    expect(handle.className).toContain('other')
    // 500px wide image → 0.5 px per reference px: a 50px drag left and 10px up moves it 100 / 20 reference px.
    // Right- and bottom-aligned, so the offsets grow: 35 → 135 from the right, 35 → 55 from the bottom.
    fireEvent.pointerDown(handle, { clientX: 0, clientY: 0, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: -50, clientY: -10, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: -50, clientY: -10, pointerId: 1 })
    expect(onLayerPlacementChange).toHaveBeenCalledWith('rating', { horizontal_align: 'right', horizontal_offset: 135, vertical_align: 'bottom', vertical_offset: 55 })
    expect(onPlacementChange).not.toHaveBeenCalled()
    // Arrow keys nudge from that layer's own offsets (right/bottom aligned: left and up grow them).
    fireEvent.keyDown(handle, { key: 'ArrowLeft', shiftKey: true })
    expect(onLayerPlacementChange).toHaveBeenLastCalledWith('rating', { horizontal_offset: 45, vertical_offset: 35 })
    fireEvent.keyDown(handle, { key: 'ArrowDown' })
    expect(onLayerPlacementChange).toHaveBeenLastCalledWith('rating', { horizontal_offset: 35, vertical_offset: 34 })
    expect(onPlacementChange).not.toHaveBeenCalled()
  })
})
