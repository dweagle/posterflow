import client, { deleteData, getData, postData } from './http'
import type { PlexLibraryConfig } from './client'

// 'images' = the uploader for a type's PNG badges (file state, not an option value).
// 'toggles' = several boolean options as one compact row of switches.
export type OverlayFieldKind = 'number' | 'toggle' | 'text' | 'color' | 'select' | 'states' | 'images' | 'toggles'

export interface OverlayField {
  kind: OverlayFieldKind
  key: string
  label: string
  help?: string
  // Sub-heading the field is grouped under (fields with the same section render together).
  section?: string
  // Key of a top-level toggle option; the field is dimmed while that toggle is off.
  depends_on?: string
  // Only rendered while every listed option holds that value (a setting that matters for one style only).
  // Shown only while each named option has that value (or one of those values).
  show_when?: Record<string, string | string[]>
  // Rendered but grayed out while every listed option holds that value (a setting another one makes moot);
  // `disabled_help` says why, on hover.
  disabled_when?: Record<string, string>
  disabled_help?: string
  min?: number
  max?: number
  step?: number // fractional = a decimal number field
  alpha?: boolean
  optional?: boolean
  options?: { value: string; label: string }[]
  // kind === 'states': one row/card per item, each with its own fields (+ an enabled toggle).
  // `disabled_fields` names fields that never apply to that item (rendered grayed out).
  items?: { key: string; label: string; fields: OverlayField[]; disabled_fields?: string[] }[]
  // kind === 'states': heading of the item column ('Status' when unset).
  item_label?: string
  // kind === 'toggles': the boolean option keys shown as one row of switches.
  toggles?: { key: string; label: string; help?: string }[]
  // kind === 'states': show the rows as this many tables side by side (long lists), or as
  // tables of these row counts.
  split?: number | number[]
  // kind === 'images': the option that holds the image set drawn (none = the type has one set), and what one file is called.
  set_option?: string
  noun?: string
}

export interface OverlayPlacement {
  horizontal_align: 'left' | 'center' | 'right'
  horizontal_offset: number
  vertical_align: 'top' | 'center' | 'bottom'
  vertical_offset: number
  font: string
  font_weight: string
  font_size: number
  font_color: string
  letter_spacing: number
  stroke_width: number
  stroke_color: string
  // false = text only: nothing behind the text, per-state backdrop colors are ignored
  back_enabled: boolean
  // solid = flat box; gradient = the box takes a gradient; band = full-width fade at the badge's poster edge, no box
  back_style: 'solid' | 'gradient' | 'band'
  back_color: string
  back_padding: number
  back_min_width: number // the box is at least this wide (reference px); 0 = fit the text
  back_max_width: number // and at most this wide: overflowing text is shrunk for that badge only; 0 = no limit
  back_radius: number
  back_line_color: string
  back_line_width: number
  // Gradient and band styles fade this color to transparent; '' = the status/backdrop color
  gradient_start: string
  gradient_mode: 'linear' | 'center' // gradient box: along the angle, or solid middle fading out to the box edges
  gradient_angle: number // linear: CSS-style degrees (0 = to top, 90 = to right)
  gradient_spread: number // center: how far the glow reaches past the box, reference px
  // band: solid side `offset` px in from the top or bottom poster edge, fading to clear over its height; side insets narrow it (reference px)
  gradient_band_offset: number
  gradient_band_height: number
  gradient_band_left: number
  gradient_band_right: number
  // band: how it fades out. linear = a straight fade; soft = eases out toward its end.
  gradient_band_curve: 'linear' | 'soft'
}

export interface OverlayTypeInfo {
  key: string
  label: string
  description: string
  order: number
  targets: string[]
  needs: string[]
  // Shared placement fields this type sets per state, so the page hides them; 'text' and
  // 'backdrop' hide those whole panels (an image badge has no text to style).
  hidden_placement: string[]
  // While the options hold these values another overlay places the badge: the Position and
  // Backdrop cards are greyed out and show the note.
  placement_lock?: { when: Record<string, unknown>; note: string } | null
  schema: OverlayField[]
  // A variant may take its label from an option's current value (`labels[value]`); a value
  // with no label hides the variant (e.g. the fallback logo when "On no service" is Nothing).
  // One that stands for a row of a states table names it (`group` = the table's option key,
  // `item` = the row): shown only while that row is on, ordered by the row's priority if any.
  variants: { key: string; label: string; option?: string; labels?: Record<string, string>; group?: string; item?: string }[]
  default_placement: OverlayPlacement
  default_options: Record<string, unknown>
}

// One named overlay of a type: it applies to every selected library, or only to the
// "instance:key" libraries it names, with its own placement and options. An item takes the
// overlay naming its library, else one set to all libraries; a library is named by one overlay per type.
export interface OverlayDefinition {
  name: string
  enabled: boolean // off = as if it did not exist, its libraries fall back to an all-libraries overlay
  all_libraries: boolean
  libraries: string[]
  placement: OverlayPlacement
  options: Record<string, unknown>
}

export interface OverlayTypeConfig {
  enabled: boolean
  overlays: OverlayDefinition[]
}

// Overlays are applied straight to the media servers' posters. The master switch turns the
// feature on; `all_libraries` covers every enabled movie/show library (including ones
// enabled later), otherwise only the "instance:key" entries in `libraries`.
export interface OverlaysConfig {
  enabled: boolean
  all_libraries: boolean
  libraries: string[]
  // Also badge items Posterflow has no poster file for, on the media server's own poster.
  // Nothing is saved: removing those badges puts the server's own poster back.
  badge_server_posters: boolean
  // After a badge goes up, remove the superseded uploads from the item's Plex bundle
  // (needs the Plex Metadata folder from the Tools page).
  prune_plex_uploads: boolean
  types: Record<string, OverlayTypeConfig>
}

export interface OverlaysConfigResponse {
  config: OverlaysConfig
  has_tmdb_key: boolean
  // Enabled movie/show libraries per media server, for the library picker.
  library_configs: PlexLibraryConfig[]
  // Items currently carrying a badge on a server (per the overlay state).
  badged_count: number
}

export interface OverlayFont {
  id: string
  label: string
  source: 'config' | 'bundled'
  // Named weights of a variable font; empty for a static font (weight control disabled).
  weights: string[]
}

export const getOverlayTypes = async (): Promise<{ types: OverlayTypeInfo[] }> => getData('/api/overlays/types')

export const getOverlaysConfig = async (): Promise<OverlaysConfigResponse> => getData('/api/overlays/config')

export const saveOverlaysConfig = async (config: OverlaysConfig): Promise<{ success: boolean; config: OverlaysConfig; badged_count: number }> =>
  postData('/api/overlays/config', config)

export const getOverlayFonts = async (): Promise<{ fonts: OverlayFont[] }> => getData('/api/overlays/fonts')


export interface OverlayPreviewResult {
  blob: Blob
  // Badge rectangle in 1000x1500 poster space (all zeros when nothing was drawn).
  rect: { x: number; y: number; w: number; h: number }
  text: string
  // The other layers that drew a badge, with their rectangles, so those can be dragged too.
  layers: { type: string; rect: { x: number; y: number; w: number; h: number } }[]
}

export type OverlayPreviewBase = 'plain' | 'bordered'

// Another enabled overlay drawn under the one being edited, so the preview shows where every badge sits.
export interface OverlayPreviewLayer {
  type: string
  variant: string
  placement: OverlayPlacement
  options: Record<string, unknown>
}

export const fetchOverlayPreview = async (params: {
  type: string
  variant: string
  placement: OverlayPlacement
  options: Record<string, unknown>
  // false: the overlay being edited is switched off; only the other layers are drawn.
  enabled?: boolean
  // 'bordered' renders the sample poster through the saved border settings first.
  base?: OverlayPreviewBase
  others?: OverlayPreviewLayer[]
}): Promise<OverlayPreviewResult> => {
  const resp = await client.post('/api/overlays/preview', params, { responseType: 'blob' })
  const raw = String(resp.headers?.['x-overlay-rect'] ?? '0,0,0,0')
  const [x, y, w, h] = raw.split(',').map((v) => parseInt(v, 10) || 0)
  // The badge's text arrives percent-encoded (a header carries plain ASCII only).
  const encoded = String(resp.headers?.['x-overlay-text'] ?? '')
  let text = encoded
  try { text = decodeURIComponent(encoded) } catch { /* not encoded: use it as it is */ }
  let layers: OverlayPreviewResult['layers'] = []
  try {
    const parsed = JSON.parse(String(resp.headers?.['x-overlay-layers'] ?? '[]')) as { type: string; rect: number[] }[]
    layers = parsed.map((l) => ({ type: l.type, rect: { x: l.rect[0], y: l.rect[1], w: l.rect[2], h: l.rect[3] } }))
  } catch { /* an older server: nothing else to drag */ }
  return { blob: resp.data as Blob, rect: { x, y, w, h }, text, layers }
}

// mode 'restore' uploads every badged item's clean poster again, whether overlays are on or off.
export const runOverlays = async (options?: { dry_run?: boolean; mode?: 'full' | 'incremental' | 'rebuild' | 'restore' }): Promise<{
  success: boolean
  job_id: number
  message: string
}> => postData('/api/overlays/run', options ?? {})

// ── image badges (one PNG per key, per set; a custom file replaces the bundled one) ──

// A type names its own sets (white/color, compact/standard, or a single default one). With no
// set given the server uses the type's first set.
export type OverlayImageSet = string

export interface OverlayImage {
  key: string
  label: string
  // custom = uploaded; bundled = shipped; fallback = this set has none, the default set's file is drawn.
  source: 'custom' | 'bundled' | 'fallback' | 'none'
  custom: boolean
}

export interface OverlayImageList {
  type: string
  set: OverlayImageSet
  set_label: string
  // The label of the type's default set, whose file stands in where this set has none.
  fallback_label: string
  noun: string
  folder: string
  images: OverlayImage[]
}

const setParams = (set?: OverlayImageSet) => (set ? { set } : {})

export const getOverlayImages = async (type: string, set?: OverlayImageSet): Promise<OverlayImageList> =>
  getData(`/api/overlays/images/${encodeURIComponent(type)}`, { params: setParams(set) })

export const fetchOverlayImage = async (type: string, key: string, set?: OverlayImageSet): Promise<Blob> => {
  const resp = await client.get(`/api/overlays/images/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, { params: setParams(set), responseType: 'blob' })
  return resp.data as Blob
}

export const uploadOverlayImage = async (type: string, key: string, file: File, set?: OverlayImageSet): Promise<{ success: boolean; type: string; key: string; set: OverlayImageSet; custom: boolean; width: number; height: number }> => {
  const formData = new FormData()
  formData.append('file', file)
  return postData(`/api/overlays/images/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, formData, { params: setParams(set), headers: { 'Content-Type': 'multipart/form-data' } })
}

export const deleteOverlayImage = async (type: string, key: string, set?: OverlayImageSet): Promise<{ success: boolean; type: string; key: string; set: OverlayImageSet; custom: boolean }> =>
  deleteData(`/api/overlays/images/${encodeURIComponent(type)}/${encodeURIComponent(key)}`, { params: setParams(set) })
