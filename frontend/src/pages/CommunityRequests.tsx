import { useState, useEffect, useCallback, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Globe, ExternalLink, Search, Upload, LogOut, Loader2, Check, Info, Plus, MessageSquare, ListChecks } from 'lucide-react'
import { getCommunityRequests, type CommunityRequest } from '../api/community'
import { getSettings } from '../api/client'
import { checkTmdbPosterAvailability, type PosterAvailability } from '../api/makerTools'
import { useDiscordAuth } from '../hooks/useDiscordAuth'
import { useUnmatched } from '../contexts/UnmatchedContext'
import { type PsdConfig, derivePsdConfig } from '../components/maker-tools/TmdbItemCard'
import RequestItemCard, { getStyleLabel, type CardMediaType } from '../components/community/RequestItemCard'
import ListsView from '../components/community/ListsView'
import { useIdarrQuickAdd } from '../components/community/useIdarrQuickAdd'
import NewCommunityRequestModal from '../components/poster-manager/NewCommunityRequestModal'
import { useToast } from '../components/Toast'
import './CommunityRequests.css'

type MediaTypeFilter = 'all' | 'movie' | 'show' | 'season' | 'collection'
type StatusFilter = 'active' | 'all' | 'pending' | 'in_progress' | 'fulfilled' | 'rejected'
type SortOrder = 'newest' | 'oldest'
type PageTab = 'requests' | 'lists'

const PAGE_TAB_STORAGE_KEY = 'posterflow.community.activeTab'

const MEDIA_TYPE_TABS: { key: MediaTypeFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'movie', label: 'Movies' },
  { key: 'show', label: 'Shows' },
  { key: 'season', label: 'Seasons' },
  { key: 'collection', label: 'Collections' },
]

// Stable fingerprint of a dropped file set, used to skip a duplicate IDarr add
// when the same files are re-submitted (e.g. via the Retry button).
function filesSignature(files: File[]): string {
  return files.map((f) => `${f.name}:${f.size}:${f.lastModified}`).sort().join('|')
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
  const { showToast } = useToast()
  const { isConnected, isMaker, isOwner, username, discordUserId, connecting, connectError, login, logout, uploadPoster, updateRequestStatus } = useDiscordAuth()
  const { refreshCommunityRequestCount } = useUnmatched()
  // Latest fetchRequests, so a failed claim can refresh the (possibly stale) list.
  const fetchRequestsRef = useRef<(() => void) | null>(null)
  const [requests, setRequests] = useState<CommunityRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaTypeFilter>('all')
  const [status, setStatus] = useState<StatusFilter>('active')
  const [sortOrder, setSortOrder] = useState<SortOrder>('newest')
  // Separate from the status/sort filters: show all requests or only the connected user's own.
  const [ownerFilter, setOwnerFilter] = useState<'all' | 'mine'>('all')
  // Per-card upload state: requestId → 'uploading' | 'done' | Error
  const [uploadStates, setUploadStates] = useState<Map<string, 'uploading' | 'done' | string>>(new Map())
  // Per-card action state (claim/complete/reject): requestId → 'loading' | error string
  const [actionStates, setActionStates] = useState<Map<string, 'loading' | string>>(new Map())
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  const [makerInfoOpen, setMakerInfoOpen] = useState(false)
  // Per-card archive-thread state: requestId → 'loading' | 'done' | error string
  const [archiveStates, setArchiveStates] = useState<Map<string, 'loading' | 'done' | string>>(new Map())
  const [archiveConfirm, setArchiveConfirm] = useState<{ requestId: string; message: string } | null>(null)
  const [claimConflict, setClaimConflict] = useState<string | null>(null)   // message shown when a claim fails (already claimed)

  const [psdConfig, setPsdConfig] = useState<PsdConfig>({ exportFolder: '', templatePath: '', openPhotopea: false, imageExportFolder: '' })
  const [posterAvailability, setPosterAvailability] = useState<Record<number, PosterAvailability>>({})
  const [posterAvailabilityChecked, setPosterAvailabilityChecked] = useState(false)
  const [newRequestModalOpen, setNewRequestModalOpen] = useState(false)
  const [tmdbApiKeyConfigured, setTmdbApiKeyConfigured] = useState(false)
  // Top-level page tab: the requests list or the published worklists.
  const [pageTab, setPageTab] = useState<PageTab>(() => {
    const saved = localStorage.getItem(PAGE_TAB_STORAGE_KEY)
    return saved === 'lists' ? 'lists' : 'requests'
  })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const uploadTargetRef = useRef<string | null>(null)
  // requestId → signature of the file set already handed to IDarr, so a Retry
  // of the same files doesn't add them to IDarr twice. Cleared when the card resets.
  const idarrSentRef = useRef<Map<string, string>>(new Map())
  // Shared "Image Drop also adds to IDarr" behaviour (also used by the Lists tab).
  const { enabled: idarrQuickAddEnabled, setEnabled: setIdarrQuickAddEnabled, doIdarrUpload, targetOptions: idarrTargets, selectedTargetValue: idarrTarget, setSelectedTarget: setIdarrTarget } = useIdarrQuickAdd()

  useEffect(() => {
    localStorage.setItem(PAGE_TAB_STORAGE_KEY, pageTab)
  }, [pageTab])

  useEffect(() => {
    getSettings().then((settings) => {
      setPsdConfig(derivePsdConfig(settings))
      setTmdbApiKeyConfigured(!!(settings.tmdb_api_key || '').trim())
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
    setPosterAvailabilityChecked(false)
    checkTmdbPosterAvailability(items)
      .then((availability) => {
        setPosterAvailability(availability)
        setPosterAvailabilityChecked(true)
      })
      .catch(() => {})
  }, [requests])

  const handleUploadClick = useCallback((requestId: string) => {
    uploadTargetRef.current = requestId
    fileInputRef.current?.click()
  }, [])

  const DISCORD_MAX_FILES = 10

  // Post one batch to Discord, auto-retrying transient failures (rate limits,
  // network blips) with a short backoff before surfacing the error to the user.
  const uploadBatchWithRetry = useCallback(async (requestId: string, batch: File[]) => {
    const MAX_ATTEMPTS = 3
    for (let attempt = 1; ; attempt++) {
      try {
        await uploadPoster(requestId, batch)
        return
      } catch (err) {
        if (attempt >= MAX_ATTEMPTS) throw err
        await new Promise((resolve) => setTimeout(resolve, 1500 * attempt))
      }
    }
  }, [uploadPoster])

  const doUpload = useCallback(async (requestId: string, files: File[]) => {
    setUploadStates((prev) => new Map(prev).set(requestId, 'uploading'))

    // IDarr is the maker's own local pipeline, independent of Discord. Kick it
    // off once, up front, so the dropped files are added even if the Discord
    // post later fails or is only partially posted. Skip it when the same files
    // were already sent (a Retry after a failed Discord post) to avoid duplicates.
    if (idarrQuickAddEnabled) {
      const signature = filesSignature(files)
      if (idarrSentRef.current.get(requestId) !== signature) {
        idarrSentRef.current.set(requestId, signature)
        void doIdarrUpload(files)
      }
    }

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
        await uploadBatchWithRetry(requestId, files.slice(i, i + DISCORD_MAX_FILES))
      }
      const label = files.length > 1 ? `Posted ${files.length}!` : 'Posted!'
      setUploadStates((prev) => new Map(prev).set(requestId, label))
      // Reset after a delay so the button can be used again. Clearing the IDarr
      // guard lets a later, deliberate re-upload of the same files add them again.
      setTimeout(() => {
        idarrSentRef.current.delete(requestId)
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
  }, [uploadBatchWithRetry, idarrQuickAddEnabled, doIdarrUpload])

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const allFiles = Array.from(e.target.files ?? [])
    const requestId = uploadTargetRef.current
    if (!allFiles.length || !requestId) return
    e.target.value = ''
    doUpload(requestId, allFiles)
  }, [doUpload])

  const handleStatusAction = useCallback(async (requestId: string, action: 'claim' | 'complete' | 'reject' | 'release' | 'remove', message?: string) => {
    setActionStates((prev) => new Map(prev).set(requestId, 'loading'))
    try {
      const result = await updateRequestStatus(requestId, action, message)
      if (action === 'remove') {
        // Removed entirely — drop it from the list.
        setRequests((prev) => prev.filter((r) => r.id !== requestId))
      } else {
        setRequests((prev) =>
          prev.map((r) =>
            r.id === requestId
              ? {
                  ...r,
                  status: result.status as CommunityRequest['status'],
                  claimed_by: result.claimed_by,
                  // The API omits claimed_by_discord_id; on claim the claimer is the
                  // current user, on release it's cleared, otherwise leave it.
                  claimed_by_discord_id: action === 'claim' ? discordUserId : action === 'release' ? null : r.claimed_by_discord_id,
                  fulfilled_by: result.fulfilled_by,
                }
              : r
          )
        )
      }
      setActionStates((prev) => {
        const next = new Map(prev)
        next.delete(requestId)
        return next
      })
      void refreshCommunityRequestCount()
      if (action === 'complete') fetchRequestsRef.current?.()   // re-fetch so the list re-sorts/re-filters
    } catch (err) {
      const msg = err instanceof Error ? err.message : `${action} failed`
      if (action === 'claim') {
        // Usually means someone claimed it first while this page was stale. Make it
        // obvious (toast + popup) and refresh so the now-claimed request updates.
        setActionStates((prev) => { const next = new Map(prev); next.delete(requestId); return next })
        showToast(msg, 'error')
        setClaimConflict(msg)
        fetchRequestsRef.current?.()
      } else {
        setActionStates((prev) => new Map(prev).set(requestId, msg))
      }
    }
  }, [updateRequestStatus, showToast, discordUserId])

  const handleArchiveThread = useCallback(async (requestId: string, message?: string) => {
    setArchiveStates((prev) => new Map(prev).set(requestId, 'loading'))
    setArchiveConfirm(null)
    try {
      await updateRequestStatus(requestId, 'close', message)
      setArchiveStates((prev) => new Map(prev).set(requestId, 'done'))
    } catch (err) {
      setArchiveStates((prev) =>
        new Map(prev).set(requestId, err instanceof Error ? err.message : 'Archive failed')
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
    fetchRequestsRef.current = fetchRequests
    fetchRequests()
  }, [fetchRequests])

  // "My requests" is applied client-side so it composes with the status/sort filters.
  const visibleRequests =
    ownerFilter === 'mine' && discordUserId
      ? requests.filter((req) => req.requested_by_discord_id === discordUserId)
      : requests

  return (
    <>
    <div className="page-container community-requests">
      <div className="community-header">
        <div className="community-header-top">
          <h1>Community Requests</h1>
          {pageTab === 'requests' && (
            <div className="community-rate-limit-notice">
              Limited to <strong>5 new requests per day</strong>.
            </div>
          )}
        </div>
        <p>
          Poster makers use this list to prioritize their work. Submit new requests using the{' '}
          <strong>New Request</strong> button or from the <strong>Unmatched Assets</strong> tab in Poster Manager.
        </p>
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

      {/* Page tabs — Requests vs published Lists */}
      <div className="community-page-tabs">
        <button className={pageTab === 'requests' ? 'active' : ''} onClick={() => setPageTab('requests')}>
          <MessageSquare size={16} />
          Requests
        </button>
        <button className={pageTab === 'lists' ? 'active' : ''} onClick={() => setPageTab('lists')}>
          <ListChecks size={16} />
          Lists
        </button>
      </div>

      {pageTab === 'requests' && (
      <>
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
        {isConnected && discordUserId && (
          <div className="community-owner-toggle">
            <button
              className={`community-tab-btn${ownerFilter === 'all' ? ' active' : ''}`}
              onClick={() => setOwnerFilter('all')}
            >
              All Requests
            </button>
            <button
              className={`community-tab-btn${ownerFilter === 'mine' ? ' active' : ''}`}
              onClick={() => setOwnerFilter('mine')}
            >
              My Requests
            </button>
          </div>
        )}
        <div className="community-toolbar-actions">
          <button className="community-refresh-btn" onClick={fetchRequests} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin-icon' : ''} />
            Refresh
          </button>
          <button className="community-new-request-btn" onClick={() => setNewRequestModalOpen(true)}>
            <Plus size={14} />
            New Request
          </button>
        </div>
      </div>

      {error && <div className="community-error">{error}</div>}

      {loading && requests.length === 0 ? (
        <div className="community-loading">
          <RefreshCw size={20} className="spin-icon" />
          <span>Loading requests…</span>
        </div>
      ) : visibleRequests.length === 0 ? (
        <div className="community-empty">
          <Globe size={48} />
          <p>{ownerFilter === 'mine' ? "You haven't submitted any requests" : 'No requests found'}</p>
          <p className="community-empty-sub">
            {ownerFilter === 'mine'
              ? 'Requests you submit will appear here.'
              : 'Submit requests from the Unmatched Assets tab in Poster Manager.'}
          </p>
        </div>
      ) : (
        <div className="community-list">
          <div className="community-list-header">
            <p className="community-count">
              {visibleRequests.length} request{visibleRequests.length !== 1 ? 's' : ''}
            </p>
            <div className="community-list-header-controls">
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
                    onChange={(e) => setIdarrQuickAddEnabled(e.target.checked)}
                  />
                  <span className="idarr-toggle-slider" />
                </span>
              </label>
            )}
            {isMaker && isConnected && idarrQuickAddEnabled && idarrTargets.length > 1 && (
              <label className="maker-idarr-target-picker">
                <span>IDarr drive</span>
                <select value={idarrTarget} onChange={(e) => setIdarrTarget(e.target.value)}>
                  {idarrTargets.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </label>
            )}
            </div>
          </div>
          {visibleRequests.map((req) => {
            const showMakerTools = isMaker && isConnected && req.tmdb_id != null
            const infoLines = (
              <>
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
              </>
            )
            const actions = (
              <>
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
                    // Only the maker who claimed it (or the server owner) may complete it.
                    const canComplete = req.claimed_by_discord_id === discordUserId || isOwner
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
                            {req.status === 'in_progress' && canComplete && (
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
                            {req.status === 'in_progress' && canComplete && (
                              <button
                                type="button"
                                className="request-action-btn"
                                disabled={actionLoading}
                                title="Release your claim (back to open)"
                                onClick={() => handleStatusAction(req.id, 'release')}
                              >
                                <span>Release</span>
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
                  {/* Archive Thread button — requester closes the thread on a completed request */}
                  {isConnected && discordUserId && req.requested_by_discord_id === discordUserId && req.discord_thread_url && req.status === 'fulfilled' && (() => {
                    const archiveState = archiveStates.get(req.id)
                    const isLoading = archiveState === 'loading'
                    const isDone = archiveState === 'done'
                    const archiveError = typeof archiveState === 'string' && archiveState !== 'loading' && archiveState !== 'done' ? archiveState : null
                    return (
                      <div className="request-status-actions">
                        <button
                          type="button"
                          className={`request-action-btn action-reject${isDone ? ' upload-done' : ''}`}
                          title={isDone ? 'Discord thread archived' : 'Archive the Discord thread for this request'}
                          disabled={isLoading || isDone}
                          onClick={() => setArchiveConfirm({ requestId: req.id, message: '' })}
                        >
                          {isLoading ? <Loader2 size={11} className="spin-icon" /> : isDone ? <Check size={11} /> : null}
                          <span>{isDone ? 'Archived' : 'Archive'}</span>
                        </button>
                        {archiveError && (
                          <span className="request-action-error" title={archiveError}>{archiveError}</span>
                        )}
                      </div>
                    )
                  })()}
                  {/* Remove button — requester or server owner; hidden once fulfilled */}
                  {isConnected && discordUserId && (req.requested_by_discord_id === discordUserId || isOwner) && req.status !== 'fulfilled' && (() => {
                    const as_ = actionStates.get(req.id)
                    const actionLoading = as_ === 'loading'
                    const removeError = typeof as_ === 'string' && as_ !== 'loading' ? as_ : null
                    const isRequester = req.requested_by_discord_id === discordUserId
                    return (
                      <div className="request-status-actions">
                        <button
                          type="button"
                          className="request-action-btn action-reject"
                          disabled={actionLoading}
                          title={isRequester ? 'Remove your request' : 'Remove this request (server owner)'}
                          onClick={() => handleStatusAction(req.id, 'remove')}
                        >
                          {actionLoading ? <Loader2 size={11} className="spin-icon" /> : null}
                          <span>Remove</span>
                        </button>
                        {removeError && (
                          <span className="request-action-error" title={removeError}>{removeError}</span>
                        )}
                      </div>
                    )
                  })()}
              </>
            )
            return (
              <RequestItemCard
                key={req.id}
                id={req.id}
                posterPath={req.poster_path}
                title={req.title}
                year={req.year}
                seasonLabels={getSeasonLabel(req) ? [getSeasonLabel(req) as string] : []}
                mediaType={req.media_type as CardMediaType}
                styleLabel={getStyleLabel(req.style_tags)}
                status={req.status}
                notes={req.notes}
                createdAt={req.created_at}
                imdbId={req.imdb_id}
                tvdbId={req.tvdb_id}
                tmdbId={req.tmdb_id}
                infoLines={infoLines}
                actions={actions}
                isMaker={isMaker}
                showMakerTools={showMakerTools}
                psdConfig={psdConfig}
                posterAvailability={req.tmdb_id != null ? posterAvailability[req.tmdb_id] : undefined}
                posterAvailabilityChecked={posterAvailabilityChecked}
                dragOver={dragOverId === req.id}
                onDragEnter={() => setDragOverId(req.id)}
                onDragLeave={() => setDragOverId(null)}
                onDrop={(e) => handleDrop(e, req.id)}
              />
            )
          })}
        </div>
      )}
      </>
      )}
      {pageTab === 'lists' && <ListsView />}
    </div>

    {/* ── Archive thread confirm modal ──────────────────────────────────────────────────────────────── */}
    {archiveConfirm && (() => {
      const PRESETS = [
        'Looks great! Thanks for the poster! 🎉',
        "You're the best! Thank you! 🙌",
        'Perfect, exactly what I was looking for! ❤️',
        'Amazing work, really appreciate it! 🔥',
      ]
      const archiveState = archiveStates.get(archiveConfirm.requestId)
      const isLoading = archiveState === 'loading'

      return (
        <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !isLoading) setArchiveConfirm(null) }}>
          <div className="modal-content schedule-modal close-confirm-modal">
            <div className="modal-header">
              <h2>Archive Discord Thread</h2>
              <button className="modal-close" onClick={() => setArchiveConfirm(null)} disabled={isLoading}>×</button>
            </div>
            <div className="modal-body">
              <p className="close-confirm-hint">
                This will lock and archive the Discord thread. You can optionally leave a message for the maker.
              </p>
              <div className="close-confirm-presets">
                {PRESETS.map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={`close-confirm-preset${archiveConfirm.message === p ? ' selected' : ''}`}
                    onClick={() => setArchiveConfirm((s) => s ? { ...s, message: s.message === p ? '' : p } : s)}
                  >
                    {p}
                  </button>
                ))}
              </div>
              <textarea
                className="request-notes-textarea"
                placeholder="Or type a custom message… (optional)"
                rows={3}
                maxLength={300}
                value={PRESETS.includes(archiveConfirm.message) ? '' : archiveConfirm.message}
                onChange={(e) => setArchiveConfirm((s) => s ? { ...s, message: e.target.value } : s)}
              />
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setArchiveConfirm(null)} disabled={isLoading}>
                Cancel
              </button>
              <button
                className="btn-primary"
                disabled={isLoading}
                onClick={() => handleArchiveThread(archiveConfirm.requestId, archiveConfirm.message.trim() || undefined)}
              >
                {isLoading ? <Loader2 size={13} className="spin-icon" /> : '🔒'}
                {' '}Archive Thread
              </button>
            </div>
          </div>
        </div>
      )
    })()}
    {/* ── Already-claimed popup ───────────────────────────────────────────── */}
    {claimConflict && (
      <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget) setClaimConflict(null) }}>
        <div className="modal-content schedule-modal" style={{ maxWidth: '380px' }}>
          <div className="modal-header">
            <h2>Already claimed</h2>
            <button className="modal-close" onClick={() => setClaimConflict(null)}>×</button>
          </div>
          <div className="modal-body">
            <p style={{ margin: 0 }}>{claimConflict}</p>
            <p className="muted" style={{ marginTop: '0.75rem' }}>
              The list has been refreshed to show its current status.
            </p>
          </div>
          <div className="modal-footer">
            <button className="btn-primary" onClick={() => setClaimConflict(null)}>Got it</button>
          </div>
        </div>
      </div>
    )}
    {/* ── New Community Request modal ─────────────────────────────────────── */}
    {newRequestModalOpen && (
      <NewCommunityRequestModal
        tmdbApiKeyConfigured={tmdbApiKeyConfigured}
        onClose={() => setNewRequestModalOpen(false)}
      />
    )}
    </>
  )
}
