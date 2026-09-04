import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import IdarrQuickAddNoticeHost from '../../src/components/IdarrQuickAddNotice'
import { ToastProvider } from '../../src/components/Toast'
import { IDARR_QUICK_ADD_NOTICE_EVENT } from '../../src/utils/idarrTargetedRun'
import { ignoreAndUploadMakerIdarrPending } from '../../src/api/client'

vi.mock('../../src/api/client', () => ({
  ignoreAndUploadMakerIdarrPending: vi.fn(),
  getApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const mockedIgnore = vi.mocked(ignoreAndUploadMakerIdarrPending)
const KEY = 'pending::mystery::::scope=t0'
const UPLOAD_LABEL = 'Upload as-is & ignore'

const pendingRow = (extra: Record<string, unknown> = {}) => ({
  source_filename: 'mystery.jpg',
  final_filename: 'mystery.jpg',
  relative_path: 'mystery.jpg',
  title: 'Mystery',
  year: null,
  asset_key: KEY,
  status: 'pending',
  reason: 'no_match',
  uploaded: false,
  ...extra,
})

const raiseNotice = (overrides: Record<string, unknown> = {}) => {
  act(() => {
    window.dispatchEvent(new CustomEvent(IDARR_QUICK_ADD_NOTICE_EVENT, {
      detail: { jobId: 7, problems: [pendingRow()], uploadedCount: 0, autoUpload: true, syncTargetIndex: 2, error: '', ...overrides },
    }))
  })
}

const mount = () => render(<ToastProvider><MemoryRouter><IdarrQuickAddNoticeHost /></MemoryRouter></ToastProvider>)

beforeEach(() => { mockedIgnore.mockReset() })
afterEach(cleanup)

describe('IdarrQuickAddNoticeHost', () => {
  it('uploads as-is, ignores the only pending drop and closes the popup', async () => {
    mockedIgnore.mockResolvedValue({ success: true, asset_key: KEY, upload_job_id: 42 })
    mount()
    raiseNotice()

    fireEvent.click(screen.getByRole('button', { name: UPLOAD_LABEL }))

    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
    expect(mockedIgnore).toHaveBeenCalledWith({ asset_key: KEY, relative_path: 'mystery.jpg', sync_target_index: 2, upload: true })
    expect(screen.getByText('Added to the ignore list · uploading to the drive unchanged (job 42)')).toBeTruthy()
  })

  it('keeps the popup open with the row marked when other files still need attention', async () => {
    mockedIgnore.mockResolvedValue({ success: true, asset_key: KEY, upload_job_id: 42 })
    mount()
    raiseNotice({ problems: [pendingRow(), pendingRow({ source_filename: 'other.jpg', asset_key: 'pending::other::::scope=t0' })] })

    fireEvent.click(screen.getAllByRole('button', { name: UPLOAD_LABEL })[0])

    expect(await screen.findByText(/Added to the ignore list/)).toBeTruthy()
    expect(screen.getByRole('alertdialog')).toBeTruthy()
    expect(screen.getByText('Ignored')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: UPLOAD_LABEL })).toHaveLength(1)
  })

  it('only ignores when quick-add auto-upload is off', async () => {
    mockedIgnore.mockResolvedValue({ success: true, asset_key: KEY, upload_job_id: null })
    mount()
    raiseNotice({ autoUpload: false })

    fireEvent.click(screen.getByRole('button', { name: 'Ignore' }))

    await waitFor(() => expect(mockedIgnore).toHaveBeenCalled())
    expect(mockedIgnore.mock.calls[0][0].upload).toBe(false)
    expect(await screen.findByText('Added to the ignore list · goes up unchanged with the next drive sync')).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull())
  })

  it('shows the error and keeps the button when the request fails', async () => {
    mockedIgnore.mockRejectedValue(new Error('boom'))
    mount()
    raiseNotice()

    fireEvent.click(screen.getByRole('button', { name: UPLOAD_LABEL }))

    expect(await screen.findByText('Failed to ignore this file')).toBeTruthy()
    expect(screen.getByRole('button', { name: UPLOAD_LABEL })).toBeTruthy()
  })

  it('offers nothing for conflicts or rows without a pending key', () => {
    mount()
    raiseNotice({
      problems: [
        pendingRow({ status: 'conflict', reason: 'rename_conflict' }),
        pendingRow({ source_filename: 'ghost.jpg', asset_key: '', status: 'missing', reason: 'not_scanned' }),
      ],
    })

    expect(screen.queryByRole('button', { name: UPLOAD_LABEL })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ignore' })).toBeNull()
  })
})
