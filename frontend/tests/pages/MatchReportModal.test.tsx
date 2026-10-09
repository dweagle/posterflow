import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MatchReportModal from '../../src/components/poster-manager/MatchReportModal'
import type { MatchReportJobStatus, MatchReportResponse } from '../../src/api/client'

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
    drives: { scanned: [{ name: 'DriveA', style_type: 'CL2K', last_synced: '2026-08-01T10:00:00+00:00', missing: false }], total_assets: 100, error: null },
    candidates: {
      considered: 4, shown: 1, omitted: 0,
      items: [{ title: 'RIPLEY', year: 2024, tmdb_id: null, tvdb_id: 111, imdb_id: null, drive: 'DriveA',
                files: ['RIPLEY (2024) {tvdb-111}.jpg'], season_numbers: [], found_by: 'title', matched: false,
                reason: 'id conflict', newest_file: null }],
    },
    placed: {
      layout: 'folders', name: 'RIPLEY {tvdb-372727}', files: ['poster.jpg', 'Season01.jpg'], year: null,
      type: 'collections', tmdb_id: null, tvdb_id: null, imdb_id: null, season_numbers: [], has_main: true,
      matched: false, reason: '', id_conflicts: [], siblings: [],
    },
  },
  report_text: 'Posterflow match report — test',
  filename: 'posterflow-match-report_ripley_2026-08-05.txt',
}

const jobStatus = (overrides: Partial<MatchReportJobStatus>): MatchReportJobStatus => ({
  job_id: 7, status: 'running', progress: 40, message: null, error: null, result: null, ...overrides,
})

vi.mock('../../src/api/client', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  startUnmatchedMatchReport: vi.fn(),
  getUnmatchedMatchReport: vi.fn(),
  downloadMatchReport: vi.fn(),
}))
vi.mock('../../src/api/jobs', () => ({ cancelJob: vi.fn().mockResolvedValue({}) }))

const item = { media_type: 'series' as const, title: 'RIPLEY', year: 2024, tvdb_id: 372727 }

async function mocks() {
  const client = await import('../../src/api/client')
  const jobs = await import('../../src/api/jobs')
  return {
    start: vi.mocked(client.startUnmatchedMatchReport),
    get: vi.mocked(client.getUnmatchedMatchReport),
    download: vi.mocked(client.downloadMatchReport),
    cancel: vi.mocked(jobs.cancelJob),
  }
}

describe('MatchReportModal', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('queues the job, shows its progress, then renders verdict, evidence and the placed folder', async () => {
    const { start, get } = await mocks()
    start.mockResolvedValue({ job_id: 7, message: 'Match report queued in background.', status: 'pending' })
    get
      .mockResolvedValueOnce(jobStatus({ message: 'Scanning drive 3/44: Drazzilb' }))
      .mockResolvedValueOnce(jobStatus({ status: 'completed', progress: 100, result: reportResponse }))

    render(<MatchReportModal item={item} onClose={vi.fn()} pollIntervalMs={5} />)
    expect(screen.getByText(/Queuing the report/)).toBeTruthy()

    await waitFor(() => expect(screen.getByText(/Scanning drive 3\/44: Drazzilb/)).toBeTruthy())
    await waitFor(() => expect(screen.getByText(/DIFFERENT id/)).toBeTruthy())
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ media_type: 'series', title: 'RIPLEY', tvdb_id: 372727 }))
    expect(get).toHaveBeenCalledWith(7)
    // Candidate line names the conflicting poster tag (ids line + filename line).
    expect(screen.getAllByText(/\{tvdb-111\}/).length).toBeGreaterThan(0)
    expect(screen.getByText(/skipped: no TMDB API key configured/)).toBeTruthy()
    for (const label of ['ids', 'folder', 'state', 'seasons', 'files', 'matcher']) {
      expect(screen.getAllByText(label, { selector: 'td' }).length).toBeGreaterThan(0)
    }
    // The placed folder is shown with its yearless warning.
    expect(screen.getByText(/RIPLEY \{tvdb-372727\}/)).toBeTruthy()
    expect(screen.getByText(/no \(year\) in name/)).toBeTruthy()
  })

  it('downloads the report file on click', async () => {
    const { start, get, download } = await mocks()
    start.mockResolvedValue({ job_id: 7, message: '', status: 'pending' })
    get.mockResolvedValue(jobStatus({ status: 'completed', progress: 100, result: reportResponse }))
    const user = userEvent.setup()

    render(<MatchReportModal item={item} onClose={vi.fn()} pollIntervalMs={5} />)
    await waitFor(() => expect(screen.getByText(/DIFFERENT id/)).toBeTruthy())

    await user.click(screen.getByRole('button', { name: /Download report/ }))
    expect(download).toHaveBeenCalledWith(reportResponse)
  })

  it('names the reverse proxy when the start call dies with a 504', async () => {
    const { start } = await mocks()
    start.mockRejectedValue({ response: { status: 504 } })

    render(<MatchReportModal item={item} onClose={vi.fn()} pollIntervalMs={5} />)
    await waitFor(() => expect(screen.getByText(/Failed to start the match report/)).toBeTruthy())
    expect(screen.getByText(/reverse proxy \(HTTP 504\)/)).toBeTruthy()
  })

  it('shows the job error when the build fails', async () => {
    const { start, get } = await mocks()
    start.mockResolvedValue({ job_id: 7, message: '', status: 'pending' })
    get.mockResolvedValue(jobStatus({ status: 'failed', error: 'TVDB exploded' }))

    render(<MatchReportModal item={item} onClose={vi.fn()} pollIntervalMs={5} />)
    await waitFor(() => expect(screen.getByText(/Failed to build the match report: TVDB exploded/)).toBeTruthy())
  })

  it('cancels a still-running job when the modal closes', async () => {
    const { start, get, cancel } = await mocks()
    start.mockResolvedValue({ job_id: 7, message: '', status: 'pending' })
    get.mockResolvedValue(jobStatus({ message: 'Scanning drive 1/44: Dweagle79' }))

    const view = render(<MatchReportModal item={item} onClose={vi.fn()} pollIntervalMs={5} />)
    await waitFor(() => expect(screen.getByText(/Scanning drive 1\/44/)).toBeTruthy())
    view.unmount()
    await waitFor(() => expect(cancel).toHaveBeenCalledWith(7))
  })
})
