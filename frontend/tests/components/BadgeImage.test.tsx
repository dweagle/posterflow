import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BadgeImage from '../../src/components/overlays/BadgeImage'

const mockUpload = vi.fn()
const mockFetch = vi.fn()

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}))

vi.mock('../../src/api/overlays', () => ({
  uploadCustomBadge: (...args: unknown[]) => mockUpload(...args),
  fetchCustomBadge: (...args: unknown[]) => mockFetch(...args),
}))

describe('BadgeImage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:badge'), revokeObjectURL: vi.fn() })
    mockFetch.mockResolvedValue(new Blob(['png']))
    mockUpload.mockResolvedValue({ success: true, id: 'abcdef012345', width: 80, height: 40 })
  })

  afterEach(() => {
    cleanup()
  })

  it('offers an upload while there is no image and hands back the id of the upload', async () => {
    const onChange = vi.fn()
    render(<BadgeImage id="ov-image" name="Netflix" imageId="" onChange={onChange} />)
    expect(screen.getByRole('button', { name: 'Upload Netflix image' })).toBeTruthy()
    expect(screen.queryByAltText('Netflix image')).toBeNull()
    expect(mockFetch).not.toHaveBeenCalled()
    const file = new File(['x'], 'ribbon.png', { type: 'image/png' })
    fireEvent.change(screen.getByLabelText('Netflix image file'), { target: { files: [file] } })
    await waitFor(() => expect(onChange).toHaveBeenCalledWith('abcdef012345'))
    expect(mockUpload).toHaveBeenCalledWith(file)
  })

  it('shows the picture it names and offers to replace it', async () => {
    render(<BadgeImage id="ov-image" name="Netflix" imageId="abcdef012345" onChange={vi.fn()} />)
    expect(await screen.findByAltText('Netflix image')).toBeTruthy()
    expect(mockFetch).toHaveBeenCalledWith('abcdef012345')
    expect(screen.getByRole('button', { name: 'Replace Netflix image' })).toBeTruthy()
  })

  it('reports an upload that failed and an image that is gone', async () => {
    mockUpload.mockRejectedValue(new Error('too big'))
    mockFetch.mockRejectedValue(new Error('404'))
    const onChange = vi.fn()
    render(<BadgeImage id="ov-image" name="Netflix" imageId="abcdef012345" onChange={onChange} />)
    expect(await screen.findByText('That image is gone. Upload it again.')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Netflix image file'), { target: { files: [new File(['x'], 'big.png', { type: 'image/png' })] } })
    expect(await screen.findByText('The image upload failed')).toBeTruthy()
    expect(onChange).not.toHaveBeenCalled()
  })
})
