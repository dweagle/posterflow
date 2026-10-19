import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import RequestItemCard from '../../src/components/community/RequestItemCard'
import { EMPTY_PSD_CONFIG, type TmdbItemCardProps } from '../../src/components/maker-tools/TmdbItemCard'
import { getTmdbOverview } from '../../src/api/client'

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/api/client')>()),
  getTmdbOverview: vi.fn(),
}))
// The maker panel is its own component; here it only needs to echo the gallery default it was given.
vi.mock('../../src/components/maker-tools/TmdbItemCard', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../src/components/maker-tools/TmdbItemCard')>()),
  default: (props: TmdbItemCardProps) => (
    <div data-testid="maker-panel" data-media-type={props.item.media_type} data-gallery-language={props.defaultGalleryLanguage ?? ''} data-highlight-language={props.highlightLanguage ?? ''} />
  ),
}))

const mockedOverview = vi.mocked(getTmdbOverview)

const mount = (overrides: Partial<React.ComponentProps<typeof RequestItemCard>> = {}) =>
  render(
    <RequestItemCard
      id="req-1"
      posterPath={null}
      title="Squid Game"
      year={2021}
      mediaType="show"
      styleLabel="CL2K"
      status="pending"
      notes="Includes: original-language poster/logo"
      createdAt="2026-10-18T00:00:00Z"
      imdbId={null}
      tvdbId={null}
      tmdbId={93405}
      actions={null}
      isMaker
      showMakerTools
      psdConfig={EMPTY_PSD_CONFIG}
      dragOver={false}
      {...overrides}
    />,
  )

describe('RequestItemCard original language', () => {
  beforeEach(() => {
    mockedOverview.mockReset()
    mockedOverview.mockResolvedValue({ overview: '', poster_url: null, original_language: 'ko' })
  })
  afterEach(() => { cleanup() })

  it('names the title language next to the notes and opens the gallery on every language', async () => {
    mount({ originalLanguage: true })

    expect(await screen.findByText('Original language: Korean')).toBeTruthy()
    expect(mockedOverview).toHaveBeenCalledWith(93405, 'tv')
    expect(screen.getByTestId('maker-panel').getAttribute('data-gallery-language')).toBe('all')
    expect(screen.getByTestId('maker-panel').getAttribute('data-highlight-language')).toBe('ko')
  })

  it('leaves the card alone when the request did not ask for the original language', () => {
    mount({ originalLanguage: false, notes: null })

    expect(screen.queryByText(/Original language/)).toBeNull()
    expect(mockedOverview).not.toHaveBeenCalled()
    expect(screen.getByTestId('maker-panel').getAttribute('data-gallery-language')).toBe('')
    expect(screen.getByTestId('maker-panel').getAttribute('data-highlight-language')).toBe('')
  })

  it('skips the lookup for a request with no TMDB id', () => {
    mount({ originalLanguage: true, tmdbId: null })

    expect(screen.queryByText(/Original language/)).toBeNull()
    expect(mockedOverview).not.toHaveBeenCalled()
  })

  it('hands the maker panel a person in its own TMDB namespace, not the show with that id', () => {
    mount({ mediaType: 'person', title: 'Tom Cruise', year: null, tmdbId: 500, originalLanguage: false, notes: null })

    expect(screen.getByTestId('maker-panel').getAttribute('data-media-type')).toBe('person')
    expect(mockedOverview).not.toHaveBeenCalled()
    expect((screen.getByRole('link', { name: /tmdb/i }) as HTMLAnchorElement).href).toBe('https://www.themoviedb.org/person/500')
  })

  it('shows nothing when TMDB has no language for the title', async () => {
    mockedOverview.mockResolvedValue({ overview: '', poster_url: null, original_language: null })
    mount({ originalLanguage: true, tmdbId: 93406 })

    await vi.waitFor(() => expect(mockedOverview).toHaveBeenCalledWith(93406, 'tv'))
    expect(screen.queryByText(/Original language/)).toBeNull()
  })
})
