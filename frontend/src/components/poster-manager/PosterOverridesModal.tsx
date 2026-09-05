import { useEffect, useState } from 'react'
import { Trash2 } from 'lucide-react'
import {
  PosterOverride,
  deletePosterOverride,
  getPosterOverrides,
} from '../../api/posterManager'
import { useToast } from '../Toast'
import ConfirmDialog from '../ConfirmDialog'

type PosterOverridesModalProps = {
  driveInfo: Map<string, { name: string; style?: string }>
  onClose: () => void
}

// Lists every stored poster override with a remove action.
export default function PosterOverridesModal({ driveInfo, onClose }: PosterOverridesModalProps) {
  const { showToast } = useToast()
  const [overrides, setOverrides] = useState<PosterOverride[] | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [tab, setTab] = useState<'poster' | 'artwork'>('poster')

  useEffect(() => {
    getPosterOverrides()
      .then(setOverrides)
      .catch(() => {
        setOverrides([])
        showToast('Failed to load overrides', 'error')
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const [removingAll, setRemovingAll] = useState(false)
  const [confirmRemoveAll, setConfirmRemoveAll] = useState(false)
  const handleRemoveAll = async (list: PosterOverride[]) => {
    setConfirmRemoveAll(false)
    if (list.length === 0) return
    setRemovingAll(true)
    try {
      const results = await Promise.allSettled(list.map((o) => deletePosterOverride(o.id)))
      const removed = new Set(list.filter((_, i) => results[i].status === 'fulfilled').map((o) => o.id))
      setOverrides((prev) => (prev ?? []).filter((o) => !removed.has(o.id)))
      if (results.some((r) => r.status === 'rejected')) showToast('Some overrides could not be removed', 'error')
    } finally {
      setRemovingAll(false)
    }
  }

  const handleRemove = async (id: number) => {
    setBusyId(id)
    try {
      await deletePosterOverride(id)
      setOverrides((prev) => (prev ?? []).filter((o) => o.id !== id))
    } catch {
      showToast('Failed to remove override', 'error')
    } finally {
      setBusyId(null)
    }
  }

  const scopeLabel = (ov: PosterOverride) => {
    if ((ov.domain ?? 'poster') === 'artwork') {
      if (ov.scope === 'set') return 'Artwork set'
      return ov.slot === 'logo' ? 'Logo' : ov.slot === 'background' ? 'Background' : ov.slot === 'square' ? 'Square' : 'Artwork'
    }
    if (ov.scope === 'set') return 'Whole set'
    if (ov.season == null) return 'Poster'
    return ov.season === 0 ? 'Specials' : `Season ${ov.season}`
  }

  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  const posterOverrides = (overrides ?? []).filter((o) => (o.domain ?? 'poster') === 'poster')
  const artworkOverrides = (overrides ?? []).filter((o) => (o.domain ?? 'poster') === 'artwork')
  const tabOverrides = tab === 'poster' ? posterOverrides : artworkOverrides
  const tabNoun = tab === 'poster' ? 'poster' : 'artwork'

  const renderOverride = (ov: PosterOverride) => {
    const info = driveInfo.get(ov.drive_id)
    const styleKey = (info?.style ?? '').toLowerCase().replace(/[^a-z0-9]/g, '')
    const badgeClass = ov.media_type === 'movie' ? 'movie' : ov.media_type === 'collection' ? 'collection' : 'series'
    const badgeLabel = ov.media_type === 'movie' ? 'Movie' : ov.media_type === 'collection' ? 'Collection' : 'Show'
    return (
      <div key={ov.id} className="unmatched-item drive-usage-item">
        <div className="drive-usage-item-body">
          <div className="unmatched-item-top">
            <div className="unmatched-item-meta">
              <span className="item-title">{ov.title}</span>
              {ov.year && <span className="item-year">({ov.year})</span>}
              <span className={`unmatched-cat-badge unmatched-cat-badge--${badgeClass}`}>{badgeLabel}</span>
              <span className={`unmatched-cat-badge ${(ov.domain ?? 'poster') === 'artwork' ? 'unmatched-cat-badge--artwork' : 'unmatched-cat-badge--season'}`}>
                {scopeLabel(ov)}
              </span>
              <span
                className={`drive-usage-source-badge${styleKey ? ` drive-usage-source-badge--${styleKey}` : ''}`}
                title={info?.name ?? ov.drive_id}
              >
                {info?.name ?? ov.drive_id}
              </span>
            </div>
          </div>
        </div>
        <div className="drive-usage-item-actions">
          <button
            className="style-usage-download-btn"
            onClick={() => handleRemove(ov.id)}
            disabled={busyId === ov.id}
            title="Remove override - back to normal priority"
          >
            <Trash2 size={13} />
            Remove
          </button>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="modal-overlay" onClick={handleOverlayClick}>
        <div className="modal-content schedule-modal list-items-modal drive-usage-modal">
          <div className="modal-header">
            <h2>Poster Overrides</h2>
            <button className="modal-close" onClick={onClose}>×</button>
          </div>

          <div className="modal-body">
            <p className="style-fallback-modal-subtitle">
              Items pinned to a specific drive instead of the priority order. Overrides apply on
              every rename and fall back to normal priority if the drive stops offering the file.
            </p>

            <div className="drive-usage-view-tabs">
              <button
                type="button"
                className={`drive-usage-view-tab${tab === 'poster' ? ' active' : ''}`}
                onClick={() => setTab('poster')}
              >
                Posters ({posterOverrides.length})
              </button>
              <button
                type="button"
                className={`drive-usage-view-tab${tab === 'artwork' ? ' active' : ''}`}
                onClick={() => setTab('artwork')}
              >
                Artwork ({artworkOverrides.length})
              </button>
            </div>

            <div className="unmatched-list">
              {overrides === null && <p className="drive-usage-hint">Loading…</p>}
              {overrides !== null && tabOverrides.length === 0 && (
                <p className="drive-usage-hint">
                  No {tabNoun} overrides yet - use the View or compare
                  buttons on a drive's {tab === 'poster' ? 'posters' : 'artwork'} to pin one.
                </p>
              )}
              {tabOverrides.map(renderOverride)}
            </div>
          </div>

          <div className="modal-footer">
            <div className="modal-footer-actions">
              <button
                className="btn-secondary"
                onClick={() => setConfirmRemoveAll(true)}
                disabled={removingAll || tabOverrides.length === 0}
                title="Remove every override on this tab - back to normal priority"
              >
                <Trash2 size={14} />
                Remove all {tabNoun} overrides
              </button>
              <button className="btn-secondary" onClick={onClose}>Close</button>
            </div>
          </div>
        </div>
      </div>

      <ConfirmDialog
        isOpen={confirmRemoveAll}
        title={`Remove All ${tab === 'poster' ? 'Poster' : 'Artwork'} Overrides?`}
        message={`Remove all ${tabOverrides.length} ${tabNoun} override${tabOverrides.length !== 1 ? 's' : ''}? The next rename goes back to normal priority for these items.`}
        confirmText="Remove All"
        cancelText="Cancel"
        variant="danger"
        onConfirm={() => handleRemoveAll(tabOverrides)}
        onCancel={() => setConfirmRemoveAll(false)}
      />
    </>
  )
}
