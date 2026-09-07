import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell, BellRing, Check, HardDrive, Pencil, RefreshCw, Trash2, X } from 'lucide-react'
import { getApiErrorMessage, reminderToSearchResult, type PosterReminder, type ReminderKind } from '../../api/client'
import { useReminders } from '../../contexts/RemindersContext'
import { useToast } from '../Toast'
import Toolbar from '../Toolbar'
import TmdbItemCard, { type PsdConfig } from './TmdbItemCard'
import ArtworkFinderCard from './ArtworkFinderCard'
import { useArtworkScopes } from './useArtworkScopes'

type Props = {
  psdConfig: PsdConfig
}

function formatAdded(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

/** The Reminders tab: every flagged item as its real maker / artwork card, with the note on top. */
export default function RemindersTab({ psdConfig }: Props) {
  const ctx = useReminders()
  const navigate = useNavigate()
  const { showToast } = useToast()
  const { scopes, selectedValue, selected, onSelectScope, scopesLoaded } = useArtworkScopes()
  const [kind, setKind] = useState<ReminderKind>('poster')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [busyId, setBusyId] = useState<number | null>(null)

  const reminders = ctx?.reminders ?? []
  const counts = {
    poster: reminders.filter((r) => r.kind === 'poster').length,
    artwork: reminders.filter((r) => r.kind === 'artwork').length,
  }
  const shown = reminders.filter((r) => r.kind === kind)

  const startEdit = (r: PosterReminder) => {
    setEditingId(r.id)
    setDraft(r.note)
  }

  const saveEdit = async (r: PosterReminder) => {
    if (!ctx) return
    setBusyId(r.id)
    try {
      await ctx.updateNote(r.id, draft.trim())
      setEditingId(null)
      showToast('Reminder updated')
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Failed to update reminder'), 'error')
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (r: PosterReminder) => {
    if (!ctx) return
    setBusyId(r.id)
    try {
      await ctx.remove(r.id)
      if (editingId === r.id) setEditingId(null)
      showToast('Reminder removed', 'info')
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Failed to remove reminder'), 'error')
    } finally {
      setBusyId(null)
    }
  }

  // Artwork cards add into an IDarr artwork scope, so the picker rides on the toolbar like the Artwork tab.
  const scopeControl = kind !== 'artwork' ? null
    : scopes.length > 0 ? (
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
    ) : null

  return (
    <div className="maker-tools-panel">
      <Toolbar
        title="Reminders"
        description="Items you flagged with the bell on a maker, artwork, request or list card, each with your note about what still needs doing. Fix it right here on the card, then remove the reminder (here or via the bell)."
        titleControl={scopeControl}
      >
        <button type="button" className="btn-toolbar" onClick={() => void ctx?.refresh()} disabled={!ctx} title="Reload the reminder list">
          <RefreshCw size={16} /> Refresh
        </button>
      </Toolbar>

      <div className="pf-subtabs" role="tablist" aria-label="Reminder kinds">
        {(['poster', 'artwork'] as ReminderKind[]).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            className={kind === k ? 'active' : ''}
            onClick={() => { setKind(k); setEditingId(null) }}
          >
            {k === 'poster' ? 'Posters' : 'Artwork'}
            <span className="reminder-subtab-count">{counts[k]}</span>
          </button>
        ))}
      </div>

      {ctx?.error && <p className="tmdb-error">{ctx.error}</p>}

      {!ctx?.loaded ? (
        <p className="tmdb-empty">Loading reminders…</p>
      ) : shown.length === 0 ? (
        <div className="unmatched-maker-empty">
          <Bell size={44} />
          <h3>No {kind} reminders</h3>
          <p>
            {kind === 'poster'
              ? 'Click the bell on any maker card (TMDB Search, Unmatched, Monitor) or community request / list card to flag a poster you want to come back to, with a note about what still needs doing.'
              : 'Click the bell on any Artwork Finder card to flag an item whose logo, backdrop or square art still needs work, with a note to your future self.'}
          </p>
        </div>
      ) : (
        <div className="reminder-list">
          {shown.map((r) => {
            const editing = editingId === r.id
            const busy = busyId === r.id
            const item = reminderToSearchResult(r)
            return (
              <div key={r.id} className="reminder-item">
                <div className="reminder-note-bar">
                  <BellRing size={14} className="reminder-note-icon" />
                  {editing ? (
                    <>
                      <textarea
                        className="reminder-note-input"
                        autoFocus
                        rows={2}
                        value={draft}
                        placeholder="What still needs doing?"
                        onChange={(e) => setDraft(e.target.value)}
                        onKeyDown={(e) => {
                          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') void saveEdit(r)
                          if (e.key === 'Escape') setEditingId(null)
                        }}
                      />
                      <button type="button" className="tmdb-copy-btn" onClick={() => void saveEdit(r)} disabled={busy}>
                        <Check size={12} /> Save
                      </button>
                      <button type="button" className="tmdb-copy-btn" onClick={() => setEditingId(null)} disabled={busy}>
                        <X size={12} /> Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <span className={`reminder-note${r.note ? '' : ' reminder-note--empty'}`}>{r.note || 'No note'}</span>
                      {r.created_at && <span className="reminder-date" title={new Date(r.created_at).toLocaleString()}>Added {formatAdded(r.created_at)}</span>}
                      <button type="button" className="tmdb-copy-btn" onClick={() => startEdit(r)} disabled={busy} title="Edit the note">
                        <Pencil size={12} /> Edit
                      </button>
                      <button type="button" className="tmdb-copy-btn reminder-remove-btn" onClick={() => void remove(r)} disabled={busy} title="Remove this reminder">
                        <Trash2 size={12} /> Remove
                      </button>
                    </>
                  )}
                </div>
                {r.kind === 'poster' ? (
                  <TmdbItemCard item={item} psdConfig={psdConfig} />
                ) : (
                  <ArtworkFinderCard
                    item={item}
                    syncTargetIndex={selected ? selected.index : null}
                    scopeLabel={selected ? selected.label : null}
                  />
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
