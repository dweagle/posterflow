import { useState, useMemo } from 'react'
import { Loader2, Send } from 'lucide-react'
import { type CommunityListItem } from '../../api/community'
import { POSTER_STYLES, EXTRA_TAGS, isValidDiscordUsername, getStoredPosterStyle } from './posterStyles'

export type MoveToRequestValues = {
  styleTags: string[]
  notes: string | null
  pingDiscordId: string | null
}

// A season item's "Seasons: 1, 2, 3" first line is what the request card parses to
// render per-season badges, so split it off: the editable box only exposes the
// free-text remainder and the encoding is always re-attached on submit.
function splitSeasonNotes(item: CommunityListItem): { seasonLine: string | null; freeNotes: string } {
  if (item.media_type === 'season' && item.notes?.startsWith('Seasons: ')) {
    const [first, ...rest] = item.notes.split('\n')
    return { seasonLine: first, freeNotes: rest.join('\n') }
  }
  return { seasonLine: null, freeNotes: item.notes ?? '' }
}

// Pre-select the style controls from the item's single style_tag, falling back to
// the remembered CL2K/MM2K choice when the item carries no recognized style.
function initialStyle(styleTag: string | null): { primary: string | null; extras: string[] } {
  const fallback = getStoredPosterStyle()
  if (!styleTag) return { primary: fallback, extras: [] }
  const primary = POSTER_STYLES.find((s) => s.value === styleTag || s.label === styleTag)
  if (primary) return { primary: primary.value, extras: [] }
  return { primary: fallback, extras: EXTRA_TAGS.includes(styleTag) ? [styleTag] : [] }
}

type Props = {
  item: CommunityListItem
  submitting: boolean
  onCancel: () => void
  onConfirm: (values: MoveToRequestValues) => void
}

export default function MoveToRequestModal({ item, submitting, onCancel, onConfirm }: Props) {
  const { seasonLine, freeNotes } = useMemo(() => splitSeasonNotes(item), [item])
  const seed = useMemo(() => initialStyle(item.style_tag), [item.style_tag])
  const [posterStyle, setPosterStyle] = useState<string | null>(seed.primary)
  const [extraTags, setExtraTags] = useState<string[]>(seed.extras)
  const [notes, setNotes] = useState(freeNotes)
  const [pingDiscordId, setPingDiscordId] = useState('')

  const toggleExtra = (tag: string) =>
    setExtraTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]))

  const canSubmit = !submitting && !!posterStyle

  const handleConfirm = () => {
    if (!canSubmit || !posterStyle) return
    const trimmedNote = notes.trim()
    const combined = seasonLine ? [seasonLine, trimmedNote].filter(Boolean).join('\n') : trimmedNote
    const trimmedPing = pingDiscordId.trim()
    onConfirm({
      styleTags: [posterStyle, ...extraTags],
      notes: combined || null,
      pingDiscordId: trimmedPing && isValidDiscordUsername(trimmedPing) ? trimmedPing : null,
    })
  }

  return (
    <div className="modal-overlay" onClick={(e) => { if (e.target === e.currentTarget && !submitting) onCancel() }}>
      <div className="modal-content schedule-modal community-req-modal">
        <div className="modal-header">
          <h2>Move to a Community Request</h2>
          <button className="modal-close" onClick={onCancel} disabled={submitting}>×</button>
        </div>

        <div className="modal-body">
          <p style={{ marginTop: 0 }}>
            <strong>{item.title}{item.year ? ` (${item.year})` : ''}</strong> will be posted as a community poster
            request — with a Discord thread makers can claim — and removed from this list.
          </p>

          {/* Poster style — required, single choice */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Poster style <span className="request-required">required</span>
          </div>
          <div className="request-style-tags">
            {POSTER_STYLES.map((s) => (
              <button
                key={s.value}
                type="button"
                className={`request-style-tag${posterStyle === s.value ? ' selected' : ''}`}
                onClick={() => setPosterStyle((prev) => (prev === s.value ? null : s.value))}
              >
                {s.label}
              </button>
            ))}
          </div>

          {/* Extra style preferences — optional */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Style preferences <span className="request-optional">(optional)</span>
          </div>
          <div className="request-style-tags">
            {EXTRA_TAGS.map((tag) => (
              <button
                key={tag}
                type="button"
                className={`request-style-tag${extraTags.includes(tag) ? ' selected' : ''}`}
                onClick={() => toggleExtra(tag)}
              >
                {tag}
              </button>
            ))}
          </div>

          {/* Ping a user — optional */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Ping a Discord user <span className="request-optional">(optional)</span>
          </div>
          <input
            type="text"
            className="request-notes-textarea"
            style={{ resize: 'none', height: 'auto', padding: '0.5rem 0.75rem' }}
            placeholder="Discord username (e.g. dweagle79)"
            value={pingDiscordId}
            onChange={(e) => setPingDiscordId(e.target.value.slice(0, 32))}
            maxLength={32}
          />
          {pingDiscordId.length > 0 && !isValidDiscordUsername(pingDiscordId) && (
            <div className="creq-field-hint" style={{ color: '#ff9800' }}>
              Must be 2–32 characters. @everyone and @here are not allowed.
            </div>
          )}

          {/* Notes — optional */}
          <div className="creq-section-label" style={{ marginTop: '0.75rem' }}>
            Notes <span className="request-optional">(optional)</span>
          </div>
          {seasonLine && <div className="creq-field-hint">{seasonLine} — kept automatically</div>}
          <textarea
            className="request-notes-textarea"
            placeholder="Any special instructions, extra context…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={3}
            maxLength={500}
          />
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel} disabled={submitting}>Cancel</button>
          <button
            className="btn-primary"
            onClick={handleConfirm}
            disabled={!canSubmit}
            title={!posterStyle ? 'Select a poster style (CL2K or MM2K)' : undefined}
          >
            {submitting ? <Loader2 size={13} className="spin-icon" /> : <Send size={13} />}
            {' '}Move to Request
          </button>
        </div>
      </div>
    </div>
  )
}
