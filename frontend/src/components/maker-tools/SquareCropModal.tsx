import { useCallback, useEffect, useRef, useState } from 'react'
import { Crop as CropIcon } from 'lucide-react'

type Sel = { x: number; y: number; size: number }   // display-pixel square selection

type Props = {
  imageUrl: string
  title: string
  saving?: boolean
  onCancel: () => void
  /** crop rect in the SOURCE image's own pixels (square). */
  onSave: (crop: { x: number; y: number; size: number }) => void
}

const MIN_OUTPUT = 500   // square art must be at least 500×500 native px
const PRESETS = [500, 1000, 1500, 2000, 2500, 3000]   // one-click native square sizes

export default function SquareCropModal({ imageUrl, title, saving, onCancel, onSave }: Props) {
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [display, setDisplay] = useState<{ w: number; h: number; nw: number; nh: number } | null>(null)
  const displayRef = useRef<{ w: number; h: number; nw: number; nh: number } | null>(null)
  const [sel, setSel] = useState<Sel | null>(null)
  const drag = useRef<{ mode: 'move' | 'resize'; startX: number; startY: number; orig: Sel } | null>(null)

  // Measure the image's CURRENT rendered size and re-fit the selection into it. Clamping/scale
  // always use live element dims so a layout change (e.g. the presets column narrowing the image)
  // can never leave the box hanging past an edge.
  const measure = useCallback(() => {
    const img = imgRef.current
    if (!img || !img.clientWidth) return
    const w = img.clientWidth
    const h = img.clientHeight
    const prev = displayRef.current
    const ratio = prev && prev.w ? w / prev.w : 1
    const next = { w, h, nw: img.naturalWidth, nh: img.naturalHeight }
    displayRef.current = next
    setDisplay(next)
    setSel((s) => {
      if (!s) {
        const size = Math.min(w, h)
        return { x: (w - size) / 2, y: (h - size) / 2, size }
      }
      const size = Math.min(s.size * ratio, Math.min(w, h))   // scale with the container, keep in bounds
      const x = Math.max(0, Math.min(s.x * ratio, w - size))
      const y = Math.max(0, Math.min(s.y * ratio, h - size))
      return { x, y, size }
    })
  }, [])

  useEffect(() => {
    const img = imgRef.current
    if (!img) return
    const ro = new ResizeObserver(() => measure())
    ro.observe(img)
    return () => ro.disconnect()
  }, [measure])

  const onPointerMove = useCallback((e: PointerEvent) => {
    const d = drag.current
    const img = imgRef.current
    if (!d || !img) return
    const w = img.clientWidth
    const h = img.clientHeight
    const dx = e.clientX - d.startX
    const dy = e.clientY - d.startY
    if (d.mode === 'move') {
      const x = Math.max(0, Math.min(d.orig.x + dx, w - d.orig.size))
      const y = Math.max(0, Math.min(d.orig.y + dy, h - d.orig.size))
      setSel({ x, y, size: d.orig.size })
    } else {
      const scale = img.naturalWidth / w                 // native px per display px
      const minSize = MIN_OUTPUT / scale                 // 500 native px, in display px
      const maxSize = Math.min(w - d.orig.x, h - d.orig.y)
      // never exceed maxSize, even if the min floor would (defensive)
      const size = Math.min(maxSize, Math.max(Math.min(minSize, maxSize), d.orig.size + Math.max(dx, dy)))
      setSel({ x: d.orig.x, y: d.orig.y, size })
    }
  }, [])

  const endDrag = useCallback(() => {
    drag.current = null
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', endDrag)
  }, [onPointerMove])

  const startDrag = useCallback((mode: 'move' | 'resize', e: React.PointerEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (!sel) return
    drag.current = { mode, startX: e.clientX, startY: e.clientY, orig: { ...sel } }
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', endDrag)
  }, [sel, onPointerMove, endDrag])

  // Set the box to an exact native square size, keeping its current centre (clamped to bounds).
  const applyPreset = useCallback((nativeSize: number) => {
    const img = imgRef.current
    if (!img || !img.clientWidth) return
    const w = img.clientWidth
    const h = img.clientHeight
    const scale = img.naturalWidth / w
    const size = Math.min(nativeSize / scale, Math.min(w, h))
    setSel((s) => {
      const cx = s ? s.x + s.size / 2 : w / 2
      const cy = s ? s.y + s.size / 2 : h / 2
      const x = Math.max(0, Math.min(cx - size / 2, w - size))
      const y = Math.max(0, Math.min(cy - size / 2, h - size))
      return { x, y, size }
    })
  }, [])

  const handleSave = useCallback(() => {
    const img = imgRef.current
    if (!sel || !img || !img.clientWidth) return
    const s = img.naturalWidth / img.clientWidth   // uniform (aspect preserved)
    const size = Math.max(1, Math.round(sel.size * s))
    const x = Math.max(0, Math.min(Math.round(sel.x * s), img.naturalWidth - size))
    const y = Math.max(0, Math.min(Math.round(sel.y * s), img.naturalHeight - size))
    onSave({ x, y, size })
  }, [sel, onSave])

  // Derived facts about the source image (for the badges / preset availability).
  const scale = display ? display.nw / display.w : 1
  const maxSquareNative = display ? Math.min(display.nw, display.nh) : 0
  const tooSmall = display ? maxSquareNative < MIN_OUTPUT : false
  const outputSize = sel ? Math.round(sel.size * scale) : 0

  return (
    <div className="modal-overlay">
      <div className="modal-content" style={{ maxWidth: 720 }}>
        <div className="modal-header">
          <h2>Crop to square art — {title}</h2>
          <button className="modal-close" onClick={onCancel}>×</button>
        </div>
        <div className="modal-body">
         <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start', justifyContent: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10, minWidth: 0 }}>
          <div style={{ position: 'relative', lineHeight: 0, userSelect: 'none', touchAction: 'none', overflow: 'hidden' }}>
            {/* Cap the height so the whole image fits inside the 80vh modal (header + footer + caption)
                without the modal-body needing to scroll — otherwise the bottom is hidden below the fold. */}
            <img ref={imgRef} src={imageUrl} onLoad={measure} draggable={false} alt="" style={{ maxWidth: '100%', maxHeight: 'min(62vh, calc(80vh - 230px))', display: 'block' }} />
            {sel && !tooSmall && (
              <>
                <div
                  onPointerDown={(e) => startDrag('move', e)}
                  style={{ position: 'absolute', left: sel.x, top: sel.y, width: sel.size, height: sel.size, border: '2px solid #64b5f6', boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.55)', cursor: 'move', boxSizing: 'border-box' }}
                >
                  <div
                    onPointerDown={(e) => startDrag('resize', e)}
                    style={{ position: 'absolute', right: 2, bottom: 2, width: 14, height: 14, background: '#64b5f6', border: '2px solid #fff', borderRadius: 3, cursor: 'nwse-resize' }}
                  />
                </div>
                {/* live size badge — a SIBLING of the box (containing block = the full image), so its
                    gray backing sizes to the text rather than the box width; lineHeight resets the
                    container's lineHeight:0. Positioned to follow the box's top-left. */}
                <div style={{ position: 'absolute', top: sel.y + 4, left: sel.x + 4, width: 'max-content', lineHeight: 'normal', background: 'rgba(220, 220, 220, 0.95)', color: '#1a1a1a', fontSize: '0.9rem', fontWeight: 700, padding: '3px 8px', borderRadius: 4, border: '1px solid rgba(0, 0, 0, 0.35)', boxShadow: '0 1px 3px rgba(0, 0, 0, 0.45)', pointerEvents: 'none', whiteSpace: 'nowrap' }}>
                  {outputSize} × {outputSize}
                </div>
              </>
            )}
          </div>
          {tooSmall ? (
            <p style={{ fontSize: '0.82rem', color: '#ffb74d', margin: 0, textAlign: 'center' }}>
              This image is only {display?.nw}×{display?.nh}px — too small to crop a {MIN_OUTPUT}×{MIN_OUTPUT} square. Pick a larger poster or backdrop.
            </p>
          ) : (
            <p style={{ fontSize: '0.78rem', color: '#999', margin: 0, textAlign: 'center' }}>
              Source image: <strong style={{ color: '#cfd6e4' }}>{display?.nw}×{display?.nh}px</strong>
              {' '}· drag to move, drag the corner to resize (min {MIN_OUTPUT}×{MIN_OUTPUT}).
            </p>
          )}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0, paddingTop: 2 }}>
            <span style={{ fontSize: '0.72rem', color: '#888', fontWeight: 600 }}>Presets</span>
            {PRESETS.map((sz) => {
              const disabled = !display || tooSmall || sz > maxSquareNative
              const active = !disabled && outputSize === sz
              return (
                <button
                  key={sz}
                  type="button"
                  className={`btn-toolbar ${active ? 'btn-primary' : ''}`}
                  style={{ minWidth: 0, fontSize: '0.75rem', padding: '5px 12px', justifyContent: 'center' }}
                  disabled={disabled}
                  onClick={() => applyPreset(sz)}
                  title={disabled ? `Image too small for ${sz}×${sz}` : `Set the crop to ${sz}×${sz}`}
                >
                  {sz} × {sz}
                </button>
              )
            })}
          </div>
         </div>
        </div>
        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel} disabled={saving}>Cancel</button>
          <button className="btn-primary" style={{ justifyContent: 'center' }} onClick={handleSave} disabled={saving || !sel || tooSmall}>
            <CropIcon size={15} /> {saving ? 'Saving…' : 'Save as Square Art'}
          </button>
        </div>
      </div>
    </div>
  )
}
