import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { fetchOverlayPreview, type OverlayPlacement, type OverlayPreviewBase, type OverlayPreviewLayer } from '../../api/overlays'

const REF_W = 1000
const REF_H = 1500
const PREVIEW_BASE_KEY = 'posterflow.overlays.previewBase'
// Snap targets: the poster's center lines and a margin inside each edge (reference px).
export const SNAP_MARGIN = 35
const SNAP_DISTANCE = 14
// The handle outline sits this far outside the badge rect (CSS px) so it clears the glyph
// edges and descenders of a text-only badge; drag and snap math still use the exact rect.
const HANDLE_PAD = 1

type Rect = { x: number; y: number; w: number; h: number }
type LayerRect = { type: string; rect: Rect }
// Renders kept by their input string: switching back to a type or state seen before shows
// it at once instead of asking the server for the same picture again.
type CachedRender = { blob: Blob; rect: Rect; text: string; layers: LayerRect[] }
const RENDER_CACHE_SIZE = 8
type HAlign = OverlayPlacement['horizontal_align']
type VAlign = OverlayPlacement['vertical_align']
type Snap = { x: HAlign | null; y: VAlign | null }

type Props = {
  type: string
  variant: string
  placement: OverlayPlacement
  options: Record<string, unknown>
  onPlacementChange: (patch: Partial<OverlayPlacement>) => void
  // Switched off (the overlay or its type): the poster shows the other layers only.
  enabled?: boolean
  large?: boolean
  // The other enabled overlays, drawn underneath so you can see where they sit while editing this one.
  others?: OverlayPreviewLayer[]
  // Their names, for their drag handles.
  labels?: Record<string, string>
  // Called when one of the other overlays' badges is dragged or nudged: its own placement changes.
  onLayerPlacementChange?: (type: string, patch: Partial<OverlayPlacement>) => void
  // Bumped when a file the badge draws changed on the server (a logo upload): fetch again.
  refreshKey?: number
}

// Where a badge of size (w, h) at reference position (x, y) lands as align+offset.
const offsetsFor = (p: OverlayPlacement, rect: Rect, x: number, y: number): Partial<OverlayPlacement> => {
  const h = p.horizontal_align === 'left' ? x : p.horizontal_align === 'right' ? REF_W - rect.w - x : x - (REF_W - rect.w) / 2
  const v = p.vertical_align === 'top' ? y : p.vertical_align === 'bottom' ? REF_H - rect.h - y : y - (REF_H - rect.h) / 2
  return { horizontal_offset: Math.round(h), vertical_offset: Math.round(v) }
}

// The badge's top-left for each snap anchor.
const anchorX = (a: HAlign, w: number) => (a === 'left' ? SNAP_MARGIN : a === 'right' ? REF_W - SNAP_MARGIN - w : (REF_W - w) / 2)
const anchorY = (a: VAlign, h: number) => (a === 'top' ? SNAP_MARGIN : a === 'bottom' ? REF_H - SNAP_MARGIN - h : (REF_H - h) / 2)

// Pull (x, y) onto the nearest snap anchor per axis when it's within SNAP_DISTANCE.
const snapPosition = (rect: Rect, x: number, y: number, disabled = false): { x: number; y: number; snap: Snap } => {
  const snap: Snap = { x: null, y: null }
  if (disabled) return { x, y, snap }
  let sx = x
  let best = SNAP_DISTANCE
  for (const a of ['left', 'center', 'right'] as HAlign[]) {
    const d = Math.abs(x - anchorX(a, rect.w))
    if (d < best) { best = d; sx = anchorX(a, rect.w); snap.x = a }
  }
  let sy = y
  best = SNAP_DISTANCE
  for (const a of ['top', 'center', 'bottom'] as VAlign[]) {
    const d = Math.abs(y - anchorY(a, rect.h))
    if (d < best) { best = d; sy = anchorY(a, rect.h); snap.y = a }
  }
  return { x: sx, y: sy, snap }
}

// A snapped axis re-anchors the alignment to that edge/center, so the badge stays pinned
// there even when its text (and therefore width) changes.
const placementAfterDrop = (p: OverlayPlacement, rect: Rect, x: number, y: number, snap: Snap): Partial<OverlayPlacement> => {
  const free = offsetsFor(p, rect, x, y)
  return {
    horizontal_align: snap.x ?? p.horizontal_align,
    horizontal_offset: snap.x ? (snap.x === 'center' ? 0 : SNAP_MARGIN) : free.horizontal_offset,
    vertical_align: snap.y ?? p.vertical_align,
    vertical_offset: snap.y ? (snap.y === 'center' ? 0 : SNAP_MARGIN) : free.vertical_offset,
  }
}

// The sample poster with the overlay rendered by the backend, plus a drag handle over the
// badge so the user can position it directly (arrow keys nudge by 1px, shift = 10px). The
// other overlays' badges get handles too: dragging one moves that overlay.
function OverlayPreview({ type, variant, placement, options, onPlacementChange, enabled = true, large = false, others = [], labels = {}, onLayerPlacementChange, refreshKey = 0 }: Props) {
  const [url, setUrl] = useState<string | null>(null)
  const [rect, setRect] = useState<Rect | null>(null)
  const [layerRects, setLayerRects] = useState<LayerRect[]>([])
  const [text, setText] = useState('')
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(true)
  const [scale, setScale] = useState(0) // displayed px per reference px
  // `layer` names the other overlay being dragged; null = the badge of the overlay being edited.
  const [drag, setDrag] = useState<{ layer: string | null; startX: number; startY: number; dx: number; dy: number; noSnap: boolean } | null>(null)
  const [base, setBase] = useState<OverlayPreviewBase>(() => {
    try { return localStorage.getItem(PREVIEW_BASE_KEY) === 'plain' ? 'plain' : 'bordered' } catch { return 'bordered' }
  })
  const imgRef = useRef<HTMLImageElement | null>(null)
  const cache = useRef(new Map<string, CachedRender>())

  const chooseBase = (next: OverlayPreviewBase) => {
    setBase(next)
    try { localStorage.setItem(PREVIEW_BASE_KEY, next) } catch { /* ignore */ }
  }

  const deps = JSON.stringify({ type, variant, placement, options, base, others, enabled, refreshKey })
  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    setError(false)
    const show = (render: CachedRender) => {
      objectUrl = URL.createObjectURL(render.blob)
      setUrl(objectUrl)
      setRect(render.rect.w > 0 ? render.rect : null)
      setLayerRects(render.layers.filter((l) => l.rect.w > 0))
      setText(render.text)
      setLoading(false)
    }
    const cached = cache.current.get(deps)
    if (cached) {
      cache.current.delete(deps)
      cache.current.set(deps, cached)  // most recently shown last, so it is evicted last
      show(cached)
      return () => {
        if (objectUrl) URL.revokeObjectURL(objectUrl)
      }
    }
    setLoading(true)
    const timer = setTimeout(() => {
      fetchOverlayPreview({ type, variant, placement, options, base, others, enabled })
        .then((result) => {
          const render = { blob: result.blob, rect: result.rect, text: result.text, layers: result.layers ?? [] }
          cache.current.set(deps, render)  // kept even if this render was overtaken: its inputs may come back
          while (cache.current.size > RENDER_CACHE_SIZE) {
            const oldest = cache.current.keys().next().value
            if (oldest === undefined) break
            cache.current.delete(oldest)
          }
          if (cancelled) return
          show(render)
        })
        .catch(() => {
          if (!cancelled) {
            setError(true)
            setLoading(false)
          }
        })
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(timer)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps])

  const measure = useCallback(() => {
    const img = imgRef.current
    if (img && img.clientWidth > 0) setScale(img.clientWidth / REF_W)
  }, [])
  useEffect(() => {
    measure()
    const img = imgRef.current
    if (!img || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(img)
    return () => ro.disconnect()
  }, [measure, url, large])

  // What a handle moves: the edited overlay's badge (layer null) or another layer's.
  const target = (layer: string | null): { rect: Rect | null; placement: OverlayPlacement | null; change: (patch: Partial<OverlayPlacement>) => void } =>
    layer === null
      ? { rect, placement, change: onPlacementChange }
      : {
          rect: layerRects.find((l) => l.type === layer)?.rect ?? null,
          placement: others.find((o) => o.type === layer)?.placement ?? null,
          change: (patch) => onLayerPlacementChange?.(layer, patch),
        }
  const dragRect = drag ? target(drag.layer).rect : null
  // Live (snapped) badge position while dragging, in reference px.
  const dragged = drag && dragRect && scale
    ? snapPosition(dragRect, dragRect.x + drag.dx / scale, dragRect.y + drag.dy / scale, drag.noSnap)
    : null

  const onPointerDown = (layer: string | null) => (e: PointerEvent<HTMLDivElement>) => {
    if (!target(layer).rect || !scale) return
    e.preventDefault()  // no image drag / text selection — but that also skips focus, so:
    e.currentTarget.focus()  // arrow keys nudge the badge after a click
    try { e.currentTarget.setPointerCapture?.(e.pointerId) } catch { /* jsdom */ }
    setDrag({ layer, startX: e.clientX, startY: e.clientY, dx: 0, dy: 0, noSnap: e.altKey || e.ctrlKey })
  }
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag) return
    setDrag({ ...drag, dx: e.clientX - drag.startX, dy: e.clientY - drag.startY, noSnap: e.altKey || e.ctrlKey })
  }
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!drag || !dragRect || !scale) return
    try { e.currentTarget.releasePointerCapture?.(e.pointerId) } catch { /* jsdom */ }
    const moved = Math.abs(drag.dx) >= 1 || Math.abs(drag.dy) >= 1
    const dropped = dragged
    const { placement: p, change } = target(drag.layer)
    setDrag(null)
    if (!moved || !dropped || !p) return
    change(placementAfterDrop(p, dragRect, dropped.x, dropped.y, dropped.snap))
  }
  // Nudge from the current offsets, not the last reported rect: the rect only refreshes after
  // the debounced re-render, so key repeats would otherwise keep producing the same position.
  const onKeyDown = (layer: string | null) => (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 10 : 1
    const moves: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }
    const move = moves[e.key]
    const { placement: p, change } = target(layer)
    if (!move || !p) return
    e.preventDefault()
    // Offsets grow away from the anchored edge, so right/bottom alignment flips the sign.
    const dx = p.horizontal_align === 'right' ? -move[0] : move[0]
    const dy = p.vertical_align === 'bottom' ? -move[1] : move[1]
    change({ horizontal_offset: p.horizontal_offset + dx, vertical_offset: p.vertical_offset + dy })
  }

  // A handle's box: the badge's rect, or where it is being dragged to.
  const handleStyle = (layer: string | null, r: Rect) => {
    const live = drag?.layer === layer && dragged ? dragged : null
    return {
      left: (live ? live.x : r.x) * scale - HANDLE_PAD,
      top: (live ? live.y : r.y) * scale - HANDLE_PAD,
      width: r.w * scale + 2 * HANDLE_PAD,
      height: r.h * scale + 2 * HANDLE_PAD,
    }
  }
  const handleClass = (layer: string | null) =>
    `overlay-drag-handle ${layer === null ? '' : 'other'} ${drag?.layer === layer ? 'dragging' : ''} ${drag?.layer === layer && (dragged?.snap.x || dragged?.snap.y) ? 'snapped' : ''}`

  const guides = drag && scale
    ? [
        { key: 'v-left', axis: 'v', at: SNAP_MARGIN * scale, active: dragged?.snap.x === 'left' },
        { key: 'v-center', axis: 'v', at: (REF_W / 2) * scale, active: dragged?.snap.x === 'center' },
        { key: 'v-right', axis: 'v', at: (REF_W - SNAP_MARGIN) * scale, active: dragged?.snap.x === 'right' },
        { key: 'h-top', axis: 'h', at: SNAP_MARGIN * scale, active: dragged?.snap.y === 'top' },
        { key: 'h-center', axis: 'h', at: (REF_H / 2) * scale, active: dragged?.snap.y === 'center' },
        { key: 'h-bottom', axis: 'h', at: (REF_H - SNAP_MARGIN) * scale, active: dragged?.snap.y === 'bottom' },
      ]
    : []

  return (
    <div className={`overlay-preview ${large ? 'large' : ''}`}>
      <label className="checkbox-label overlay-preview-base" title="Render the sample poster through your saved border settings first, so the preview matches the real output">
        <input type="checkbox" checked={base === 'bordered'} onChange={(e) => chooseBase(e.target.checked ? 'bordered' : 'plain')} />
        Use my border settings
      </label>
      <div className="overlay-preview-stage">
        {error && <div className="overlay-preview-error">No preview</div>}
        {url && !error && <img ref={imgRef} src={url} alt={`Overlay preview${text ? `: ${text}` : ''}`} onLoad={measure} draggable={false} />}
        {!url && !error && <div className="overlay-preview-loading" />}
        {guides.map((g) => (
          <div
            key={g.key}
            className={`overlay-guide ${g.axis === 'v' ? 'vertical' : 'horizontal'} ${g.active ? 'active' : ''}`}
            style={g.axis === 'v' ? { left: g.at } : { top: g.at }}
            aria-hidden="true"
          />
        ))}
        {/* The other overlays' handles first, so the edited overlay's stays on top where they overlap. */}
        {scale > 0 && !error && layerRects.map((l) => {
          const p = others.find((o) => o.type === l.type)?.placement
          return p ? (
            <div
              key={l.type}
              className={handleClass(l.type)}
              style={handleStyle(l.type, l.rect)}
              role="slider"
              aria-label={`${labels[l.type] ?? l.type} position (drag or use arrow keys)`}
              aria-valuetext={`${p.horizontal_offset}, ${p.vertical_offset}`}
              tabIndex={0}
              onPointerDown={onPointerDown(l.type)}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={() => setDrag(null)}
              onKeyDown={onKeyDown(l.type)}
            />
          ) : null
        })}
        {rect && scale > 0 && !error && (
          <div
            className={handleClass(null)}
            style={handleStyle(null, rect)}
            role="slider"
            aria-label="Badge position (drag or use arrow keys)"
            aria-valuetext={`${placement.horizontal_offset}, ${placement.vertical_offset}`}
            tabIndex={0}
            onPointerDown={onPointerDown(null)}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={() => setDrag(null)}
            onKeyDown={onKeyDown(null)}
          />
        )}
      </div>
      <div className="overlay-preview-caption">{loading ? 'Rendering…' : !enabled ? 'Switched off: nothing is drawn for this overlay' : text ? '' : 'Nothing to draw for this state'}</div>
    </div>
  )
}

export default OverlayPreview
