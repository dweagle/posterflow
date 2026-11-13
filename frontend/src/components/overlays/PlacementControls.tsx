import NumberField from '../poster-manager/NumberField'
import { ColorField, InfoLabel, Toggle } from './OptionFields'
import ListPicker from './ListPicker'
import PositionAnchors from './PositionAnchors'
import type { OverlayFont, OverlayPlacement } from '../../api/overlays'

const H_ALIGNS = [{ value: 'left', label: 'Left' }, { value: 'center', label: 'Center' }, { value: 'right', label: 'Right' }]
const V_ALIGNS = [{ value: 'top', label: 'Top' }, { value: 'center', label: 'Center' }, { value: 'bottom', label: 'Bottom' }]
const BACK_STYLES = [{ value: 'solid', label: 'Solid box' }, { value: 'gradient', label: 'Gradient box' }, { value: 'band', label: 'Edge band' }]
const GRADIENT_MODES = [{ value: 'linear', label: 'Linear' }, { value: 'center', label: 'From center' }]
const BAND_CURVES = [{ value: 'linear', label: 'Linear' }, { value: 'soft', label: 'Soft' }]

type Props = {
  placement: OverlayPlacement
  fonts: OverlayFont[]
  onChange: (patch: Partial<OverlayPlacement>) => void
  // Placement keys the overlay type sets itself (per state) — not shown here. 'text' and
  // 'backdrop' hide those whole panels (an image badge has no text to style).
  hidden?: string[]
  // Which panel(s) to draw; the page lays each one out as its own card.
  part?: 'all' | 'position' | 'style' | 'text' | 'backdrop'
  // Set while another overlay places the badge: the Position and Backdrop cards are greyed out
  // and the Position card shows this note.
  lockedNote?: string
}

// Kometa-style placement: where the badge sits (align + offset in 1000x1500 poster space),
// how the text looks, and the backdrop behind it. Shared by every overlay type.
function PlacementControls({ placement, fonts, onChange, hidden = [], part = 'all', lockedNote }: Props) {
  const draw = (name: 'position' | 'text' | 'backdrop') => part === 'all' || part === name || (part === 'style' && name !== 'position')
  const show = (key: keyof OverlayPlacement) => !hidden.includes(key)
  const panel = (name: 'text' | 'backdrop') => !hidden.includes(name)
  const weights = fonts.find((f) => f.id === placement.font)?.weights ?? []
  const isVariable = weights.length > 0
  const locked = Boolean(lockedNote)
  const backOff = !placement.back_enabled || locked
  const style = placement.back_style
  const hasBox = style !== 'band'
  const num = (key: keyof OverlayPlacement, label: string, min: number, max: number, help?: string, disabled = false) => (
    <div className="field-group">
      <InfoLabel htmlFor={`pl-${key}`} label={label} help={help} />
      <NumberField
        id={`pl-${key}`}
        className="overlay-num"
        value={Number(placement[key])}
        onChange={(n) => onChange({ [key]: n } as Partial<OverlayPlacement>)}
        fallback={Number(placement[key])}
        min={min}
        max={max}
        disabled={disabled}
        title={help}
      />
    </div>
  )
  return (
    <>
      {draw('position') && (
      <div className={`overlay-panel ${locked ? 'off' : ''}`}>
        <h3>Position</h3>
        <fieldset className="overlay-panel-fields" disabled={locked}>
        <div className="overlay-panel-grid">
          <div className="field-group">
            <InfoLabel htmlFor="pl-horizontal_align" label="Horizontal" help="Which side of the poster the badge is measured from." />
            <ListPicker id="pl-horizontal_align" value={placement.horizontal_align} disabled={locked} options={H_ALIGNS} onChange={(v) => onChange({ horizontal_align: v as OverlayPlacement['horizontal_align'] })} />
          </div>
          {num('horizontal_offset', 'Horizontal offset', -1000, 1000, 'Distance from that side in px of a 1000×1500 poster; from the middle when centered.', locked)}
          <div className="field-group">
            <InfoLabel htmlFor="pl-vertical_align" label="Vertical" help="Whether the badge is measured from the top, the middle or the bottom." />
            <ListPicker id="pl-vertical_align" value={placement.vertical_align} disabled={locked} options={V_ALIGNS} onChange={(v) => onChange({ vertical_align: v as OverlayPlacement['vertical_align'] })} />
          </div>
          {num('vertical_offset', 'Vertical offset', -1500, 1500, 'Distance from that edge in px; from the middle when centered.', locked)}
        </div>
        </fieldset>
        <div className="overlay-position-footer">
          <PositionAnchors placement={placement} onChange={onChange} disabled={locked} />
          {locked ? (
            <p className="overlay-panel-hint overlay-lock-note">{lockedNote}</p>
          ) : (
            <ul className="overlay-panel-hint overlay-position-tips">
              <li>Offsets are pixels on a 1000×1500 poster.</li>
              <li>Drag any badge to move it, another overlay's too.</li>
              <li>Arrow keys nudge a selected badge (Shift = 10px).</li>
              <li className="overlay-snap-hint">Hold Alt to skip the snap lines.</li>
            </ul>
          )}
        </div>
      </div>
      )}

      {draw('text') && panel('text') && (
      <div className="overlay-panel">
        <h3>Text</h3>
        <div className="overlay-panel-grid">
          <div className="field-group">
            <InfoLabel htmlFor="pl-font" label="Font" help="Bundled fonts plus any .ttf/.otf you drop into config/artwork/fonts." />
            <ListPicker
              id="pl-font"
              value={placement.font}
              options={[
                ...(fonts.some((f) => f.id === placement.font) ? [] : [{ value: placement.font, label: placement.font }]),
                ...fonts.map((f) => ({ value: f.id, label: `${f.label}${f.source === 'config' ? ' (yours)' : ''}` })),
              ]}
              onChange={(v) => onChange({ font: v })}
            />
          </div>
          <div className={`field-group ${isVariable ? '' : 'disabled'}`}>
            <InfoLabel htmlFor="pl-font_weight" label="Weight" help="Named weights built into a variable font. A fixed font has one weight." />
            <ListPicker
              id="pl-font_weight"
              value={isVariable ? placement.font_weight : ''}
              disabled={!isVariable}
              title={isVariable ? 'Named weights built into this variable font' : 'This font has one fixed weight; pick a variable font (like the bundled Roboto Condensed) to change it'}
              options={isVariable
                ? [
                    { value: '', label: 'Font default' },
                    ...(!weights.includes(placement.font_weight) && placement.font_weight ? [{ value: placement.font_weight, label: placement.font_weight }] : []),
                    ...weights.map((w) => ({ value: w, label: w })),
                  ]
                : [{ value: '', label: 'Not a variable font' }]}
              onChange={(v) => onChange({ font_weight: v })}
            />
          </div>
          {num('font_size', 'Size', 8, 400, 'Text height in px of a 1000×1500 poster; scales with the real poster.')}
          {num('letter_spacing', 'Letter spacing', 0, 100, 'Extra space between letters, in px.')}
          <div className="field-group">
            <InfoLabel htmlFor="pl-font_color" label="Default text color" help="Used by every status unless a status sets its own text color below." />
            <ColorField id="pl-font_color" value={placement.font_color} alpha onChange={(v) => onChange({ font_color: v })} />
          </div>
          <div className="overlay-field-pair">
            {num('stroke_width', 'Outline width', 0, 20, 'Stroke drawn around the letters; 0 = none.')}
            <div className={`field-group ${placement.stroke_width > 0 ? '' : 'disabled'}`}>
              <InfoLabel htmlFor="pl-stroke_color" label="Outline color" help="Color of the stroke around the letters." />
              <ColorField
                id="pl-stroke_color"
                value={placement.stroke_color}
                disabled={placement.stroke_width <= 0}
                title={placement.stroke_width > 0 ? undefined : 'Set an outline width first'}
                onChange={(v) => onChange({ stroke_color: v })}
              />
            </div>
          </div>
        </div>
        <small className="overlay-panel-hint">Drop .ttf/.otf files into config/artwork/fonts to add your own.</small>
      </div>
      )}

      {draw('backdrop') && panel('backdrop') && (
      <div className={`overlay-panel ${backOff ? 'off' : ''}`} title={lockedNote}>
        <div className="overlay-panel-head">
          <h3>Backdrop</h3>
          <div className="overlay-toggle-row">
            <Toggle checked={placement.back_enabled} disabled={locked} onChange={(v) => onChange({ back_enabled: v })} label={placement.back_enabled ? 'Backdrop on' : 'Backdrop off'} visibleLabel={false} />
            <span className="overlay-toggle-state">{placement.back_enabled ? 'On' : 'Off'}</span>
          </div>
        </div>
        <fieldset className="overlay-panel-fields" disabled={backOff}>
        <div className="overlay-panel-grid">
          <div className="field-group">
            <InfoLabel htmlFor="pl-back_style" label="Style" help="Solid box: a flat box behind the badge. Gradient box: the box fades along an angle, or stays solid and glows out past its edges. Edge band: a full-width fade down from the top of the poster instead of a box. The band is drawn once per poster even when several overlays use it." />
            <ListPicker id="pl-back_style" value={style} disabled={backOff} options={BACK_STYLES} onChange={(v) => onChange({ back_style: v as OverlayPlacement['back_style'] })} />
          </div>
          {style === 'solid' && show('back_color') && (
            <div className="field-group">
              <InfoLabel htmlFor="pl-back_color" label="Color" help="Color of the box behind the badge." />
              <ColorField id="pl-back_color" value={placement.back_color} alpha disabled={backOff} onChange={(v) => onChange({ back_color: v })} />
            </div>
          )}
          {style !== 'solid' && (
            <div className="field-group">
              <InfoLabel htmlFor="pl-gradient_start" label="Fade color" help="Fades to transparent. Leave blank to use the backdrop color." />
              <ColorField id="pl-gradient_start" value={placement.gradient_start} alpha optional placeholder="backdrop color" disabled={backOff} onChange={(v) => onChange({ gradient_start: v })} />
            </div>
          )}
          {style === 'gradient' && (
            <>
              <div className="field-group">
                <InfoLabel htmlFor="pl-gradient_mode" label="Direction" help="Linear fades along the angle. From center keeps the box solid and glows out past its edges by the spread." />
                <ListPicker id="pl-gradient_mode" value={placement.gradient_mode} disabled={backOff} options={GRADIENT_MODES} onChange={(v) => onChange({ gradient_mode: v as OverlayPlacement['gradient_mode'] })} />
              </div>
              {placement.gradient_mode === 'center' ? (
                num('gradient_spread', 'Spread', 0, 500, 'How far the glow reaches past the box, in px.', backOff)
              ) : (
                num('gradient_angle', 'Angle', 0, 359, 'Direction of the fade: 0 = to top, 90 = to right, 180 = to bottom.', backOff)
              )}
            </>
          )}
          {style === 'band' && (
            <>
              <div className="field-group">
                <InfoLabel htmlFor="pl-gradient_band_curve" label="Fade" help="How the band fades out. Linear is a straight fade over its height. Soft starts the same and eases out toward the end, for a gentler edge." />
                <ListPicker id="pl-gradient_band_curve" value={placement.gradient_band_curve ?? 'linear'} disabled={backOff} options={BAND_CURVES} onChange={(v) => onChange({ gradient_band_curve: v as OverlayPlacement['gradient_band_curve'] })} />
              </div>
              <div className="overlay-field-pair compact">
                {num('gradient_band_offset', 'From top', 0, 1400, 'Distance from the top of the poster to the solid side of the band.', backOff)}
                {num('gradient_band_height', 'Height', 20, 1500, 'How far the band fades in from its solid side toward the middle.', backOff)}
              </div>
              <div className="overlay-field-pair compact">
                {num('gradient_band_left', 'From left', 0, 1000, 'Inset from the left side of the poster.', backOff)}
                {num('gradient_band_right', 'From right', 0, 1000, 'Inset from the right side of the poster.', backOff)}
              </div>
            </>
          )}
          {hasBox && (
            <>
              <div className="overlay-field-pair compact">
                {num('back_padding', 'Padding', 0, 200, 'Space between the badge and the edge of the box.', backOff)}
                {num('back_radius', 'Radius', 0, 500, 'Rounding of the box corners; 0 = square.', backOff)}
              </div>
              <div className="overlay-field-pair compact">
                {num('back_min_width', 'Min', 0, 1000, 'Minimum box width: the box is at least this wide, so short and long badges get the same size box. 0 = fit the badge.', backOff)}
                {num('back_max_width', 'Max', 0, 1000, 'Maximum box width: the box is never wider than this. A badge that would not fit is made smaller for that item only. 0 = no limit.', backOff)}
              </div>
              <div className="overlay-field-pair">
                {num('back_line_width', 'Outline width', 0, 50, 'Line drawn around the box; 0 = none.', backOff)}
                <div className={`field-group ${!backOff && placement.back_line_width > 0 ? '' : 'disabled'}`}>
                  <InfoLabel htmlFor="pl-back_line_color" label="Outline color" help="Color of the line around the box." />
                  <ColorField
                    id="pl-back_line_color"
                    value={placement.back_line_color}
                    alpha
                    disabled={backOff || placement.back_line_width <= 0}
                    title={backOff || placement.back_line_width > 0 ? undefined : 'Set an outline width first'}
                    onChange={(v) => onChange({ back_line_color: v })}
                  />
                </div>
              </div>
            </>
          )}
        </div>
        </fieldset>
      </div>
      )}
    </>
  )
}

export default PlacementControls
