import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MatchReportModal from '../../src/components/poster-manager/MatchReportModal'
import type { MatchReportResponse } from '../../src/api/client'

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const reportResponse: MatchReportResponse = {
  report: {
    generated_at: '2026-08-05 12:00 UTC',
    app_version: '0.0.test',
    item: { media_type: 'series', title: 'RIPLEY', year: 2024, tmdb_id: null, tvdb_id: 372727, imdb_id: null, missing_seasons: [] },
    verdicts: [
      { level: 'problem', code: 'poster_id_conflict', message: 'A poster with the same title and year carries a DIFFERENT id.' },
    ],
    library: {
      found: true,
      records: [{ instance: 'Sonarr', title: 'RIPLEY', year: 2024, folder: 'RIPLEY (2024)', folder_has_year: true,
                  tmdb_id: null, tvdb_id: 372727, imdb_id: null, monitored: true, status: 'ended', available: true,
                  alternate_titles: [], seasons_with_episodes: [1] }],
      effective_ids: { tmdb_id: null, tvdb_id: 372727, imdb_id: null },
      ids_source: 'Sonarr',
      manual_entry: false,
      on_ignore_list: false,
    },
    reference: {
      tmdb: { skipped: 'no TMDB API key configured' },
      tvdb: { tvdb_id: 372727, tmdb_id: null, imdb_id: 'tt11016042', title: 'RIPLEY', year: 2024 },
      plex: { tvdb_id: 372727, tmdb_id: null, imdb_id: null, title: 'RIPLEY', year: 2024, library: 'TV Shows', instance: 'Plex' },
    },
    drives: { scanned: [{ name: 'DriveA', style_type: 'CL2K', last_synced: '2026-08-01 10:00', missing: false }], total_assets: 100, error: null },
    candidates: {
      considered: 4, shown: 1, omitted: 0,
      items: [{ title: 'RIPLEY', year: 2024, tmdb_id: null, tvdb_id: 111, imdb_id: null, drive: 'DriveA',
                files: ['RIPLEY (2024) {tvdb-111}.jpg'], season_numbers: [], found_by: 'title', matched: false,
                reason: 'id conflict', newest_file: null }],
    },
  },
  report_text: 'Posterflow match report — test',
  filename: 'posterflow-match-report_ripley_2026-08-05.txt',
}

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  fetchUnmatchedMatchReport: vi.fn(),
  downloadMatchReport: vi.fn(),
}))

const item = { media_type: 'series' as const, title: 'RIPLEY', year: 2024, tvdb_id: 372727 }

describe('MatchReportModal', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('fetches the report on mount and renders verdict + candidate evidence', async () => {
    const { fetchUnmatchedMatchReport } = await import('../../src/api/client')
    vi.mocked(fetchUnmatchedMatchReport).mockResolvedValue(reportResponse)

    render(<MatchReportModal item={item} onClose={vi.fn()} />)
    expect(screen.getByText(/Gathering info/)).toBeTruthy()

    await waitFor(() => expect(screen.getByText(/DIFFERENT id/)).toBeTruthy())
    expect(fetchUnmatchedMatchReport).toHaveBeenCalledWith(expect.objectContaining({
      media_type: 'series', title: 'RIPLEY', tvdb_id: 372727,
    }))
    // Candidate line names the conflicting poster tag (ids line + filename line).
    expect(screen.getAllByText(/\{tvdb-111\}/).length).toBeGreaterThan(0)
    expect(screen.getByText(/skipped: no TMDB API key configured/)).toBeTruthy()
    // Library record rows carry their labels.
    for (const label of ['ids', 'folder', 'state', 'seasons']) {
      expect(screen.getByText(label, { selector: 'td' })).toBeTruthy()
    }
  })

  it('downloads the report file on click', async () => {
    const { fetchUnmatchedMatchReport, downloadMatchReport } = await import('../../src/api/client')
    vi.mocked(fetchUnmatchedMatchReport).mockResolvedValue(reportResponse)
    const user = userEvent.setup()

    render(<MatchReportModal item={item} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/DIFFERENT id/)).toBeTruthy())

    await user.click(screen.getByRole('button', { name: /Download report/ }))
    expect(downloadMatchReport).toHaveBeenCalledWith(reportResponse)
  })

  it('shows the failure state when the backend errors', async () => {
    const { fetchUnmatchedMatchReport } = await import('../../src/api/client')
    vi.mocked(fetchUnmatchedMatchReport).mockRejectedValue(new Error('boom'))

    render(<MatchReportModal item={item} onClose={vi.fn()} />)
    await waitFor(() => expect(screen.getByText(/Failed to build the match report/)).toBeTruthy())
  })
})
