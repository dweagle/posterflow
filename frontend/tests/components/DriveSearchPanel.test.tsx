import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const searchPosters = vi.fn()
const searchArtwork = vi.fn()
vi.mock('../../src/api/client', () => ({
  searchPosters: (...args: unknown[]) => searchPosters(...args),
  searchArtwork: (...args: unknown[]) => searchArtwork(...args),
}))
vi.mock('../../src/api/http', () => ({ API_URL: '' }))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import DriveSearchPanel from '../../src/components/DriveSearchPanel'

const FILE = '/drives/cl/Movie One (2020).jpg'
const hit = {
  poster_name: 'Movie One (2020)',
  drive_count: 1,
  drives: [{
    drive_id: 'cl-1', drive_name: 'CL Drive', drive_type: 'cl2k', style_type: 'CL2K', is_custom: false,
    poster_id: 1, image_url: '/api/stats/posters/1/image', file_path: FILE,
  }],
}

describe('DriveSearchPanel', () => {
  afterEach(() => {
    cleanup()
    searchPosters.mockReset()
  })

  it('offers a Use action per result and reports the picked file', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    const onUse = vi.fn()
    const user = userEvent.setup()
    render(<DriveSearchPanel initialQuery="Movie One" onUse={onUse} useLabel="Use for…" />)

    await user.click(await screen.findByRole('button', { name: 'Use for… Movie One (2020)' }))
    expect(onUse).toHaveBeenCalledWith({
      poster_name: 'Movie One (2020)', drive_id: 'cl-1', drive_name: 'CL Drive', file_path: FILE, artwork_type: null,
    })
  })

  it('marks the pinned file as in use', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(<DriveSearchPanel initialQuery="Movie One" onUse={vi.fn()} pickedFiles={[FILE]} />)

    const btn = await screen.findByRole('button', { name: 'Stop using Movie One (2020)' })
    expect(btn.className).toContain('active')
    expect(btn.textContent).toContain('Using')
  })

  it('reads "In use" instead of offering Use for the current file, and shows file notes', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(
      <DriveSearchPanel
        initialQuery="Movie One"
        onUse={vi.fn()}
        inUseFiles={[FILE]}
        fileBadges={{ [FILE]: [{ kind: 'pinned', text: 'Pinned · Elsewhere (2001)' }] }}
      />,
    )

    expect(await screen.findByText('In use')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Use Movie One/ })).toBeNull()
    expect(screen.getByText('Pinned · Elsewhere (2001)')).toBeTruthy()
  })

  it('stays read-only without a handler', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(<DriveSearchPanel initialQuery="Movie One" />)

    await screen.findByRole('button', { name: 'Copy title Movie One (2020)' })
    expect(screen.queryByRole('button', { name: /Use/ })).toBeNull()
  })
})

describe('DriveSearchPanel artwork mode', () => {
  afterEach(() => {
    cleanup()
    searchPosters.mockReset()
    searchArtwork.mockReset()
  })

  const artHit = {
    artwork_name: 'Movie One (2020)',
    artwork_type: 'background',
    drive_count: 1,
    drives: [{
      drive_id: 'art-a', drive_name: 'Art Drive A', drive_type: 'artwork', is_custom: false,
      artwork_id: 7, image_url: '/api/stats/artwork/7/image', file_path: '/drives/art/backgrounds/Movie One (2020).jpg',
    }],
  }

  it('switches to the artwork index, shows the type, and reports artwork picks with their type', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    searchArtwork.mockResolvedValue({ query: 'Movie One', count: 1, items: [artHit] })
    const onUse = vi.fn()
    const user = userEvent.setup()
    render(<DriveSearchPanel initialQuery="Movie One" onUse={onUse} useLabel="Use for…" />)
    await screen.findByRole('button', { name: 'Use for… Movie One (2020)' })

    await user.click(screen.getByRole('tab', { name: 'Artwork' }))
    await waitFor(() => expect(searchArtwork).toHaveBeenCalledWith('Movie One'))
    expect(await screen.findByText('Background')).toBeTruthy()
    expect(screen.getByText('ARTWORK')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Backgrounds' }).className).toContain('active')

    await user.click(screen.getByRole('button', { name: 'Use for… Movie One (2020)' }))
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({
      artwork_type: 'background', drive_id: 'art-a', file_path: '/drives/art/backgrounds/Movie One (2020).jpg',
    }))

    // Type filters hide rows client-side.
    await user.click(screen.getByRole('button', { name: 'Backgrounds' }))
    expect(screen.queryByText('Movie One (2020)')).toBeNull()
  })

  it('hides the kind toggle when locked to posters', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(<DriveSearchPanel initialQuery="Movie One" fixedKind="posters" />)
    await screen.findByText('Movie One (2020)')
    expect(screen.queryByRole('tab')).toBeNull()
  })
})

describe('DriveSearchPanel hover preview', () => {
  afterEach(() => {
    cleanup()
    searchPosters.mockReset()
    vi.useRealTimers()
  })

  it('shows the popup after the hover delay, by the cursor, and follows the mouse without re-rendering', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(<DriveSearchPanel initialQuery="Movie One" />)
    const title = await screen.findByText('Movie One (2020)')

    vi.useFakeTimers()
    fireEvent.mouseEnter(title, { clientX: 100, clientY: 120 })
    expect(document.querySelector('.hover-preview-popup')).toBeNull()
    act(() => {
      vi.advanceTimersByTime(300)
    })
    const popup = document.querySelector('.hover-preview-popup') as HTMLDivElement
    expect(popup).toBeTruthy()
    // jsdom reports a 0x0 box, so the popup lands at cursor + offset.
    expect(popup.style.left).toBe('118px')
    expect(popup.style.top).toBe('138px')

    fireEvent.mouseMove(title, { clientX: 200, clientY: 220 })
    expect(popup.style.left).toBe('218px')
    expect(popup.style.top).toBe('238px')

    fireEvent.mouseLeave(title)
    expect(document.querySelector('.hover-preview-popup')).toBeNull()
  })
})

describe('DriveSearchPanel button tips', () => {
  afterEach(() => {
    cleanup()
    searchPosters.mockReset()
    vi.useRealTimers()
  })

  it('shows a body-level tip for the Use button, clamped inside the viewport', async () => {
    searchPosters.mockResolvedValue({ query: 'Movie One', count: 1, items: [hit] })
    render(<DriveSearchPanel initialQuery="Movie One" onUse={vi.fn()} useLabel="Use for…" useTitle="Pin this file" />)
    const btn = await screen.findByRole('button', { name: 'Use for… Movie One (2020)' })
    expect(btn.getAttribute('title')).toBeNull()

    vi.useFakeTimers()
    // A trigger hugging the right edge: the tip must not be placed past it.
    btn.getBoundingClientRect = () => ({ left: 1000, right: 1024, top: 300, bottom: 320, width: 24, height: 20, x: 1000, y: 300, toJSON: () => ({}) })
    fireEvent.pointerEnter(btn, { pointerType: 'mouse' })
    act(() => {
      vi.advanceTimersByTime(700)
    })
    const tip = document.body.querySelector('.float-tip') as HTMLDivElement
    expect(tip.textContent).toBe('Pin this file')
    expect(tip.style.display).toBe('block')
    expect(parseInt(tip.style.left, 10)).toBeLessThanOrEqual(1024 - 8)
    expect(parseInt(tip.style.left, 10)).toBeGreaterThanOrEqual(8)

    fireEvent.pointerLeave(btn)
    expect(tip.style.display).toBe('none')
  })
})
