import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Logs from '../../src/pages/Logs'

const mockShowToast = vi.fn()
const mockGetLogs = vi.fn()
const mockClearLogs = vi.fn()
const mockGetJobLogs = vi.fn()
const mockGetJobLogContent = vi.fn()
const mockDownloadJobLog = vi.fn()

vi.mock('../../src/components/Toast', () => ({
  useToast: () => ({ showToast: mockShowToast }),
}))

vi.mock('../../src/api/client', () => ({
  getLogs: (...args: unknown[]) => mockGetLogs(...args),
  clearLogs: (...args: unknown[]) => mockClearLogs(...args),
  getJobLogs: (...args: unknown[]) => mockGetJobLogs(...args),
  getJobLogContent: (...args: unknown[]) => mockGetJobLogContent(...args),
  downloadJobLog: (...args: unknown[]) => mockDownloadJobLog(...args),
}))

vi.mock('../../src/components/ConfirmDialog', () => ({
  default: (props: {
    isOpen: boolean
    title: string
    onConfirm: () => void
    onCancel: () => void
  }) => (props.isOpen ? (
    <div>
      <div>{props.title}</div>
      <button type="button" onClick={props.onConfirm}>Confirm</button>
      <button type="button" onClick={props.onCancel}>Cancel</button>
    </div>
  ) : null),
}))

class MockWebSocket {
  static OPEN = 1
  readyState = MockWebSocket.OPEN
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null

  constructor(_url: string) {
    setTimeout(() => {
      if (this.onopen) this.onopen(new Event('open'))
    }, 0)
  }

  close() {
    if (this.onclose) {
      this.onclose({ code: 1000 } as CloseEvent)
    }
  }
}

describe('Logs', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()

    vi.stubGlobal('WebSocket', MockWebSocket)
    vi.stubGlobal('open', vi.fn())

    mockGetLogs.mockResolvedValue([
      { timestamp: '26:02:15 10:00:00', level: 'INFO', message: 'System started' },
    ])
    mockClearLogs.mockResolvedValue({ message: 'ok' })
    mockGetJobLogs.mockResolvedValue({
      sync_one: [],
      sync_all: [],
      workflow: [
        { name: 'workflow.log', path: 'workflow/workflow.log', size: 128, modified: 1700000000 },
      ],
      poster_renamer: [],
      border_replacer: [],
      unmatched_assets: [],
    })
    mockGetJobLogContent.mockResolvedValue({ content: 'workflow content', filename: 'workflow.log' })
    mockDownloadJobLog.mockReturnValue('https://example.test/log-download')
  })

  it('loads system logs when auto-refresh is disabled', async () => {
    const user = userEvent.setup()
    render(<Logs />)

    const checkbox = screen.getByRole('checkbox', { name: /Auto-refresh/i })
    await user.click(checkbox)

    await waitFor(() => {
      expect(mockGetLogs).toHaveBeenCalledWith(1000, undefined)
    })

    expect(screen.getByText('System started')).toBeTruthy()
  })

  it('clears logs after confirmation', async () => {
    const user = userEvent.setup()
    render(<Logs />)

    await user.click(screen.getByRole('button', { name: /Clear Logs/i }))
    expect(screen.getByText('Clear All Logs')).toBeTruthy()

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    await waitFor(() => {
      expect(mockClearLogs).toHaveBeenCalledTimes(1)
    })
    expect(mockShowToast).toHaveBeenCalledWith('Logs cleared successfully')
  })

  it('loads job log file content from the job logs tab', async () => {
    const user = userEvent.setup()
    render(<Logs />)

    await user.click(screen.getByRole('button', { name: 'Job Logs' }))

    await screen.findByText('workflow.log')
    await user.click(screen.getByText('workflow.log'))

    await waitFor(() => {
      expect(mockGetJobLogContent).toHaveBeenCalledWith('workflow', 'workflow.log')
    })
    expect(screen.getByText('workflow content')).toBeTruthy()
  })
})
