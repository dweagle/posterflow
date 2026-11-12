import { SNAP_MARGIN } from './OverlayPreview'
import type { OverlayPlacement } from '../../api/overlays'

type HAlign = OverlayPlacement['horizontal_align']
type VAlign = OverlayPlacement['vertical_align']

const ANCHORS: { h: HAlign; v: VAlign; label: string }[] = [
  { h: 'left', v: 'top', label: 'Top left' }, { h: 'center', v: 'top', label: 'Top center' }, { h: 'right', v: 'top', label: 'Top right' },
  { h: 'left', v: 'center', label: 'Middle left' }, { h: 'center', v: 'center', label: 'Center' }, { h: 'right', v: 'center', label: 'Middle right' },
  { h: 'left', v: 'bottom', label: 'Bottom left' }, { h: 'center', v: 'bottom', label: 'Bottom center' }, { h: 'right', v: 'bottom', label: 'Bottom right' },
]

type Props = {
  placement: OverlayPlacement
  onChange: (patch: Partial<OverlayPlacement>) => void
}

// 3×3 grid of snap positions: the poster's center lines and SNAP_MARGIN inside each edge.
function PositionAnchors({ placement, onChange }: Props) {
  const isAnchor = (h: HAlign, v: VAlign) =>
    placement.horizontal_align === h && placement.vertical_align === v
    && placement.horizontal_offset === (h === 'center' ? 0 : SNAP_MARGIN)
    && placement.vertical_offset === (v === 'center' ? 0 : SNAP_MARGIN)

  return (
    <div className="overlay-anchors" role="group" aria-label="Snap to position">
      {ANCHORS.map((a) => (
        <button
          key={a.label}
          type="button"
          className={isAnchor(a.h, a.v) ? 'active' : ''}
          title={a.label}
          aria-label={a.label}
          onClick={() => onChange({
            horizontal_align: a.h,
            horizontal_offset: a.h === 'center' ? 0 : SNAP_MARGIN,
            vertical_align: a.v,
            vertical_offset: a.v === 'center' ? 0 : SNAP_MARGIN,
          })}
        />
      ))}
    </div>
  )
}

export default PositionAnchors
