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

  return (
    <div className="batch-pull-panel">
      <p className="batch-pull-intro">
        Auto-fetch the artwork each item is missing and drop it into <strong>{scopeLabel || 'the selected scope'}</strong>.
        Logos use a hard white-only filter; backgrounds are TMDB textless; square art is Plex-only.
      </p>

      {/* Source */}
      <div>
        <div className="batch-pull-label">Source</div>
        <div className="batch-pull-sources pf-subtabs">
          {SOURCES.map((s) => (
            <button
              key={s.value}
              type="button"
              className={source === s.value ? 'active' : ''}
              onClick={() => setSource(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>
        <small className="text-dim block mt-sm">{SOURCES.find((s) => s.value === source)?.hint}</small>
      </div>

      {/* Config laid across the screen: Items | Types | Options */}
      <div className="batch-pull-columns">
        <div className="batch-pull-col-items">
          {source === 'poster_drives' && (
            <>
              <div className="batch-pull-label">Poster drives <span>(none checked = all subscribed)</span></div>
              <div className="batch-pull-drive-list">
                {drives.length === 0 && <span className="text-faint">No subscribed poster drives.</span>}
                {drives.map((d) => (
                  <label key={d.id} className="checkbox-label">
                    <input type="checkbox" checked={driveIds.includes(d.id)} onChange={() => toggleDrive(d.id)} />
                    {d.display_name || d.name} <span className="text-faint">({d.style_type})</span>
                  </label>
                ))}
              </div>
            </>
          )}
          {source === 'list' && (
            <>
              <div className="batch-pull-label">Titles</div>
              <textarea
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                placeholder={'Back to the Future (1985) {tmdb-105}\nThe Office (2005) {tvdb-73244}\nDune (2021) {tmdb-438631}\nHarry Potter Collection {tmdb-1241}'}
                rows={7}
                className="batch-pull-paste"
              />
              <small className="text-dim batch-pull-paste-help">
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
              <div className="batch-pull-label">Items</div>
              <small className="text-dim">
                The same media set the Asset Renamer works from: movies and series from your Radarr/Sonarr instance(s) or media-server libraries
                — both include upcoming/unreleased items the arrs are tracking — plus collections from Plex/Jellyfin (filtered by the
                Asset Renamer's library selection). Pair with a Dry Run first if you want to preview the item count.
              </small>
            </>
          )}
          {source === 'scope' && (
            <>
              <div className="batch-pull-label">Items</div>
              <small className="text-dim">Backfills every item already present in {scopeLabel || 'the scope'} — fetching only the types it's missing.</small>
            </>
          )}
        </div>

        <div className="batch-pull-col-types">
          <div className="batch-pull-label">Artwork types</div>
          <div className="batch-pull-stack">
            {TYPES.map((t) => (
              <label key={t.value} className="checkbox-label">
                <input type="checkbox" checked={types.includes(t.value)} onChange={() => toggleType(t.value)} />
                {t.label}
              </label>
            ))}
          </div>
        </div>

        <div className="batch-pull-col-options">
          <div className="batch-pull-label">Options</div>
          <div className="batch-pull-stack">
            <label className="checkbox-label">
              <input type="checkbox" checked={makeWhite} onChange={(e) => setMakeWhite(e.target.checked)} />
              Recolor non-white logos to white
            </label>
            {source === 'libraries' && (
              <label className="checkbox-label" title="Skip arr items whose status is announced/upcoming/tba — TMDB rarely has art for them yet. Applies to Sonarr/Radarr items only; media-server items are already in your library.">
                <input type="checkbox" checked={skipUnreleased} onChange={(e) => setSkipUnreleased(e.target.checked)} />
                Skip unreleased items (arr only)
              </label>
            )}
            <label className="checkbox-label" title="Ignore what the scope already has: re-download every selected type for every item, archiving replaced files to the IDarr duplicates folder. Off = only fetch what's missing.">
              <input type="checkbox" checked={forceRefetch} onChange={(e) => setForceRefetch(e.target.checked)} />
              Re-fetch everything (overwrite existing)
            </label>
            <label className="checkbox-label">
              <input type="checkbox" checked={syncAfter} onChange={(e) => setSyncAfter(e.target.checked)} />
              Run IDarr &amp; upload after
            </label>
            <label className="checkbox-label">
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              Dry run (report only)
            </label>
            <label className="row">
              Min backdrop width
              <input type="number" value={minWidth} onChange={(e) => setMinWidth(e.target.value)} className="batch-pull-min-width" />
            </label>
          </div>
        </div>
      </div>

      {job && (
        <span className={running ? 'text-accent' : job.status === 'failed' ? 'text-danger' : 'text-success'}>
          {running ? `${job.progress ?? 0}% — ${job.message || 'working…'}` : `Last run — ${job.status}: ${job.message || ''}`}
        </span>
      )}
      {syncTargetIndex == null && <small className="text-warning">Pick an artwork scope at the top first.</small>}
    </div>
  )
}
