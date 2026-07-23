import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import UnmatchedTab from '../../src/components/poster-manager/UnmatchedTab'
import type { PlexLibraryConfig, UnmatchedStats } from '../../src/api/client'

/**
 * Behavioural coverage for the poster Unmatched view. Assertions target what the user
 * sees/does (cards, counts, buttons, handlers) rather than internals, so they hold if
 * the poster + artwork views are later unified behind one component.
 */

const libraryConfigs: PlexLibraryConfig[] = [
  {
    instance_name: 'Main',
    libraries: [{ key: 'k_movies', title: 'Movies', type: 'movie', enabled: true }],
  } as unknown as PlexLibraryConfig,
]

const statsWithResults = (): UnmatchedStats => ({
  summary: {
    movies: { total: 10, unmatched: 2, percent_complete: 80 },
    series: { total: 5, unmatched: 1, percent_complete: 80 },
    seasons: { total: 8, unmatched: 3, percent_complete: 62.5 },
    collections: { total: 4, unmatched: 1, percent_complete: 75 },
    grand_total: { total: 19, unmatched: 4, percent_complete: 78.9 },
    by_library: {},
  },
  unmatched: {
    movies: [
      { title: 'Inception', year: 2010, instance: 'Radarr' },
      { title: 'Dune', year: 2021, instance: 'Radarr' },
    ],
    series: [
      { title: 'Breaking Bad', year: 2008, missing_seasons: [1, 2], missing_main_poster: true, instance: 'Sonarr' },
    ],
    collections: [{ title: 'Marvel Collection', year: 0, instance: 'Plex' }],
  },
  last_run: '2026-07-15T00:00:00Z',
})

function makeProps(overrides: Record<string, unknown> = {}) {
  return {
    unmatchedStats: statsWithResults(),
    hasUnsavedLibraryChanges: false,
    hasUnsavedUnmatchedSettings: false,
    saving: false,
    detectingUnmatched: false,
    libraryConfigs,
    selectedLibraries: new Set<string>(['Main:k_movies']),
    cardPreviewLimit: 5,
    formatPercent: (p: number) => p.toFixed(1),
    scope: 'posters' as const,
    onScopeChange: vi.fn(),
    settingsActive: false,
    onShowSettings: vi.fn(),
    onSaveSettings: vi.fn(),
    onDetectUnmatched: vi.fn(),
    onDownloadReport: vi.fn(),
    onToggleLibrarySelection: vi.fn(),
    unmatchedIgnoreRootFoldersText: '',
    unmatchedIgnoreCollectionsText: '',
    unmatchedIgnoreUnmonitored: false,
    onSetUnmatchedIgnoreRootFoldersText: vi.fn(),
    onSetUnmatchedIgnoreCollectionsText: vi.fn(),
    onSetUnmatchedIgnoreUnmonitored: vi.fn(),
    onOpenModal: vi.fn(),
    ...overrides,
  }
}

function renderTab(overrides: Record<string, unknown> = {}) {
  const props = makeProps(overrides)
  render(
    <MemoryRouter>
      {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
      <UnmatchedTab {...(props as any)} />
    </MemoryRouter>,
  )
  return props
}

describe('UnmatchedTab (posters)', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders the unified header with all four asset-type switches', () => {
    renderTab()

    expect(screen.getByText('Unmatched Assets')).toBeTruthy()
    for (const label of ['Posters', 'Logos', 'Backdrops', 'Square Art']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy()
    }
  })

  it('switching asset type reports the new scope', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    await user.click(screen.getByRole('button', { name: 'Logos' }))
    expect(props.onScopeChange).toHaveBeenCalledWith('logo')

    await user.click(screen.getByRole('button', { name: 'Backdrops' }))
    expect(props.onScopeChange).toHaveBeenCalledWith('background')

    await user.click(screen.getByRole('button', { name: 'Square Art' }))
    expect(props.onScopeChange).toHaveBeenCalledWith('squareart')
  })

  it('renders a card per media type with its missing count', () => {
    renderTab()

    // Scope to the card headings — "Movies" also appears as a library name.
    expect(screen.getByText('Movies', { selector: 'h3' })).toBeTruthy()
    expect(screen.getByText('Series (Main Posters)', { selector: 'h3' })).toBeTruthy()
    expect(screen.getByText('Season Posters', { selector: 'h3' })).toBeTruthy()
    expect(screen.getByText('Collections', { selector: 'h3' })).toBeTruthy()

    // Movies: 2 missing, seasons: 3 missing.
    expect(screen.getAllByText('2 missing').length).toBeGreaterThan(0)
    expect(screen.getAllByText('3 missing').length).toBeGreaterThan(0)
  })

  it('lists the missing items in the card previews', () => {
    renderTab()

    expect(screen.getByText('Inception')).toBeTruthy()
    expect(screen.getByText('Dune')).toBeTruthy()
    expect(screen.getAllByText('Breaking Bad').length).toBeGreaterThan(0)
    expect(screen.getByText('Marvel Collection')).toBeTruthy()
  })

  it('opens the right modal from a card "View All" button', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    await user.click(screen.getByRole('button', { name: /View All 2 Missing/i }))
    expect(props.onOpenModal).toHaveBeenCalledWith('movies')
  })

  it('shows the empty state and no cards before a detection run', () => {
    renderTab({ unmatchedStats: { ...statsWithResults(), last_run: null } })

    expect(screen.getByText('No Unmatched Detection Results')).toBeTruthy()
    expect(screen.queryByText('Season Posters')).toBeNull()
  })

  it('detect button triggers the unified unmatched detection', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    await user.click(screen.getByRole('button', { name: /Detect Unmatched/i }))
    expect(props.onDetectUnmatched).toHaveBeenCalled()
  })

  it('detect button says "Detect Unmatched" on every scope (one pass covers all)', () => {
    renderTab({ scope: 'logo', typeNoun: 'Logos' })

    expect(screen.getByRole('button', { name: /Detect Unmatched/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Detect Artwork/i })).toBeNull()
  })

  it('save is disabled with no pending changes and active once dirty', async () => {
    const user = userEvent.setup()

    renderTab()
    expect((screen.getByRole('button', { name: /Save Settings/i }) as HTMLButtonElement).disabled).toBe(true)

    cleanup()
    const props = renderTab({ hasUnsavedLibraryChanges: true })
    const save = screen.getByRole('button', { name: /Save Settings/i }) as HTMLButtonElement
    expect(save.disabled).toBe(false)

    await user.click(save)
    expect(props.onSaveSettings).toHaveBeenCalled()
  })

  it('offers view/search and download only once there are results', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    await user.click(screen.getByRole('button', { name: /View \/ Search/i }))
    expect(props.onOpenModal).toHaveBeenCalledWith('all')

    await user.click(screen.getByRole('button', { name: /Download Report/i }))
    expect(props.onDownloadReport).toHaveBeenCalled()
  })

  it('hides view/search and download before a detection run', () => {
    renderTab({ unmatchedStats: { ...statsWithResults(), last_run: null } })

    expect(screen.queryByRole('button', { name: /View \/ Search/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Download Report/i })).toBeNull()
  })

  // The same component renders the artwork types. Artwork has no season dimension —
  // the backend zeroes its seasons stats, so that card must drop out on its own.
  it('renders artwork mode without a seasons card and with the artwork noun', () => {
    const artworkStats = statsWithResults()
    artworkStats.summary.seasons = { total: 0, unmatched: 0, percent_complete: 100 }
    renderTab({ unmatchedStats: artworkStats, typeNoun: 'Logos', scope: 'logo' })

    expect(screen.queryByText('Season Posters')).toBeNull()
    // Series card reads plainly (no "Main Logos"), and labels use the artwork noun.
    expect(screen.getByText('Series', { selector: 'h3' })).toBeTruthy()
    expect(screen.queryByText('Series (Main Logos)')).toBeNull()
    expect(screen.getAllByText('With Logos:').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Missing Logos:').length).toBeGreaterThan(0)
  })

  // Detection Settings is its own destination at the end of the type row, since those
  // settings are shared across every asset type rather than belonging to one.
  it('detection settings is a separate view, not shown alongside the stats', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    // Stats view: cards visible, settings hidden.
    expect(screen.getByText('Movies', { selector: 'h3' })).toBeTruthy()
    expect(screen.queryByText('Detection Settings', { selector: 'h2' })).toBeNull()
    expect(document.querySelector('.library-checkbox')).toBeNull()

    await user.click(screen.getByRole('button', { name: /Detection Settings/i }))
    expect(props.onShowSettings).toHaveBeenCalled()
  })

  it('settings view shows the shared settings and hides the stats', () => {
    renderTab({ settingsActive: true })

    expect(screen.getByText('Detection Settings', { selector: 'h2' })).toBeTruthy()
    expect(screen.getByText('Library Selection', { selector: 'h2' })).toBeTruthy()
    expect(screen.queryByText('Movies', { selector: 'h3' })).toBeNull()
  })

  it('toggling a library reports the selection', async () => {
    const user = userEvent.setup()
    const props = renderTab({ settingsActive: true })

    // The library grid renders one checkbox per enabled library.
    const libraryCheckbox = document.querySelector('.library-checkbox input[type="checkbox"]') as HTMLInputElement
    expect(libraryCheckbox).toBeTruthy()
    expect(libraryCheckbox.checked).toBe(true) // seeded as selected

    await user.click(libraryCheckbox)
    expect(props.onToggleLibrarySelection).toHaveBeenCalledWith('Main', 'k_movies')
  })
})
