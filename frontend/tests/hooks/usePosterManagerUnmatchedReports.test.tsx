import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { usePosterManagerUnmatchedReports } from '../../src/hooks/usePosterManagerUnmatchedReports'
import type { UnmatchedStats } from '../../src/api/client'

/**
 * The report names the asset type it was generated for. Reports are downloaded from the
 * one Unmatched view, so poster wording must not leak into a logo/backdrop/square report.
 */

let captured: { content: string; filename: string } | null = null

// jsdom has no object URLs; capture the Blob the hook hands to the download link instead.
beforeEach(() => {
  captured = null
  const blobs = new Map<string, Blob>()
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: (blob: Blob) => {
      const url = 'blob:report'
      blobs.set(url, blob)
      return url
    },
    revokeObjectURL: () => {},
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    const blob = blobs.get(this.href)
    if (blob) {
      // Blob text is async; the hook builds it synchronously, so read the parts directly.
      captured = { content: (blob as Blob & { __text?: string }).__text ?? '', filename: this.download }
    }
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  cleanup()
})

// Blob in jsdom doesn't expose its parts synchronously — patch it to keep the raw text.
const OriginalBlob = globalThis.Blob
class TextBlob extends OriginalBlob {
  __text: string
  constructor(parts: BlobPart[], options?: BlobPropertyBag) {
    super(parts, options)
    this.__text = parts.join('')
  }
}
globalThis.Blob = TextBlob as unknown as typeof Blob

const posterStats = (): UnmatchedStats => ({
  summary: {
    movies: { total: 10, unmatched: 2, percent_complete: 80 },
    series: { total: 5, unmatched: 1, percent_complete: 80 },
    seasons: { total: 8, unmatched: 3, percent_complete: 62.5 },
    collections: { total: 4, unmatched: 1, percent_complete: 75 },
    grand_total: { total: 19, unmatched: 4, percent_complete: 78.9 },
    by_library: {},
  },
  unmatched: {
    movies: [{ title: 'Inception', year: 2010, instance: 'Radarr' }],
    series: [{ title: 'Breaking Bad', year: 2008, missing_seasons: [1, 2], missing_main_poster: true, instance: 'Sonarr' }],
    collections: [{ title: 'Marvel Collection', year: 0, instance: 'Plex' }],
  },
  last_run: '2026-07-15T00:00:00Z',
})

// Artwork carries no seasons — the backend zeroes that dimension.
const artworkStats = (): UnmatchedStats => {
  const stats = posterStats()
  stats.summary.seasons = { total: 0, unmatched: 0, percent_complete: 100 }
  stats.unmatched.series = [
    { title: 'Breaking Bad', year: 2008, missing_seasons: [], missing_main_poster: true, instance: 'Sonarr' },
  ]
  return stats
}

function download(stats: UnmatchedStats, typeNoun?: string) {
  const { result } = renderHook(() =>
    usePosterManagerUnmatchedReports({ unmatchedStats: stats, showToast: vi.fn(), typeNoun }),
  )
  result.current.downloadCompleteReport()
  return captured!
}

describe('usePosterManagerUnmatchedReports', () => {
  it('names posters throughout the default report', () => {
    const { content, filename } = download(posterStats())

    expect(filename).toBe(`unmatched-posters-complete-report-${new Date().toISOString().split('T')[0]}.txt`)
    expect(content).toContain('COMPLETE UNMATCHED POSTERS REPORT')
    expect(content).toContain('With Posters: 15')
    expect(content).toContain('Missing Posters: 4')
    expect(content).toContain('MOVIES WITHOUT POSTERS (1)')
    expect(content).toContain('SERIES WITHOUT MAIN POSTERS (1)')
    expect(content).toContain('SERIES WITH MISSING SEASON POSTERS (1)')
    expect(content).toContain('COLLECTIONS WITHOUT POSTERS (1)')
  })

  it('names the artwork type instead of posters', () => {
    const { content, filename } = download(artworkStats(), 'Logos')

    expect(filename).toContain('unmatched-logos-complete-report-')
    expect(content).toContain('COMPLETE UNMATCHED LOGOS REPORT')
    expect(content).toContain('With Logos: 15')
    expect(content).toContain('Missing Logos: 4')
    expect(content).toContain('MOVIES WITHOUT LOGOS (1)')
    expect(content).not.toMatch(/POSTERS/)
  })

  it('drops "main" and the seasons section for artwork, which has no seasons', () => {
    const { content } = download(artworkStats(), 'Backdrops')

    expect(content).toContain('SERIES WITHOUT BACKDROPS (1)')
    expect(content).not.toContain('MAIN BACKDROPS')
    expect(content).not.toContain('SEASON BACKDROPS')
  })

  it('builds a filesystem-safe slug for multi-word types', () => {
    const { filename, content } = download(artworkStats(), 'Square Art')

    expect(filename).toContain('unmatched-square-art-complete-report-')
    expect(content).toContain('MOVIES WITHOUT SQUARE ART (1)')
  })

  it('per-type lists name the asset type too', () => {
    const { result } = renderHook(() =>
      usePosterManagerUnmatchedReports({ unmatchedStats: artworkStats(), showToast: vi.fn(), typeNoun: 'Logos' }),
    )

    result.current.downloadUnmatchedList('movies')
    expect(captured!.filename).toContain('unmatched-logos-movies-')
    expect(captured!.content).toContain('1 movies without logos')

    result.current.downloadUnmatchedList('collections')
    expect(captured!.filename).toContain('unmatched-logos-collections-')
    expect(captured!.content).toContain('1 collections without logos')
  })
})
