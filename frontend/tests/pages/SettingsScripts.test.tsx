import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SettingsScriptsSection from '../../src/components/settings/SettingsScriptsSection'

const mockShowToast = vi.fn()
const mockGetAvailableScripts = vi.fn()
const mockGetHooks = vi.fn()
const mockSaveHooks = vi.fn()

vi.mock('../../src/api/scripts', () => ({
  getAvailableScripts: (...args: unknown[]) => mockGetAvailableScripts(...args),
  getHooks: (...args: unknown[]) => mockGetHooks(...args),
  saveHooks: (...args: unknown[]) => mockSaveHooks(...args),
}))

const DEFAULT_HOOKS = {
  sync_one:       { enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Sync (Single Drive)' },
  sync_all:       { enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Sync All' },
  poster_workflow:{ enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Poster Workflow' },
  poster_renamer: { enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Poster Renamer' },
  border_replacer:{ enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Border Replacer' },
  unmatched:      { enabled: false, script: '', run_when: 'always', trigger_on: 'always', label: 'Unmatched Detection' },
}

describe('SettingsScriptsSection', () => {
  afterEach(() => {
    cleanup()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetHooks.mockResolvedValue({ hooks: DEFAULT_HOOKS, scripts_dir: '/config/scripts' })
    mockGetAvailableScripts.mockResolvedValue({ scripts: ['test_hook.sh', 'notify.sh'], scripts_dir: '/config/scripts' })
    mockSaveHooks.mockResolvedValue({ message: 'Hooks saved' })
  })

  it('renders all six job type rows after loading', async () => {
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => {
      expect(screen.getByText('Sync (Single Drive)')).toBeTruthy()
      expect(screen.getByText('Sync All')).toBeTruthy()
      expect(screen.getByText('Poster Workflow')).toBeTruthy()
      expect(screen.getByText('Poster Renamer')).toBeTruthy()
      expect(screen.getByText('Border Replacer')).toBeTruthy()
      expect(screen.getByText('Unmatched Detection')).toBeTruthy()
    })
  })

  it('shows the scripts directory path in the info banner', async () => {
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => {
      expect(screen.getAllByText('/config/scripts').length).toBeGreaterThan(0)
    })
  })

  it('shows empty notice when no scripts are available', async () => {
    mockGetAvailableScripts.mockResolvedValue({ scripts: [], scripts_dir: '/config/scripts' })

    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => {
      expect(screen.getByText(/no scripts found/i)).toBeTruthy()
    })
  })

  it('populates script dropdowns with available scripts', async () => {
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => {
      // Each dropdown should contain the available script options
      const options = screen.getAllByRole('option', { name: 'test_hook.sh' })
      expect(options.length).toBe(6) // one per job row
    })
  })

  it('calls saveHooks with correct payload when Save is clicked', async () => {
    const user = userEvent.setup()
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    // Wait for load
    await waitFor(() => screen.getByText('Sync (Single Drive)'))

    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockSaveHooks).toHaveBeenCalledTimes(1)
      const [hooks] = mockSaveHooks.mock.calls[0]
      // All six keys present
      expect(Object.keys(hooks)).toEqual(
        expect.arrayContaining(['sync_one', 'sync_all', 'poster_workflow', 'poster_renamer', 'border_replacer', 'unmatched'])
      )
    })
  })

  it('shows success toast after saving', async () => {
    const user = userEvent.setup()
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => screen.getByText('Sync (Single Drive)'))
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Scripts configuration saved', 'success')
    })
  })

  it('shows error toast when save fails', async () => {
    mockSaveHooks.mockRejectedValue(new Error('network error'))
    const user = userEvent.setup()
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => screen.getByText('Sync (Single Drive)'))
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      expect(mockShowToast).toHaveBeenCalledWith('Failed to save scripts configuration', 'error')
    })
  })

  it('refreshes the script list when Refresh is clicked', async () => {
    const user = userEvent.setup()
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => screen.getByText('Sync (Single Drive)'))

    mockGetAvailableScripts.mockResolvedValue({ scripts: ['new_script.sh'], scripts_dir: '/config/scripts' })
    await user.click(screen.getByRole('button', { name: /refresh/i }))

    await waitFor(() => {
      expect(mockGetAvailableScripts).toHaveBeenCalledTimes(2)
      expect(mockShowToast).toHaveBeenCalledWith('Found 1 script(s)', 'info')
    })
  })

  it('does not send label field in save payload', async () => {
    const user = userEvent.setup()
    render(<SettingsScriptsSection showToast={mockShowToast} />)

    await waitFor(() => screen.getByText('Sync (Single Drive)'))
    await user.click(screen.getByRole('button', { name: /save/i }))

    await waitFor(() => {
      const [hooks] = mockSaveHooks.mock.calls[0]
      for (const value of Object.values(hooks)) {
        expect(value).not.toHaveProperty('label')
      }
    })
  })
})
