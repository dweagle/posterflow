import { afterEach, describe, it, expect, vi } from 'vitest'
import { cleanup, render, screen, fireEvent } from '@testing-library/react'
import { GoogleDriveCredentialsGuide, GoogleCredentialsConflictNotice } from '../../src/components/settings/GoogleDriveCredentialsGuide'

describe('GoogleDriveCredentialsGuide', () => {
  afterEach(() => {
    cleanup()
  })

  it('is collapsed until toggled and then shows both options', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<GoogleDriveCredentialsGuide open={false} onToggle={onToggle} />)
    expect(screen.queryByText(/Option A: Service account/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /How to set up Google Drive access/ }))
    expect(onToggle).toHaveBeenCalledTimes(1)

    rerender(<GoogleDriveCredentialsGuide open onToggle={onToggle} />)
    expect(screen.getByRole('tab', { name: /Option A: Service account/ }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText(/A3\. Create the service account/)).toBeTruthy()
    expect(screen.queryByText(/B4\. Branding/)).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('opens a screenshot from its Preview button and closes it on Escape', () => {
    render(<GoogleDriveCredentialsGuide open onToggle={vi.fn()} />)
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Preview 1: The project picker and the New project dialog/ }))
    const dialog = screen.getByRole('dialog')
    expect(dialog.querySelector('img')?.getAttribute('src')).toMatch(/a1-new-project/)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('numbers a step\'s previews in reading order and says which one is open', () => {
    render(<GoogleDriveCredentialsGuide open onToggle={vi.fn()} />)
    const a4 = screen.getByText(/A4\. Download a key file/).closest('.instruction-step') as HTMLElement
    const labels = Array.from(a4.querySelectorAll('button')).map((b) => b.textContent)
    expect(labels).toEqual([
      'Preview 1: The Keys tab and the Add key button',
      'Preview 2: Add key open, then Create new key',
      'Preview 3: The Create private key dialog with JSON selected, then Create',
    ])
    fireEvent.click(screen.getByRole('button', { name: /Preview 2: Add key open/ }))
    expect(screen.getByRole('dialog').textContent).toMatch(/^2 of 3: Add key open, then Create new key$/)
  })

  it('switches to the OAuth steps on the Option B tab', () => {
    render(<GoogleDriveCredentialsGuide open onToggle={vi.fn()} />)
    fireEvent.click(screen.getByRole('tab', { name: /Option B: OAuth client/ }))
    expect(screen.getByRole('tab', { name: /Option B: OAuth client/ }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByText(/B1\. Create a Google Cloud project/)).toBeTruthy()
    expect(screen.getByText(/B4\. Branding/)).toBeTruthy()
    expect(screen.queryByText(/A3\. Create the service account/)).toBeNull()
  })
})

describe('GoogleCredentialsConflictNotice', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders only when a service account and an OAuth field are both set', () => {
    const { container, rerender } = render(
      <GoogleCredentialsConflictNotice serviceAccountFile="/config/sa.json" clientId="" clientSecret="" token="" />
    )
    expect(container.firstChild).toBeNull()

    rerender(<GoogleCredentialsConflictNotice serviceAccountFile="" clientId="id" clientSecret="secret" token="token" />)
    expect(container.firstChild).toBeNull()

    rerender(<GoogleCredentialsConflictNotice serviceAccountFile="/config/sa.json" clientId="id" clientSecret="" token="" />)
    expect(screen.getByRole('alert').textContent).toMatch(/service account for everything/)
  })
})
