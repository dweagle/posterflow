import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Globe, ExternalLink, Search, Upload, LogOut, Loader2, Check, Info } from 'lucide-react'
import { getCommunityRequests, type CommunityRequest } from '../api/community'
import { getMakerIdarrConfig, uploadMakerIdarrFiles, startIdarr, getSettings } from '../api/client'
import { checkTmdbPosterAvailability, type PosterAvailability } from '../api/makerTools'
import { useDiscordAuth } from '../hooks/useDiscordAuth'
import { useUnmatched } from '../contexts/UnmatchedContext'
import TmdbItemCard, { type PsdConfig } from '../components/maker-tools/TmdbItemCard'
import './CommunityRequests.css'

type MediaTypeFilter = 'all' | 'movie' | 'show' | 'season' | 'collection'
type StatusFilter = 'active' | 'all' | 'pending' | 'in_progress' | 'fulfilled' | 'rejected'
type SortOrder = 'newest' | 'oldest'

const MEDIA_TYPE_TABS: { key: MediaTypeFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'movie', label: 'Movies' },
  { key: 'show', label: 'Shows' },
  { key: 'season', label: 'Seasons' },
  { key: 'collection', label: 'Collections' },
]

function getTmdbLink(req: CommunityRequest): string {
  if (!req.tmdb_id) return ''
  if (req.media_type === 'movie') return `https://www.themoviedb.org/movie/${req.tmdb_id}`
  if (req.media_type === 'collection') return `https://www.themoviedb.org/collection/${req.tmdb_id}`
  return `https://www.themoviedb.org/tv/${req.tmdb_id}`
}

function getTvdbLink(req: CommunityRequest): string {
  if (!req.tvdb_id) return ''
  return `https://thetvdb.com/dereferrer/series/${req.tvdb_id}`
}

function formatRequestDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
    + ' · '
    + d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function getSeasonLabel(req: CommunityRequest): string | null {
  if (req.season_number != null) return `S${req.season_number}`
  if (req.media_type === 'season' && req.notes?.startsWith('Seasons: ')) {
    const parts = req.notes.split('\n')[0].slice('Seasons: '.length)
    return parts
      .split(', ')
      .map((s) => `S${s.trim()}`)
      .join(', ')
  }
  return null
}

export default function CommunityRequests() {
  const navigate = useNavigate()
  const { isConnected, isMaker, username, connecting, connectError, login, logout, uploadPoster, updateRequestStatus } = useDiscordAuth()
  const { refreshCommunityRequestCount } = useUnmatched()
  const [requests, setRequests] = useState<CommunityRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaTypeFilter>('all')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest')
  // Per-card upload state: requestId → 'uploading' | 'done' | Error
  const [uploadStates, setUploadStates] = useState<Map<string, 'uploading' | 'done' | string>>(new Map())
  // Per-card action state (claim/complete/reject): requestId → 'loading' | error string
  const [actionStates, setActionStates] = useState<Map<string, 'loading' | string>>(new Map())
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [makerInfoOpen, setMakerInfoOpen] = useState(false)

  const [psdConfig, setPsdConfig] = useState<PsdConfig>({ exportFolder: '', templatePath: '', openPhotopea: false })
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  const [idarrQuickAddEnabled, setIdarrQuickAddEnabled] = useState(() =>
    localStorage.getItem('posterflow.communityRequests.idarrQuickAdd') === 'true'
  )
  const fileInputRef = useRef<HTMLInputElement>(null)
  const uploadTargetRef = useRef<string | null>(null)

  useEffect(() => {
    getSettings().then((settings) => {
      setPsdConfig({
        exportFolder: (settings.psd_export_folder || '').trim(),
        templatePath: (settings.psd_template_path || '').trim(),
        openPhotopea: (settings.psd_open_photopea || '').trim().toLowerCase() === 'true',
      })
    }).catch(() => {})
  }, [])

  // Fetch poster availability whenever the visible request list changes
  useEffect(() => {
    const items = requests
      .filter((r) => r.tmdb_id != null)
      .map((r) => ({
        tmdb_id: r.tmdb_id!,
        title: r.title,
        year: r.year ? String(r.year) : '',
        media_type: r.media_type === 'movie' ? 'movie' : r.media_type === 'collection' ? 'collection' : ('tv' as const),
      }))
    if (items.length === 0) return
    checkTmdbPosterAvailability(items)
      .then(setPosterAvailability)
      .catch(() => {})
  }, [requests])

  const handleUploadClick = useCallback((requestId: string) => {
    uploadTargetRef.current = requestId
    fileInputRef.current?.click()
  }, [])

  const DISCORD_MAX_FILES = 10

  const doIdarrUpload = useCallback(async (files: File[]) => {
    try {
      const config = await getMakerIdarrConfig()
      const syncTargets = Array.isArray(config.sync_targets) ? config.sync_targets : []
      if (!syncTargets.length) return

      const storedKey = 'posterflow.idarr.selectedSyncTarget'
      const storedValue = localStorage.getItem(storedKey)
      let resolvedIndex = -1
      if (storedValue) {
        resolvedIndex = syncTargets.findIndex((t) => {
          const scopeToken = String(t.scope_token || '').trim()
          if (scopeToken) return `scope:${scopeToken}` === storedValue
          return `${String(t.personal_drive_id || '')}::${String(t.source_dir || '')}::${String(t.label || '')}` === storedValue
        })
      }
      const syncTargetIndex = resolvedIndex >= 0 ? resolvedIndex : 0

      const response = await uploadMakerIdarrFiles(syncTargetIndex, files)
      if (config.auto_rename_quick_add && response.uploaded_count > 0) {
        await startIdarr(false, syncTargetIndex, response.uploaded, config.auto_upload_quick_add)
      }
    } catch {
      // Silently ignore — Discord upload was the primary action
    }
  }, [])

  const doUpload = useCallback(async (requestId: string, files: File[]) => {
    setUploadStates((prev) => new Map(prev).set(requestId, 'uploading'))
    try {
      // Send in batches of DISCORD_MAX_FILES (Discord limit per message).
      // Wait 1.5 s between batches to avoid Discord channel rate limits.
      const totalBatches = Math.ceil(files.length / DISCORD_MAX_FILES)
      for (let i = 0; i < files.length; i += DISCORD_MAX_FILES) {
        if (i > 0) await new Promise((resolve) => setTimeout(resolve, 1500))
        if (totalBatches > 1) {
          const batchNum = Math.floor(i / DISCORD_MAX_FILES) + 1
          setUploadStates((prev) => new Map(prev).set(requestId, `uploading-${batchNum}/${totalBatches}`))
        }
        await uploadPoster(requestId, files.slice(i, i + DISCORD_MAX_FILES))
      }
      const label = files.length > 1 ? `Posted ${files.length}!` : 'Posted!'
      setUploadStates((prev) => new Map(prev).set(requestId, label))
      if (idarrQuickAddEnabled) {
        void doIdarrUpload(files)
      }
      // Reset after a delay so the button can be used again
      setTimeout(() => {
        setUploadStates((prev) => {
          const next = new Map(prev)
          next.delete(requestId)
          return next
        })
      }, 3000)
    } catch (err) {
      setUploadStates((prev) =>
        new Map(prev).set(requestId, err instanceof Error ? err.message : 'Upload failed')
      )
    }
  }, [uploadPoster, idarrQuickAddEnabled, doIdarrUpload])

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const allFiles = Array.from(e.target.files ?? [])
    const requestId = uploadTargetRef.current
    if (!allFiles.length || !requestId) return
    e.target.value = ''
    doUpload(requestId, allFiles)
  }, [doUpload])

  const handleStatusAction = useCallback(async (requestId: string, action: 'claim' | 'complete' | 'reject') => {
    setActionStates((prev) => new Map(prev).set(requestId, 'loading'))
    try {
      const result = await updateRequestStatus(requestId, action)
      setRequests((prev) =>
        prev.map((r) =>
          r.id === requestId
            ? {
                ...r,
                status: result.status as CommunityRequest['status'],
                claimed_by: result.claimed_by,
                fulfilled_by: result.fulfilled_by,
              }
            : r
        )
      )
      setActionStates((prev) => {
        const next = new Map(prev)
        next.delete(requestId)
        return next
      })
      void refreshCommunityRequestCount()
    } catch (err) {
      setActionStates((prev) =>
        new Map(prev).set(requestId, err instanceof Error ? err.message : `${action} failed`)
      )
    }
  }, [updateRequestStatus])

  const handleDrop = useCallback((e: React.DragEvent, requestId: string) => {
    e.preventDefault()
    setDragOverId(null)
    const allFiles = Array.from(e.dataTransfer.files)
    if (!allFiles.length) return
    doUpload(requestId, allFiles)
  }, [doUpload])

  const fetchRequests = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string> = {}
      if (mediaType !== 'all') params.media_type = mediaType
      if (status !== 'active') params.status = status
      const data = await getCommunityRequests(params)
      const isActive = (s: string) => s === 'pending' || s === 'in_progress'
      const sorted = [...data.requests].sort((a, b) => {
        if (status === 'active') {
          const aActive = isActive(a.status) ? 0 : 1
          const bActive = isActive(b.status) ? 0 : 1
          if (aActive !== bActive) return aActive - bActive
        }
        return sortOrder === 'newest'
          ? new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
          : new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      })
      setRequests(sorted)
    } catch {
      setError('Failed to load community requests. Check your network connection.')
    } finally {
      setLoading(false)
    }
  }, [mediaType, status, sortOrder])

  useEffect(() => {
    fetchRequests()
  }, [fetchRequests])

  return (
    <div className="page-container community-requests">
      <div className="community-header">
        <h1>Community Requests</h1>
        <p>
          Poster makers use this list to prioritize their work. Submit new requests from the{' '}
          <strong>Unmatched Assets</strong> tab in Poster Manager.
        </p>
      </div>

      <div className="community-rate-limit-notice">
        Limited to <strong>5 new requests per day</strong>.
      </div>

      {/* Maker connect bar */}
      <div className="maker-connect-bar">
        {!isConnected ? (
          <>
            <span className="maker-connect-hint">
              Are you a poster maker or community member?
              <button
                className="maker-info-btn"
                aria-label="What does this do?"
                onClick={() => setMakerInfoOpen((o) => !o)}
              >
                <Info size={13} />
              </button>
            </span>
            <button className="maker-connect-btn" onClick={login} disabled={connecting}>
              {connecting ? <Loader2 size={13} className="spin-icon" /> : null}
              {connecting ? 'Connecting…' : 'Connect with Discord'}
            </button>
            {makerInfoOpen && (
              <div className="maker-info-popdown">
                <p>Connecting your Discord account has two benefits depending on your role:</p>
                <ul>
                  <li>
                    <strong>Community members</strong> — your Discord username is automatically used as your name when submitting poster requests, so you never have to type it manually.
                  </li>
                  <li>
                    <strong>Poster makers</strong> — if you have the <strong>Poster Maker</strong> role in the PosterFlow Discord server, an <strong>Upload Poster</strong> button is unlocked on each request card so you can post your work directly to the request's Discord thread.
                  </li>
                </ul>
                <p>Here is exactly what is requested and why:</p>
                <ul>
                  <li><strong>Your username</strong> — used as your name on requests and uploaded posters.</li>
                  <li><strong>Your role in the PosterFlow Discord server only</strong> — checked once to confirm Poster Maker status. Roles from any other server are never read or stored.</li>
                </ul>
                <p>What we do <strong>not</strong> access:</p>
                <ul>
                  <li>Your email address</li>
                  <li>Any other Discord server you are in</li>
                  <li>Your friends list, DMs, or messages</li>
                  <li>Anything else — the scopes requested are <code>identify</code> and <code>guilds.members.read</code> only</li>
                </ul>
                <p className="maker-info-privacy">The resulting token is stored only in your browser's local storage, never sent to PosterFlow's servers, and expires after 30 days.</p>
              </div>
            )}
            {connectError && <span className="maker-connect-error">{connectError}</span>}
          </>
        ) : (
          <>
            <span className="maker-connect-status">
              {isMaker ? '🎨 Signed in as' : '👤 Signed in as'}
              {' '}<strong>{username}</strong>
              {!isMaker && <span className="maker-no-role"> — no Maker role</span>}
            </span>
            <button className="maker-disconnect-btn" onClick={logout}>
              <LogOut size={12} />
              Disconnect
            </button>
          </>
        )}
      </div>

      {/* Hidden file input shared across all upload buttons */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple
        style={{ display: 'none' }}
        onChange={handleFileChange}
      />

      <div className="community-toolbar">
        <div className="community-type-tabs">
          {MEDIA_TYPE_TABS.map((tab) => (
            <button
              key={tab.key}
              className={`community-tab-btn${mediaType === tab.key ? ' active' : ''}`}
              onClick={() => setMediaType(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="community-filters">
          <div className="community-filter-group">
            <label>Status</label>
            <select value={status} onChange={(e) => setStatus(e.target.value as StatusFilter)}>
              <option value="active">Active</option>
              <option value="all">All</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="fulfilled">Fulfilled</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>
          <div className="community-filter-group">
            <label>Sort</label>
            <select value={sortOrder} onChange={(e) => setSortOrder(e.target.value as SortOrder)}>
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
            </select>
          </div>
        </div>
        <button className="community-refresh-btn" onClick={fetchRequests} disabled={loading}>
          <RefreshCw size={14} className={loading ? 'spin-icon' : ''} />
          Refresh
        </button>
      </div>

      {error && <div className="community-error">{error}</div>}

      {loading && requests.length === 0 ? (
        <div className="community-loading">
          <RefreshCw size={20} className="spin-icon" />
          <span>Loading requests…</span>
        </div>
      ) : requests.length === 0 ? (
        <div className="community-empty">
          <Globe size={48} />
          <p>No requests found</p>
          <p className="community-empty-sub">
            Submit requests from the Unmatched Assets tab in Poster Manager.
          </p>
        </div>
      ) : (
        <div className="community-list">
          <div className="community-list-header">
            <p className="community-count">
              {requests.length} request{requests.length !== 1 ? 's' : ''}
            </p>
            {isMaker && isConnected && (
              <label className="maker-idarr-toggle-label">
                <span>Image Drop also adds to IDarr</span>
                <span className="maker-idarr-toggle-info-wrap">
                  <span className="maker-idarr-info-icon"><Info size={12} /></span>
                  <span className="maker-idarr-tooltip">
                    When enabled, any image you drop or upload to a request card is also sent to your IDarr quick add folder and processed — identical to dragging files onto the IDarr sidebar icon.
                  </span>
                </span>
                <span className="idarr-toggle-control">
                  <input
                    type="checkbox"
                    checked={idarrQuickAddEnabled}
                    onChange={(e) => {
                      const next = e.target.checked
                      setIdarrQuickAddEnabled(next)
                      localStorage.setItem('posterflow.communityRequests.idarrQuickAdd', String(next))
                    }}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </label>
            )}
          </div>
          {requests.map((req) => {
            const tmdbLink = getTmdbLink(req)
            const tvdbLink = getTvdbLink(req)
            const showMakerTools = isMaker && isConnected && req.tmdb_id != null
            return (
              <div key={req.id} className="community-request-wrapper">
              <div
                className={`community-request-item${dragOverId === req.id ? ' drag-over' : ''}`}
                onDragOver={isMaker ? (e) => { e.preventDefault(); setDragOverId(req.id) } : undefined}
                onDragLeave={isMaker ? () => setDragOverId(null) : undefined}
                onDrop={isMaker ? (e) => handleDrop(e, req.id) : undefined}
              >
                <div className="request-poster">
                  {req.poster_path ? (
                    <img src={req.poster_path} alt="" loading="lazy" />
                  ) : (
                    <div className="request-poster-empty" />
                  )}
                </div>

                <div className="request-info">
                  <div className="request-title-row">
                    <span className="request-title">{req.title}</span>
                    {req.year && <span className="request-year">({req.year})</span>}
                    {(() => {
                      const lbl = getSeasonLabel(req)
                      return lbl ? <span className="request-season">{lbl}</span> : null
                    })()}
                    <span className={`request-type-badge type-${req.media_type}`}>{req.media_type}</span>
                    <span className={`request-status-badge status-${req.status}`}>
                      {req.status === 'in_progress' ? 'in progress' : req.status}
                    </span>
                  </div>
                  {(tmdbLink || tvdbLink) && (
                    <div className="request-id-links">
                      {tmdbLink && (
                        <a className="request-tmdb-link" href={tmdbLink} target="_blank" rel="noopener noreferrer" title="Open on TMDB">
                          <ExternalLink size={11} />
                          TMDB
                        </a>
                      )}
                      {tvdbLink && (
                        <a className="request-tmdb-link request-tvdb-link" href={tvdbLink} target="_blank" rel="noopener noreferrer" title="Open on TheTVDB">
                          <ExternalLink size={11} />
                          TVDB
                        </a>
                      )}
                    </div>
                  )}
                  {req.requested_by && (
                    <div className="request-maker-info request-maker-requested">
                      👤 Requested by <strong>{req.requested_by}</strong>
                    </div>
                  )}
                  {req.status === 'in_progress' && req.claimed_by && (
                    <div className="request-maker-info request-maker-claimed">
                      🎨 Claimed by <strong>{req.claimed_by}</strong>
                    </div>
                  )}
                  {req.status === 'fulfilled' && req.fulfilled_by && (
                    <div className="request-maker-info request-maker-fulfilled">
                      ✅ Completed by <strong>{req.fulfilled_by}</strong>
                    </div>
                  )}
                  {req.notes && (
                    <div className="request-notes">{req.notes}</div>
                  )}
                  <div className="request-timestamp">{formatRequestDate(req.created_at)}</div>
                </div>

                <div className="request-maker-actions-group">
                  {showMakerTools && (
                    <div className="request-maker-tools-panel">
                      <TmdbItemCard
                        item={{
                          tmdb_id: req.tmdb_id!,
                          media_type: req.media_type === 'movie' ? 'movie' : req.media_type === 'collection' ? 'collection' : 'tv',
                          title: req.title,
                          year: req.year ? String(req.year) : '',
                          overview: '',
                          poster_url: req.poster_path || '',
                          homepage: req.tmdb_id != null
                            ? req.media_type === 'movie'
                              ? `https://www.themoviedb.org/movie/${req.tmdb_id}`
                              : req.media_type === 'collection'
                                ? `https://www.themoviedb.org/collection/${req.tmdb_id}`
                                : `https://www.themoviedb.org/tv/${req.tmdb_id}`
                            : '',
                          imdb_id: req.imdb_id,
                          tvdb_id: req.tvdb_id ?? null,
                        }}
                        psdConfig={psdConfig}
                        posterAvailability={posterAvailability[req.tmdb_id!]}
                        hidePoster
                        hideTitle
                        galleryPortalId={`gallery-portal-${req.id}`}
                      />
                      <div className={`request-drop-zone${dragOverId === req.id ? ' drop-active' : ''}`}>
                        <Upload size={22} />
                        <span>Drop poster(s) here</span>
                      </div>
                    </div>
                  )}

                  <div className="request-actions">
                  {req.discord_thread_url && (
                    <a
                      className="request-tmdb-link request-discord-link"
                      href={req.discord_thread_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="View Discord thread"
                    >
                      <ExternalLink size={11} />
                      Discord
                    </a>
                  )}
                  <button
                    type="button"
                    className="request-maker-btn"
                    title="Search in Maker Tools"
                    onClick={() => navigate('/maker-tools', { state: { tmdbSearch: req.year ? `${req.title} ${req.year}` : req.title } })}
                  >
                    <Search size={11} />
                    <span>Maker</span>
                  </button>
                  {isMaker && (() => {
                    const us = uploadStates.get(req.id)
                    const as_ = actionStates.get(req.id)
                    const actionLoading = as_ === 'loading'
                    const actionError = typeof as_ === 'string' && as_ !== 'loading' ? as_ : null
                    const canAct = req.status === 'pending' || req.status === 'in_progress'
                    const isUploading = us === 'uploading' || (typeof us === 'string' && us.startsWith('uploading-'))
                    const isPosted = typeof us === 'string' && us.startsWith('Posted')
                    const uploadLabel = isUploading
                      ? (typeof us === 'string' && us.startsWith('uploading-') ? `Uploading ${us.slice('uploading-'.length)}…` : 'Uploading…')
                      : isPosted ? us : us ? 'Retry' : 'Upload'
                    return (
                      <>
                        <button
                          type="button"
                          className={`request-upload-btn${isUploading || isPosted ? (isUploading ? '' : ' upload-done') : us ? ' upload-error' : ''}`}
                          title={typeof us === 'string' && !isUploading && !isPosted ? us : 'Upload poster(s) to Discord thread'}
                          disabled={isUploading || isPosted}
                          onClick={() => handleUploadClick(req.id)}
                        >
                          {isUploading ? <Loader2 size={11} className="spin-icon" /> :
                           isPosted ? <Check size={11} /> :
                           <Upload size={11} />}
                          <span>{uploadLabel}</span>
                        </button>
                        {canAct && (
                          <div className="request-status-actions">
                            {req.status === 'pending' && (
                              <button
                                type="button"
                                className="request-action-btn action-claim"
                                disabled={actionLoading}
                                title="Claim this request"
                                onClick={() => handleStatusAction(req.id, 'claim')}
                              >
                                {actionLoading ? <Loader2 size={11} className="spin-icon" /> : null}
                                <span>Claim</span>
                              </button>
                            )}
                            {req.status === 'in_progress' && (
                              <button
                                type="button"
                                className="request-action-btn action-complete"
                                disabled={actionLoading}
                                title="Mark as complete"
                                onClick={() => handleStatusAction(req.id, 'complete')}
                              >
                                {actionLoading ? <Loader2 size={11} className="spin-icon" /> : <Check size={11} />}
                                <span>Complete</span>
                              </button>
                            )}
                            <button
                              type="button"
                              className="request-action-btn action-reject"
                              disabled={actionLoading}
                              title="Reject this request"
                              onClick={() => handleStatusAction(req.id, 'reject')}
                            >
                              <span>Reject</span>
                            </button>
                            {actionError && (
                              <span className="request-action-error" title={actionError}>
                                {actionError}
                              </span>
                            )}
                          </div>
                        )}
                      </>
                    )
                  })()}
                  </div>
                </div>

              </div>

              {/* Gallery panel portals here when Browse Images is open */}
              <div id={`gallery-portal-${req.id}`} />
            </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
