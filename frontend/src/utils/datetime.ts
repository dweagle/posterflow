// Timestamps arrive from the API as ISO-8601 UTC. Rendering always defers to the
// browser's zone — that is the whole point of storing UTC — so nothing here passes a
// timeZone option to Intl.

const EMPTY = '—'

const parse = (value: string | null | undefined): Date | null => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDateTime(value: string | null | undefined): string {
  return parse(value)?.toLocaleString() ?? EMPTY
}

export function formatDate(value: string | null | undefined): string {
  return parse(value)?.toLocaleDateString() ?? EMPTY
}

export function formatTime(value: string | null | undefined): string {
  return parse(value)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) ?? EMPTY
}

export function formatNextRun(value: string | null | undefined): string {
  if (!value) return 'Not scheduled'
  const date = parse(value)
  if (!date) return 'Invalid date'
  return date.toLocaleString([], {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function timeZoneOptions(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  if (typeof intl.supportedValuesOf !== 'function') return []
  try {
    return intl.supportedValuesOf('timeZone')
  } catch {
    return []
  }
}
