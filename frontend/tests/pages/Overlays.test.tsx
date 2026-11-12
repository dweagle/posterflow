import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Overlays from '../../src/pages/Overlays'
import { pick, picker, pickerOptions } from '../utils/listPicker'

const mockShowToast = vi.fn()
const mockGetJob = vi.fn()
const mockGetOverlayTypes = vi.fn()
const mockGetOverlaysConfig = vi.fn()
const mockSaveOverlaysConfig = vi.fn()
const mockGetOverlayFonts = vi.fn()
const mockFetchOverlayPreview = vi.fn()
const mockRunOverlays = vi.fn()
const mockRunTool = vi.fn()
const mockGetTools = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: () => 'api error',
  getJob: (...args: unknown[]) => mockGetJob(...args),
}))

vi.mock('../../src/api/tools', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/tools')>()),
  runTool: (...args: unknown[]) => mockRunTool(...args),
  getTools: (...args: unknown[]) => mockGetTools(...args),
}))

vi.mock('../../src/api/overlays', async () => {
  const actual = await vi.importActual<typeof import('../../src/api/overlays')>('../../src/api/overlays')
  return {
    ...actual,
    getOverlayTypes: (...args: unknown[]) => mockGetOverlayTypes(...args),
    getOverlaysConfig: (...args: unknown[]) => mockGetOverlaysConfig(...args),
    saveOverlaysConfig: (...args: unknown[]) => mockSaveOverlaysConfig(...args),
    getOverlayFonts: (...args: unknown[]) => mockGetOverlayFonts(...args),
    fetchOverlayPreview: (...args: unknown[]) => mockFetchOverlayPreview(...args),
    runOverlays: (...args: unknown[]) => mockRunOverlays(...args),
  }
})

const placement = {
  horizontal_align: 'center',
  horizontal_offset: 0,
  vertical_align: 'top',
  vertical_offset: 35,
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

const statusType = {
  key: 'status',
  label: 'Airing Status',
  description: 'AIRING / ENDED badges',
  order: 10,
  targets: ['movie', 'show', 'season'],  // as the real Airing Status type: movies get a New badge
  needs: ['tmdb'],
  hidden_placement: ['back_color'],
  schema: [
    { kind: 'number', key: 'new_days', label: 'New for (days)', min: 1, max: 365, section: 'Timing' },
    { kind: 'toggle', key: 'apply_to_seasons', label: 'Season posters too', section: 'Seasons', help: 'Also badge each season poster.' },
    { kind: 'toggle', key: 'show_dates', label: 'Show dates', section: 'Dates' },
    {
      kind: 'states',
      key: 'states',
      label: 'Statuses',
      items: [
        { key: 'airing', label: 'Airing', fields: [{ kind: 'text', key: 'text', label: 'Text' }, { kind: 'text', key: 'text_with_date', label: 'Text with date', depends_on: 'show_dates' }, { kind: 'color', key: 'back_color', label: 'Backdrop', alpha: true }], disabled_fields: [] },
        { key: 'ended', label: 'Ended', fields: [{ kind: 'text', key: 'text', label: 'Text' }, { kind: 'text', key: 'text_with_date', label: 'Text with date', depends_on: 'show_dates' }, { kind: 'color', key: 'back_color', label: 'Backdrop', alpha: true }], disabled_fields: ['text_with_date'] },
      ],
    },
  ],
  variants: [
    { key: 'airing', label: 'Airing', group: 'states', item: 'airing' },
    { key: 'ended', label: 'Ended', group: 'states', item: 'ended' },
  ],
  default_placement: placement,
  default_options: { new_days: 30, apply_to_seasons: false, show_dates: true, states: { airing: { enabled: true, text: 'AIRING', back_color: '#1E88E5E6' }, ended: { enabled: true, text: 'ENDED', back_color: '#424242E6' } } },
}

const ratingType = {
  key: 'rating',
  label: 'Rating',
  description: 'The score on a color-coded box',
  order: 20,
  targets: ['movie', 'show'],
  needs: [],
  hidden_placement: ['back_color'],
  schema: [
    { kind: 'select', key: 'source', label: 'Source', section: 'Source', options: [{ value: 'server_audience', label: 'Audience rating (Plex / Jellyfin)' }, { value: 'tmdb', label: 'TMDB score' }] },
    { kind: 'number', key: 'hide_below', label: 'Hide below', min: 0, max: 10, step: 0.1, section: 'Number' },
    { kind: 'toggle', key: 'tmdb_only', label: 'TMDB only', section: 'Source', show_when: { source: 'tmdb' } },
    {
      kind: 'states',
      key: 'tiers',
      label: 'Tiers',
      items: [
        { key: 'top', label: 'Top', fields: [{ kind: 'number', key: 'min', label: 'From', min: 0, max: 10, step: 0.1 }, { kind: 'color', key: 'back_color', label: 'Backdrop', alpha: true }] },
        { key: 'low', label: 'Low', fields: [{ kind: 'number', key: 'min', label: 'From', min: 0, max: 10, step: 0.1 }, { kind: 'color', key: 'back_color', label: 'Backdrop', alpha: true }] },
      ],
    },
  ],
  variants: [{ key: 'top', label: 'Top (9.2)', group: 'tiers', item: 'top' }, { key: 'low', label: 'Low (4.3)', group: 'tiers', item: 'low' }, { key: 'sample', label: 'Audience sample', option: 'source', labels: { server_audience: 'Audience sample', tmdb: 'TMDB sample' } }],
  default_placement: { ...placement, horizontal_align: 'right', vertical_align: 'bottom' },
  default_options: { source: 'server_audience', hide_below: 0, tmdb_only: false, tiers: { top: { enabled: true, min: 9, back_color: '#0B6E0BFF' }, low: { enabled: true, min: 0, back_color: '#E23133FF' } } },
}

// Dropdowns on this page are ListPickers (see tests/utils/listPicker): opened to read or choose.
const stateLabels = () => pickerOptions('Preview State')
const pickState = (label: string) => pick('Preview State', label)

const baseConfig = () => ({
  enabled: false,
  all_libraries: true,
  libraries: [],
  badge_server_posters: false,
  prune_plex_uploads: true,
  types: {
    status: { enabled: false, overlays: [{ name: 'Airing Status', enabled: true, all_libraries: true, libraries: [], placement: { ...placement }, options: JSON.parse(JSON.stringify(statusType.default_options)) }] },
    rating: { enabled: false, overlays: [{ name: 'Rating', enabled: true, all_libraries: true, libraries: [], placement: { ...ratingType.default_placement }, options: JSON.parse(JSON.stringify(ratingType.default_options)) }] },
  },
})

const renderPage = () => render(<MemoryRouter><Overlays /></MemoryRouter>)

describe('Overlays', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:preview'), revokeObjectURL: vi.fn() })
    Object.defineProperty(HTMLImageElement.prototype, 'clientWidth', { configurable: true, get: () => 500 })
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType] })
    mockGetOverlaysConfig.mockResolvedValue({ config: baseConfig(), has_tmdb_key: true, library_configs: [], badged_count: 0 })
    mockRunTool.mockImplementation(async (id: string) => ({ success: true, job_id: id === 'plex_cleanup' ? 11 : 12, message: 'queued' }))
    mockGetTools.mockResolvedValue({ groups: [
      { id: 'plex', label: 'Plex', instances: ['plex'], tools: [{ id: 'plex_reset_posters', label: 'Reset Plex posters', available: true }] },
      { id: 'jellyfin', label: 'Jellyfin', instances: [], tools: [{ id: 'jellyfin_reset_posters', label: 'Reset Jellyfin posters', available: false }] },
    ] })
    mockGetOverlayFonts.mockResolvedValue({ fonts: [
      { id: 'RobotoCondensed-Variable', label: 'Roboto Condensed', source: 'bundled', weights: ['Regular', 'SemiBold', 'Bold'] },
      { id: 'LiberationSans-Regular', label: 'Liberation Sans', source: 'bundled', weights: [] },
    ] })
    mockSaveOverlaysConfig.mockImplementation(async (config: unknown) => ({ success: true, config, badged_count: 0 }))
    mockGetJob.mockResolvedValue({ id: 7, status: 'completed', message: 'Overlays complete: 3 restored', error: null })
    mockFetchOverlayPreview.mockResolvedValue({ blob: new Blob(['x']), rect: { x: 300, y: 35, w: 400, h: 80 }, text: 'AIRING' })
    mockRunOverlays.mockResolvedValue({ success: true, job_id: 7, message: 'queued' })
  })

  afterEach(() => {
    cleanup()
  })

  it('lists overlay types, enables one, and saves the config', async () => {
    const user = userEvent.setup()
    renderPage()
    expect(await screen.findByRole('heading', { level: 2, name: 'Airing Status' })).toBeTruthy()
    expect(screen.getByText('Overlays are OFF')).toBeTruthy()
    // Every status sets its own backdrop, so the shared backdrop color is hidden for this type.
    expect(screen.queryByLabelText('Color')).toBeNull()
    expect(screen.getByLabelText('Default text color')).toBeTruthy()

    await user.click(screen.getByLabelText('Overlays are OFF'))
    await user.click(screen.getByLabelText('Airing Status off'))
    // Toolbar, the card's head and its foot all offer the Save + Cancel pair.
    expect(screen.getAllByRole('button', { name: /^Save$/ })).toHaveLength(3)
    expect(screen.getAllByRole('button', { name: /^Cancel$/ })).toHaveLength(3)
    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[2])

    await waitFor(() => expect(mockSaveOverlaysConfig).toHaveBeenCalledTimes(1))
    const saved = mockSaveOverlaysConfig.mock.calls[0][0]
    expect(saved.enabled).toBe(true)
    expect(saved.types.status.enabled).toBe(true)
    expect(mockShowToast).toHaveBeenCalledWith('Overlay settings saved', 'success')
  })

  it('renders the preview for the selected state and moves the badge by dragging', async () => {
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalled())
    expect(mockFetchOverlayPreview.mock.calls[0][0]).toMatchObject({ type: 'status', variant: 'airing' })

    const handle = await screen.findByRole('slider', { name: /Badge position/ })
    fireEvent.load(screen.getByAltText(/Overlay preview/))
    fireEvent.pointerDown(handle, { clientX: 0, clientY: 0, pointerId: 1 })
    expect(document.activeElement).toBe(handle)  // a click focuses the handle so arrow keys work
    fireEvent.pointerMove(handle, { clientX: 50, clientY: 20, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 50, clientY: 20, pointerId: 1 })

    // 500px wide image → 0.5 px per reference px: dx 50 → 100, dy 20 → 40.
    // Centered badge (w 400) at x 300 moved to 400 → offset 100; top-aligned y 35 → 75.
    await waitFor(() => {
      const last = mockFetchOverlayPreview.mock.calls.at(-1)?.[0]
      expect(last.placement.horizontal_offset).toBe(100)
      expect(last.placement.vertical_offset).toBe(75)
    })

    // Shift+arrow nudges 10 reference px from the current offsets, and repeats accumulate
    // even though the (mocked, static) preview never reports a new badge rect.
    fireEvent.keyDown(screen.getByRole('slider', { name: /Badge position/ }), { key: 'ArrowRight', shiftKey: true })
    fireEvent.keyDown(screen.getByRole('slider', { name: /Badge position/ }), { key: 'ArrowRight', shiftKey: true })
    fireEvent.keyDown(screen.getByRole('slider', { name: /Badge position/ }), { key: 'ArrowUp' })
    await waitFor(() => {
      const last = mockFetchOverlayPreview.mock.calls.at(-1)?.[0]
      expect(last.placement.horizontal_offset).toBe(120)
      expect(last.placement.vertical_offset).toBe(74)
    })
  })

  it('snaps a nearby drag onto the center line / edge margin and re-anchors the alignment', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const handle = await screen.findByRole('slider', { name: /Badge position/ })
    fireEvent.load(screen.getByAltText(/Overlay preview/))
    // Badge (x 300, w 400) is already centered; 5px right and 5px up at 0.5 scale is
    // 10 / 10 reference px — inside the snap distance of the center line and top margin.
    fireEvent.pointerDown(handle, { clientX: 0, clientY: 0, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 5, clientY: -5, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientX: 5, clientY: -5, pointerId: 1 })
    await waitFor(() => {
      const last = mockFetchOverlayPreview.mock.calls.at(-1)?.[0]
      expect(last.placement).toMatchObject({ horizontal_align: 'center', horizontal_offset: 0, vertical_align: 'top', vertical_offset: 35 })
    })

    // The anchor grid pins the badge to an edge pair with the same margin.
    await user.click(screen.getByRole('button', { name: 'Bottom right' }))
    await waitFor(() => {
      const last = mockFetchOverlayPreview.mock.calls.at(-1)?.[0]
      expect(last.placement).toMatchObject({ horizontal_align: 'right', horizontal_offset: 35, vertical_align: 'bottom', vertical_offset: 35 })
    })
  })

  it('switches preview state and edits an option field', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    pickState('Ended')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].variant).toBe('ended'))

    // The shared cards, the type's sections as their own cards, and the statuses as one table.
    for (const name of ['Preview', 'Position', 'Text', 'Backdrop', 'Timing', 'Dates', 'Statuses']) {
      expect(screen.getByRole('heading', { level: 3, name })).toBeTruthy()
    }
    const endedText = screen.getByLabelText('Ended Text')
    await user.clear(endedText)
    await user.type(endedText, 'FINISHED')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.states.ended.text).toBe('FINISHED'))
    expect(screen.getByRole('row', { name: /Ended/ })).toBeTruthy()
    // A status that never carries a date has its dated text locked; a dated one keeps it editable.
    expect((screen.getByLabelText('Ended Text with date') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Airing Text with date') as HTMLInputElement).disabled).toBe(false)
    expect((screen.getByLabelText('Ended Backdrop') as HTMLInputElement).disabled).toBe(false)
    // Backdrop off locks the Backdrop tab's fields and every status's backdrop color.
    expect((screen.getByLabelText('Padding') as HTMLInputElement).disabled).toBe(false)
    await user.click(screen.getByLabelText('Backdrop on'))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.back_enabled).toBe(false))
    expect((screen.getByLabelText('Padding') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Ended Backdrop') as HTMLInputElement).disabled).toBe(true)
    await user.click(screen.getByLabelText('Ended on'))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.states.ended.enabled).toBe(false))
    // A state switched off leaves the preview list; the pick falls back to one that is on.
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].variant).toBe('airing'))
    expect(picker('Preview State').textContent).toBe('Airing')
    expect(stateLabels()).toEqual(['Airing'])
  })

  it('shows a second overlay type as its own tab with decimal tier thresholds', async () => {
    const user = userEvent.setup()
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType, ratingType] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await user.click(screen.getByRole('tab', { name: /Rating/ }))
    await screen.findByRole('heading', { level: 2, name: 'Rating' })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0]).toMatchObject({ type: 'rating', variant: 'top' }))
    expect(screen.getByLabelText('Source')).toBeTruthy()
    const from = screen.getByLabelText('Top From') as HTMLInputElement
    expect(from.step).toBe('0.1')
    fireEvent.change(from, { target: { value: '8.5' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.tiers.top.min).toBe(8.5))
    fireEvent.change(screen.getByLabelText('Hide below'), { target: { value: '6.5' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.hide_below).toBe(6.5))
  })

  it('draws the other enabled overlays under the one being edited', async () => {
    const user = userEvent.setup()
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType, ratingType] })
    const cfg = baseConfig()
    cfg.types.status.enabled = true
    cfg.types.rating.enabled = true
    mockGetOverlaysConfig.mockResolvedValue({ config: cfg, has_tmdb_key: true, library_configs: [], badged_count: 0 })
    mockFetchOverlayPreview.mockResolvedValue({ blob: new Blob(['x']), rect: { x: 300, y: 35, w: 400, h: 80 }, text: 'AIRING', layers: [{ type: 'rating', rect: { x: 665, y: 1365, w: 300, h: 100 } }] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    // Editing status: the rating badge is drawn underneath, in the rating tab's own placement.
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].others).toEqual([
      expect.objectContaining({ type: 'rating', variant: 'top', placement: expect.objectContaining({ horizontal_align: 'right' }) }),
    ]))
    // The rating badge has a handle of its own: a nudge on it changes the rating overlay, not the status one.
    const ratingHandle = await screen.findByRole('slider', { name: /Rating position/ })
    fireEvent.load(screen.getByAltText(/Overlay preview/))
    fireEvent.keyDown(ratingHandle, { key: 'ArrowLeft', shiftKey: true })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0]).toMatchObject({
      type: 'status', placement: expect.objectContaining({ horizontal_offset: 0 }),
      others: [expect.objectContaining({ type: 'rating', placement: expect.objectContaining({ horizontal_align: 'right', horizontal_offset: 10 }) })],  // right-aligned: left grows the offset
    }))
    // Switch tabs: now the status badge is the context layer.
    await user.click(screen.getByRole('tab', { name: /Rating/ }))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0]).toMatchObject({ type: 'rating', others: [expect.objectContaining({ type: 'status', variant: 'airing' })] }))
    // Turn status off: it leaves the rating preview.
    await user.click(screen.getByLabelText('Airing Status on'))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].others).toEqual([]))
  })

  it('cancels unsaved edits back to the saved settings', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const cancels = screen.getAllByRole('button', { name: /^Cancel$/ }) as HTMLButtonElement[]
    expect(cancels.every((b) => b.disabled)).toBe(true)  // nothing to cancel yet
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(1))  // the saved settings, rendered once
    fireEvent.change(screen.getByLabelText('Padding'), { target: { value: '99' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.back_padding).toBe(99))
    expect(cancels.every((b) => !b.disabled)).toBe(true)
    const rendered = mockFetchOverlayPreview.mock.calls.length
    await user.click(cancels[1])
    expect((screen.getByLabelText('Padding') as HTMLInputElement).value).toBe('22')
    // The saved settings were rendered when the page opened: that preview comes back from the cache, no new request.
    await new Promise((r) => setTimeout(r, 450))
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(rendered)
    expect(cancels.every((b) => b.disabled)).toBe(true)
    expect(mockSaveOverlaysConfig).not.toHaveBeenCalled()
  })

  it('puts one status\u2019s colors back to the type defaults', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const reset = screen.getByRole('button', { name: 'Ended default colors' }) as HTMLButtonElement
    expect(reset.disabled).toBe(true)  // already at defaults
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(1))  // the defaults, rendered once
    const ended = screen.getByLabelText('Ended Backdrop')
    await user.clear(ended)
    await user.type(ended, '#112233FF')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.states.ended.back_color).toBe('#112233FF'))
    expect(reset.disabled).toBe(false)
    const rendered = mockFetchOverlayPreview.mock.calls.length
    await user.click(reset)
    expect((screen.getByLabelText('Ended Backdrop') as HTMLInputElement).value).toBe('#424242E6')
    // Text and the on/off state are left alone.
    expect((screen.getByLabelText('Ended Text') as HTMLInputElement).value).toBe('ENDED')
    // Back at the defaults the page opened with: that preview comes back from the cache, no new request.
    await new Promise((r) => setTimeout(r, 450))
    expect(mockFetchOverlayPreview).toHaveBeenCalledTimes(rendered)
  })

  it('only lets a saved, enabled config run and queues the job', async () => {
    const user = userEvent.setup()
    mockGetOverlaysConfig.mockResolvedValue({ config: { ...baseConfig(), enabled: true, types: { status: { ...baseConfig().types.status, enabled: true } } }, has_tmdb_key: true, library_configs: [], badged_count: 3 })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const apply = screen.getByRole('button', { name: /Apply Changes/ })
    expect((apply as HTMLButtonElement).disabled).toBe(false)
    await user.click(apply)
    await waitFor(() => expect(mockRunOverlays).toHaveBeenCalledWith({ mode: 'incremental' }))
    expect(mockShowToast).toHaveBeenCalledWith('Overlays Run started (Job ID: 7)', 'success')
    // Rebuild All re-uploads every badge: its own mode, so the engine cannot skip identical ones.
    await waitFor(() => expect((screen.getByRole('button', { name: /Rebuild All/ }) as HTMLButtonElement).disabled).toBe(false))
    await user.click(screen.getByRole('button', { name: /Rebuild All/ }))
    await waitFor(() => expect(mockRunOverlays).toHaveBeenCalledWith({ mode: 'rebuild' }))
    expect(mockShowToast).toHaveBeenCalledWith('Rebuild All started (Job ID: 7)', 'success')
    // Remove Overlays is available while ON too, as an explicit restore run.
    await waitFor(() => expect((screen.getByRole('button', { name: 'Remove All (3)' }) as HTMLButtonElement).disabled).toBe(false))  // the count of badged items
    await user.click(screen.getByRole('button', { name: 'Remove All (3)' }))
    await waitFor(() => expect(mockRunOverlays).toHaveBeenCalledWith({ mode: 'restore' }))

    await user.click(screen.getByLabelText('Overlays are ON'))
    // Off + unsaved: the run button becomes "Remove Overlays" but waits for a save…
    const remove = screen.getByRole('button', { name: 'Remove All (3)' }) as HTMLButtonElement
    expect(remove.disabled).toBe(true)
    // …saving "off" changes nothing on the servers by itself…
    mockSaveOverlaysConfig.mockImplementationOnce(async (config: unknown) => ({ success: true, config, badged_count: 3 }))
    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[0])
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Overlay settings saved', 'success'))
    expect(mockRunOverlays).toHaveBeenCalledTimes(3)  // apply, rebuild, remove: saving queued nothing
    // …the button does, and once its job finishes the count refreshes from the server.
    mockShowToast.mockClear()
    mockGetOverlaysConfig.mockResolvedValue({ config: { ...baseConfig(), enabled: false, types: { status: { ...baseConfig().types.status, enabled: true } } }, has_tmdb_key: true, library_configs: [], badged_count: 0 })
    await user.click(screen.getByRole('button', { name: 'Remove All (3)' }))
    await waitFor(() => expect(mockRunOverlays).toHaveBeenLastCalledWith({ mode: 'restore' }))
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Remove Overlays finished: Overlays complete: 3 restored', 'success'))
    expect((screen.getByRole('button', { name: 'Remove All' }) as HTMLButtonElement).disabled).toBe(true)  // 0 badged now: no count
  })

  it('previews on the bordered poster by default and can switch to the plain one', async () => {
    localStorage.removeItem('posterflow.overlays.previewBase')
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalled())
    expect(mockFetchOverlayPreview.mock.calls[0][0].base).toBe('bordered')
    expect((screen.getByLabelText('Use my border settings') as HTMLInputElement).checked).toBe(true)
    await user.click(screen.getByLabelText('Use my border settings'))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].base).toBe('plain'))
    expect(localStorage.getItem('posterflow.overlays.previewBase')).toBe('plain')
  })

  it('keeps a saved plain-poster choice', async () => {
    localStorage.setItem('posterflow.overlays.previewBase', 'plain')
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await waitFor(() => expect(mockFetchOverlayPreview).toHaveBeenCalled())
    expect(mockFetchOverlayPreview.mock.calls[0][0].base).toBe('plain')
    expect((screen.getByLabelText('Use my border settings') as HTMLInputElement).checked).toBe(false)
    localStorage.removeItem('posterflow.overlays.previewBase')
  })

  it('switches the backdrop style between solid, gradient box and edge band', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    // Solid: box fields, no fade fields.
    expect(screen.getByLabelText('Padding')).toBeTruthy()
    expect(screen.queryByLabelText('Fade color')).toBeNull()
    // Gradient box: fade color + angle, and the box fields stay.
    pick('Style', 'Gradient box')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.back_style).toBe('gradient'))
    expect(screen.getByLabelText('Fade color')).toBeTruthy()
    expect(screen.getByLabelText('Padding')).toBeTruthy()
    expect(screen.queryByLabelText('Height')).toBeNull()
    fireEvent.change(screen.getByLabelText('Angle'), { target: { value: '180' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.gradient_angle).toBe(180))
    // From the centre out: the angle goes; spread takes its place.
    pick('Direction', 'From center')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.gradient_mode).toBe('center'))
    expect(screen.queryByLabelText('Angle')).toBeNull()
    fireEvent.change(screen.getByLabelText('Spread'), { target: { value: '40' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.gradient_spread).toBe(40))
    // Band: no box, so the box fields go and the top offset/height/side-inset fields appear.
    pick('Style', 'Edge band')
    expect(screen.getByLabelText('Height')).toBeTruthy()
    expect(screen.queryByLabelText('Padding')).toBeNull()
    // The band can fade in a straight line or ease out softly.
    expect(pickerOptions('Fade')).toEqual(['Linear', 'Soft'])
    pick('Fade', 'Soft')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.gradient_band_curve).toBe('soft'))
    fireEvent.change(screen.getByLabelText('Height'), { target: { value: '300' } })
    fireEvent.change(screen.getByLabelText('From left'), { target: { value: '150' } })
    fireEvent.change(screen.getByLabelText('From top'), { target: { value: '80' } })
    expect(screen.queryByLabelText('Edge')).toBeNull()  // the band always sits at the top
    await waitFor(() => {
      const last = mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement
      expect(last.gradient_band_height).toBe(300)
      expect(last.gradient_band_left).toBe(150)
      expect(last.gradient_band_offset).toBe(80)
    })
    // The one switch turns everything off.
    await user.click(screen.getByLabelText('Backdrop on'))
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].placement.back_enabled).toBe(false))
    expect((screen.getByLabelText('Height') as HTMLInputElement).disabled).toBe(true)
  })

  it('disables the weight control for a font without variable weights', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    expect(picker('Weight').disabled).toBe(false)
    expect(pickerOptions('Weight')).toEqual(['Font default', 'Regular', 'SemiBold', 'Bold'])
    pick('Font', /Liberation Sans/)
    expect(picker('Weight').disabled).toBe(true)
    expect(picker('Weight').textContent).toBe('Not a variable font')
  })

  it('lets libraries be narrowed and saves the selection', async () => {
    const user = userEvent.setup()
    mockGetOverlaysConfig.mockResolvedValue({
      config: baseConfig(), has_tmdb_key: true, badged_count: 0,
      library_configs: [{ instance_name: 'plex', libraries: [
        { key: '1', title: 'Movies', type: 'movie', enabled: true },
        { key: '2', title: 'TV', type: 'show', enabled: true },
      ] }],
    })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await user.click(screen.getByRole('button', { name: /Libraries: all \(2\)/ }))
    const grid = () => within(screen.getByRole('region', { name: 'Libraries' }))  // the page-wide picker, not an overlay's
    // "All" on → every library shows ticked.
    const all = screen.getByLabelText('All enabled movie and TV libraries') as HTMLInputElement
    expect(all.checked).toBe(true)
    expect((grid().getByLabelText(/^TV/) as HTMLInputElement).checked).toBe(true)
    expect((grid().getByLabelText(/^Movies/) as HTMLInputElement).checked).toBe(true)
    // Unticking one drops "All" and keeps the other.
    await user.click(grid().getByLabelText(/^TV/))
    expect(all.checked).toBe(false)
    expect((grid().getByLabelText(/^Movies/) as HTMLInputElement).checked).toBe(true)
    expect(screen.getByRole('button', { name: /Libraries: 1 of 2/ })).toBeTruthy()
    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[0])
    await waitFor(() => expect(mockSaveOverlaysConfig.mock.calls[0][0]).toMatchObject({ all_libraries: false, libraries: ['plex:1'] }))
    // Ticking the last one back turns "All" on again; unticking "All" clears everything.
    await user.click(grid().getByLabelText(/^TV/))
    expect(all.checked).toBe(true)
    await user.click(all)
    expect(screen.getByRole('button', { name: /Libraries: none/ })).toBeTruthy()
    expect((grid().getByLabelText(/^Movies/) as HTMLInputElement).checked).toBe(false)
  })

  it('runs the Plex cleanup tool, and the poster reset tool after a confirmation', async () => {
    const user = userEvent.setup()
    mockGetOverlaysConfig.mockResolvedValue({ config: baseConfig(), has_tmdb_key: true, library_configs: [], badged_count: 0 })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await user.click(screen.getByRole('button', { name: /Clean Up Plex/ }))
    await waitFor(() => expect(mockRunTool).toHaveBeenCalledWith('plex_cleanup', { settings: { kinds: ['poster'] } }))
    expect(mockShowToast).toHaveBeenCalledWith('Plex Cleanup started (Job ID: 11)', 'success')
    await user.click(screen.getByRole('button', { name: /Reset Posters/ }))
    expect(mockRunTool).toHaveBeenCalledTimes(1)  // needs the confirmation first
    await user.click(screen.getByRole('button', { name: 'Yes, reset them' }))
    await waitFor(() => expect(mockRunTool).toHaveBeenLastCalledWith('plex_reset_posters', { confirm: true, settings: { kinds: ['poster'] } }))
    expect(mockShowToast).toHaveBeenCalledWith('Reset Plex posters started (Job ID: 12)', 'success')
    expect(mockRunTool).not.toHaveBeenCalledWith('jellyfin_reset_posters', expect.anything())  // no Jellyfin server
  })

  it('offers badging items with no Posterflow poster as an opt-in that is saved with the settings', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const box = screen.getByLabelText('Badge items with no Posterflow poster') as HTMLInputElement
    expect(box.checked).toBe(false)  // off unless asked for
    await user.click(box)
    expect(box.checked).toBe(true)
    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[0])
    await waitFor(() => expect(mockSaveOverlaysConfig.mock.calls[0][0]).toMatchObject({ badge_server_posters: true }))
  })

  it('cleans up Plex uploads after badging unless switched off, and saves the choice', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const box = screen.getByLabelText('Clean up Plex uploads after badging') as HTMLInputElement
    expect(box.checked).toBe(true)
    await user.click(box)
    expect(box.checked).toBe(false)
    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[0])
    await waitFor(() => expect(mockSaveOverlaysConfig.mock.calls[0][0]).toMatchObject({ prune_plex_uploads: false }))
  })

  it('gives the season toggle its own Seasons card among the second-row settings', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const group = screen.getByRole('group', { name: 'Overlay name and libraries' })
    expect(within(group).queryByLabelText('Season posters too')).toBeNull()
    const card = screen.getByRole('heading', { level: 3, name: 'Seasons' }).closest('.overlay-panel') as HTMLElement
    expect(card.className).toContain('overlay-settings-panel')  // a second-row card
    const box = within(card).getByLabelText('Season posters too') as HTMLInputElement
    expect(screen.getAllByLabelText('Season posters too')).toHaveLength(1)  // nowhere else on the page
    expect(box.checked).toBe(false)
    await user.click(box)
    expect((within(card).getByLabelText('Season posters too') as HTMLInputElement).checked).toBe(true)
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.apply_to_seasons).toBe(true))
  })

  it('relabels a preview state that follows an option, and drops it when the value has no label', async () => {
    const user = userEvent.setup()
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType, ratingType] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await user.click(screen.getByRole('tab', { name: /Rating/ }))
    await screen.findByRole('heading', { level: 2, name: 'Rating' })
    expect(stateLabels()).toEqual(['Top (9.2)', 'Low (4.3)', 'Audience sample'])
    pick('Source', 'TMDB score')
    expect(await screen.findByLabelText('TMDB only')).toBeTruthy()  // the option change has landed
    expect(stateLabels()).toEqual(['Top (9.2)', 'Low (4.3)', 'TMDB sample'])
    // Pick it, then switch the option to a value with no label: the entry goes and the pick falls back.
    pickState('TMDB sample')
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].variant).toBe('sample'))
    fireEvent.change(screen.getByLabelText('Hide below'), { target: { value: '1' } })  // any edit keeps the page live
    pick('Source', 'Audience rating (Plex / Jellyfin)')
    await waitFor(() => expect(screen.queryByLabelText('TMDB only')).toBeNull())
    expect(stateLabels()).toEqual(['Top (9.2)', 'Low (4.3)', 'Audience sample'])
  })

  it('shows a field only while the option it depends on holds the right value', async () => {
    const user = userEvent.setup()
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType, ratingType] })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    await user.click(screen.getByRole('tab', { name: /Rating/ }))
    await screen.findByRole('heading', { level: 2, name: 'Rating' })
    expect(screen.queryByLabelText('TMDB only')).toBeNull()  // the source is not TMDB yet
    pick('Source', 'TMDB score')
    expect(await screen.findByLabelText('TMDB only')).toBeTruthy()
    pick('Source', 'Audience rating (Plex / Jellyfin)')
    await waitFor(() => expect(screen.queryByLabelText('TMDB only')).toBeNull())
  })

  it('offers an overlay only the libraries of the kind its type badges', async () => {
    const user = userEvent.setup()
    const libraryConfigs = [{ instance_name: 'srv', libraries: [
      { key: '1', title: 'Movies', type: 'movie', enabled: true },
      { key: '2', title: 'TV Shows', type: 'show', enabled: true },
    ] }]
    const editionType = { ...statusType, key: 'edition', label: 'Edition', targets: ['movie'] }
    const showsOnly = { ...ratingType, key: 'shows', label: 'Shows Only', targets: ['show', 'season'] }
    mockGetOverlayTypes.mockResolvedValue({ types: [statusType, editionType, showsOnly] })
    const cfg = baseConfig()
    cfg.types.edition = JSON.parse(JSON.stringify(cfg.types.status))
    cfg.types.shows = JSON.parse(JSON.stringify(cfg.types.rating))
    mockGetOverlaysConfig.mockResolvedValue({ config: cfg, has_tmdb_key: true, library_configs: libraryConfigs, badged_count: 0 })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const panel = () => screen.getByRole('group', { name: 'Overlay name and libraries' })
    // Airing Status badges movies, shows and seasons: both libraries.
    expect(within(panel()).getByLabelText(/Movies/)).toBeTruthy()
    expect(within(panel()).getByLabelText(/TV Shows/)).toBeTruthy()
    // A movies-only type lists the movie library alone; a shows-only one the TV library alone.
    await user.click(screen.getByRole('tab', { name: /Edition/ }))
    expect(within(panel()).getByLabelText(/Movies/)).toBeTruthy()
    expect(within(panel()).queryByLabelText(/TV Shows/)).toBeNull()
    await user.click(screen.getByRole('tab', { name: /Shows Only/ }))
    expect(within(panel()).queryByLabelText(/Movies/)).toBeNull()
    expect(within(panel()).getByLabelText(/TV Shows/)).toBeTruthy()
  })

  it('adds named overlays per type, each for all libraries or a picked set', async () => {
    const user = userEvent.setup()
    const libraryConfigs = [{ instance_name: 'srv', libraries: [
      { key: '1', title: 'Movies', type: 'movie', enabled: true },
      { key: '2', title: 'TV Shows', type: 'show', enabled: true },
    ] }]
    mockGetOverlaysConfig.mockResolvedValue({ config: baseConfig(), has_tmdb_key: true, library_configs: libraryConfigs, badged_count: 0 })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const panel = () => screen.getByRole('group', { name: 'Overlay name and libraries' })
    const overlayTabs = () => within(screen.getByRole('tablist', { name: 'Airing Status overlays' }))
    expect(overlayTabs().getByRole('tab', { name: 'Airing Status', selected: true })).toBeTruthy()  // the config's one overlay
    expect((within(panel()).getByLabelText('All libraries') as HTMLInputElement).checked).toBe(true)
    // The libraries stay in view while All is on: shown as covered, not pickable.
    expect((within(panel()).getByLabelText(/Movies/) as HTMLInputElement).disabled).toBe(true)
    expect((within(panel()).getByLabelText(/Movies/) as HTMLInputElement).checked).toBe(true)

    await user.click(screen.getByRole('button', { name: /Add Overlay/ }))
    expect(overlayTabs().getByRole('tab', { name: 'Airing Status 2', selected: true })).toBeTruthy()
    await user.clear(within(panel()).getByLabelText('Name'))
    await user.type(within(panel()).getByLabelText('Name'), 'TV only')
    expect(overlayTabs().getByRole('tab', { name: 'TV only' })).toBeTruthy()
    await user.click(within(panel()).getByLabelText('All libraries'))
    await user.click(within(panel()).getByLabelText(/TV Shows/))
    // Edits on this tab change this overlay only.
    fireEvent.change(screen.getByLabelText('New for (days)'), { target: { value: '7' } })
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].options.new_days).toBe(7))
    await user.click(overlayTabs().getByRole('tab', { name: 'Airing Status' }))
    expect((screen.getByLabelText('New for (days)') as HTMLInputElement).value).toBe('30')
    // A library one overlay names is not offered to another.
    await user.click(screen.getByRole('button', { name: /Add Overlay/ }))
    await user.click(within(panel()).getByLabelText('All libraries'))
    expect((within(panel()).getByLabelText(/TV Shows/) as HTMLInputElement).disabled).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Remove Overlay' }))
    expect(overlayTabs().queryByRole('tab', { name: 'Airing Status 3' })).toBeNull()
    // Each overlay has its own switch; off keeps it (and its library claims) but applies nothing.
    await user.click(screen.getByLabelText('TV only on'))
    expect(screen.getByLabelText('TV only off')).toBeTruthy()
    await waitFor(() => expect(mockFetchOverlayPreview.mock.calls.at(-1)?.[0].enabled).toBe(false))  // the preview drops it
    expect(await screen.findByText(/Switched off/)).toBeTruthy()

    await user.click(screen.getAllByRole('button', { name: /^Save$/ })[0])
    await waitFor(() => expect(mockSaveOverlaysConfig).toHaveBeenCalledTimes(1))
    const saved = mockSaveOverlaysConfig.mock.calls[0][0]
    expect(saved.types.status.overlays).toHaveLength(2)
    expect(saved.types.status.overlays[1]).toMatchObject({ name: 'TV only', enabled: false, all_libraries: false, libraries: ['srv:2'] })
    expect(saved.types.status.overlays[0].enabled).toBe(true)
    expect(saved.types.status.overlays[1].options.new_days).toBe(7)
    expect(saved.types.status.overlays[0].options.new_days).toBe(30)
  })

  it('shows an empty state for a type without overlays', async () => {
    const user = userEvent.setup()
    const cfg = baseConfig()
    cfg.types.status.overlays = []
    mockGetOverlaysConfig.mockResolvedValue({ config: cfg, has_tmdb_key: true, library_configs: [], badged_count: 0 })
    renderPage()
    await screen.findByRole('heading', { level: 2, name: 'Airing Status' })
    const overlayTabs = () => within(screen.getByRole('tablist', { name: 'Airing Status overlays' }))
    expect(screen.getByText(/No Airing Status overlays yet/)).toBeTruthy()
    expect(screen.queryByRole('slider', { name: /Badge position/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Add Overlay/ }))
    expect(overlayTabs().getByRole('tab', { name: 'Airing Status', selected: true })).toBeTruthy()
    expect(await screen.findByRole('slider', { name: /Badge position/ })).toBeTruthy()
  })

  it('warns when a TMDB-backed overlay is enabled without a key', async () => {
    mockGetOverlaysConfig.mockResolvedValue({ config: { ...baseConfig(), enabled: true, types: { status: { ...baseConfig().types.status, enabled: true } } }, has_tmdb_key: false, library_configs: [], badged_count: 0 })
    renderPage()
    expect(await screen.findByText(/Add a TMDB API key/)).toBeTruthy()
  })
})
