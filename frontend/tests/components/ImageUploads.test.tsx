import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ImageUploads from '../../src/components/overlays/ImageUploads'

const mockGetOverlayImages = vi.fn()
const mockFetchOverlayImage = vi.fn()
const mockUploadOverlayImage = vi.fn()
const mockDeleteOverlayImage = vi.fn()

vi.mock('../../src/api/overlays', () => ({
  getOverlayImages: (...args: unknown[]) => mockGetOverlayImages(...args),
  fetchOverlayImage: (...args: unknown[]) => mockFetchOverlayImage(...args),
  uploadOverlayImage: (...args: unknown[]) => mockUploadOverlayImage(...args),
  deleteOverlayImage: (...args: unknown[]) => mockDeleteOverlayImage(...args),
}))

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: () => 'api error',
}))

const logos = (netflixCustom: boolean) => ({
  type: 'streaming',
  set: 'white',
  set_label: 'White',
  fallback_label: 'White',
  noun: 'logo',
  folder: '/config/overlays/streaming/white',
  images: [
    { key: 'netflix', label: 'Netflix', source: netflixCustom ? 'custom' : 'bundled', custom: netflixCustom },
    { key: 'hulu', label: 'Hulu', source: 'bundled', custom: false },
  ],
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:logo', revokeObjectURL: () => undefined })
  mockGetOverlayImages.mockResolvedValue(logos(false))
  mockFetchOverlayImage.mockResolvedValue(new Blob(['png']))
  mockUploadOverlayImage.mockResolvedValue({ success: true, type: 'streaming', key: 'netflix', custom: true, width: 300, height: 100 })
  mockDeleteOverlayImage.mockResolvedValue({ success: true, type: 'streaming', key: 'netflix', custom: false })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// The picker draws its own list: it is opened to read or choose an entry, and closed again.
// Opening changes the page, so these are never called inside waitFor (it would re-run on its own changes).
const labelsOf = (picker: HTMLElement) => {
  fireEvent.click(picker)
  const labels = screen.queryAllByRole('option').map((o) => o.textContent)
  if (picker.getAttribute('aria-expanded') === 'true') fireEvent.click(picker)
  return labels
}
const choose = (picker: HTMLElement, label: string) => {
  fireEvent.click(picker)
  fireEvent.click(screen.getByRole('option', { name: label }))
}

describe('ImageUploads', () => {
  it('lists the keys, shows the current file and uploads a file for the selected one', async () => {
    const onChanged = vi.fn()
    render(<ImageUploads typeKey="streaming" idPrefix="ov" imageSet="white" onChanged={onChanged} />)
    const select = (await screen.findByRole('combobox')) as HTMLButtonElement
    await waitFor(() => expect(select.dataset.value).toBe('netflix'))
    expect(select.textContent).toBe('Netflix')
    expect(labelsOf(select)).toEqual(['Netflix', 'Hulu'])
    await waitFor(() => expect(mockGetOverlayImages).toHaveBeenCalledWith('streaming', 'white'))
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('streaming', 'netflix', 'white'))
    expect(screen.queryByRole('button', { name: /Use bundled/ })).toBeNull()

    choose(select, 'Hulu')
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('streaming', 'hulu', 'white'))

    const file = new File(['png-bytes'], 'hulu.png', { type: 'image/png' })
    mockGetOverlayImages.mockResolvedValue({ ...logos(false), images: [{ key: 'netflix', label: 'Netflix', source: 'bundled', custom: false }, { key: 'hulu', label: 'Hulu', source: 'custom', custom: true }] })
    fireEvent.change(screen.getByLabelText('Image file'), { target: { files: [file] } })
    await waitFor(() => expect(mockUploadOverlayImage).toHaveBeenCalledWith('streaming', 'hulu', file, 'white'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect((await screen.findByRole('status')).textContent).toContain('Hulu logo (White) now uses hulu.png (300 x 100)')
    await waitFor(() => expect(screen.getByRole('button', { name: /Use bundled/ })).toBeTruthy())
  })

  it('puts the bundled file back and reports API errors', async () => {
    mockGetOverlayImages.mockResolvedValue(logos(true))
    const onChanged = vi.fn()
    render(<ImageUploads typeKey="streaming" idPrefix="ov" imageSet="white" onChanged={onChanged} />)
    const restore = await screen.findByRole('button', { name: /Use bundled/ })
    mockGetOverlayImages.mockResolvedValue(logos(false))
    fireEvent.click(restore)
    await waitFor(() => expect(mockDeleteOverlayImage).toHaveBeenCalledWith('streaming', 'netflix', 'white'))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect((await screen.findByRole('status')).textContent).toContain('Netflix logo (White) is the bundled one again')
    await waitFor(() => expect(screen.queryByRole('button', { name: /Use bundled/ })).toBeNull())

    mockUploadOverlayImage.mockRejectedValue(new Error('nope'))
    fireEvent.change(screen.getByLabelText('Image file'), { target: { files: [new File(['x'], 'bad.txt')] } })
    await waitFor(() => expect(screen.getByRole('status').textContent).toBe('api error'))
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('follows the color set and marks keys that draw the default set\u2019s file', async () => {
    mockGetOverlayImages.mockResolvedValue({
      ...logos(false),
      set: 'color',
      set_label: 'Color',
      folder: '/config/overlays/streaming/color',
      images: [
        { key: 'netflix', label: 'Netflix', source: 'bundled', custom: false },
        { key: 'starz', label: 'Starz', source: 'fallback', custom: false },
      ],
    })
    render(<ImageUploads typeKey="streaming" idPrefix="ov" imageSet="color" />)
    const select = (await screen.findByRole('combobox')) as HTMLButtonElement
    await waitFor(() => expect(select.dataset.value).toBe('netflix'))
    expect(labelsOf(select)).toEqual(['Netflix', 'Starz (white logo)'])
    expect(mockGetOverlayImages).toHaveBeenCalledWith('streaming', 'color')
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('streaming', 'netflix', 'color'))
    fireEvent.change(screen.getByLabelText('Image file'), { target: { files: [new File(['x'], 'n.png')] } })
    await waitFor(() => expect(mockUploadOverlayImage).toHaveBeenCalledWith('streaming', 'netflix', expect.any(File), 'color'))
  })

  it('serves another type with its own noun and set names', async () => {
    mockGetOverlayImages.mockResolvedValue({
      type: 'content_rating',
      set: 'color',
      set_label: 'Color boxes',
      fallback_label: 'Color boxes',
      noun: 'badge',
      folder: '/config/overlays/content_rating/color',
      images: [{ key: 'r', label: 'R', source: 'bundled', custom: false }],
    })
    mockUploadOverlayImage.mockResolvedValue({ success: true, type: 'content_rating', key: 'r', custom: true, width: 119, height: 60 })
    render(<ImageUploads typeKey="content_rating" idPrefix="ov" imageSet="color" noun="badge" />)
    await screen.findByRole('combobox')
    await waitFor(() => expect(mockGetOverlayImages).toHaveBeenCalledWith('content_rating', 'color'))
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('content_rating', 'r', 'color'))
    expect(screen.getByAltText('R badge')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Image file'), { target: { files: [new File(['x'], 'r.png')] } })
    await waitFor(() => expect(mockUploadOverlayImage).toHaveBeenCalledWith('content_rating', 'r', expect.any(File), 'color'))
    expect((await screen.findByRole('status')).textContent).toBe('R badge (Color boxes) now uses r.png (119 x 60)')
  })

  it('leaves the set unnamed for a type with a single set', async () => {
    mockGetOverlayImages.mockResolvedValue({
      type: 'resolution',
      set: 'default',
      set_label: 'Default',
      fallback_label: 'Default',
      noun: 'badge',
      folder: '/config/overlays/resolution/default',
      images: [{ key: '4k', label: '4K', source: 'bundled', custom: false }],
    })
    mockUploadOverlayImage.mockResolvedValue({ success: true, type: 'resolution', key: '4k', custom: true, width: 200, height: 50 })
    render(<ImageUploads typeKey="resolution" idPrefix="ov" noun="badge" />)
    await screen.findByRole('combobox')
    await waitFor(() => expect(mockGetOverlayImages).toHaveBeenCalledWith('resolution', undefined))
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('resolution', '4k', undefined))
    fireEvent.change(screen.getByLabelText('Image file'), { target: { files: [new File(['x'], '4k.png')] } })
    await waitFor(() => expect(mockUploadOverlayImage).toHaveBeenCalledWith('resolution', '4k', expect.any(File), undefined))
    expect((await screen.findByRole('status')).textContent).toBe('4K badge now uses 4k.png (200 x 50)')  // no set to name
  })

  it('never shows or asks for the previous type\u2019s badges after the type changes', async () => {
    const ratings = { type: 'content_rating', set: 'color', set_label: 'Color boxes', fallback_label: 'Color boxes', noun: 'badge', folder: '/c', images: [{ key: 'r', label: 'R', source: 'bundled', custom: false }] }
    const resolutions = { type: 'resolution', set: 'default', set_label: 'Default', fallback_label: 'Default', noun: 'badge', folder: '/r', images: [{ key: '4k', label: '4K', source: 'bundled', custom: false }] }
    mockGetOverlayImages.mockResolvedValueOnce(ratings)
    const view = render(<ImageUploads typeKey="content_rating" idPrefix="ov" imageSet="color" noun="badge" />)
    const select = (await screen.findByRole('combobox')) as HTMLButtonElement
    await waitFor(() => expect(select.dataset.value).toBe('r'))
    expect(labelsOf(select)).toEqual(['R'])
    await waitFor(() => expect(screen.getByAltText('R badge')).toBeTruthy())

    // The next type's list is slow to arrive: until it does the picker is empty, not the old list.
    let arrive: (value: unknown) => void = () => undefined
    mockGetOverlayImages.mockReturnValueOnce(new Promise((resolve) => { arrive = resolve }))
    mockFetchOverlayImage.mockClear()
    view.rerender(<ImageUploads typeKey="resolution" idPrefix="ov" noun="badge" />)
    expect(select.disabled).toBe(true)
    expect(select.textContent).toBe('')  // no entry of the old type is shown, and the list cannot be opened
    expect(labelsOf(select)).toEqual([])
    expect(screen.queryByRole('img')).toBeNull()
    await new Promise((r) => setTimeout(r, 30))
    expect(mockFetchOverlayImage).not.toHaveBeenCalled()  // the rating key is never asked for under the other type

    arrive(resolutions)
    await waitFor(() => expect(select.disabled).toBe(false))
    expect(labelsOf(select)).toEqual(['4K'])
    await waitFor(() => expect(mockFetchOverlayImage).toHaveBeenCalledWith('resolution', '4k', undefined))
    expect(mockFetchOverlayImage).toHaveBeenCalledTimes(1)
  })
})
