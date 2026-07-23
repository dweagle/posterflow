import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import FlowTab from '../../src/components/poster-manager/FlowTab'
import type { FlowConfig } from '../../src/api/client'

/**
 * The Sync Drives step covers two separate drive types. Artwork lives in its own table
 * with its own sync job, so before this the workflow synced poster drives only and then
 * organized artwork from whatever a manual sync last left on disk.
 */

const flowConfig = (
  sync: Partial<FlowConfig['sync_drives']> = {},
  detect: Partial<FlowConfig['detect_unmatched']> = {},
): FlowConfig =>
  ({
    idarr: { enabled: false, stop_on_error: false, scope_indices: [], sync_after_run: false },
    sync_drives: { enabled: true, stop_on_error: true, posters: true, artwork: true, ...sync },
    rename_assets: { enabled: true, stop_on_error: true },
    detect_unmatched: { enabled: true, stop_on_error: true, ...detect },
    border_replacer: { enabled: false, stop_on_error: true },
    plex_upload: { enabled: false, stop_on_error: false },
    cleanup_assets: { enabled: true, delete_unknown: false },
  }) as FlowConfig

function renderTab(overrides: Record<string, unknown> = {}) {
  const props = {
    flowConfig: flowConfig(),
    flowRunning: false,
    savingFlowConfig: false,
    hasUnsavedFlowChanges: false,
    onChangeFlowConfig: vi.fn(),
    onSaveFlowConfig: vi.fn(),
    onRunFlow: vi.fn(),
    idarrSyncTargets: [],
    workflows: [],
    activeWorkflowId: null,
    ...overrides,
  }
  render(
    <MemoryRouter>
      <FlowTab {...(props as never)} />
    </MemoryRouter>,
  )
  return props
}

describe('Workflow Sync Drives step', () => {
  afterEach(() => {
    cleanup()
  })

  it('offers a checkbox per drive type', () => {
    renderTab()

    expect(screen.getByLabelText(/Sync poster drives/i)).toBeTruthy()
    expect(screen.getByLabelText(/Sync artwork drives/i)).toBeTruthy()
  })

  it('reflects the saved selection', () => {
    renderTab({ flowConfig: flowConfig({ posters: true, artwork: false }) })

    expect((screen.getByLabelText(/Sync poster drives/i) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/Sync artwork drives/i) as HTMLInputElement).checked).toBe(false)
  })

  it('reports each drive type independently', async () => {
    const user = userEvent.setup()
    const props = renderTab()

    await user.click(screen.getByLabelText(/Sync artwork drives/i))
    expect(props.onChangeFlowConfig).toHaveBeenCalledWith('sync_drives', 'artwork', false)

    await user.click(screen.getByLabelText(/Sync poster drives/i))
    expect(props.onChangeFlowConfig).toHaveBeenCalledWith('sync_drives', 'posters', false)
  })

  it('hides the drive-type choices when the step is off', () => {
    renderTab({ flowConfig: flowConfig({ enabled: false }) })

    expect(screen.queryByLabelText(/Sync poster drives/i)).toBeNull()
    expect(screen.queryByLabelText(/Sync artwork drives/i)).toBeNull()
  })
})

describe('Workflow Detect Unmatched step', () => {
  afterEach(() => {
    cleanup()
  })

  it('has no per-asset-type choices — one pass covers posters and artwork', () => {
    renderTab()

    expect(screen.getByText('Detect Unmatched Assets')).toBeTruthy()
    expect(screen.queryByLabelText(/Detect unmatched posters/i)).toBeNull()
    expect(screen.queryByLabelText(/Detect unmatched artwork/i)).toBeNull()
  })
})
