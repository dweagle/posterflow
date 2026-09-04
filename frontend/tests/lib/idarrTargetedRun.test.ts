import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getMakerIdarrRunResult } from '../../src/api/client'
import {
  IDARR_QUICK_ADD_NOTICE_EVENT,
  describeOutcomeReason,
  describeOutcomeStatus,
  notifyIdarrTargetedRun,
  waitForIdarrTargetedRun,
} from '../../src/utils/idarrTargetedRun'

vi.mock('../../src/api/client', () => ({ getMakerIdarrRunResult: vi.fn() }))

const mockedGet = vi.mocked(getMakerIdarrRunResult)

const outcome = (status: string, extra: Record<string, unknown> = {}) => ({
  source_filename: 'dropped.jpg',
  final_filename: 'dropped.jpg',
  relative_path: 'dropped.jpg',
  title: '',
  year: null,
  status,
  reason: '',
  uploaded: status === 'uploaded',
  ...extra,
}) as never

const result = (overrides: Record<string, unknown> = {}) => ({
  job_id: 7,
  status: 'completed',
  finished: true,
  outcomes: [],
  error: '',
  ...overrides,
}) as never

beforeEach(() => {
  vi.useFakeTimers()
  mockedGet.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('waitForIdarrTargetedRun', () => {
  it('stays quiet when every file landed on the drive', async () => {
    mockedGet.mockResolvedValue(result({ outcomes: [outcome('uploaded')] }))
    await expect(waitForIdarrTargetedRun(7, true, 0)).resolves.toBeNull()
  })

  it('stays quiet for ready files when auto-upload is off — nothing went wrong', async () => {
    mockedGet.mockResolvedValue(result({ outcomes: [outcome('ready')] }))
    await expect(waitForIdarrTargetedRun(7, false, 0)).resolves.toBeNull()
  })

  it('stays quiet for a re-dropped ignored title that went up unchanged', async () => {
    mockedGet.mockResolvedValue(result({ outcomes: [outcome('ignored', { reason: 'ignored_title', uploaded: true })] }))
    await expect(waitForIdarrTargetedRun(7, true, 0)).resolves.toBeNull()
  })

  it('counts an ignored file that was uploaded as-is among the uploads', async () => {
    mockedGet.mockResolvedValue(result({
      outcomes: [outcome('ignored', { uploaded: true }), outcome('pending', { source_filename: 'mystery.jpg' })],
    }))

    const notice = await waitForIdarrTargetedRun(7, true, 0)
    expect(notice?.uploadedCount).toBe(1)
    expect(notice?.problems).toHaveLength(1)
  })

  it('reports pending files and counts the ones that did upload', async () => {
    mockedGet.mockResolvedValue(result({
      outcomes: [outcome('uploaded'), outcome('pending', { source_filename: 'mystery.jpg', reason: 'no_match' })],
    }))

    const notice = await waitForIdarrTargetedRun(7, true, 0)

    expect(notice?.problems.map((row) => row.source_filename)).toEqual(['mystery.jpg'])
    expect(notice?.uploadedCount).toBe(1)
    expect(notice?.autoUpload).toBe(true)
    expect(notice?.syncTargetIndex).toBe(0)
  })

  it('reports upload failures and files that never got scanned', async () => {
    mockedGet.mockResolvedValue(result({
      outcomes: [outcome('upload_failed', { reason: 'quota exceeded' }), outcome('missing')],
    }))

    const notice = await waitForIdarrTargetedRun(7, true, 0)
    expect(notice?.problems.map((row) => row.status)).toEqual(['upload_failed', 'missing'])
  })

  it('reports a failed run even with no per-file outcomes', async () => {
    mockedGet.mockResolvedValue(result({ status: 'failed', error: 'TMDB unavailable' }))

    const notice = await waitForIdarrTargetedRun(7, true, 0)
    expect(notice?.error).toBe('TMDB unavailable')
  })

  it('keeps polling while the job is still queued or running', async () => {
    mockedGet
      .mockResolvedValueOnce(result({ status: 'pending', finished: false }))
      .mockResolvedValueOnce(result({ status: 'running', finished: false }))
      .mockResolvedValueOnce(result({ outcomes: [outcome('pending')] }))

    const promise = waitForIdarrTargetedRun(7, true, 0)
    await vi.advanceTimersByTimeAsync(10_000)

    expect((await promise)?.problems).toHaveLength(1)
    expect(mockedGet).toHaveBeenCalledTimes(3)
  })

  it('gives up quietly when the endpoint errors', async () => {
    mockedGet.mockRejectedValue(new Error('network'))
    await expect(waitForIdarrTargetedRun(7, true, 0)).resolves.toBeNull()
  })
})

describe('notifyIdarrTargetedRun', () => {
  it('raises the popup event only when something needs the maker', async () => {
    const events: CustomEvent[] = []
    const listener = (event: Event) => events.push(event as CustomEvent)
    window.addEventListener(IDARR_QUICK_ADD_NOTICE_EVENT, listener)

    mockedGet.mockResolvedValue(result({ outcomes: [outcome('uploaded')] }))
    await notifyIdarrTargetedRun(7, true, 0)
    expect(events).toHaveLength(0)

    mockedGet.mockResolvedValue(result({ outcomes: [outcome('pending')] }))
    await notifyIdarrTargetedRun(7, true, 0)
    expect(events).toHaveLength(1)
    expect(events[0].detail.problems[0].status).toBe('pending')

    window.removeEventListener(IDARR_QUICK_ADD_NOTICE_EVENT, listener)
  })
})

describe('describeOutcomeReason', () => {
  it('turns reason codes into something a maker can act on', () => {
    expect(describeOutcomeReason(outcome('pending', { reason: 'no_match' }))).toBe('No TMDB match found')
    expect(describeOutcomeReason(outcome('conflict', { reason: 'rename_conflict' })))
      .toBe('Another file already uses the corrected name')
  })

  it('passes an rclone error through verbatim', () => {
    expect(describeOutcomeReason(outcome('upload_failed', { reason: 'quota exceeded' }))).toBe('quota exceeded')
  })

  it('names an ignored title', () => {
    expect(describeOutcomeReason(outcome('ignored', { reason: 'ignored_title' }))).toBe('Title is on the IDarr ignore list')
    expect(describeOutcomeStatus('ignored')).toBe('Ignored')
  })

  it('falls back for an unknown code', () => {
    expect(describeOutcomeReason(outcome('pending', { reason: '' }))).toBe('Needs a manual match')
  })
})
