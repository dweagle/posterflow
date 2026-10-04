import { describe, expect, it } from 'vitest'
import {
  formatDate,
  formatDateShortTime,
  formatDateTime,
  formatMediumDate,
  formatMediumDateTime,
  formatNextRun,
  sameTimeZone,
} from '../../src/utils/datetime'

// Built from local parts so the expected wall clock holds in any host timezone.
const LOCAL = new Date(2026, 9, 3, 14, 7, 33).toISOString()

describe('datetime formatters', () => {
  it('keeps the formats each screen already used', () => {
    expect(formatDate(LOCAL)).toBe('10/3/2026')
    expect(formatDateTime(LOCAL)).toMatch(/^10\/3\/2026, 2:07:33\sPM$/)
    expect(formatDateShortTime(LOCAL)).toMatch(/^10\/3\/2026 02:07\sPM$/)
    expect(formatMediumDate(LOCAL)).toBe('Oct 3, 2026')
    expect(formatMediumDateTime(LOCAL)).toMatch(/^Oct 3, 2026 · 2:07\sPM$/)
    expect(formatNextRun(LOCAL)).toMatch(/^Sat,? 02:07\sPM$/)
  })

  it('reads an offset timestamp as an instant, not as local wall clock', () => {
    const instant = new Date(Date.UTC(2026, 9, 3, 18, 7, 33))

    expect(formatDateTime('2026-10-03T18:07:33+00:00')).toBe(instant.toLocaleString())
    expect(formatDateTime('2026-10-03T18:07:33.123456Z')).toBe(instant.toLocaleString())
    expect(formatDateTime('2026-10-03T14:07:33-04:00')).toBe(instant.toLocaleString())
  })

  it('returns an empty string for missing or unparseable values', () => {
    for (const format of [formatDate, formatDateTime, formatDateShortTime, formatMediumDate, formatMediumDateTime]) {
      expect(format(null)).toBe('')
      expect(format(undefined)).toBe('')
      expect(format('not a date')).toBe('')
    }
  })

  it('labels a missing or bad next run', () => {
    expect(formatNextRun(null)).toBe('Not scheduled')
    expect(formatNextRun('not a date')).toBe('Invalid date')
  })
})

describe('sameTimeZone', () => {
  it('treats alias names as the same zone', () => {
    expect(sameTimeZone('Asia/Calcutta', 'Asia/Kolkata')).toBe(true)
    expect(sameTimeZone('US/Eastern', 'America/New_York')).toBe(true)
    expect(sameTimeZone('America/New_York', 'America/New_York')).toBe(true)
  })

  it('tells apart zones whose wall clocks differ', () => {
    expect(sameTimeZone('America/New_York', 'America/Chicago')).toBe(false)
    expect(sameTimeZone('UTC', 'Europe/London')).toBe(false)
  })

  it('tells apart zones that only agree for part of the year', () => {
    expect(sameTimeZone('America/Phoenix', 'America/Denver')).toBe(false)
  })

  it('treats a zone the browser does not know as different', () => {
    expect(sameTimeZone('Not/AZone', 'America/New_York')).toBe(false)
  })
})
