import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Bell, BellRing, Trash2 } from 'lucide-react'
import { getApiErrorMessage, type ReminderItem, type ReminderKind } from '../../api/client'
import { useReminders } from '../../contexts/RemindersContext'
import { useToast } from '../Toast'

type Props = {
  kind: ReminderKind
  item: ReminderItem
}

const POP_WIDTH = 300

/** Bell button in a card's header. Click opens a small note popover: on an unflagged item it adds
 *  a reminder, on a flagged one (amber bell) it edits or removes it. Hidden without a RemindersProvider. */
export default function ReminderToggle({ kind, item }: Props) {
  const ctx = useReminders()
  const { showToast } = useToast()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const bellRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const reminder = ctx?.find(kind, item)
  const active = Boolean(reminder)

  // Fixed positioning (portaled to body) so the card's overflow / gallery panels never clip it.
  // The bell sits at the card's right edge, so the popover hangs from its top-right corner and
  // opens inward; it flips above the bell when there's no room below.
  const computePos = useCallback(() => {
    const r = bellRef.current?.getBoundingClientRect()
    if (!r) return null
    const left = Math.max(8, Math.min(r.right - POP_WIDTH, window.innerWidth - POP_WIDTH - 8))
    const height = popRef.current?.offsetHeight ?? 170
    const below = r.bottom + 6
    return { top: below + height > window.innerHeight - 8 ? Math.max(8, r.top - height - 6) : below, left }
  }, [])
  const place = useCallback(() => {
    const next = computePos()
    if (next) setPos(next)
  }, [computePos])

  // Layout effect: re-measure with the real popover height before the browser paints, so the
  // first frame never shows it at a stale spot.
  useLayoutEffect(() => {
    if (!open) return
    place()
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (bellRef.current?.contains(t) || popRef.current?.contains(t)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  if (!ctx) return null

  const toggle = () => {
    if (open) { setOpen(false); return }
    setDraft(reminder?.note ?? '')
    // Position from the bell in the same render that mounts the popover, not one frame later.
    const next = computePos()
    if (next) setPos(next)
    setOpen(true)
  }

  const remove = async () => {
    if (!reminder) return
    setBusy(true)
    try {
      await ctx.remove(reminder.id)
      showToast('Reminder removed', 'info')
      setOpen(false)
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Failed to remove reminder'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    try {
      await ctx.save(kind, item, draft.trim())
      showToast(active ? 'Reminder updated' : 'Reminder added')
      setOpen(false)
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Failed to save reminder'), 'error')
    } finally {
      setBusy(false)
    }
  }

  const label = item.year ? `${item.title} (${item.year})` : item.title
  const what = kind === 'artwork' ? 'artwork' : 'poster'
  const hint = active
    ? `Reminder: ${reminder?.note || '(no note)'}\nClick to edit or remove`
    : `Remind me to come back to this ${what}`

  return (
    <>
      <button
        type="button"
        ref={bellRef}
        className={`tmdb-reminder-bell${active ? ' active' : ''}`}
        aria-pressed={active}
        aria-label={active ? `Edit reminder for ${label}` : `Add reminder for ${label}`}
        data-tooltip={open ? undefined : hint}
        onClick={toggle}
        disabled={busy}
      >
        {active ? <BellRing size={14} /> : <Bell size={14} />}
      </button>

      {open && createPortal(
        <div
          className="tmdb-reminder-pop"
          ref={popRef}
          style={{ top: pos.top, left: pos.left, width: POP_WIDTH }}
          role="dialog"
          aria-label={active ? 'Edit reminder' : 'Add reminder'}
        >
          <div className="tmdb-reminder-pop-title">
            <BellRing size={13} /> {active ? 'Edit reminder' : 'Add reminder'}
            <span className="tmdb-reminder-pop-item" title={label}>{label}</span>
          </div>
          <textarea
            autoFocus
            rows={3}
            value={draft}
            placeholder="What still needs doing? Wait for better poster, new logo, e.g."
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') void save() }}
          />
          <div className="tmdb-reminder-pop-actions">
            {active && (
              <button type="button" className="tmdb-reminder-pop-btn tmdb-reminder-pop-btn--danger" onClick={() => void remove()} disabled={busy}>
                <Trash2 size={12} /> Remove
              </button>
            )}
            <span className="tmdb-reminder-pop-spacer" />
            <button type="button" className="tmdb-reminder-pop-btn" onClick={() => setOpen(false)} disabled={busy}>Cancel</button>
            <button type="button" className="tmdb-reminder-pop-btn tmdb-reminder-pop-btn--primary" onClick={() => void save()} disabled={busy}>
              {busy ? 'Saving…' : active ? 'Save' : 'Add reminder'}
            </button>
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}
