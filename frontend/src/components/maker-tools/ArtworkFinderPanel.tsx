import { useCallback, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Clapperboard as MovieIcon, FolderOpen, HardDrive, Play, Search, Tv } from 'lucide-react'
import {
  getApiErrorMessage,
  searchTmdb,
  type ArtworkItemSource,
  type TmdbSearchFilter,
  type TmdbSearchResult,
} from '../../api/client'
import Toolbar from '../Toolbar'
import ArtworkFinderCard from './ArtworkFinderCard'
import BatchPullPanel from './BatchPullPanel'
import DriveItemsPanel from './DriveItemsPanel'
import { useArtworkScopes } from './useArtworkScopes'

type ItemSource = {
  key: string
  label: string
  hint: string
  source: ArtworkItemSource
  itemScopeIndex: number | null
}

export default function ArtworkFinderPanel() {
  const navigate = useNavigate()
  const { scopes, posterScopes, selectedValue, selected, onSelectScope, scopesLoaded } = useArtworkScopes()

  // What My Drive lists. The artwork scope only knows the files it holds, so an item with no
  // artwork at all has nothing to enumerate and can never appear under it — the poster scopes are
  // how those show up. One button per poster scope, since a gap list is only actionable against
  // the library you actually keep.
  const itemSources: ItemSource[] = useMemo(() => [
    { key: 'scope', label: 'This drive', source: 'scope', itemScopeIndex: null,
      hint: "Items that already have artwork here, and which types they're still missing." },
    ...posterScopes.map((s) => ({
      key: `poster:${s.index}`, label: s.label, source: 'poster_scope' as ArtworkItemSource,
      itemScopeIndex: s.index,
      hint: `Everything in ${s.label} — including items with no artwork here yet.`,
    })),
  ], [posterScopes])

  const [itemSourceKey, setItemSourceKey] = useState('scope')
  const itemSource = useMemo(
    () => itemSources.find((s) => s.key === itemSourceKey) ?? itemSources[0],
    [itemSources, itemSourceKey],
  )

  const [mode, setMode] = useState<'search' | 'batch' | 'drive'>('search')
  const batchRunRef = useRef<(() => void) | null>(null)
  const [batchState, setBatchState] = useState({ running: false, busy: false, canRun: false })
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<TmdbSearchFilter>('all')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<TmdbSearchResult[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const doSearch = useCallback(async (q: string, f: TmdbSearchFilter) => {
    const term = q.trim()
    if (!term) return
    setSearching(true)
    setError(null)
    setResults(null)
    try {
      setResults(await searchTmdb(term, f))
    } catch (e) {
      setError(getApiErrorMessage(e, 'Search failed'))
    } finally {
      setSearching(false)
    }
  }, [])

  return (
    <div className="maker-tools-panel">
      <Toolbar
        title="Artwork Finder"
        description="Find logos, backgrounds, and square art from TMDB and Plex, then add them to a chosen IDarr artwork scope — IDarr renames and uploads them to your drive."
        titleControl={scopes.length > 0 ? (
          <div className="artwork-scope-control">
            <HardDrive size={15} />
            <span className="artwork-scope-label">Artwork scope:</span>
            <select value={selectedValue} onChange={(e) => onSelectScope(e.target.value)}>
              {scopes.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
        ) : scopesLoaded ? (
          <button type="button" className="btn-toolbar" style={{ marginLeft: 12 }} onClick={() => navigate('/IDarr')} title='Add an artwork scope — enable the "Assets Drive" toggle on an IDarr sync target'>
            <HardDrive size={15} /> Add artwork scope
          </button>
        ) : null}
      >
        {mode === 'batch' && (
          <button
            type="button"
            className="btn-toolbar btn-primary"
            onClick={() => batchRunRef.current?.()}
            disabled={batchState.running || batchState.busy || !batchState.canRun}
          >
            <Play size={16} /> {batchState.running ? 'Running…' : batchState.busy ? 'Starting…' : 'Run Pull'}
          </button>
        )}
      </Toolbar>

      <div className="pf-subtabs">
        <button type="button" className={mode === 'search' ? 'active' : ''} onClick={() => setMode('search')}>Find &amp; Add</button>
        <button type="button" className={mode === 'batch' ? 'active' : ''} onClick={() => setMode('batch')}>Batch Pull</button>
        <button type="button" className={mode === 'drive' ? 'active' : ''} onClick={() => setMode('drive')}>My Drive</button>

        {/* My Drive's item sources ride in the same row, in their own colour: they don't switch
            panels like the tabs to their left, they switch what that one panel is listing. */}
        {mode === 'drive' && (
          <>
            <span className="pf-subtabs-divider" aria-hidden="true" />
            {itemSources.map((s) => (
              <button
                key={s.key}
                type="button"
                className={`pf-subtab-source${itemSourceKey === s.key ? ' active' : ''}`}
                onClick={() => setItemSourceKey(s.key)}
                title={s.hint}
              >
                {s.label}
              </button>
            ))}
          </>
        )}
      </div>

      {mode === 'drive' ? (
        <DriveItemsPanel
          syncTargetIndex={selected ? selected.index : null}
          scopeLabel={selected ? selected.label : null}
          source={itemSource.source}
          itemScopeIndex={itemSource.itemScopeIndex}
          sourceHint={itemSource.hint}
          sourceLabel={itemSource.label}
        />
      ) : mode === 'batch' ? (
        <BatchPullPanel
          syncTargetIndex={selected ? selected.index : null}
          scopeLabel={selected ? selected.label : null}
          runRef={batchRunRef}
          onStateChange={setBatchState}
        />
      ) : (
      <div className="tmdb-search-panel">
        {/* Search */}
        <div className="tmdb-search-bar">
          <Search size={18} className="tmdb-search-icon" />
          <input
            type="text"
            className="tmdb-search-input"
            placeholder="Search movies, TV shows, collections… add (year) to narrow results"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void doSearch(query, filter) }}
          />
          <button className="btn-toolbar btn-primary" type="button" onClick={() => void doSearch(query, filter)} disabled={searching || !query.trim()}>
            {searching ? 'Searching…' : 'Search'}
          </button>
        </div>

        <div className="tmdb-filter-bar">
          {(['all', 'movie', 'tv', 'collection'] as TmdbSearchFilter[]).map((f) => (
            <button
              key={f}
              type="button"
              className={`tmdb-filter-btn${filter === f ? ' active' : ''}`}
              onClick={() => { setFilter(f); if (query.trim()) void doSearch(query, f) }}
            >
              {f === 'all' && 'All'}
              {f === 'movie' && <><MovieIcon size={13} /> Movies</>}
              {f === 'tv' && <><Tv size={13} /> TV Shows</>}
              {f === 'collection' && <><FolderOpen size={13} /> Collections</>}
            </button>
          ))}
        </div>

        {error && <p className="tmdb-error">{error}</p>}

        {results !== null && (
          results.length === 0
            ? <p className="tmdb-empty">No results found.</p>
            : (
              <div style={{ marginTop: 8 }}>
                {results.map((item) => (
                  <ArtworkFinderCard
                    key={`${item.media_type}-${item.tmdb_id}`}
                    item={item}
                    syncTargetIndex={selected ? selected.index : null}
                    scopeLabel={selected ? selected.label : null}
                  />
                ))}
              </div>
            )
        )}
      </div>
      )}
    </div>
  )
}
