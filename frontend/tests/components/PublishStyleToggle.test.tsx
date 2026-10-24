import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import PublishStyleToggle from '../../src/components/poster-manager/PublishStyleToggle'
import { getStoredPosterStyle, setStoredPosterStyle } from '../../src/components/community/posterStyles'
import { resetHiddenPosterStylesCache, setHiddenPosterStylesCache } from '../../src/hooks/useHiddenPosterStyles'

vi.mock('../../src/api/settings', () => ({ getSettings: vi.fn(async () => ({})) }))

describe('PublishStyleToggle with hidden styles', () => {
  afterEach(() => {
    cleanup()
    resetHiddenPosterStylesCache()
    localStorage.clear()
  })

  it('offers both community styles by default', () => {
    setHiddenPosterStylesCache([])
    render(<PublishStyleToggle value={null} onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'CL2K' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'MM2K' })).toBeTruthy()
  })

  it('drops a hidden style from the picker', () => {
    setHiddenPosterStylesCache(['MM2K'])
    render(<PublishStyleToggle value={null} onChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'CL2K' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'MM2K' })).toBeNull()
  })

  it('forgets a remembered style once it is hidden', () => {
    setStoredPosterStyle('MM2K Style')
    setHiddenPosterStylesCache([])
    expect(getStoredPosterStyle()).toBe('MM2K Style')
    setHiddenPosterStylesCache(['MM2K'])
    expect(getStoredPosterStyle()).toBeNull()
  })
})
