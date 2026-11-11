import { useEffect, useMemo, useRef, useState } from 'react'
import {
  addTextLogo,
  defaultTextLogoFields,
  getApiErrorMessage,
  listTextLogoFonts,
  previewTextLogo,
  type TextLogoFields,
  type TextLogoFont,
  type TextLogoRenderOptions,
  type TmdbSearchResult,
} from '../../api/client'
import { useToast } from '../Toast'
import Modal from '../../components/Modal'

// Mirrors the server's default metrics (the PSD values, except main tracking preferred at 100).
const TUNING_DEFAULTS = { top_tracking: '50', top_scale: '100', main_tracking: '100', main_scale: '100' }
type TuningKey = keyof typeof TUNING_DEFAULTS

// What renders when no font is picked ('' in the dropdowns).
const DEFAULT_FONT_LABEL = { top: 'Roboto Condensed (default)', main: 'Bebas Neue (default)' }

// Last-used font picks, remembered across dialogs/sessions.
const FONT_STORAGE_KEY = 'posterflow.textLogo.fonts'

const loadStoredFonts = (): { top: string; main: string } => {
  try {
    const parsed = JSON.parse(localStorage.getItem(FONT_STORAGE_KEY) || '{}')
    return { top: String(parsed.top || ''), main: String(parsed.main || '') }
  } catch {
    return { top: '', main: '' }
  }
}

type Props = {
  item: TmdbSearchResult
  /** Index into the full IDarr sync_targets list of the chosen artwork scope. */
  syncTargetIndex: number
  scopeLabel: string | null
  onClose: () => void
}

/** Build a styled text logo (the CollectionMain.psd LOGO look) server-side and save it into the
 * scope as the item's logo. Fields prefill from the title; the preview is live. */
export default function TextLogoModal({ item, syncTargetIndex, scopeLabel, onClose }: Props) {
  const { showToast } = useToast()
  const [fields, setFields] = useState<TextLogoFields>(() => defaultTextLogoFields(item))
  const [tuning, setTuning] = useState<Record<TuningKey, string>>(TUNING_DEFAULTS)
  const [fontList, setFontList] = useState<TextLogoFont[]>([])
  const [fontSel, setFontSel] = useState<{ top: string; main: string }>(loadStoredFonts)   // '' = default
  const [preview, setPreview] = useState<string | null>(null)   // data: URL
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [rendering, setRendering] = useState(false)
  const [saving, setSaving] = useState(false)
  const [overwrite, setOverwrite] = useState<string | null>(null)   // existing filename to confirm
  const seq = useRef(0)

  useEffect(() => {
    let cancelled = false
    listTextLogoFonts()
      .then((res) => {
        if (cancelled) return
        setFontList(res.fonts)
        // A remembered font may have been renamed/removed since — fall back to the default.
        const ids = new Set(res.fonts.map((f) => f.id))
        setFontSel((s) => ({ top: ids.has(s.top) ? s.top : '', main: ids.has(s.main) ? s.main : '' }))
      })
      .catch(() => { /* dropdowns just offer the defaults */ })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    try { localStorage.setItem(FONT_STORAGE_KEY, JSON.stringify(fontSel)) } catch { /* private mode etc. */ }
  }, [fontSel])

  // Blank/invalid tuning boxes fall back to the server's PSD defaults.
  const renderOptions = useMemo<TextLogoRenderOptions>(() => {
    const num = (s: string): number | undefined => {
      const n = Number(s)
      return s.trim() !== '' && Number.isFinite(n) ? n : undefined
    }
    return {
      top_tracking: num(tuning.top_tracking),
      top_scale: num(tuning.top_scale),
      main_tracking: num(tuning.main_tracking),
      main_scale: num(tuning.main_scale),
      top_font: fontSel.top || undefined,
      main_font: fontSel.main || undefined,
    }
  }, [tuning, fontSel])

  // Debounced live preview; stale responses are dropped by sequence number.
  useEffect(() => {
    if (!fields.main.trim()) {
      setPreview(null)
      setPreviewError(null)
      return
    }
    const mySeq = ++seq.current
    setRendering(true)
    const timer = setTimeout(() => {
      previewTextLogo({ ...fields, ...renderOptions })
        .then((res) => {
          if (seq.current !== mySeq) return
          setPreview(`data:image/png;base64,${res.png_base64}`)
          setPreviewError(null)
        })
        .catch((e) => {
          if (seq.current !== mySeq) return
          setPreviewError(getApiErrorMessage(e, 'Preview failed'))
        })
        .finally(() => { if (seq.current === mySeq) setRendering(false) })
    }, 350)
    return () => clearTimeout(timer)
  }, [fields, renderOptions])

  const doSave = async (confirmOverwrite = false) => {
    setSaving(true)
    try {
      const res = await addTextLogo({
        sync_target_index: syncTargetIndex,
        tmdb_id: item.tmdb_id || undefined,   // 0 = id-less custom collection
        media_type: item.media_type,
        title: item.title,
        year: item.year || undefined,   // '' would fail the API's int parse
        tvdb_id: item.tvdb_id,
        imdb_id: item.imdb_id,
        ...fields,
        ...renderOptions,
        confirm_overwrite: confirmOverwrite,
      })
      if (res.status === 'exists') {
        setOverwrite(res.written)
        return
      }
      showToast(`Added ${res.written}`, 'success')
      onClose()
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Save failed'), 'error')
    } finally {
      setSaving(false)
    }
  }

  const setField = (key: keyof TextLogoFields) => (value: string) =>
    setFields((f) => ({ ...f, [key]: value }))

  return (
  <>
      <Modal onClose={onClose} closeOnOverlay={false}>
        <Modal.Header title={<>Text logo — {item.year ? `${item.title} (${item.year})` : item.title}</>} />
        <Modal.Body>
          <div className="text-logo-form">
            {([
              ['top', 'Top line', 'Optional — e.g. THE'],
              ['main', 'Main line', 'The big line'],
            ] as const).map(([key, label, placeholder]) => (
              <div key={key} className="text-logo-field">
                <label>
                  {label}
                  <input
                    type="text"
                    value={fields[key]}
                    onChange={(e) => setField(key)(e.target.value)}
                    placeholder={placeholder}
                  />
                </label>
                <div className="text-logo-tuning">
                  <label className="text-logo-tuning-font">
                    Font
                    <select value={fontSel[key]} onChange={(e) => setFontSel((s) => ({ ...s, [key]: e.target.value }))}>
                      <option value="">{DEFAULT_FONT_LABEL[key]}</option>
                      {fontList.map((f) => (
                        <option key={f.id} value={f.id}>{f.label}{f.source === 'config' ? ' •' : ''}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Tracking
                    <input type="number" value={tuning[`${key}_tracking`]} onChange={(e) => setTuning((t) => ({ ...t, [`${key}_tracking`]: e.target.value }))} />
                  </label>
                  <label>
                    Width %
                    <input type="number" value={tuning[`${key}_scale`]} onChange={(e) => setTuning((t) => ({ ...t, [`${key}_scale`]: e.target.value }))} />
                  </label>
                </div>
              </div>
            ))}
            <div className="text-logo-field">
              <span className="text-logo-field-title">Bottom line</span>
              <label className="checkbox-label text-logo-collection">
                <input
                  type="checkbox"
                  checked={fields.suffix === 'COLLECTION'}
                  onChange={(e) => setField('suffix')(e.target.checked ? 'COLLECTION' : '')}
                />
                COLLECTION
              </label>
            </div>
          </div>
          <div className="text-logo-preview">
            {preview && !previewError
              ? <img src={preview} alt="Logo preview" />
              : <span className="text-dim">{previewError ?? (fields.main.trim() ? 'Rendering…' : 'Enter a main line to preview.')}</span>}
          </div>
          <p className="field-hint mt-sm">
            Rendered server-side with the CollectionMain styling{rendering ? ' — updating…' : ''}.
            {scopeLabel && <> Saves into scope: <strong>{scopeLabel}</strong> as the item&apos;s logo.</>}
          </p>
        </Modal.Body>
        <Modal.Footer>
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button
            className="btn-primary"
            disabled={saving || !fields.main.trim() || !!previewError}
            onClick={() => void doSave()}
          >
            {saving ? 'Saving…' : 'Add to scope'}
          </button>
        </Modal.Footer>
      </Modal>

      {overwrite && (
        <Modal onClose={() => setOverwrite(null)} closeOnOverlay={false}>
          <Modal.Header title="Overwrite existing logo?" />
          <Modal.Body>
            <p className="modal-text">
              This scope already has a file with this name:
            </p>
            <div className="psd-not-found-filename"><code>{overwrite}</code></div>
            {scopeLabel && (
              <div className="psd-not-found-folder">
                <span className="psd-not-found-folder-label">Scope:</span>
                <code>{scopeLabel}</code>
              </div>
            )}
            <p className="modal-note text-warning">
              Overwriting moves the existing file to the IDarr duplicates folder and saves this one in its place.
            </p>
          </Modal.Body>
          <Modal.Footer>
            <button className="btn-secondary" onClick={() => setOverwrite(null)}>Cancel</button>
            <button
              className="btn-danger"
              onClick={() => {
                setOverwrite(null)
                void doSave(true)
              }}
            >
              Overwrite
            </button>
          </Modal.Footer>
        </Modal>
      )}
    </>
  )
}
