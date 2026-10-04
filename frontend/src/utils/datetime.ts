// API timestamps carry a UTC offset; everything here renders in the browser's zone

type DateInput = string | null | undefined

const parse = (value: DateInput): Date | null => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const MEDIUM_DATE: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', year: 'numeric' }

// 10/3/2026, 2:07:33 PM
export function formatDateTime(value: DateInput): string {
  return parse(value)?.toLocaleString() ?? ''
}

// 10/3/2026
export function formatDate(value: DateInput): string {
  return parse(value)?.toLocaleDateString() ?? ''
}

// 10/3/2026 02:07 PM
export function formatDateShortTime(value: DateInput): string {
  const date = parse(value)
  if (!date) return ''
  return `${date.toLocaleDateString()} ${date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
}

// Oct 3, 2026
export function formatMediumDate(value: DateInput): string {
  return parse(value)?.toLocaleDateString(undefined, MEDIUM_DATE) ?? ''
}

// Oct 3, 2026 · 2:07 PM
export function formatMediumDateTime(value: DateInput): string {
  const date = parse(value)
  if (!date) return ''
  return `${date.toLocaleDateString(undefined, MEDIUM_DATE)} · ${date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })}`
}

// Sat, 02:30 PM
export function formatNextRun(value: DateInput): string {
  if (!value) return 'Not scheduled'
  const date = parse(value)
  if (!date) return 'Invalid date'
  return date.toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: true })
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

const wallClock = (zone: string, date: Date): string | null => {
  try {
    return date.toLocaleString('en-US', { timeZone: zone })
  } catch {
    return null
  }
}

// alias names (Asia/Calcutta vs Asia/Kolkata) and zones that share a wall clock count as the same
export function sameTimeZone(a: string, b: string): boolean {
  if (a === b) return true
  const year = new Date().getFullYear()
  const samples = [new Date(), ...[0, 3, 6, 9].map((month) => new Date(Date.UTC(year, month, 15, 12)))]
  return samples.every((date) => {
    const clock = wallClock(a, date)
    return clock !== null && clock === wallClock(b, date)
  })
}
