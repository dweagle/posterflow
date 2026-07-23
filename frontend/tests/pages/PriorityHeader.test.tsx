import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PriorityHeader from '../../src/components/poster-manager/PriorityHeader'

/**
 * The poster and artwork tabs both render this, so the toolbar reads the same whichever
 * scope is showing — only the panels below it swap. Save state is per-tab, so it's passed in.
 */

function renderHeader(overrides: Record<string, unknown> = {}) {
  const props = {
    scope: 'posters' as const,
    onScopeChange: vi.fn(),
    saving: false,
    hasUnsavedChanges: false,
    onSave: vi.fn(),
    ...overrides,
  }
  render(<PriorityHeader {...(props as never)} />)
  return props
}

describe('PriorityHeader', () => {
  afterEach(() => {
    cleanup()
  })

  it('keeps one title across both scopes', () => {
    renderHeader({ scope: 'posters' })
    expect(screen.getByRole('heading', { name: 'Configure Drive Priority' })).toBeTruthy()

    cleanup()
    renderHeader({ scope: 'artwork' })
    expect(screen.getByRole('heading', { name: 'Configure Drive Priority' })).toBeTruthy()
  })

  // The two originals each described only their own scope; the merged one covers both.
  it('describes what both scopes contribute', () => {
    renderHeader()

    const tooltip = document.querySelector('.toolbar-tooltip')
    expect(tooltip?.textContent).toMatch(/poster styles/i)
    expect(tooltip?.textContent).toMatch(/logo \/ backdrop \/ square-art/i)
    expect(tooltip?.textContent).toMatch(/override lower ones/i)
  })

  it('renders the scope switches and reports a change', async () => {
    const user = userEvent.setup()
    const props = renderHeader({ scope: 'posters' })

    expect(screen.getByRole('button', { name: 'Poster Drives' }).className).toContain('active')

    await user.click(screen.getByRole('button', { name: 'Artwork Drives' }))
    expect(props.onScopeChange).toHaveBeenCalledWith('artwork')
  })

  it('disables save with no pending changes', () => {
    renderHeader()
    expect((screen.getByRole('button', { name: /Save Settings/i }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('saves the owning tab once dirty', async () => {
    const user = userEvent.setup()
    const props = renderHeader({ hasUnsavedChanges: true })

    const save = screen.getByRole('button', { name: /Save Settings/i }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
    expect(save.className).toContain('btn-unsaved')

    await user.click(save)
    expect(props.onSave).toHaveBeenCalled()
  })

  it('blocks a second save while one is in flight', () => {
    renderHeader({ hasUnsavedChanges: true, saving: true })

    const save = screen.getByRole('button', { name: /Saving/i }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
  })
})
