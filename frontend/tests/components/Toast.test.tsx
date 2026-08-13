import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ToastProvider, useToast } from '../../src/components/Toast'

// Fires N toasts synchronously on mount — same tick, same Date.now() millisecond
const FireToasts = ({ messages }: { messages: string[] }) => {
  const { showToast } = useToast()
  const fired = { current: false }
  if (!fired.current) {
    fired.current = true
    messages.forEach(m => showToast(m, 'info'))
  }
  return null
}

describe('ToastProvider', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('gives same-tick toasts unique keys and clears them all', () => {
    const keyWarnings: string[] = []
    const consoleError = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
      const text = args.map(String).join(' ')
      if (text.includes('same key')) keyWarnings.push(text)
    })

    render(
      <ToastProvider>
        <FireToasts messages={['Subscribed to 8 drives', '8 drive(s) added to priority', 'Initial scan queued']} />
      </ToastProvider>,
    )

    // All three render — duplicate ids would collapse or mis-key them.
    expect(screen.getByText('Subscribed to 8 drives')).toBeTruthy()
    expect(screen.getByText('8 drive(s) added to priority')).toBeTruthy()
    expect(screen.getByText('Initial scan queued')).toBeTruthy()
    expect(keyWarnings).toEqual([])

    // And all three clear after the dismissal window — none left stuck.
    act(() => {
      vi.advanceTimersByTime(4100)
    })
    expect(document.querySelectorAll('.toast').length).toBe(0)

    consoleError.mockRestore()
  })
})
