import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  const openWorkflowLog = async (user: ReturnType<typeof userEvent.setup>, content: string) => {
    mockGetJobLogContent.mockResolvedValue({ content, filename: 'workflow.log' })
    render(<Logs />)
    await user.click(screen.getByRole('button', { name: 'Job Logs' }))
    await screen.findByText('workflow.log')
    await user.click(screen.getByText('workflow.log'))
    await waitFor(() => expect(mockGetJobLogContent).toHaveBeenCalled())
  }

  it('searches the open job log only on submit, then restores the file on clear', async () => {
    const user = userEvent.setup()
    await openWorkflowLog(user, 'alpha line\nbeta line\nalpha again')
    await screen.findByText('beta line')

    await user.type(screen.getByPlaceholderText('Search this log file…'), 'alpha')
    expect(screen.getByText('beta line')).toBeTruthy() // typing alone never filters

    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => {
      expect(screen.queryByText('beta line')).toBeNull()
    })
    expect(document.querySelectorAll('mark.log-match').length).toBe(2)
    expect(screen.getByText('2 / 2')).toBeTruthy()

    await user.click(screen.getByTitle('Clear search'))
    expect(screen.getByText('beta line')).toBeTruthy()
  })

  it('reports when a job log search has no matches', async () => {
    const user = userEvent.setup()
    await openWorkflowLog(user, 'alpha line\nbeta line')
    await user.type(screen.getByPlaceholderText('Search this log file…'), 'gamma')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('No matches in this log file')).toBeTruthy()
  })

  const mountedRows = () => document.querySelectorAll('.log-pane-row').length

  it('mounts large files as a window that extends on scroll', async () => {
    const user = userEvent.setup()
    const content = Array.from({ length: 4100 }, (_, i) => `line ${i}`).join('\n')
    await openWorkflowLog(user, content)
    await screen.findByText('line 0')

    // Only the first window is in the DOM, with a note about the rest.
    expect(mountedRows()).toBe(2000)
    expect(screen.getByText(`— ${(2100).toLocaleString()} more lines — keep scrolling —`)).toBeTruthy()

    // jsdom has zero heights, so any scroll counts as "near the bottom".
    fireEvent.scroll(document.querySelector('.log-pane')!)
    expect(mountedRows()).toBe(4000)

    fireEvent.scroll(document.querySelector('.log-pane')!)
    expect(mountedRows()).toBe(4100)
    expect(screen.queryByText(/more lines — keep scrolling/)).toBeNull()
  })

  it('windows large search results but counts every match', async () => {
    const user = userEvent.setup()
    const content = Array.from({ length: 3000 }, (_, i) => `alpha ${i}`).join('\n')
    await openWorkflowLog(user, content)
    await screen.findByText('alpha 0')

    await user.type(screen.getByPlaceholderText('Search this log file…'), 'alpha')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    // Newest 2000 matches mounted, older ones behind the scroll-up note; counter shows all.
    await waitFor(() => expect(mountedRows()).toBe(2000))
    const rows = document.querySelectorAll('.log-pane-row')
    expect(rows[0].textContent).toBe('alpha 1000')
    expect(rows[rows.length - 1].textContent).toBe('alpha 2999')
    expect(screen.getByText(`— ${(1000).toLocaleString()} older matches — scroll up to load —`)).toBeTruthy()
    expect(screen.getByText(`${(3000).toLocaleString()} / ${(3000).toLocaleString()}`)).toBeTruthy()

    // Cycling wraps to the oldest match, which forces the window to grow around it.
    await user.click(screen.getByTitle('Next (newer) match'))
    expect(mountedRows()).toBe(3000)
    expect(screen.getByText(`1 / ${(3000).toLocaleString()}`)).toBeTruthy()

    // Editing the term returns to the file, again mounting only its window.
    await user.type(screen.getByPlaceholderText('Search this log file…'), 'x')
    await waitFor(() => expect(mountedRows()).toBe(2000))
    expect(document.querySelectorAll('mark.log-match').length).toBe(0)
  })
})
