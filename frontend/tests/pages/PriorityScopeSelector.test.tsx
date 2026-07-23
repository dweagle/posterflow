import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PriorityScopeSelector from '../../src/components/poster-manager/PriorityScopeSelector'

/**
 * The Drive Priority scope row. Poster and artwork drives are separate community drive
 * types, so this only switches which priority list is shown — it saves nothing itself.
 */

describe('PriorityScopeSelector', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders a button per drive type', () => {
    render(<PriorityScopeSelector value="posters" onChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Poster Drives' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Artwork Drives' })).toBeTruthy()
  })

  it('marks only the current scope active', () => {
    render(<PriorityScopeSelector value="artwork" onChange={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Artwork Drives' }).className).toContain('active')
    expect(screen.getByRole('button', { name: 'Poster Drives' }).className).not.toContain('active')
  })

  it('reports the picked scope', async () => {
    const user = userEvent.setup()
    const onChange = vi.fn()
    render(<PriorityScopeSelector value="posters" onChange={onChange} />)

    await user.click(screen.getByRole('button', { name: 'Artwork Drives' }))
    expect(onChange).toHaveBeenCalledWith('artwork')
  })
})
