import { describe, expect, it, vi, afterEach } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import Toolbar from '../../src/components/Toolbar'

// The 7 migrated call sites rely on this exact DOM shape, since the shared CSS in
// globals.css targets .toolbar > .toolbar-title / .action-buttons.
describe('Toolbar structure', () => {
  afterEach(() => cleanup())

  it('emits the markup the hand-rolled toolbars used to write by hand', () => {
    render(
      <Toolbar title="X" description="desc">
        <button className="btn-toolbar">Go</button>
      </Toolbar>,
    )
    expect(document.querySelector('.toolbar > .toolbar-title > h2')?.textContent).toBe('X')
    expect(document.querySelector('.toolbar-title > .toolbar-info > svg')).toBeTruthy()
    expect(document.querySelector('.toolbar-info > .toolbar-tooltip')?.textContent).toBe('desc')
    expect(document.querySelector('.toolbar > .action-buttons > .btn-toolbar')).toBeTruthy()
  })

  it('renders a rich description (GDrives/ArtworkDrivesPanel pass JSX)', () => {
    render(<Toolbar title="X" description={<>Use <strong>Scheduling</strong> and <code>logos/</code></>} />)
    const tip = document.querySelector('.toolbar-tooltip')
    expect(tip?.querySelector('strong')?.textContent).toBe('Scheduling')
    expect(tip?.querySelector('code')?.textContent).toBe('logos/')
  })

  it('omits action-buttons when there are none', () => {
    render(<Toolbar title="X" description="d" />)
    expect(document.querySelector('.action-buttons')).toBeNull()
  })
})
