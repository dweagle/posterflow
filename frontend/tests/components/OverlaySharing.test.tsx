import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { OverlayExportDialog, OverlayImportDialog } from '../../src/components/overlays/OverlaySharing'
import type { OverlayImportContents, OverlayTypeInfo, OverlaysConfig } from '../../src/api/overlays'

const mockOptions = vi.fn()
const mockExport = vi.fn()
const mockImport = vi.fn()

vi.mock('../../src/api/client', () => ({ getApiErrorMessage: (_err: unknown, fallback: string) => fallback }))

vi.mock('../../src/api/overlays', async () => ({
  ...(await vi.importActual<typeof import('../../src/api/overlays')>('../../src/api/overlays')),
  getOverlayExportOptions: (...args: unknown[]) => mockOptions(...args),
  exportOverlays: (...args: unknown[]) => mockExport(...args),
  importOverlays: (...args: unknown[]) => mockImport(...args),
}))

const type = (key: string, label: string, schema: unknown[] = []) => ({ key, label, schema } as unknown as OverlayTypeInfo)
const overlay = (name: string, options: Record<string, unknown> = {}) => ({ name, enabled: true, all_libraries: true, libraries: [], borders: [], placement: {}, options })
const types = [type('status', 'Airing Status'), type('streaming', 'Streaming'), type('rating', 'Rating'), type('custom', 'Custom', [{ kind: 'badges', key: 'badges' }])]
const config = {
  types: {
    status: { enabled: true, overlays: [overlay('Status')] },
    streaming: { enabled: true, overlays: [overlay('Where to watch'), overlay('')] },
    rating: { enabled: false, overlays: [] },
    custom: { enabled: true, overlays: [overlay('Networks', { badges: [{}, {}, {}] }), overlay('Awards', { badges: [{}] })] },
  },
} as unknown as OverlaysConfig

const box = (name: string | RegExp) => screen.getByRole('checkbox', { name }) as HTMLInputElement

describe('OverlayExportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockOptions.mockResolvedValue({ images: { streaming: 4, status: 1 } })
    mockExport.mockResolvedValue(undefined)
  })
  afterEach(() => { cleanup() })

  const open = (current?: { type: string; index: number }) => {
    const onDone = vi.fn()
    const onClose = vi.fn()
    render(<OverlayExportDialog types={types} config={config} current={current} onClose={onClose} onDone={onDone} />)
    return { onDone, onClose }
  }

  it('starts with the overlay being edited and exports one, some or all', async () => {
    const { onDone } = open({ type: 'custom', index: 0 })
    // Every type that has overlays is a group; a type with none is not listed.
    expect(screen.getAllByRole('group').map((g) => within(g).queryByText(/Airing Status|Streaming|Rating|Custom/)?.textContent)).toEqual(['Airing Status', 'Streaming', 'Custom'])
    expect(box(/^Networks/).checked).toBe(true)
    expect(screen.getByText('3 badges')).toBeTruthy()
    expect(screen.getByText('1 of 5 picked')).toBeTruthy()
    expect(box(/^Overlay 2/).checked).toBe(false)  // an overlay without a name

    fireEvent.click(screen.getByRole('button', { name: 'Export 1 Overlay' }))
    await waitFor(() => expect(mockExport).toHaveBeenCalledWith([{ type: 'custom', index: 0 }], []))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(1))
  })

  it('offers a type’s replaced images only while one of its overlays is picked', async () => {
    const { onDone } = open()
    expect((screen.getByRole('button', { name: 'Export' }) as HTMLButtonElement).disabled).toBe(true)
    await waitFor(() => expect(mockOptions).toHaveBeenCalled())
    expect(screen.queryByRole('checkbox', { name: /you replaced/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Select All' }))
    expect(screen.getByText('5 of 5 picked')).toBeTruthy()
    expect(box('Include the 4 images you replaced').checked).toBe(true)
    expect(box('Include the 1 image you replaced').checked).toBe(true)
    fireEvent.click(box('Include the 1 image you replaced'))
    fireEvent.click(box(/^Awards/))
    fireEvent.click(screen.getByRole('button', { name: 'Export 4 Overlays' }))
    await waitFor(() => expect(mockExport).toHaveBeenCalledWith(
      [{ type: 'status', index: 0 }, { type: 'streaming', index: 0 }, { type: 'streaming', index: 1 }, { type: 'custom', index: 0 }],
      ['streaming'],
    ))
    await waitFor(() => expect(onDone).toHaveBeenCalledWith(4))
  })

  it('leaves out the images of a type with nothing picked and says when the export fails', async () => {
    mockExport.mockRejectedValue(new Error('nope'))
    const { onDone } = open()
    await waitFor(() => expect(mockOptions).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: 'Select All' }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    fireEvent.click(box(/^Status/))
    fireEvent.click(screen.getByRole('button', { name: 'Export 1 Overlay' }))
    await waitFor(() => expect(mockExport).toHaveBeenCalledWith([{ type: 'status', index: 0 }], ['status']))
    expect((await screen.findByRole('alert')).textContent).toBe('The export failed')
    expect(onDone).not.toHaveBeenCalled()
    expect((screen.getByRole('button', { name: 'Export 1 Overlay' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('OverlayImportDialog', () => {
  const file = new File(['zip'], 'shared.zip', { type: 'application/zip' })
  const contents: OverlayImportContents = {
    file: 'shared.zip',
    overlays: [
      { index: 0, type: 'custom', type_label: 'Custom', name: 'Networks', badges: 288, images: 285, missing_images: 3, notes: ['2 conditions use a regex pattern. Check them before switching the overlay on.'] },
      { index: 2, type: 'status', type_label: 'Airing Status', name: 'Status', badges: null, images: 0, missing_images: 0, notes: [] },
      { index: 3, type: 'custom', type_label: 'Custom', name: 'Awards', badges: 1, images: 0, missing_images: 0, notes: [] },
    ],
    images: [
      { type: 'streaming', label: 'Streaming', noun: 'logo', count: 4, replaces: 0 },
      { type: 'audio', label: 'Audio', noun: 'badge', count: 1, replaces: 1 },
    ],
    unknown: 1,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockImport.mockResolvedValue({ success: true, overlays: [], images: {} })
  })
  afterEach(() => { cleanup() })

  it('lists what the file holds, with its notes, and imports what is picked', async () => {
    const onImported = vi.fn()
    render(<OverlayImportDialog file={file} contents={contents} onClose={vi.fn()} onImported={onImported} />)
    expect(screen.getByText('shared.zip')).toBeTruthy()
    expect(screen.getByText('288 badges, 285 images')).toBeTruthy()
    expect(screen.getByText('3 images of its badges are not in the file.')).toBeTruthy()
    expect(screen.getByText(/2 conditions use a regex pattern/)).toBeTruthy()
    expect(screen.getByText('1 overlay in the file cannot be read by this version. It is left out.')).toBeTruthy()
    expect([box(/^Networks/).checked, box(/^Status/).checked, box(/^Awards/).checked]).toEqual([true, true, true])
    // Images that replace none of the user's own come along; the ones that would are left for the user to tick.
    expect(box('Use the file’s 4 Streaming logos').checked).toBe(true)
    expect(box('Use the file’s 1 Audio badge').checked).toBe(false)
    expect(screen.getByText(/1 image of your own is replaced\./)).toBeTruthy()

    fireEvent.click(box(/^Status/))
    fireEvent.click(box('Use the file’s 1 Audio badge'))
    fireEvent.click(screen.getByRole('button', { name: 'Import 2 Overlays' }))
    await waitFor(() => expect(mockImport).toHaveBeenCalledWith(file, [0, 3], ['streaming', 'audio']))
    await waitFor(() => expect(onImported).toHaveBeenCalledWith({ success: true, overlays: [], images: {} }))
  })

  it('imports images alone, nothing at all is not offered, and a failure is said', async () => {
    mockImport.mockRejectedValue(new Error('nope'))
    const onImported = vi.fn()
    render(<OverlayImportDialog file={file} contents={contents} onClose={vi.fn()} onImported={onImported} />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(screen.getByText('0 of 3 picked')).toBeTruthy()
    fireEvent.click(box('Use the file’s 4 Streaming logos'))
    expect((screen.getByRole('button', { name: 'Import' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(box('Use the file’s 4 Streaming logos'))
    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    await waitFor(() => expect(mockImport).toHaveBeenCalledWith(file, [], ['streaming']))
    expect((await screen.findByRole('alert')).textContent).toBe('The import failed')
    expect(onImported).not.toHaveBeenCalled()
  })
})
