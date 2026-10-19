import { useCallback, useEffect, useState } from 'react'
import { Check, Download, Pin, RefreshCw, Type as TypeIcon, WandSparkles } from 'lucide-react'
import {
  GEMINI_IMAGE_MODELS,
  type GeminiImageSize,
  type GeminiImageResponse,
  type GeminiOperation,
  type GeminiPromptSet,
  type ImageSource,
  type KeptGeminiImage,
  type TmdbImage,
  getGeminiPrompts,
  runGeminiImageEdit,
  keepGeminiImage,
  getApiErrorMessage,
} from '../../api/client'

export type GeminiMode = GeminiOperation

type Item = { title: string; media_type: string; year?: string | number | null; tmdb_id?: number | null; tvdb_id?: number | null; imdb_id?: string | null }

type Props = {
  image: TmdbImage
  role: 'poster' | 'background'
  season?: number | null
  source: ImageSource        // gallery tab the image came from; a kept result goes back onto it
  item: Item
  mode: GeminiMode
  onKeep?: (kept: KeptGeminiImage) => void
  onClose: () => void
}

const SIZES: GeminiImageSize[] = ['1K', '2K', '4K']
// Mirrors the backend's POSTER_MIN_SIZE: a textless poster never comes back smaller than this.
const POSTER_MIN = { w: 1000, h: 1500 }

const COPY: Record<GeminiMode, { title: string; action: string; result: string; failed: string }> = {
  remove_text: { title: 'Remove text with Gemini', action: 'Remove text', result: 'Gemini', failed: 'Gemini could not edit this image' },
  extract_logo: { title: 'Make a logo with Gemini', action: 'Make logo', result: 'Logo', failed: 'Gemini could not make a logo from this image' },
}

function downloadBase64(b64: string, mime: string, filename: string) {
  const bytes = atob(b64)
  const buf = new Uint8Array(bytes.length)
  for (let i = 0; i < bytes.length; i++) buf[i] = bytes.charCodeAt(i)
  const url = URL.createObjectURL(new Blob([buf], { type: mime }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// The modal is capped at MODAL_MAX_HEIGHT; header, footer, body padding, title and caption take
// about 300px of it, and the images get the rest so the body never has to scroll.
const MODAL_MAX_HEIGHT = '80vh'
const IMAGE_MAX_HEIGHT = `max(200px, calc(${MODAL_MAX_HEIGHT} - 300px))`

/** Experimental: send a gallery poster/backdrop to Gemini for a textless redraw or a lifted-out logo,
 *  and show the result beside the original. Either image opens full size on click. */
export default function GeminiImageModal({ image, role, season, source, item, mode, onKeep, onClose }: Props) {
  const copy = COPY[mode]
  const isLogo = mode === 'extract_logo'
  const [model, setModel] = useState(GEMINI_IMAGE_MODELS[0].id)
  const [size, setSize] = useState<GeminiImageSize>('2K')
  const [prompt, setPrompt] = useState('')
  const [prompts, setPrompts] = useState<GeminiPromptSet | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<GeminiImageResponse | null>(null)
  const [kept, setKept] = useState<'no' | 'saving' | 'yes'>('no')
  const [zoom, setZoom] = useState<{ src: string; logo: boolean } | null>(null)

  // Prefill the prompt box with the built-in text; a blank box falls back to it server-side anyway.
  useEffect(() => {
    let alive = true
    getGeminiPrompts().then((p) => {
      if (!alive) return
      setPrompts(p[mode] ?? null)
      setPrompt(p[mode]?.detailed || '')
    }).catch(() => {})
    return () => { alive = false }
  }, [mode])

  const run = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      setResult(await runGeminiImageEdit(item, image.file_path, role, { operation: mode, imageSize: size, model, prompt: prompt.trim(), season, source }))
      setKept('no')
    } catch (err) {
      setError(getApiErrorMessage(err, copy.failed))
    } finally {
      setRunning(false)
    }
  }, [item, image.file_path, role, mode, size, model, prompt, season, source, copy.failed])

  // Keep pins the parked server copy as a gallery tile; tagging and selecting happen in the gallery.
  const keep = useCallback(async () => {
    if (!result?.ref) return
    setKept('saving')
    setError(null)
    try {
      const tile = await keepGeminiImage(result.ref)
      onKeep?.(tile)
      setKept('yes')
    } catch (err) {
      setError(getApiErrorMessage(err, 'Could not keep this image'))
      setKept('no')
    }
  }, [result, onKeep])

  const resultSrc = result ? `data:${result.mime_type};base64,${result.image_base64}` : null
  const upscaled = !isLogo && role === 'poster' && (image.width < POSTER_MIN.w || image.height < POSTER_MIN.h)
  const note = isLogo
    ? 'Gemini redraws the title lettering as white on black. The black becomes transparent and the logo is trimmed, so it downloads as a logo PNG.'
    : `Gemini redraws the whole image, so expect small changes outside the text too. ${upscaled
      ? `This poster is under ${POSTER_MIN.w}×${POSTER_MIN.h}, so the result is scaled up to at least that size.`
      : `The result is scaled back to ${image.width}×${image.height}.`}`

  const imgStyle = { maxWidth: '100%', maxHeight: IMAGE_MAX_HEIGHT, display: 'block', borderRadius: 6, cursor: 'zoom-in' } as const
  const captionStyle = { fontSize: '0.75rem', lineHeight: '1.2rem', color: '#999', marginTop: 6 } as const
  const captionHeight = 'calc(1.2rem + 6px)'
  const fieldLabel = { display: 'block', fontSize: '0.78rem', color: '#999', marginBottom: 8 } as const
  const fieldInput = { display: 'block', width: '100%', marginTop: 4, boxSizing: 'border-box' } as const

  // A textless redraw is poster-sized and sits beside the original; a logo is wide and short, so it
  // sits small at the top of the controls column (the zoom shows it full size).
  const resultFigure = result && resultSrc && (
    <figure style={{ margin: isLogo ? '0 0 12px' : 0, textAlign: 'center', minWidth: 0, flex: isLogo ? undefined : '0 1 auto' }}>
      <img
        src={resultSrc}
        alt=""
        className={isLogo ? 'tmdb-gallery-lightbox-img--logo' : undefined}
        style={isLogo ? { ...imgStyle, maxHeight: 140, margin: '0 auto', padding: 12 } : imgStyle}
        title="Click to view larger"
        onClick={() => setZoom({ src: resultSrc, logo: isLogo })}
        data-testid="gemini-result"
      />
      <figcaption style={captionStyle}>
        {copy.result} · {result.width}×{result.height} (rendered at {result.output_width}×{result.output_height} in {result.elapsed_seconds}s)
      </figcaption>
    </figure>
  )

  return (
    <div className="modal-overlay">
      <div className="modal-content" style={{ maxWidth: result && !isLogo ? 1240 : role === 'background' ? 1000 : 840, maxHeight: MODAL_MAX_HEIGHT }}>
        <div className="modal-header">
          <h2>{copy.title}</h2>
          <button className="modal-close" onClick={onClose} disabled={running}>×</button>
        </div>
        <div className="modal-body">
          <p style={{ margin: '0 0 10px', fontSize: '0.85rem', color: '#cfd6e4', textAlign: 'center' }}>
            {item.title}{item.year ? ` (${item.year})` : ''}
          </p>
          <div style={{ display: 'flex', gap: 18, justifyContent: 'center', alignItems: 'flex-start', flexWrap: 'nowrap' }}>
            <figure style={{ margin: 0, textAlign: 'center', minWidth: 0, flex: '0 1 auto' }}>
              <img src={image.url_full} alt="" style={imgStyle} title="Click to view larger" onClick={() => setZoom({ src: image.url_full, logo: false })} />
              <figcaption style={captionStyle}>Original · {image.width}×{image.height}</figcaption>
            </figure>
            {!isLogo && resultFigure}
            {/* The column stretches to the row's height (the images) and the prompt box takes whatever is
                left, ending level with the image's bottom edge, so the whole prompt shows without scrolling. */}
            <div style={{ flex: isLogo ? '0 0 320px' : '0 0 280px', alignSelf: 'stretch', display: 'flex', flexDirection: 'column', paddingBottom: captionHeight, boxSizing: 'border-box' }}>
              {isLogo && resultFigure}
              <label style={fieldLabel}>
                Model
                <select aria-label="Gemini model" className="gemini-field-select" value={model} onChange={(e) => setModel(e.target.value)} disabled={running} style={fieldInput}>
                  {GEMINI_IMAGE_MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </label>
              <label style={fieldLabel}>
                Output
                <select aria-label="Output size" className="gemini-field-select" value={size} onChange={(e) => setSize(e.target.value as GeminiImageSize)} disabled={running} style={fieldInput}>
                  {SIZES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <p style={{ fontSize: '0.76rem', color: '#999', margin: '0 0 10px' }}>
                {note} Each run is billed to your Gemini key. Keep adds the result to the gallery, where you tag and select it like any other image. Kept images are deleted after the PSD export that uses them.
              </p>
              {running && (
                <p style={{ fontSize: '0.8rem', color: '#64b5f6', margin: '0 0 10px' }}>Working. This usually takes 10 to 60 seconds.</p>
              )}
              {error && (
                <p style={{ fontSize: '0.8rem', color: '#ef5350', margin: '0 0 10px' }}>{error}</p>
              )}
              <div style={{ ...fieldLabel, marginBottom: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <span>Prompt</span>
                  {/* One-click swap between the two built-in prompts; neither lights up once the text is edited. */}
                  <span style={{ display: 'flex', gap: 4 }}>
                    {(['detailed', 'simple'] as const).map((kind) => (
                      <button
                        key={kind}
                        type="button"
                        className={`btn-toolbar${prompts && prompt === prompts[kind] ? ' btn-primary' : ''}`}
                        style={{ fontSize: '0.68rem', padding: '1px 8px', minWidth: 0 }}
                        disabled={!prompts || running}
                        title={kind === 'simple' ? 'Use the short, plain prompt' : 'Use the full built-in prompt'}
                        onClick={() => prompts && setPrompt(prompts[kind])}
                      >
                        {kind === 'simple' ? 'Simple' : 'Detailed'}
                      </button>
                    ))}
                  </span>
                </div>
                <textarea
                  aria-label="Gemini prompt"
                  className="gemini-prompt-box"
                  rows={4}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  disabled={running}
                  style={{ ...fieldInput, flex: 1, minHeight: 90 }}
                />
              </div>
            </div>
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose} disabled={running}>Close</button>
          {result && (
            <button className="btn-secondary" onClick={() => downloadBase64(result.image_base64, result.mime_type, result.filename)}>
              <Download size={15} /> Download
            </button>
          )}
          {result && result.ref && (
            <button className="btn-secondary" onClick={() => void keep()} disabled={kept !== 'no' || running} title="Add this result to the gallery">
              {kept === 'yes' ? <><Check size={15} /> Kept</> : kept === 'saving' ? <><Pin size={15} /> Keeping…</> : <><Pin size={15} /> Keep</>}
            </button>
          )}
          <button className="btn-primary" style={{ justifyContent: 'center' }} onClick={() => void run()} disabled={running}>
            {running
              ? <><RefreshCw size={15} className="gemini-spin" /> Working…</>
              : result
                ? <><RefreshCw size={15} /> Run again</>
                : <>{isLogo ? <TypeIcon size={15} /> : <WandSparkles size={15} />} {copy.action}</>}
          </button>
        </div>
      </div>
      {/* Full-size view of either image; sits inside the modal's stacking context, so it covers the modal. */}
      {zoom && (
        <div className="tmdb-lightbox-overlay" style={{ zIndex: 1001, cursor: 'zoom-out' }} onClick={() => setZoom(null)} data-testid="gemini-zoom">
          <img
            className={`tmdb-gallery-lightbox-img${zoom.logo ? ' tmdb-gallery-lightbox-img--logo' : ''}`}
            src={zoom.src}
            alt=""
            style={{ maxWidth: '92vw', maxHeight: '92vh', padding: zoom.logo ? 24 : 0 }}
          />
          <button type="button" className="tmdb-lightbox-close" onClick={() => setZoom(null)}>×</button>
        </div>
      )}
    </div>
  )
}
