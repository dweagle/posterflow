import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import TmdbItemCard, { EMPTY_PSD_CONFIG } from '../../src/components/maker-tools/TmdbItemCard'
import { getTmdbOverview, getTvDetails } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getTmdbOverview: vi.fn(),
  getTvDetails: vi.fn(),
  getSettings: vi.fn(),
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
