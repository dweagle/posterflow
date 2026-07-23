import { useEffect, useRef, useState } from 'react'
import {
  ArtworkType,
  ArtworkUnmatchedStats,
  UnmatchedStats,
  getArtworkUnmatchedStats,
} from '../api/client'
import { useAppEvents } from '../contexts/AppEventsContext'

// Reads the artwork stats the unified Detect Unmatched pass produces (detection isn't run here),
// refreshing when that job finishes; library/ignore settings live on the page, shared with posters.
export function useArtworkUnmatched(selectedType: ArtworkType) {
  const { jobs } = useAppEvents()

  const [stats, setStats] = useState<ArtworkUnmatchedStats | null>(null)

  const prevJobStatusRef = useRef<Record<number, string>>({})
  const jobsInitRef = useRef(false)

  const fetchStats = () => { getArtworkUnmatchedStats().then(setStats).catch(() => {}) }

  useEffect(() => { fetchStats() }, [])

  // The unified detection pass refreshes artwork stats too — refetch when it finishes.
  useEffect(() => {
    for (const job of jobs ?? []) {
      const prev = prevJobStatusRef.current[job.id]
      if (jobsInitRef.current && job.job_type === 'Unmatched Detection'
        && (job.status === 'completed' || job.status === 'failed') && prev !== job.status) {
        fetchStats()
      }
      prevJobStatusRef.current[job.id] = job.status
    }
    jobsInitRef.current = true
  }, [jobs])

  const current: UnmatchedStats | null = stats ? stats[selectedType] : null

  return { stats: current }
}
