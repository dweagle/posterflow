import { ReactNode, useRef, useState } from 'react'
import { Trash2, Upload } from 'lucide-react'
import NumberField from './NumberField'
import AuthedImage from './AuthedImage'
import type { BorderStyle, GradientDirection, InnerEffect } from '../../hooks/usePosterManagerBorder'
import { BorderOverlay, deleteBorderOverlay, fetchBorderPreview, uploadBorderOverlay } from '../../api/posterManager'

// One bundle of border-style settings, shared by main posters, seasons, and holidays.
export type BorderStyleValue = {
  style: BorderStyle
  colors: string[]
  gradientColors: string[]
  gradientDirection: GradientDirection
  overlayImage: string
  removeExisting: boolean
  innerEffect: InnerEffect
  innerColor: string
  innerOpacity: number
  innerWidth: number
  fadeWidth: number
}

// A style with nothing renderable set leaves posters untouched at runtime, so the
// preview should show the original poster rather than a placeholder border.
export function isBorderPassthrough(style: BorderStyle, colors: string[], gradientColors: string[], overlayImage: string) {
  if (style === 'solid') return colors.length === 0
  if (style === 'gradient') return gradientColors.filter((c) => c.trim()).length === 0
  if (style === 'image') return !overlayImage.trim()
  return false
}

// Live preview of a full border-style value, composited onto the sample poster.
export function StyleValuePreview({ value, borderWidth }: { value: BorderStyleValue; borderWidth: number }) {
  return (
    <AuthedImage
      load={() =>
        fetchBorderPreview({
          style: value.style,
          color: value.colors[0] || '#000000',
          border_width: borderWidth,
          gradient_colors: value.gradientColors,
          gradient_direction: value.gradientDirection,
          overlay: value.style === 'image' ? value.overlayImage : '',
          remove_existing: value.removeExisting,
          inner_effect: value.innerEffect,
          inner_color: value.innerColor,
          inner_opacity: value.innerOpacity,
          inner_width: value.innerWidth,
          fade_width: value.fadeWidth,
          passthrough: isBorderPassthrough(value.style, value.colors, value.gradientColors, value.overlayImage),
        })
      }
      deps={[JSON.stringify(value), borderWidth]}
      debounceMs={350}
      alt="Border preview"
      className="border-style-preview"
      expandable
    />
  )
}

// Dropdown of available overlay frames (bundled presets + user uploads).
export function OverlaySelect({
  overlays,
  value,
  onChange,
}: {
  overlays: BorderOverlay[]
  value: string
  onChange: (name: string) => void
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ maxWidth: '240px' }}>
      <option value="">No border image</option>
      {overlays.map((overlay) => (
        <option key={overlay.name} value={overlay.name}>
          {overlay.name}{overlay.source === 'preset' ? ' (preset)' : ''}
        </option>
      ))}
    </select>
  )
}

type Props = {
  value: BorderStyleValue
  onChange: (patch: Partial<BorderStyleValue>) => void
  overlays: BorderOverlay[]
  refreshOverlays: () => void
  // Unique prefix so radio groups in different instances don't collide.
  idPrefix: string
  // Optional label above the style dropdown.
  label?: string
  // Optional live preview, rendered in a column beside the controls.
  preview?: ReactNode
  // Show helper descriptions under each control (used by the Season section).
  verbose?: boolean
  // Offer a "Remove borders" style option (used by Plex rules).
  allowRemove?: boolean
}

function BorderStyleControls({ value, onChange, overlays, refreshOverlays, idPrefix, label = 'Border Style', preview, verbose = false, allowRemove = false }: Props) {
  const [newColor, setNewColor] = useState('#ffffff')
  const [newGradientColor, setNewGradientColor] = useState('#ffffff')
  const [overlayError, setOverlayError] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addColor = () => {
    if (newColor && !value.colors.includes(newColor)) {
      onChange({ colors: [...value.colors, newColor] })
      setNewColor('#ffffff')
    }
  }
  const removeColor = (color: string) => onChange({ colors: value.colors.filter((c) => c !== color) })

  const addGradientColor = () => {
    if (newGradientColor && !value.gradientColors.includes(newGradientColor)) {
      onChange({ gradientColors: [...value.gradientColors, newGradientColor] })
      setNewGradientColor('#ffffff')
    }
  }
  const removeGradientColor = (color: string) => onChange({ gradientColors: value.gradientColors.filter((c) => c !== color) })

  const handleUploadOverlay = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setOverlayError('')
    try {
      const result = await uploadBorderOverlay(file)
      refreshOverlays()
      onChange({ overlayImage: result.name })
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setOverlayError(detail || 'Failed to upload overlay. Must be a 1000x1500 PNG with transparency.')
    }
  }

  const handleDeleteOverlay = async (name: string) => {
    setOverlayError('')
    try {
      await deleteBorderOverlay(name)
      if (value.overlayImage === name) onChange({ overlayImage: '' })
      refreshOverlays()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setOverlayError(detail || 'Failed to delete overlay.')
    }
  }

  return (
    <div className="border-style-layout">
      {preview && (
        <div className="border-style-preview-col">
          <div className="border-style-preview-wrap">
            <span className="border-style-preview-label">Preview — click to enlarge</span>
            {preview}
          </div>
        </div>
      )}
      <div className="border-style-settings-col">
      <div className="settings-grid">
      <div className="field-group">
      <label>{label}</label>
      {verbose && (
        <small style={{ marginBottom: '0.75rem', display: 'block' }}>
          How the replaced border is rendered. <strong>Solid</strong> uses the colors below;
          <strong> Gradient</strong> blends colors across the band; <strong>Image overlay</strong> composites a PNG frame you design.
        </small>
      )}
      <select
        value={value.style}
        onChange={(e) => onChange({ style: e.target.value as BorderStyle })}
        style={{ maxWidth: '240px', display: 'block' }}
      >
        <option value="solid">Solid color</option>
        <option value="gradient">Gradient band</option>
        <option value="image">Image overlay (frame)</option>
        {allowRemove && <option value="remove">Remove borders</option>}
      </select>

      {value.style === 'remove' && (
        <small style={{ marginTop: '0.5rem', display: 'block' }}>
          Matched items have their existing border stripped — exactly like Remove Borders mode
          (top/left/right cropped, black title bar). No new border or inner effect is applied.
        </small>
      )}

      {value.style === 'solid' && (
        <div style={{ marginTop: '0.75rem' }}>
          {verbose && (
            <>
              <label>Colors</label>
              <small style={{ marginBottom: '0.5rem', display: 'block' }}>Add one or more hex colors. Colors cycle through posters.</small>
            </>
          )}
          <div className="color-input-row">
            <input type="color" value={newColor} onChange={(e) => setNewColor(e.target.value)} className="color-picker" />
            <input type="text" value={newColor} onChange={(e) => setNewColor(e.target.value)} placeholder="#000000" />
            <button className="btn-secondary" onClick={addColor} disabled={!newColor || value.colors.includes(newColor)}>Add Color</button>
          </div>
          {value.colors.length > 0 ? (
            <div className="color-list" style={{ marginTop: '0.5rem' }}>
              {value.colors.map((color, index) => (
                <div key={index} className="color-item">
                  <div className="color-preview" style={{ background: color }} />
                  <span className="color-value">{color}</span>
                  <button className="btn-remove-color" onClick={() => removeColor(color)} title="Remove color"><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-config"><p>No colors added. Add at least one color.</p></div>
          )}
        </div>
      )}

      {value.style === 'gradient' && (
        <div style={{ marginTop: '0.75rem' }}>
          <label>Gradient Colors</label>
          {verbose && <small style={{ marginBottom: '0.5rem', display: 'block' }}>Two or more colors, blended in order across the border.</small>}
          <div className="color-input-row">
            <input type="color" value={newGradientColor} onChange={(e) => setNewGradientColor(e.target.value)} className="color-picker" />
            <input type="text" value={newGradientColor} onChange={(e) => setNewGradientColor(e.target.value)} placeholder="#000000" />
            <button className="btn-secondary" onClick={addGradientColor} disabled={!newGradientColor || value.gradientColors.includes(newGradientColor)}>Add Color</button>
          </div>
          {value.gradientColors.length > 0 ? (
            <div className="color-list" style={{ marginTop: '0.5rem' }}>
              {value.gradientColors.map((color, index) => (
                <div key={index} className="color-item">
                  <div className="color-preview" style={{ background: color }} />
                  <span className="color-value">{color}</span>
                  <button className="btn-remove-color" onClick={() => removeGradientColor(color)} title="Remove color"><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-config"><p>Add at least two colors for a gradient.</p></div>
          )}
          <div style={{ marginTop: '0.5rem' }}>
            <label>Direction</label>
            <select
              value={value.gradientDirection}
              onChange={(e) => onChange({ gradientDirection: e.target.value as GradientDirection })}
              style={{ maxWidth: '200px', display: 'block' }}
            >
              <option value="vertical">Vertical</option>
              <option value="horizontal">Horizontal</option>
              <option value="diagonal">Diagonal</option>
            </select>
          </div>
        </div>
      )}

      {value.style === 'image' && (
        <div style={{ marginTop: '0.75rem' }}>
          <label>Border Frame Image</label>
          {verbose && (
            <small style={{ marginBottom: '0.5rem', display: 'block' }}>
              Choose a 1000×1500 PNG frame (transparent center). Bundled presets and your uploads are listed.
            </small>
          )}
          <div className="border-overlay-row">
            <OverlaySelect overlays={overlays} value={value.overlayImage} onChange={(name) => onChange({ overlayImage: name })} />
            <input ref={fileInputRef} type="file" accept="image/png" style={{ display: 'none' }} onChange={handleUploadOverlay} />
            <button className="btn-secondary" onClick={() => fileInputRef.current?.click()}><Upload size={16} /> Upload</button>
            {value.overlayImage && overlays.find((o) => o.name === value.overlayImage)?.source === 'user' && (
              <button className="btn-remove-color" onClick={() => handleDeleteOverlay(value.overlayImage)} title="Delete uploaded overlay"><Trash2 size={16} /></button>
            )}
          </div>
          {overlayError && <small style={{ color: 'var(--error, #e55)', display: 'block' }}>{overlayError}</small>}
        </div>
      )}

      {value.style !== 'remove' && (
      <>
      <div className="toggle-field" style={{ marginTop: '0.75rem' }}>
        <label className="toggle-switch">
          <input type="checkbox" checked={value.removeExisting} onChange={(e) => onChange({ removeExisting: e.target.checked })} />
          <span className="toggle-slider"></span>
        </label>
        <span className="toggle-label">Remove existing border first</span>
      </div>
      {verbose && (
        <small style={{ display: 'block' }}>
          Strip the existing border first — exactly like Remove Borders mode (top/left/right cropped, black title bar) — before applying the new border.
        </small>
      )}
      </>
      )}
      </div>
      </div>
      </div>
      {value.style !== 'remove' && (
      <div className="border-style-settings-col">
      <div className="settings-grid">
      <div className="field-group">
        <label>Inner Edge Effect</label>
        {verbose && (
          <small style={{ marginBottom: '0.75rem', display: 'block' }}>
            Adds a soft transition just inside the border. Source posters already carry some baked-in inner glow, so keep these subtle.
          </small>
        )}
        <div className="season-mode-options">
          <label className="radio-label"><input type="radio" name={`${idPrefix}-inner`} checked={value.innerEffect === 'none'} onChange={() => onChange({ innerEffect: 'none' })} /><span>None</span></label>
          <label className="radio-label"><input type="radio" name={`${idPrefix}-inner`} checked={value.innerEffect === 'glow'} onChange={() => onChange({ innerEffect: 'glow' })} /><span>Dark inner glow</span></label>
          <label className="radio-label"><input type="radio" name={`${idPrefix}-inner`} checked={value.innerEffect === 'fade'} onChange={() => onChange({ innerEffect: 'fade' })} /><span>Border-color fade</span></label>
        </div>
        {value.innerEffect === 'glow' && (
          <div style={{ marginTop: '0.5rem' }}>
            <div className="color-input-row">
              <input type="color" value={value.innerColor} onChange={(e) => onChange({ innerColor: e.target.value })} className="color-picker" />
              <input type="text" value={value.innerColor} onChange={(e) => onChange({ innerColor: e.target.value })} placeholder="#000000" />
            </div>
            <div style={{ marginTop: '0.5rem' }}>
              <label>Opacity: {value.innerOpacity}%</label>
              <input type="range" min="0" max="100" value={value.innerOpacity} onChange={(e) => onChange({ innerOpacity: parseInt(e.target.value) || 0 })} style={{ display: 'block', maxWidth: '240px' }} />
            </div>
            <div style={{ marginTop: '0.5rem' }}>
              <label>Glow Width (pixels)</label>
              <NumberField value={value.innerWidth} onChange={(n) => onChange({ innerWidth: n })} fallback={1} min={1} max={200} style={{ maxWidth: '120px' }} />
            </div>
          </div>
        )}
        {value.innerEffect === 'fade' && (
          <div style={{ marginTop: '0.5rem' }}>
            <label>Fade Width (pixels)</label>
            <NumberField value={value.fadeWidth} onChange={(n) => onChange({ fadeWidth: n })} fallback={1} min={1} max={200} style={{ maxWidth: '120px' }} />
          </div>
        )}
      </div>
      </div>
      </div>
      )}
    </div>
  )
}

export default BorderStyleControls
