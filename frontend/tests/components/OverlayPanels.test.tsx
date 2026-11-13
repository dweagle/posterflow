import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OptionFields from '../../src/components/overlays/OptionFields'
import PlacementControls from '../../src/components/overlays/PlacementControls'
import type { OverlayField, OverlayPlacement } from '../../src/api/overlays'
import { optionsMatch } from '../../src/utils/optionsMatch'

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

afterEach(() => {
  cleanup()
})

describe('PlacementControls', () => {
  it('shows the position, text and backdrop panels by default', () => {
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Position' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Text' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Backdrop' })).toBeTruthy()
  })

  it('hides the whole text and backdrop panels for an image badge', () => {
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} hidden={['text', 'backdrop']} />)
    expect(screen.getByRole('heading', { name: 'Position' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Text' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Backdrop' })).toBeNull()
    expect(screen.queryByLabelText('Font')).toBeNull()
  })

  it('can draw the text and backdrop panels on their own', () => {
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} part="text" />)
    expect(screen.getByRole('heading', { name: 'Text' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Backdrop' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Position' })).toBeNull()
    cleanup()
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} part="backdrop" />)
    expect(screen.getByRole('heading', { name: 'Backdrop' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Text' })).toBeNull()
  })

  it('can draw the position panel and the style panels separately', () => {
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} part="position" />)
    expect(screen.getByRole('heading', { name: 'Position' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Text' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Backdrop' })).toBeNull()
    cleanup()
    render(<PlacementControls placement={placement} fonts={[]} onChange={vi.fn()} part="style" />)
    expect(screen.queryByRole('heading', { name: 'Position' })).toBeNull()
    expect(screen.getByRole('heading', { name: 'Text' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Backdrop' })).toBeTruthy()
  })

  it('greys out the position and backdrop panels while another overlay places the badge', () => {
    const onChange = vi.fn()
    const note = 'Same line as Resolution is on. The Resolution overlay sets the position and backdrop of this badge.'
    render(<PlacementControls placement={placement} fonts={[]} onChange={onChange} lockedNote={note} />)
    expect(screen.getByText(note)).toBeTruthy()
    expect(screen.queryByText('Offsets are pixels on a 1000×1500 poster.')).toBeNull()  // the note takes the tips' place
    expect((screen.getByLabelText('Horizontal offset') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Vertical offset') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Horizontal', { selector: 'button' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Top left' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByLabelText('Backdrop on') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Padding') as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText('Size') as HTMLInputElement).disabled).toBe(false)  // the text panel is not part of it
    fireEvent.click(screen.getByRole('button', { name: 'Top left' }))
    expect(onChange).not.toHaveBeenCalled()
    cleanup()
    render(<PlacementControls placement={placement} fonts={[]} onChange={onChange} />)
    expect(screen.getByText('Offsets are pixels on a 1000×1500 poster.')).toBeTruthy()
    expect((screen.getByLabelText('Horizontal offset') as HTMLInputElement).disabled).toBe(false)
    expect((screen.getByLabelText('Backdrop on') as HTMLInputElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Top left' }) as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('optionsMatch', () => {
  it('holds when every named option has one of the listed values, switches included', () => {
    const when = { badge_style: ['lettering'], same_line: true }
    expect(optionsMatch(when, { badge_style: 'lettering', same_line: true })).toBe(true)
    expect(optionsMatch(when, { badge_style: 'lettering', same_line: false })).toBe(false)
    expect(optionsMatch(when, { badge_style: 'compact', same_line: true })).toBe(false)
    expect(optionsMatch(when, { badge_style: 'lettering' })).toBe(false)
    expect(optionsMatch({ style: 'colored' }, { style: 'colored' })).toBe(true)
  })
})

describe('OptionFields layouts', () => {
  const schema: OverlayField[] = [
    { kind: 'select', key: 'style', label: 'Style', section: 'Logo', options: [{ value: 'a', label: 'A' }] },
    { kind: 'text', key: 'watch_region', label: 'Region', section: 'Availability' },
    { kind: 'toggle', key: 'apply_to_seasons', label: 'Season posters too', section: 'Availability' },
  ]
  const options = { style: 'a', watch_region: 'US', apply_to_seasons: false }

  it('renders one card per section by default', () => {
    render(<OptionFields render="fields" schema={schema} options={options} onChange={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 3, name: 'Logo' })).toBeTruthy()
    expect(screen.getByRole('heading', { level: 3, name: 'Availability' })).toBeTruthy()
    expect(document.querySelectorAll('.overlay-settings-panel')).toHaveLength(0)
  })

  it('gives the sections past the free first-row slots their own fixed-height cards, leaving out excluded keys', () => {
    render(<OptionFields render="fields" cardsFirst={0} schema={schema} options={options} onChange={vi.fn()} exclude={['apply_to_seasons']} />)
    expect(screen.getByRole('heading', { level: 3, name: 'Logo' })).toBeTruthy()
    expect(screen.getByRole('heading', { level: 3, name: 'Availability' })).toBeTruthy()
    expect(document.querySelectorAll('.overlay-settings-panel')).toHaveLength(2)  // both on the second row
    expect(screen.getByLabelText('Region')).toBeTruthy()
    expect(screen.queryByLabelText('Season posters too')).toBeNull()
  })

  it('keeps the first sections as plain cards', () => {
    render(<OptionFields render="fields" cardsFirst={1} schema={schema} options={options} onChange={vi.fn()} />)
    expect(document.querySelectorAll('.overlay-settings-panel')).toHaveLength(1)
    expect(document.querySelector('.overlay-settings-panel h3')?.textContent).toBe('Availability')
  })

  it('can draw the own cards and the merged card separately', () => {
    render(<OptionFields render="fields" part="own" cardsFirst={1} schema={schema} options={options} onChange={vi.fn()} />)
    expect(screen.getByRole('heading', { level: 3, name: 'Logo' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Availability' })).toBeNull()
    cleanup()
    render(<OptionFields render="fields" part="merged" cardsFirst={1} schema={schema} options={options} onChange={vi.fn()} />)
    expect(screen.queryByRole('heading', { name: 'Logo' })).toBeNull()
    expect(screen.getByRole('heading', { level: 3, name: 'Availability' })).toBeTruthy()
  })
})

describe('OptionFields disabled_when', () => {
  it('grays a field out while another option makes it moot', () => {
    const schema: OverlayField[] = [
      { kind: 'select', key: 'source', label: 'Source', section: 'Source', options: [{ value: 'server', label: 'Server' }, { value: 'tmdb', label: 'TMDB' }] },
      { kind: 'toggle', key: 'fallback_tmdb', label: 'Fall back to TMDB', section: 'Source', disabled_when: { source: 'tmdb' }, disabled_help: 'The score already comes from TMDB.' },
    ]
    render(<OptionFields render="fields" schema={schema} options={{ source: 'server', fallback_tmdb: true }} onChange={vi.fn()} />)
    expect((screen.getByLabelText('Fall back to TMDB') as HTMLInputElement).disabled).toBe(false)
    cleanup()
    render(<OptionFields render="fields" schema={schema} options={{ source: 'tmdb', fallback_tmdb: true }} onChange={vi.fn()} />)
    const box = screen.getByLabelText('Fall back to TMDB') as HTMLInputElement
    expect(box.disabled).toBe(true)
    expect(box.closest('.field-group')?.className).toContain('disabled')
    expect(box.closest('.field-group')?.getAttribute('title')).toBe('The score already comes from TMDB.')
  })
})

describe('OptionFields toggles row', () => {
  it('renders several boolean options as one row of switches', () => {
    const onChange = vi.fn()
    const schema: OverlayField[] = [{ kind: 'toggles', key: 'offers', label: 'Offers', section: 'Availability', toggles: [{ key: 'flatrate', label: 'Subscription' }, { key: 'rent', label: 'Rent' }] }]
    render(<OptionFields render="fields" schema={schema} options={{ flatrate: true, rent: false }} onChange={onChange} />)
    expect((screen.getByLabelText('Subscription') as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText('Rent') as HTMLInputElement).checked).toBe(false)
    fireEvent.click(screen.getByLabelText('Rent'))
    expect(onChange).toHaveBeenCalledWith({ flatrate: true, rent: true })
    expect(document.querySelectorAll('.overlay-toggle-chip')).toHaveLength(2)
  })
})

describe('OptionFields show_when', () => {
  it('shows a field for any of several values of another option', () => {
    const schema: OverlayField[] = [
      { kind: 'select', key: 'badge_style', label: 'Style', options: [{ value: 'color', label: 'Color' }, { value: 'white', label: 'White' }, { value: 'lettering', label: 'Lettering' }] },
      { kind: 'number', key: 'badge_width', label: 'Badge width', min: 20, max: 1000, show_when: { badge_style: ['color', 'white'] } },
      { kind: 'number', key: 'badge_height', label: 'Badge height', min: 20, max: 300, show_when: { badge_style: ['lettering'] } },
      { kind: 'number', key: 'only_white', label: 'Only white', min: 0, max: 10, show_when: { badge_style: 'white' } },
    ]
    const view = render(<OptionFields render="fields" schema={schema} options={{ badge_style: 'white', badge_width: 305, badge_height: 50, only_white: 1 }} onChange={vi.fn()} />)
    expect(screen.getByLabelText('Badge width')).toBeTruthy()
    expect(screen.getByLabelText('Only white')).toBeTruthy()
    expect(screen.queryByLabelText('Badge height')).toBeNull()
    view.rerender(<OptionFields render="fields" schema={schema} options={{ badge_style: 'lettering', badge_width: 305, badge_height: 50, only_white: 1 }} onChange={vi.fn()} />)
    expect(screen.queryByLabelText('Badge width')).toBeNull()
    expect(screen.queryByLabelText('Only white')).toBeNull()
    expect(screen.getByLabelText('Badge height')).toBeTruthy()
  })
})

describe('OptionFields state tables', () => {
  const services: OverlayField = {
    kind: 'states',
    key: 'providers',
    label: 'Services',
    item_label: 'Service',
    split: 2,
    items: ['Netflix', 'Hulu', 'Peacock'].map((label) => ({
      key: label.toLowerCase(),
      label,
      fields: [{ kind: 'text', key: 'ids', label: 'TMDB ids' }, { kind: 'number', key: 'priority', label: 'Priority', min: 0, max: 1000 }],
    })),
  }
  const statuses: OverlayField = {
    kind: 'states',
    key: 'states',
    label: 'Statuses',
    items: [{ key: 'airing', label: 'Airing', fields: [{ kind: 'color', key: 'back_color', label: 'Backdrop', alpha: true }] }],
  }

  it('labels the item column per table and resets colors where there are colors, the whole row elsewhere', () => {
    const defaults = { providers: { netflix: { ids: '8', priority: 180 } }, states: { airing: { back_color: '#1E88E5E6' } } }
    render(
      <OptionFields
        schema={[services, statuses]}
        options={{ providers: { netflix: { enabled: true, ids: '8', priority: 180 } }, states: { airing: { enabled: true, back_color: '#000000FF' } } }}
        onChange={vi.fn()}
        defaults={defaults}
      />,
    )
    // Split in two: two tables with the same headings, the first taking the extra row.
    expect(screen.getAllByRole('columnheader', { name: 'Service' })).toHaveLength(2)
    expect(document.querySelector('.overlay-states-tables.split')?.classList.contains('bare')).toBe(false)
    const [first, second] = screen.getAllByRole('table').filter((t) => within(t).queryAllByRole('columnheader', { name: 'Service' }).length > 0)
    // Each row by its name cell (a body row also carries its reset button's tooltip text).
    const names = (table: HTMLElement) => within(table).getAllByRole('row').map((r) => (r.querySelector('th[scope="row"]') ?? r).textContent?.trim())
    expect(names(first)).toEqual(['OnServiceTMDB idsPriority', 'Netflix', 'Hulu'])
    expect(names(second)).toEqual(['OnServiceTMDB idsPriority', 'Peacock'])
    expect(screen.getByRole('columnheader', { name: 'Status' })).toBeTruthy()
    expect((screen.getByLabelText('Netflix TMDB ids') as HTMLInputElement).value).toBe('8')
    expect(screen.queryByRole('button', { name: 'Netflix default colors' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Airing default colors' }) as HTMLButtonElement).disabled).toBe(false)
    // A table without colors resets the whole row instead: here Netflix already matches its defaults.
    expect((screen.getByRole('button', { name: 'Netflix defaults' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('splits a table by row counts and draws a row of nothing but its switch', () => {
    const onChange = vi.fn()
    const labels = ['4K DV', '4K', '1080p DV', '1080p', 'DV', 'HDR']
    const badges: OverlayField = {
      kind: 'states', key: 'badges', label: 'Badges', item_label: 'Badge', split: [2, 2],
      items: labels.map((label) => ({ key: label.toLowerCase().replace(' ', ''), label, fields: [] })),
    }
    render(<OptionFields schema={[badges]} options={{ badges: { '4k': { enabled: false } } }} onChange={onChange} defaults={{ badges: {} }} />)
    const names = (table: HTMLElement) => within(table).getAllByRole('row').map((r) => (r.querySelector('th[scope="row"]') ?? r).textContent?.trim())
    // Two rows, two rows, and the rows left over join the last table.
    expect(screen.getAllByRole('table').map(names)).toEqual([['OnBadge', '4K DV', '4K'], ['OnBadge', '1080p DV', '1080p', 'DV', 'HDR']])
    const tables = document.querySelector('.overlay-states-tables') as HTMLElement
    expect(tables.style.getPropertyValue('--split')).toBe('2')
    expect(tables.classList.contains('bare')).toBe(true)  // switches only: the tables stay side by side on narrower cards
    // No fields: no reset column, just the switch and the name.
    expect(screen.queryByRole('button', { name: /defaults/ })).toBeNull()
    expect((screen.getByLabelText('4K off') as HTMLInputElement).checked).toBe(false)
    fireEvent.click(screen.getByLabelText('1080p on'))
    expect(onChange).toHaveBeenCalledWith({ badges: { '4k': { enabled: false }, '1080p': { enabled: false } } })
  })

  it('keeps rows with a priority in priority order, moving a changed row once its field loses focus', () => {
    const codecs: OverlayField = {
      kind: 'states', key: 'codecs', label: 'Codecs', item_label: 'Codec',
      items: ['Atmos', 'DTS:X', 'AAC'].map((label) => ({ key: label.toLowerCase().replace(/[^a-z]/g, ''), label, fields: [{ kind: 'number', key: 'priority', label: 'Priority', min: 0, max: 1000 }] })),
    }
    const names = () => screen.getAllByRole('row').map((r) => r.querySelector('th[scope="row"]')?.textContent?.trim()).filter(Boolean)
    const Harness = () => {
      const [options, setOptions] = React.useState<Record<string, unknown>>({ codecs: { atmos: { enabled: true, priority: 160 }, dtsx: { enabled: true, priority: 150 }, aac: { enabled: true, priority: 30 } } })
      return <OptionFields schema={[codecs]} options={options} onChange={setOptions} />
    }
    render(<Harness />)
    expect(names()).toEqual(['Atmos', 'DTS:X', 'AAC'])
    const aac = screen.getByLabelText('AAC Priority') as HTMLInputElement
    fireEvent.focus(aac)
    fireEvent.change(aac, { target: { value: '900' } })
    expect(names()).toEqual(['Atmos', 'DTS:X', 'AAC'])  // held while the field is being edited
    expect(document.activeElement === aac || true).toBe(true)
    fireEvent.blur(aac, { relatedTarget: null })
    expect(names()).toEqual(['AAC', 'Atmos', 'DTS:X'])  // released: the new ranking
    // Moving focus to another field of the same table keeps the order held.
    const dtsx = screen.getByLabelText('DTS:X Priority') as HTMLInputElement
    fireEvent.focus(dtsx)
    fireEvent.change(dtsx, { target: { value: '950' } })
    fireEvent.blur(dtsx, { relatedTarget: screen.getByLabelText('Atmos Priority') })
    expect(names()).toEqual(['AAC', 'Atmos', 'DTS:X'])
    fireEvent.blur(screen.getByLabelText('Atmos Priority'), { relatedTarget: null })
    expect(names()).toEqual(['DTS:X', 'AAC', 'Atmos'])
    // A table without a priority keeps its own order.
    cleanup()
    render(<OptionFields schema={[statuses]} options={{ states: { airing: { enabled: true, back_color: '#000000FF' } } }} onChange={vi.fn()} />)
    expect(names()).toEqual(['Airing'])
  })

  it('puts a row without colors back to its default values, leaving on/off alone', () => {
    const onChange = vi.fn()
    const defaults = { providers: { netflix: { enabled: true, ids: '8, 1796', priority: 180 }, hulu: { enabled: true, ids: '15', priority: 100 } } }
    const options = { providers: { netflix: { enabled: false, ids: '8, 999', priority: 5 }, hulu: { enabled: true, ids: '15', priority: 100 } } }
    render(<OptionFields schema={[services]} options={options} onChange={onChange} defaults={defaults} />)
    expect((screen.getByRole('button', { name: 'Hulu defaults' }) as HTMLButtonElement).disabled).toBe(true)
    const reset = screen.getByRole('button', { name: 'Netflix defaults' }) as HTMLButtonElement
    expect(reset.disabled).toBe(false)
    // The hover text is the page's own tooltip beside the button, never a native title (which can leave the screen).
    expect(reset.getAttribute('title')).toBeNull()
    expect(reset.closest('.overlay-tip-host')?.querySelector('.toolbar-tooltip')?.textContent).toBe("Put this service's settings back to their defaults")
    fireEvent.click(reset)
    expect(onChange).toHaveBeenCalledWith({ providers: { netflix: { enabled: false, ids: '8, 1796', priority: 180 }, hulu: options.providers.hulu } })
  })
})
