import React, { useState } from 'react'
import { Info, RotateCcw } from 'lucide-react'
import NumberField from '../poster-manager/NumberField'
import ImageUploads from './ImageUploads'
import ListPicker from './ListPicker'
import type { OverlayField } from '../../api/overlays'
import { optionsMatch } from '../../utils/optionsMatch'

const HEX_RGB = /^#[0-9a-fA-F]{6}$/
const HEX_RGBA = /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/

const isHexColor = (value: string, alpha: boolean) => (alpha ? HEX_RGBA : HEX_RGB).test(value)

type ColorFieldProps = {
  value: string
  onChange: (value: string) => void
  alpha?: boolean
  optional?: boolean
  id?: string
  disabled?: boolean
  title?: string
  // Accessible name for the hex input (table cells have no <label>).
  ariaLabel?: string
  placeholder?: string
}

// Hex text + native picker (RGB part) + optional alpha slider. Alpha rides along as
// the trailing AA byte so the backend gets one '#RRGGBBAA' string.
export function ColorField({ value, onChange, alpha = false, optional = false, id, disabled = false, title, ariaLabel, placeholder }: ColorFieldProps) {
  const rgb = HEX_RGBA.test(value) ? value.slice(0, 7) : '#000000'
  const alphaByte = HEX_RGBA.test(value) && value.length === 9 ? parseInt(value.slice(7, 9), 16) : 255
  const alphaPct = Math.round((alphaByte / 255) * 100)
  const setAlpha = (pct: number) => {
    const byte = Math.round((Math.max(0, Math.min(100, pct)) / 100) * 255)
    onChange(`${rgb}${byte.toString(16).padStart(2, '0').toUpperCase()}`)
  }
  const valid = value === '' ? optional : isHexColor(value, alpha)
  return (
    <div className={`overlay-color-field ${disabled ? 'disabled' : ''}`} title={title}>
      <input
        type="color"
        value={rgb}
        aria-label="Pick color"
        disabled={disabled}
        onChange={(e) => onChange(alpha && value.length === 9 ? `${e.target.value.toUpperCase()}${value.slice(7, 9)}` : e.target.value.toUpperCase())}
      />
      <input
        id={id}
        type="text"
        aria-label={ariaLabel}
        className={valid ? '' : 'invalid'}
        value={value}
        placeholder={placeholder ?? (optional ? 'default' : alpha ? '#RRGGBBAA' : '#RRGGBB')}
        spellCheck={false}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value.trim())}
      />
      {alpha && (
        <label className="overlay-alpha" title={`Opacity: ${alphaPct}%`}>
          <span>{alphaPct}%</span>
          <input type="range" min={0} max={100} value={alphaPct} aria-label="Opacity" disabled={disabled} onChange={(e) => setAlpha(parseInt(e.target.value, 10))} />
        </label>
      )}
    </div>
  )
}

export function Toggle({ checked, onChange, label, id, visibleLabel = true, disabled = false }: { checked: boolean; onChange: (v: boolean) => void; label?: string; id?: string; visibleLabel?: boolean; disabled?: boolean }) {
  return (
    <div className="toggle-field">
      <label className="toggle-switch" title={visibleLabel ? undefined : label}>
        <input id={id} type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} aria-label={label} />
        <span className="toggle-slider"></span>
      </label>
      {label && visibleLabel && <span className="toggle-label">{label}</span>}
    </div>
  )
}

// Where a tooltip may show without being cut: the viewport, narrowed by every ancestor that
// clips or scrolls (the page clips sideways; the content area scrolls).
const tooltipBounds = (el: HTMLElement) => {
  const b = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight }
  for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
    const cs = getComputedStyle(a)
    const r = a.getBoundingClientRect()
    if (cs.overflowX !== 'visible') { b.left = Math.max(b.left, r.left); b.right = Math.min(b.right, r.right) }
    if (cs.overflowY !== 'visible') { b.top = Math.max(b.top, r.top); b.bottom = Math.min(b.bottom, r.bottom) }
  }
  return b
}

// Keep a tooltip on screen as its host is hovered or focused: nudged sideways to stay inside
// the page, and opened above the host when there is no room below.
const placeTip = (e: React.SyntheticEvent<HTMLElement>) => {
  const el = e.currentTarget
  const tip = el.querySelector<HTMLElement>('.toolbar-tooltip')
  if (!tip) return
  tip.style.setProperty('--tip-shift', '0px')
  tip.style.top = ''
  tip.style.bottom = ''
  tip.style.display = 'block'
  const r = tip.getBoundingClientRect()
  if (r.width === 0) return  // no layout (tests)
  const b = tooltipBounds(el)
  const pad = 8
  let dx = 0
  if (r.right > b.right - pad) dx = b.right - pad - r.right
  if (r.left + dx < b.left + pad) dx = b.left + pad - r.left
  if (dx) tip.style.setProperty('--tip-shift', `${Math.round(dx)}px`)
  const host = el.getBoundingClientRect()
  if (r.bottom > b.bottom - pad && host.top - b.top > r.height + pad) {
    tip.style.top = 'auto'
    tip.style.bottom = 'calc(100% + 8px)'
  }
}

const resetTip = (e: React.SyntheticEvent<HTMLElement>) => {
  const tip = e.currentTarget.querySelector<HTMLElement>('.toolbar-tooltip')
  if (!tip) return
  tip.style.display = ''
  tip.style.top = ''
  tip.style.bottom = ''
  tip.style.removeProperty('--tip-shift')
}

// The hover/focus "i" that explains a setting, with a tooltip that places itself (see placeTip).
export function InfoIcon({ help }: { help: string }) {
  return (
    <span className="toolbar-info" tabIndex={0} aria-label={help} onMouseEnter={placeTip} onFocus={placeTip} onMouseLeave={resetTip} onBlur={resetTip}>
      <Info size={14} />
      <div className="toolbar-tooltip">{help}</div>
    </span>
  )
}

// A field caption with its info icon beside it. Every setting on the page uses this.
export function InfoLabel({ htmlFor, label, help }: { htmlFor?: string; label: string; help?: string }) {
  return (
    <div className="field-label-row">
      <label htmlFor={htmlFor} title={label}>{label}</label>
      {help && <InfoIcon help={help} />}
    </div>
  )
}

function FieldLabel({ field, htmlFor }: { field: OverlayField; htmlFor?: string }) {
  return <InfoLabel htmlFor={htmlFor} label={field.label} help={field.help} />
}

type Values = Record<string, unknown>
type StateItem = NonNullable<OverlayField['items']>[number]

function SingleField({ field, values, onChange, idPrefix, typeKey, onAssetsChanged }: { field: OverlayField; values: Values; onChange: (key: string, value: unknown) => void; idPrefix: string; typeKey: string; onAssetsChanged?: () => void }) {
  const id = `${idPrefix}-${field.key}`
  const raw = values[field.key]
  // Grayed out while another option makes this one moot (e.g. a TMDB fallback when TMDB is the source).
  const off = Boolean(field.disabled_when) && Object.entries(field.disabled_when ?? {}).every(([key, value]) => String(values[key] ?? '') === value)
  const group = `field-group ${off ? 'disabled' : ''}`
  const why = off ? field.disabled_help : undefined
  switch (field.kind) {
    case 'toggles':
      // Several boolean options as a block of checkboxes (the five offer kinds), one cell wide.
      return (
        <div className="field-group overlay-toggles-cell">
          <FieldLabel field={field} />
          <div className="overlay-toggle-chips">
            {(field.toggles ?? []).map((t) => (
              <div key={t.key} className="overlay-toggle-chip">
                <input id={`${idPrefix}-${t.key}`} type="checkbox" checked={values[t.key] === true} onChange={(e) => onChange(t.key, e.target.checked)} />
                <label htmlFor={`${idPrefix}-${t.key}`}>{t.label}</label>
                {t.help && <InfoIcon help={t.help} />}
              </div>
            ))}
          </div>
        </div>
      )
    case 'images':
      return (
        <div className="field-group overlay-image-uploads-cell">
          <FieldLabel field={field} htmlFor={`${id}-key`} />
          <ImageUploads typeKey={typeKey} idPrefix={id} imageSet={field.set_option ? String(values[field.set_option] ?? '') || undefined : undefined} noun={field.noun} onChanged={onAssetsChanged} />
        </div>
      )
    case 'toggle':
      return (
        <div className={group} title={why}>
          <FieldLabel field={field} htmlFor={id} />
          <div className="overlay-toggle-row">
            <Toggle id={id} checked={raw === true} onChange={(v) => onChange(field.key, v)} label={field.label} visibleLabel={false} disabled={off} />
            <span className="overlay-toggle-state">{raw === true ? 'On' : 'Off'}</span>
          </div>
        </div>
      )
    case 'number':
      return (
        <div className={group} title={why}>
          <FieldLabel field={field} htmlFor={id} />
          <NumberField
            id={id}
            value={typeof raw === 'number' ? raw : Number(raw) || 0}
            onChange={(n) => onChange(field.key, n)}
            fallback={field.min ?? 0}
            min={field.min}
            max={field.max}
            step={field.step}
            disabled={off}
            className="field-w-xs"
          />
        </div>
      )
    case 'select':
      return (
        <div className={group} title={why}>
          <FieldLabel field={field} htmlFor={id} />
          <ListPicker id={id} value={String(raw ?? '')} disabled={off} options={field.options ?? []} onChange={(v) => onChange(field.key, v)} />
        </div>
      )
    case 'color':
      return (
        <div className={group} title={why}>
          <FieldLabel field={field} htmlFor={id} />
          <ColorField id={id} value={String(raw ?? '')} alpha={field.alpha} optional={field.optional} disabled={off} onChange={(v) => onChange(field.key, v)} />
        </div>
      )
    case 'text':
    default:
      return (
        <div className={group} title={why}>
          <FieldLabel field={field} htmlFor={id} />
          <input id={id} type="text" value={String(raw ?? '')} disabled={off} onChange={(e) => onChange(field.key, e.target.value)} />
        </div>
      )
  }
}

type OptionFieldsProps = {
  schema: OverlayField[]
  options: Values
  onChange: (next: Values) => void
  idPrefix?: string
  // 'fields' = the plain options as panels (siblings, for a shared panel grid);
  // 'states' = the per-state tables; 'all' = both.
  render?: 'all' | 'fields' | 'states'
  // When the shared placement has its backdrop off, per-state back_* fields are locked.
  backdropEnabled?: boolean
  // The type's default options: each state row gets a button that puts its colors back to these.
  defaults?: Values
  // The overlay type whose options these are (an image uploader addresses its files by it).
  typeKey?: string
  // Called when a field changed files on the server (an image upload), so the preview re-renders.
  onAssetsChanged?: () => void
  // Option keys the page renders somewhere else (the season toggle sits with the overlay's libraries).
  exclude?: string[]
  // render 'fields': this many sections fill the free slots in the grid's first row; the
  // sections after them are the second row's cards, which share one fixed height. Unset = all first-row.
  cardsFirst?: number
  // render 'fields': draw only the first-row cards, only the second-row cards, or both.
  part?: 'all' | 'own' | 'merged'
}

// Group fields by their `section` hint, keeping first-appearance order ('' = no heading).
const bySection = (fields: OverlayField[]) => {
  const groups: { section: string; fields: OverlayField[] }[] = []
  for (const field of fields) {
    const section = field.section ?? ''
    const group = groups.find((g) => g.section === section)
    if (group) group.fields.push(field)
    else groups.push({ section, fields: [field] })
  }
  return groups
}

// A long list can ask to be shown as several tables side by side (`split`). A number is that
// many consecutive runs of near-equal length, the first ones taking the extra row. A list is
// each table's row count, for tables that end where a group of rows ends; rows left over
// join the last table.
const chunk = <T,>(items: T[], parts: number | number[]): T[][] => {
  if (Array.isArray(parts)) {
    const out: T[][] = []
    let at = 0
    for (const size of parts) {
      if (at >= items.length) break
      out.push(items.slice(at, at + Math.max(1, size)))
      at += Math.max(1, size)
    }
    if (at < items.length) {
      if (out.length > 0) out[out.length - 1].push(...items.slice(at))
      else out.push(items.slice(at))
    }
    return out
  }
  const n = Math.max(1, Math.min(parts, items.length || 1))
  const size = Math.ceil(items.length / n)
  return Array.from({ length: n }, (_, i) => items.slice(i * size, (i + 1) * size)).filter((c) => c.length > 0)
}

// A "states" group whose items all share the same fields renders as one table, a row per
// state; otherwise (mixed fields) each item gets its own card.
const uniformFields = (group: OverlayField): OverlayField[] | null => {
  const items = group.items ?? []
  if (items.length === 0) return null
  const keys = items[0].fields.map((f) => f.key).join('|')
  return items.every((it) => it.fields.map((f) => f.key).join('|') === keys) ? items[0].fields : null
}

function StateCell({ field, values, onChange, id, ariaLabel, dimmed, unavailable, locked }: { field: OverlayField; values: Values; onChange: (key: string, value: unknown) => void; id: string; ariaLabel: string; dimmed: boolean; unavailable: boolean; locked?: string }) {
  const raw = values[field.key]
  const cls = dimmed || unavailable || locked ? 'overlay-state-cell dimmed' : 'overlay-state-cell'
  if (unavailable) {
    // This field never applies to this state (e.g. ENDED has no date): show it grayed and locked.
    return (
      <td className={cls} data-label={field.label}>
        <input id={id} type="text" aria-label={ariaLabel} value="" placeholder="n/a" disabled title="Never applies to this status" onChange={() => undefined} />
      </td>
    )
  }
  switch (field.kind) {
    case 'color':
      return <td className={cls} data-label={field.label}><ColorField id={id} ariaLabel={ariaLabel} value={String(raw ?? '')} alpha={field.alpha} optional={field.optional} disabled={Boolean(locked)} title={locked} onChange={(v) => onChange(field.key, v)} /></td>
    case 'toggle':
      return <td className={cls} data-label={field.label}><Toggle id={id} checked={raw === true} onChange={(v) => onChange(field.key, v)} label={ariaLabel} visibleLabel={false} /></td>
    case 'number':
      return <td className={cls} data-label={field.label}><NumberField id={id} ariaLabel={ariaLabel} value={typeof raw === 'number' ? raw : Number(raw) || 0} onChange={(n) => onChange(field.key, n)} fallback={field.min ?? 0} min={field.min} max={field.max} step={field.step} /></td>
    case 'select':
      return (
        <td className={cls} data-label={field.label}>
          <ListPicker id={id} ariaLabel={ariaLabel} value={String(raw ?? '')} options={field.options ?? []} onChange={(v) => onChange(field.key, v)} />
        </td>
      )
    case 'text':
    default:
      return <td className={cls} data-label={field.label}><input id={id} type="text" aria-label={ariaLabel} value={String(raw ?? '')} disabled={Boolean(locked)} title={locked} onChange={(e) => onChange(field.key, e.target.value)} /></td>
  }
}

// Renders an overlay type's option schema. Every overlay type ships its own schema from the
// backend, so a new type needs no frontend code unless it wants a custom panel.
function OptionFields({ schema, options, onChange, idPrefix = 'ov', typeKey = '', render = 'all', backdropEnabled = true, defaults, onAssetsChanged, exclude = [], cardsFirst, part = 'all' }: OptionFieldsProps) {
  const lockReason = (field: OverlayField) => (!backdropEnabled && field.key.startsWith('back_') ? 'Backdrop is off (Backdrop panel)' : undefined)
  const set = (key: string, value: unknown) => onChange({ ...options, [key]: value })
  const shown = (field: OverlayField) =>
    !field.show_when || optionsMatch(field.show_when, options)
  const simple = render === 'states' ? [] : schema.filter((f) => f.kind !== 'states' && !exclude.includes(f.key) && shown(f))
  const groups = render === 'fields' ? [] : schema.filter((f) => f.kind === 'states')
  const dependencyOff = (field: OverlayField) => Boolean(field.depends_on) && options[field.depends_on as string] !== true
  const sections = bySection(simple)
  // Rows with a priority stand in priority order, highest first. Not under the user's fingers,
  // though: while a field in a table has focus its order is held, and the rows move once focus
  // leaves the table. Group key → the item keys in the held order.
  const [held, setHeld] = useState<Record<string, string[]>>({})
  const own = part === 'merged' ? [] : sections.slice(0, cardsFirst ?? sections.length)
  const merged = part === 'own' ? [] : sections.slice(cardsFirst ?? sections.length)
  const grid = (section: { fields: OverlayField[] }) => (
    <div className="overlay-panel-grid">
      {section.fields.map((field) => (
        <SingleField key={field.key} field={field} values={options} onChange={set} idPrefix={idPrefix} typeKey={typeKey} onAssetsChanged={onAssetsChanged} />
      ))}
    </div>
  )
  return (
    <>
      {own.map((section) => (
        <div key={section.section || '_'} className="overlay-panel">
          {section.section && <h3>{section.section}</h3>}
          {grid(section)}
        </div>
      ))}
      {merged.map((section) => (
        <div key={section.section || '_'} className="overlay-panel overlay-settings-panel">
          {section.section && <h3>{section.section}</h3>}
          {grid(section)}
        </div>
      ))}
      {groups.map((group) => {
        const groupValues = (options[group.key] && typeof options[group.key] === 'object' ? options[group.key] : {}) as Record<string, Values>
        const setItem = (itemKey: string, key: string, value: unknown) =>
          onChange({ ...options, [group.key]: { ...groupValues, [itemKey]: { ...(groupValues[itemKey] ?? {}), [key]: value } } })
        const columns = uniformFields(group)
        // The per-row reset: a table with colors puts just those back (the wording stays the
        // user's); a table without any puts every field of the row back. On/off is never touched.
        const hasColors = (columns ?? []).some((f) => f.kind === 'color')
        const resettable = Boolean(defaults) && (columns ?? []).length > 0
        const rowNoun = (group.item_label ?? 'status').toLowerCase()
        const ranked = Boolean(columns?.some((f) => f.key === 'priority'))
        const priorityOf = (item: StateItem) => Number((groupValues[item.key] ?? {}).priority) || 0
        const byRank = (items: StateItem[]) => items.map((it, i) => ({ it, i })).sort((a, b) => priorityOf(b.it) - priorityOf(a.it) || a.i - b.i).map((x) => x.it)
        const all = group.items ?? []
        const heldOrder = held[group.key]
        const ordered = !ranked ? all : heldOrder ? heldOrder.map((k) => all.find((it) => it.key === k)).filter((it): it is StateItem => Boolean(it)) : byRank(all)
        const hold = () => { if (ranked && !held[group.key]) setHeld((prev) => ({ ...prev, [group.key]: ordered.map((it) => it.key) })) }
        const release = (e: React.FocusEvent<HTMLDivElement>) => {
          if (!ranked || e.currentTarget.contains(e.relatedTarget as Node | null)) return  // focus stayed in the table
          setHeld((prev) => { const next = { ...prev }; delete next[group.key]; return next })
        }
        const tables = chunk(ordered, group.split ?? 1)
        // The default values one row's reset restores, and whether the row already matches them.
        const rowDefaults = (item: StateItem) => {
          const groupDefaults = (defaults?.[group.key] ?? {}) as Record<string, Values>
          const row = groupDefaults[item.key] ?? {}
          return Object.fromEntries(item.fields.filter((f) => !hasColors || f.kind === 'color').map((f) => [f.key, row[f.key] ?? '']))
        }
        const atRowDefaults = (item: StateItem, values: Values) =>
          Object.entries(rowDefaults(item)).every(([key, value]) => String(values[key] ?? '') === String(value))
        const resetRow = (item: StateItem) =>
          onChange({ ...options, [group.key]: { ...groupValues, [item.key]: { ...(groupValues[item.key] ?? {}), ...rowDefaults(item) } } })
        return (
          <div key={group.key} className="overlay-states" onFocus={hold} onBlur={release}>
            <h3>{group.label}</h3>
            {group.help && <small className="overlay-states-help">{group.help}</small>}
            {columns ? (
              <div className={`overlay-states-tables ${tables.length > 1 ? 'split' : ''} ${columns.length === 0 ? 'bare' : ''}`} style={{ '--split': tables.length } as React.CSSProperties}>
                {tables.map((items, part) => (
              <div key={part} className="overlay-states-table-wrap">
                <table className="overlay-states-table">
                  <thead>
                    <tr>
                      <th className="overlay-states-on">On</th>
                      <th>{group.item_label ?? 'Status'}</th>
                      {columns.map((f) => (
                        <th key={f.key} className={dependencyOff(f) || lockReason(f) ? 'dimmed' : ''} title={lockReason(f) ?? (dependencyOff(f) ? `Off while "${simple.find((s) => s.key === f.depends_on)?.label ?? f.depends_on}" is off` : undefined)}>
                          <span className="overlay-th-label">{f.label}{f.help && <InfoIcon help={lockReason(f) ?? f.help} />}</span>
                        </th>
                      ))}
                      {resettable && <th className="overlay-states-reset" aria-label={hasColors ? 'Reset colors' : 'Reset to defaults'} />}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const values = groupValues[item.key] ?? {}
                      const enabled = values.enabled !== false
                      return (
                        <tr key={item.key} className={enabled ? '' : 'disabled'}>
                          <td className="overlay-states-on">
                            <Toggle checked={enabled} onChange={(v) => setItem(item.key, 'enabled', v)} label={`${item.label} ${enabled ? 'on' : 'off'}`} visibleLabel={false} />
                          </td>
                          <th scope="row">{item.label}</th>
                          {item.fields.map((field) => (
                            <StateCell
                              key={field.key}
                              field={field}
                              values={values}
                              onChange={(key, value) => setItem(item.key, key, value)}
                              id={`${idPrefix}-${group.key}-${item.key}-${field.key}`}
                              ariaLabel={`${item.label} ${field.label}`}
                              dimmed={!enabled || dependencyOff(field)}
                              unavailable={(item.disabled_fields ?? []).includes(field.key)}
                              locked={lockReason(field)}
                            />
                          ))}
                          {resettable && (
                            <td className="overlay-states-reset">
                              {/* The page's own tooltip, not the browser's: a native one can run off the right edge of the screen. */}
                              <span className="overlay-tip-host" onMouseEnter={placeTip} onFocus={placeTip} onMouseLeave={resetTip} onBlur={resetTip}>
                                <button
                                  type="button"
                                  className="btn-icon btn-icon-sm btn-icon-outlined"
                                  aria-label={hasColors ? `${item.label} default colors` : `${item.label} defaults`}
                                  disabled={atRowDefaults(item, values)}
                                  onClick={() => resetRow(item)}
                                >
                                  <RotateCcw size={13} />
                                </button>
                                <div className="toolbar-tooltip">
                                  {hasColors ? `Put this ${rowNoun}'s backdrop and text colors back to their defaults` : `Put this ${rowNoun}'s settings back to their defaults`}
                                </div>
                              </span>
                            </td>
                          )}
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
                ))}
              </div>
            ) : (
              <div className="overlay-states-grid">
                {(group.items ?? []).map((item) => {
                  const values = groupValues[item.key] ?? {}
                  const enabled = values.enabled !== false
                  return (
                    <div key={item.key} className={`overlay-state-card ${enabled ? '' : 'disabled'}`}>
                      <div className="overlay-state-head">
                        <strong>{item.label}</strong>
                        <Toggle checked={enabled} onChange={(v) => setItem(item.key, 'enabled', v)} label={enabled ? 'On' : 'Off'} />
                      </div>
                      {enabled && (
                        <div className="overlay-state-fields">
                          {item.fields.filter((field) => !(item.disabled_fields ?? []).includes(field.key)).map((field) => (
                            <SingleField
                              key={field.key}
                              field={field}
                              values={values}
                              typeKey=""
                              onChange={(key, value) => setItem(item.key, key, value)}
                              idPrefix={`${idPrefix}-${group.key}-${item.key}`}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}

export default OptionFields
