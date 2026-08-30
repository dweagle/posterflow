import { useEffect, useMemo, useState, type MutableRefObject } from 'react'
import {
  getApiErrorMessage,
  getDrives,
  startArtworkPull,
  type ArtworkPullSource,
  type ArtworkSubtype,
  type Drive,
} from '../../api/client'
import { useAppEvents } from '../../contexts/AppEventsContext'
import { useToast } from '../Toast'

export type BatchState = { running: boolean; busy: boolean; canRun: boolean }

type Props = {
  syncTargetIndex: number | null
  scopeLabel: string | null
  /** The parent (toolbar) drives the run; we publish our run fn + status here. */
  runRef: MutableRefObject<(() => void) | null>
  onStateChange: (s: BatchState) => void
}

const SOURCES: { value: ArtworkPullSource; label: string; hint: string }[] = [
  { value: 'scope', label: 'Scope backfill', hint: "Look at items already in this scope and fill in the types they're missing." },
  { value: 'poster_drives', label: 'Poster drives', hint: 'Scan your subscribed poster drives for items and fetch what each is missing.' },
  { value: 'libraries', label: 'Arr & Media Server', hint: 'Everything in your configured sources — movies/series from Radarr/Sonarr (including upcoming/unreleased) or your media server libraries, plus collections from Plex/Jellyfin.' },
  { value: 'list', label: 'Paste a list', hint: 'Fetch for the titles you paste, one per line — see the accepted formats below.' },
]

const TYPES: { value: ArtworkSubtype; label: string }[] = [
  { value: 'logo', label: 'Logos' },
  { value: 'background', label: 'Backgrounds' },
  { value: 'squareart', label: 'Square Art' },
]

export default function BatchPullPanel({ syncTargetIndex, scopeLabel, runRef, onStateChange }: Props) {
  const { showToast } = useToast()
  const { jobs } = useAppEvents()

  const [source, setSource] = useState<ArtworkPullSource>('scope')
  const [drives, setDrives] = useState<Drive[]>([])
  const [driveIds, setDriveIds] = useState<number[]>([])
  const [paste, setPaste] = useState('')
  const [types, setTypes] = useState<ArtworkSubtype[]>(['logo', 'background', 'squareart'])
  const [minWidth, setMinWidth] = useState('1920')
  const [makeWhite, setMakeWhite] = useState(false)
  const [skipUnreleased, setSkipUnreleased] = useState(true)
  const [forceRefetch, setForceRefetch] = useState(false)
  const [syncAfter, setSyncAfter] = useState(false)
  const [dryRun, setDryRun] = useState(false)
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    getDrives().then((d) => setDrives(d.filter((x) => x.subscribed && !x.is_deprecated))).catch(() => {})
  }, [])

  const job = useMemo(() => {
    const mine = (jobs ?? []).filter((j) => j.job_type === 'artwork_pull')
    return mine.sort((a, b) => b.id - a.id)[0] || null
  }, [jobs])
  const running = job?.status === 'pending' || job?.status === 'running'

  const toggleType = (t: ArtworkSubtype) =>
    setTypes((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]))
  const toggleDrive = (id: number) =>
    setDriveIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))

  const run = async () => {
    if (syncTargetIndex == null) { showToast('Pick an artwork scope first', 'error'); return }
    if (types.length === 0) { showToast('Select at least one artwork type', 'error'); return }
    if (source === 'list' && !paste.trim()) { showToast('Paste at least one title', 'error'); return }
    setStarting(true)
    try {
      const res = await startArtworkPull({
        sync_target_index: syncTargetIndex,
        source,
        drive_ids: source === 'poster_drives' && driveIds.length ? driveIds : undefined,
        paste: source === 'list' ? paste : undefined,
        types,
        min_backdrop_width: Number(minWidth) || 1920,
        make_white_logos: makeWhite,
        skip_unreleased: skipUnreleased,
        force_refetch: forceRefetch,
        sync_after_run: syncAfter,
        dry_run: dryRun,
      })
      showToast(res.message || 'Artwork Pull queued', 'success')
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Failed to start pull'), 'error')
    } finally {
      setStarting(false)
    }
  }

  // Publish run + status up to the toolbar button (which lives in ArtworkFinderPanel).
  const canRun = types.length > 0 && syncTargetIndex != null && (source !== 'list' || paste.trim().length > 0)
  runRef.current = () => { void run() }
  useEffect(() => {
    onStateChange({ running: !!running, busy: starting, canRun })
  }, [running, starting, canRun, onStateChange])

  const label = { fontWeight: 600, fontSize: '0.8rem', color: '#cfd6e4', marginBottom: 6 } as const

  return (
    <div style={{ background: '#1e1e1e', border: '1px solid #333', borderRadius: 8, padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <p style={{ fontSize: '0.85rem', color: '#aaa', margin: 0 }}>
        Auto-fetch the artwork each item is missing and drop it into <strong>{scopeLabel || 'the selected scope'}</strong>.
        Logos use a hard white-only filter; backgrounds are TMDB textless; square art is Plex-only.
      </p>

      {/* Source */}
      <div>
        <div style={label}>Source</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {SOURCES.map((s) => (
            <button
              key={s.value}
              type="button"
              className={`tmdb-filter-btn${source === s.value ? ' active' : ''}`}
              onClick={() => setSource(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <small className="muted" style={{ display: 'block', marginTop: 6 }}>{SOURCES.find((s) => s.value === source)?.hint}</small>
      </div>

      {/* Config laid across the screen: Items | Types | Options */}
      <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        <div style={{ flex: '2 1 340px', minWidth: 260 }}>
          {source === 'poster_drives' && (
            <>
              <div style={label}>Poster drives <span style={{ fontWeight: 400, color: '#888' }}>(none checked = all subscribed)</span></div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 220, overflowY: 'auto', border: '1px solid #333', borderRadius: 6, padding: 8 }}>
                {drives.length === 0 && <span style={{ fontSize: '0.8rem', color: '#888' }}>No subscribed poster drives.</span>}
                {drives.map((d) => (
                  <label key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }}>
                    <input type="checkbox" checked={driveIds.includes(d.id)} onChange={() => toggleDrive(d.id)} />
                    {d.display_name || d.name} <span style={{ color: '#777' }}>({d.style_type})</span>
                  </label>
                ))}
              </div>
            </>
          )}
          {source === 'list' && (
            <>
              <div style={label}>Titles</div>
              <textarea
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                placeholder={'Back to the Future (1985) {tmdb-105}\nThe Office (2005) {tvdb-73244}\nDune (2021) {tmdb-438631}\nHarry Potter Collection {tmdb-1241}'}
                rows={7}
                style={{ width: '100%', boxSizing: 'border-box', background: '#151515', border: '1px solid #424242', borderRadius: 6, color: '#fff', padding: 8, fontSize: '0.85rem', fontFamily: 'monospace' }}
              />
              <small className="muted" style={{ display: 'block', marginTop: 6, lineHeight: 1.7 }}>
                One item per line. Each must be specific — a <strong>title</strong>, a <strong>year</strong>, and an <strong>id</strong>:
                <br /><code>Title (Year) {'{tmdb-###}'}</code> — e.g. <code>Back to the Future (1985) {'{tmdb-105}'}</code>
                <br /><code>Title (Year) {'{tvdb-###}'}</code> — a show, e.g. <code>The Office (2005) {'{tvdb-73244}'}</code>
                <br /><code>Name Collection {'{tmdb-###}'}</code> — a collection (backgrounds only; no year)
                <br />Extra ids on a line are fine (imdb is ignored). Lines without a year and an id are skipped.
              </small>
            </>
          )}
          {source === 'libraries' && (
            <>
              <div style={label}>Items</div>
              <small className="muted">
                The same media set the Asset Renamer works from: movies and series from your Radarr/Sonarr instance(s) or media-server libraries
                — both include upcoming/unreleased items the arrs are tracking — plus collections from Plex/Jellyfin (filtered by the
                Asset Renamer's library selection). Pair with a Dry Run first if you want to preview the item count.
              </small>
            </>
          )}
          {source === 'scope' && (
            <>
              <div style={label}>Items</div>
              <small className="muted">Backfills every item already present in {scopeLabel || 'the scope'} — fetching only the types it's missing.</small>
            </>
          )}
        </div>

        <div style={{ flex: '0 1 170px' }}>
          <div style={label}>Artwork types</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {TYPES.map((t) => (
              <label key={t.value} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.85rem', cursor: 'pointer', alignSelf: 'flex-start' }}>
                <input type="checkbox" checked={types.includes(t.value)} onChange={() => toggleType(t.value)} />
                {t.label}
              </label>
            ))}
          </div>
        </div>

        <div style={{ flex: '1 1 250px' }}>
          <div style={label}>Options</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }}>
              <input type="checkbox" checked={makeWhite} onChange={(e) => setMakeWhite(e.target.checked)} />
              Recolor non-white logos to white
            </label>
            {source === 'libraries' && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }} title="Skip arr items whose status is announced/upcoming/tba — TMDB rarely has art for them yet. Applies to Sonarr/Radarr items only; media-server items are already in your library.">
                <input type="checkbox" checked={skipUnreleased} onChange={(e) => setSkipUnreleased(e.target.checked)} />
                Skip unreleased items (arr only)
              </label>
            )}
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }} title="Ignore what the scope already has: re-download every selected type for every item, archiving replaced files to the IDarr duplicates folder. Off = only fetch what's missing.">
              <input type="checkbox" checked={forceRefetch} onChange={(e) => setForceRefetch(e.target.checked)} />
              Re-fetch everything (overwrite existing)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }}>
              <input type="checkbox" checked={syncAfter} onChange={(e) => setSyncAfter(e.target.checked)} />
              Run IDarr &amp; upload after
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem', cursor: 'pointer', alignSelf: 'flex-start' }}>
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              Dry run (report only)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem' }}>
              Min backdrop width
              <input type="number" value={minWidth} onChange={(e) => setMinWidth(e.target.value)} style={{ width: 80, background: '#151515', border: '1px solid #424242', borderRadius: 4, color: '#fff', padding: '2px 6px' }} />
            </label>
          </div>
        </div>
      </div>

      {job && (
        <span style={{ fontSize: '0.82rem', color: running ? '#64b5f6' : job.status === 'failed' ? '#ef5350' : '#8bc34a' }}>
          {running ? `${job.progress ?? 0}% — ${job.message || 'working…'}` : `Last run — ${job.status}: ${job.message || ''}`}
        </span>
      )}
      {syncTargetIndex == null && <small style={{ color: '#ffb74d' }}>Pick an artwork scope at the top first.</small>}
    </div>
  )
}
