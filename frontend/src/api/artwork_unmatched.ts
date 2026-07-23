import { getData } from './http'
import { UnmatchedStats } from './posterManager'

// Each artwork type carries the same shape as poster UnmatchedStats (seasons zeroed),
// so the existing poster unmatched components can render it directly.
export interface ArtworkUnmatchedStats {
  logo: UnmatchedStats
  background: UnmatchedStats
  squareart: UnmatchedStats
  last_run: string | null
}

export type ArtworkType = 'logo' | 'background' | 'squareart'

export const getArtworkUnmatchedStats = async (): Promise<ArtworkUnmatchedStats> => {
  return getData('/api/posterflow/artwork-unmatched-stats')
}
