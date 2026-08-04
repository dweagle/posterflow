import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import UpdateBanner, { shouldShowUpdateBanner } from '../../src/components/UpdateBanner'
import { NEW_VERSION_EVENT } from '../../src/api/http'

afterEach(cleanup)

const fireNewVersion = (version: string) => {
  act(() => {
    window.dispatchEvent(new CustomEvent(NEW_VERSION_EVENT, { detail: version }))
  })
}

describe('shouldShowUpdateBanner', () => {
  const now = 1_000_000

  it('shows on a mismatch with no snooze', () => {
    expect(shouldShowUpdateBanner('0.14.0', null, now)).toBe(true)
  })

  it('hides while snoozed for the same version', () => {
    const snooze = JSON.stringify({ version: '0.14.0', until: now + 1000 })
    expect(shouldShowUpdateBanner('0.14.0', snooze, now)).toBe(false)
  })

  it('shows again once the snooze expires', () => {
    const snooze = JSON.stringify({ version: '0.14.0', until: now - 1 })
    expect(shouldShowUpdateBanner('0.14.0', snooze, now)).toBe(true)
  })

  it('shows for a different version even while snoozed', () => {
    const snooze = JSON.stringify({ version: '0.14.0', until: now + 1000 })
    expect(shouldShowUpdateBanner('0.15.0', snooze, now)).toBe(true)
  })

  it('shows when the stored snooze is corrupt', () => {
    expect(shouldShowUpdateBanner('0.14.0', 'not json', now)).toBe(true)
  })
})

describe('UpdateBanner', () => {
  it('renders nothing until a new-version event arrives', () => {
    const { container } = render(<UpdateBanner />)
    expect(container.innerHTML).toBe('')
  })

  it('shows the banner with the server version on the event', () => {
    render(<UpdateBanner />)
    fireNewVersion('9.9.9')
    expect(screen.getByRole('status').textContent).toContain('9.9.9')
    expect(screen.getByRole('button', { name: /refresh/i })).toBeTruthy()
  })

  it('dismiss snoozes: banner hides, snooze is stored, same version stays hidden', () => {
    render(<UpdateBanner />)
    fireNewVersion('9.9.9')
    act(() => {
      screen.getByRole('button', { name: /remind me tomorrow/i }).click()
    })
    expect(screen.queryByRole('status')).toBeNull()
    const snooze = JSON.parse(localStorage.getItem('posterflow.update.snooze') ?? '{}')
    expect(snooze.version).toBe('9.9.9')
    expect(snooze.until).toBeGreaterThan(Date.now())
    fireNewVersion('9.9.9')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('an expired snooze lets the banner return', () => {
    localStorage.setItem('posterflow.update.snooze', JSON.stringify({ version: '9.9.9', until: Date.now() - 1 }))
    render(<UpdateBanner />)
    fireNewVersion('9.9.9')
    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('a newer version overrides an active snooze', () => {
    localStorage.setItem('posterflow.update.snooze', JSON.stringify({ version: '9.9.9', until: Date.now() + 60_000 }))
    render(<UpdateBanner />)
    fireNewVersion('9.9.9')
    expect(screen.queryByRole('status')).toBeNull()
    fireNewVersion('10.0.0')
    expect(screen.getByRole('status').textContent).toContain('10.0.0')
  })
})
