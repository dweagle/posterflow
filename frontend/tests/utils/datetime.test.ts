import { describe, expect, it } from 'vitest'
import { browserTimeZone, formatNextRun, timeZoneOptions } from '../../src/utils/datetime'

describe('timeZoneOptions', () => {
  it('never throws when Intl.supportedValuesOf is unavailable or rejects the key', () => {
    // The picker degrades to free text rather than crashing on older engines.
    const intl = Intl as unknown as Record<string, unknown>
    const original = intl.supportedValuesOf

    try {
      delete intl.supportedValuesOf
      expect(timeZoneOptions()).toEqual([])

      intl.supportedValuesOf = () => {
        throw new Error('unsupported key')
      }
      expect(timeZoneOptions()).toEqual([])
    } finally {
      if (original) {
        intl.supportedValuesOf = original
      }
    }
  })
})

describe('browserTimeZone', () => {
  it('returns a non-empty zone name', () => {
    // The scheduling field is prefilled from this, so an empty string would show nothing.
    expect(browserTimeZone().length).toBeGreaterThan(0)
  })
})

describe('formatNextRun', () => {
  it('renders in the browser zone on a 12-hour clock with a weekday', () => {
    const timestamp = '2026-09-30T14:30:00Z'
    const local = new Date(timestamp)
    const hour12 = local.getHours() % 12 || 12

    const rendered = formatNextRun(timestamp)
    expect(rendered).toContain(`${hour12}:`)
    // Guards the point of the refactor: UTC in, browser-local out, no timeZone option.
    expect(rendered).toBe(
      local.toLocaleString([], {
        weekday: 'short',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
      }),
    )
  })

  it('labels unscheduled and unparseable input', () => {
    expect(formatNextRun(null)).toBe('Not scheduled')
    expect(formatNextRun('')).toBe('Not scheduled')
    expect(formatNextRun('not a date')).toBe('Invalid date')
  })
})
