import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import GeminiImageModal from '../../src/components/maker-tools/GeminiImageModal'
import { getGeminiPrompts, keepGeminiImage, runGeminiImageEdit } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getGeminiPrompts: vi.fn(),
  runGeminiImageEdit: vi.fn(),
  keepGeminiImage: vi.fn(),
}))

const mockedRun = vi.mocked(runGeminiImageEdit)
const mockedPrompts = vi.mocked(getGeminiPrompts)
const mockedKeep = vi.mocked(keepGeminiImage)
const image = { file_path: '/p.jpg', width: 2000, height: 3000, language: 'en', vote_average: 5, url_thumb: 'https://t/p.jpg', url_full: 'https://f/p.jpg' }
const item = { title: 'Fight Club', media_type: 'movie', year: '1999', tmdb_id: 550, tvdb_id: null, imdb_id: null }
const prompts = {
  remove_text: { detailed: 'Built-in remove prompt.', simple: 'Remove text from this image' },
  extract_logo: { detailed: 'Built-in logo prompt.', simple: 'Pull the logo from this image.' },
}

const response = (overrides: Record<string, unknown> = {}) => ({
  operation: 'remove_text' as const, ref: 'gemini:tmdb-550/poster-abcdefabcdef.jpg', file_url: '/api/maker-tools/gemini-image/file?ref=x',
  image_base64: 'AAAA', mime_type: 'image/jpeg', width: 2000, height: 3000,
  output_width: 1365, output_height: 2048, model: 'gemini-nano-banana-2.1', image_size: '2K' as const,
  elapsed_seconds: 12.3, filename: 'Fight Club (1999) {tmdb-550} - textless.jpg', ...overrides,
})

describe('GeminiImageModal', () => {
  beforeEach(() => {
    mockedRun.mockReset()
    mockedPrompts.mockReset()
    mockedPrompts.mockResolvedValue(prompts)
    mockedKeep.mockReset()
  })
  afterEach(() => { cleanup() })

  it('remove text: prefills the built-in prompt, runs with the chosen size, shows the result', async () => {
    mockedRun.mockResolvedValue(response({ image_size: '4K' }))
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onClose={() => {}} />)

    expect(screen.getByText('Remove text with Gemini')).toBeTruthy()
    expect(screen.getByText(/scaled back to 2000×3000/)).toBeTruthy()
    await waitFor(() => expect((screen.getByLabelText('Gemini prompt') as HTMLTextAreaElement).value).toBe('Built-in remove prompt.'))
    fireEvent.change(screen.getByLabelText('Output size'), { target: { value: '4K' } })
    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))

    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    expect(mockedRun).toHaveBeenCalledWith(item, '/p.jpg', 'poster', {
      operation: 'remove_text', imageSize: '4K', model: 'gemini-nano-banana-2.1', prompt: 'Built-in remove prompt.', season: undefined, source: 'tmdb',
    })
    const img = screen.getByTestId('gemini-result')
    expect(img.getAttribute('src')).toBe('data:image/jpeg;base64,AAAA')
    expect(img.className).toBe('')
    expect(screen.getByText(/rendered at 1365×2048 in 12.3s/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /download/i })).toBeTruthy()
    expect(screen.getByRole('button', { name: /run again/i })).toBeTruthy()
  })

  it('tells the user a small poster will be scaled up to the floor', () => {
    render(<GeminiImageModal mode="remove_text" image={{ ...image, width: 680, height: 1000 }} role="poster" source="tmdb" item={item} onClose={() => {}} />)
    expect(screen.getByText(/under 1000×1500, so the result is scaled up to at least that size/)).toBeTruthy()

    cleanup()
    // Backdrops have no floor, so they keep the plain wording.
    render(<GeminiImageModal mode="remove_text" image={{ ...image, width: 1280, height: 720 }} role="background" source="tmdb" item={item} onClose={() => {}} />)
    expect(screen.getByText(/scaled back to 1280×720/)).toBeTruthy()
  })

  it('make logo: sends the edited prompt and renders the PNG on a checkerboard', async () => {
    mockedRun.mockResolvedValue(response({
      operation: 'extract_logo', mime_type: 'image/png', width: 1166, height: 424, filename: 'Fight Club (1999) {tmdb-550} - logo.png',
    }))
    render(<GeminiImageModal mode="extract_logo" image={image} role="background" season={null} source="fanart" item={item} onClose={() => {}} />)

    expect(screen.getByText('Make a logo with Gemini')).toBeTruthy()
    await waitFor(() => expect((screen.getByLabelText('Gemini prompt') as HTMLTextAreaElement).value).toBe('Built-in logo prompt.'))
    fireEvent.change(screen.getByLabelText('Gemini prompt'), { target: { value: ' Solid white logo on solid black. ' } })
    fireEvent.click(screen.getByRole('button', { name: /make logo/i }))

    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    expect(mockedRun).toHaveBeenCalledWith(item, '/p.jpg', 'background', {
      operation: 'extract_logo', imageSize: '2K', model: 'gemini-nano-banana-2.1', prompt: 'Solid white logo on solid black.', season: null, source: 'fanart',
    })
    const img = screen.getByTestId('gemini-result')
    expect(img.getAttribute('src')).toBe('data:image/png;base64,AAAA')
    expect(img.className).toContain('tmdb-gallery-lightbox-img--logo')
    expect(screen.getByText(/Logo · 1166×424/)).toBeTruthy()
  })

  it('opens the result full size on click and closes the zoom on click', async () => {
    mockedRun.mockResolvedValue(response())
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))
    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    expect(screen.queryByTestId('gemini-zoom')).toBeNull()

    fireEvent.click(screen.getByTestId('gemini-result'))
    const zoom = screen.getByTestId('gemini-zoom')
    expect(zoom.querySelector('img')?.getAttribute('src')).toBe('data:image/jpeg;base64,AAAA')

    fireEvent.click(zoom)
    expect(screen.queryByTestId('gemini-zoom')).toBeNull()
  })

  it('swaps between the detailed and simple prompts and sends the chosen one', async () => {
    mockedRun.mockResolvedValue(response())
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onClose={() => {}} />)
    const box = () => screen.getByLabelText('Gemini prompt') as HTMLTextAreaElement
    await waitFor(() => expect(box().value).toBe('Built-in remove prompt.'))

    fireEvent.click(screen.getByRole('button', { name: 'Simple' }))
    expect(box().value).toBe('Remove text from this image')
    expect(screen.getByRole('button', { name: 'Simple' }).className).toContain('btn-primary')
    expect(screen.getByRole('button', { name: 'Detailed' }).className).not.toContain('btn-primary')

    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))
    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    expect(mockedRun.mock.calls[0][3].prompt).toBe('Remove text from this image')

    fireEvent.click(screen.getByRole('button', { name: 'Detailed' }))
    expect(box().value).toBe('Built-in remove prompt.')
    // Edited text lights up neither button.
    fireEvent.change(box(), { target: { value: 'My own words' } })
    expect(screen.getByRole('button', { name: 'Simple' }).className).not.toContain('btn-primary')
    expect(screen.getByRole('button', { name: 'Detailed' }).className).not.toContain('btn-primary')
  })

  it('Keep pins the parked result and hands the tile to the card', async () => {
    mockedRun.mockResolvedValue(response())
    const tile = { ref: 'gemini:tmdb-550/poster-abcdefabcdef.jpg', role: 'poster' as const, source: 'tmdb' as const, season: null, operation: 'remove_text' as const, width: 2000, height: 3000, download_name: 'x.jpg', url_full: '/f', url_thumb: '/f' }
    mockedKeep.mockResolvedValue(tile)
    const onKeep = vi.fn()
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onKeep={onKeep} onClose={() => {}} />)
    expect(screen.queryByRole('button', { name: /keep/i })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))
    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /^keep$/i }))

    await waitFor(() => expect(screen.getByRole('button', { name: /kept/i })).toBeTruthy())
    expect(mockedKeep).toHaveBeenCalledWith('gemini:tmdb-550/poster-abcdefabcdef.jpg')
    expect(onKeep).toHaveBeenCalledWith(tile)
    expect((screen.getByRole('button', { name: /kept/i }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows the API error and keeps the controls usable', async () => {
    mockedRun.mockRejectedValue({ response: { data: { detail: 'Gemini billing problem (HTTP 402): credits depleted' } } })
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onClose={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))

    expect(await screen.findByText('Gemini billing problem (HTTP 402): credits depleted')).toBeTruthy()
    expect(screen.queryByTestId('gemini-result')).toBeNull()
    expect((screen.getByRole('button', { name: /remove text/i }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('still runs when the prompt lookup fails, leaving the prompt blank for the server default', async () => {
    mockedPrompts.mockRejectedValue(new Error('offline'))
    mockedRun.mockResolvedValue(response())
    render(<GeminiImageModal mode="remove_text" image={image} role="poster" source="tmdb" item={item} onClose={() => {}} />)

    fireEvent.click(screen.getByRole('button', { name: /remove text/i }))

    await waitFor(() => expect(screen.getByTestId('gemini-result')).toBeTruthy())
    expect(mockedRun.mock.calls[0][3].prompt).toBe('')
  })
})
