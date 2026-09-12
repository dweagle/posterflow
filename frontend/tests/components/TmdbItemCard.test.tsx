import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import TmdbItemCard, { EMPTY_PSD_CONFIG } from '../../src/components/maker-tools/TmdbItemCard'
import { getAppleImages, getTmdbImages, getTmdbOverview, getTvDetails, getTvdbImages } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getTmdbOverview: vi.fn(),
  getTvDetails: vi.fn(),
  getSettings: vi.fn(),
  getTmdbImages: vi.fn(),
  getTvdbImages: vi.fn(),
  getFanartImages: vi.fn(),
  getAppleImages: vi.fn(),
}))
vi.mock('../../src/components/Toast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('../../src/components/PosterDriveSearchModal', () => ({ default: () => null }))
vi.mock('../../src/components/maker-tools/SquareCropModal', () => ({ default: () => null }))
vi.mock('../../src/components/maker-tools/ServiceLinks', () => ({ default: () => null }))

const mockedOverview = vi.mocked(getTmdbOverview)
const mockedTvDetails = vi.mocked(getTvDetails)

const item = (overrides: Record<string, unknown> = {}) => ({
  tmdb_id: 153312,
  media_type: 'tv' as const,
  title: 'Tulsa King',
  year: '2022',
  overview: '',
  poster_url: '',
  homepage: '',
  imdb_id: null,
  tvdb_id: null,
  ...overrides,
})

const mount = (overrides: Record<string, unknown> = {}, props: { hideOverview?: boolean } = {}) =>
  render(<TmdbItemCard item={item(overrides)} psdConfig={EMPTY_PSD_CONFIG} hideTitle {...props} />)

describe('TmdbItemCard description with hideTitle', () => {
  beforeEach(() => {
    mockedOverview.mockReset()
    mockedTvDetails.mockReset()
    mockedTvDetails.mockResolvedValue({ season_count: 0, seasons: [], series_type: null, season_source: 'tmdb', tmdb_seasons: [], tvdb_seasons: [] })
  })
  afterEach(() => { cleanup() })

  it('shows a lazily fetched description when the item carried none', async () => {
    mockedOverview.mockResolvedValue({ overview: 'A fetched synopsis.', poster_url: null })
    const { container } = mount()

    expect(await screen.findByText('A fetched synopsis.')).toBeTruthy()
    expect(container.querySelector('.tmdb-result-title')).toBeNull()
    expect(mockedOverview).toHaveBeenCalledWith(153312, 'tv')
  })

  it('shows the item description directly without fetching', () => {
    mount({ overview: 'Carried synopsis.' })

    expect(screen.getByText('Carried synopsis.')).toBeTruthy()
    expect(mockedOverview).not.toHaveBeenCalled()
  })

  it('omits the info column entirely when nothing can describe the item', () => {
    const { container } = mount({ tmdb_id: 0 })

    expect(container.querySelector('.tmdb-result-info')).toBeNull()
    expect(mockedOverview).not.toHaveBeenCalled()
  })

  it('hideOverview keeps the column out and skips the lookup even with an id and text', () => {
    const { container } = mount({ overview: 'Carried synopsis.' }, { hideOverview: true })

    expect(container.querySelector('.tmdb-result-info')).toBeNull()
    expect(screen.queryByText('Carried synopsis.')).toBeNull()
    expect(mockedOverview).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Image gallery availability — keyed by whichever ids the item carries
// ---------------------------------------------------------------------------

const mockedTmdbImages = vi.mocked(getTmdbImages)
const mockedTvdbImages = vi.mocked(getTvdbImages)
const mockedAppleImages = vi.mocked(getAppleImages)
const noImages = { posters: [], backdrops: [], logos: [] }
const tvdbPoster = {
  posters: [{ file_path: '/tvdb/p1.jpg', width: 680, height: 1000, language: 'en', vote_average: 0, url_thumb: 'https://x/t.jpg', url_full: 'https://x/f.jpg' }],
  backdrops: [],
  logos: [],
}
const withSources = { ...EMPTY_PSD_CONFIG, tvdbEnabled: true }

describe('TmdbItemCard gallery sources', () => {
  beforeEach(() => {
    mockedOverview.mockReset()
    mockedTvDetails.mockReset()
    mockedTmdbImages.mockReset()
    mockedTvdbImages.mockReset()
    mockedAppleImages.mockReset()
    mockedTvDetails.mockResolvedValue({ season_count: 0, seasons: [], series_type: null, season_source: 'tvdb', tmdb_seasons: [], tvdb_seasons: [] })
  })
  afterEach(() => { cleanup() })

  it('offers the gallery for a TheTVDB-only show when TheTVDB is configured', async () => {
    mockedTvdbImages.mockResolvedValue(tvdbPoster)
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 0, tvdb_id: 78435, title: 'Popeye the Sailor', year: '1933' })} psdConfig={withSources} hideTitle hideOverview />,
    )

    expect(container.querySelector('.tmdb-psd-export-group--standalone')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Browse images/ }))

    await waitFor(() => expect(container.querySelector('.tmdb-gallery-panel')).not.toBeNull())
    expect(mockedTvdbImages).toHaveBeenCalledTimes(1)
    expect(mockedTmdbImages).not.toHaveBeenCalled()
    // Seasons are looked up by the TheTVDB id alone.
    expect(mockedTvDetails).toHaveBeenCalledWith(0, 78435)
    expect(container.querySelector('.tmdb-gallery-sources')).toBeNull()   // one source → no tab row
  })

  it('shows the standalone export group when no source can be asked', () => {
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 0, tvdb_id: 78435 })} psdConfig={EMPTY_PSD_CONFIG} hideTitle hideOverview />,
    )

    expect(screen.queryByRole('button', { name: /Browse images/ })).toBeNull()
    expect(container.querySelector('.tmdb-psd-export-group--standalone')).not.toBeNull()
    expect(mockedTvDetails).not.toHaveBeenCalled()
  })

  it('falls through to TheTVDB when the TMDB id is not a TV entry', async () => {
    mockedTmdbImages.mockRejectedValue(new Error('TMDB returned 404'))
    mockedTvdbImages.mockResolvedValue(tvdbPoster)
    // Fictional ids: the card caches tv-details per id pair across mounts, and the P90X2 test
    // below needs its own details response.
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 111111, tvdb_id: 222222, title: 'Dead TMDB id', year: '2011' })} psdConfig={withSources} hideTitle hideOverview />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Browse images/ }))

    await waitFor(() => expect(container.querySelector('.tmdb-gallery-panel')).not.toBeNull())
    expect(mockedTmdbImages).toHaveBeenCalledTimes(1)
    expect(mockedTvdbImages).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.tmdb-gallery-source--tvdb.active')).not.toBeNull()
    expect(container.querySelector('.tmdb-gallery-thumb')).not.toBeNull()
  })

  it('skips TMDB entirely once the details say TMDB has no entry for the show', async () => {
    // P90X2: TheTVDB's remote id is a TMDB movie, so /tv/<id> is a 404 — the details call reports it.
    mockedTvDetails.mockResolvedValue({ season_count: 3, seasons: [], series_type: null, season_source: 'tvdb', tmdb_found: false, tmdb_seasons: [], tvdb_seasons: [] })
    mockedTmdbImages.mockRejectedValue(new Error('TMDB returned 404'))
    mockedTvdbImages.mockResolvedValue(tvdbPoster)
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 498801, tvdb_id: 394871, title: 'P90X2', year: '2011' })} psdConfig={{ ...withSources, appleEnabled: true }} hideTitle hideOverview />,
    )
    await waitFor(() => expect(mockedTvDetails).toHaveBeenCalledWith(498801, 394871))

    fireEvent.click(screen.getByRole('button', { name: /Browse images/ }))

    await waitFor(() => expect(container.querySelector('.tmdb-gallery-panel')).not.toBeNull())
    expect(mockedTmdbImages).not.toHaveBeenCalled()
    expect(mockedTvdbImages).toHaveBeenCalledTimes(1)
    const tabs = [...container.querySelectorAll('.tmdb-gallery-source')].map((el) => el.getAttribute('aria-label'))
    expect(tabs).toEqual(['Browse TheTVDB images', 'Browse Apple TV images'])

    // The Apple tab is asked without the dead id, so its storefront hints don't chase it either.
    mockedAppleImages.mockResolvedValue(noImages)
    fireEvent.click(screen.getByRole('button', { name: 'Browse Apple TV images' }))
    await waitFor(() => expect(mockedAppleImages).toHaveBeenCalledTimes(1))
    expect(mockedAppleImages.mock.calls[0][0]).toMatchObject({ tmdb_id: 0, title: 'P90X2' })
  })

  it('opens empty with the export buttons when every source fails', async () => {
    mockedTmdbImages.mockRejectedValue(new Error('TMDB returned 404'))
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 498801 })} psdConfig={EMPTY_PSD_CONFIG} hideTitle hideOverview />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Browse images/ }))

    await waitFor(() => expect(container.querySelector('.tmdb-gallery-panel')).not.toBeNull())
    expect(screen.getByText('No images could be loaded for this title.')).toBeTruthy()
    expect(screen.getByRole('button', { name: /New Export/ })).toBeTruthy()
  })

  it('only lists sources that can be asked', async () => {
    mockedTvdbImages.mockResolvedValue(noImages)
    const { container } = render(
      <TmdbItemCard item={item({ tmdb_id: 0, tvdb_id: 83294 })} psdConfig={{ ...withSources, appleEnabled: true }} hideTitle hideOverview />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Browse images/ }))

    await waitFor(() => expect(container.querySelector('.tmdb-gallery-panel')).not.toBeNull())
    const tabs = [...container.querySelectorAll('.tmdb-gallery-source')].map((el) => el.getAttribute('aria-label'))
    expect(tabs).toEqual(['Browse TheTVDB images', 'Browse Apple TV images'])
  })
})
