import { useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import NumberField from '../poster-manager/NumberField'
import BadgeImage from './BadgeImage'
import RuleEditor from './RuleEditor'
import type { OverlayBadgeRow, OverlayField, OverlayRule, OverlayRuleField } from '../../api/overlays'

type Props = {
  // The schema's `badges` field: the catalog of things a condition can look at, and the modes.
  field: OverlayField
  rows: OverlayBadgeRow[]
  // Whether the overlay's badges are text or images.
  kind: string
  onChange: (rows: OverlayBadgeRow[]) => void
  // The badge being previewed, whose conditions are open. Unset = this keeps track itself.
  selected?: string
  onSelect?: (id: string) => void
  idPrefix: string
  // The libraries the overlay covers ("instance:key"): their labels, genres and the like are offered as values.
  scope?: string[]
  // The page's own switch and info icon.
  toggle: (checked: boolean, onChange: (on: boolean) => void, label: string) => ReactNode
  info: (help: string) => ReactNode
}

const newId = (taken: Set<string>): string => {
  for (;;) {
    const id = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0')
    if (!taken.has(id)) return id
  }
}

// What a badge's conditions come to, in a few words.
const summary = (row: OverlayBadgeRow, specs: OverlayRuleField[]): string => {
  if (row.rules.length === 0) return 'Every poster'
  if (row.rules.length > 1) return `${row.rules.length} conditions, ${row.match === 'any' ? 'any' : 'all'}`
  const rule = row.rules[0]
  const spec = specs.find((s) => s.key === rule.field)
  if (!spec) return '1 condition'
  const op = spec.ops.find((o) => o.value === rule.op)?.label ?? rule.op
  // Each value, with the instance or server it is one of; several are read as "this or that".
  const said = [rule, ...(rule.also ?? [])].map((part) => {
    const value = spec.kind === 'choice' ? spec.options.find((o) => o.value === part.value)?.label ?? String(part.value)
      : spec.kind === 'days' && (rule.op === 'within' || rule.op === 'older') ? `${part.value} ${rule.op === 'older' ? 'days ago' : 'days'}` : String(part.value)
    return `${value}${part.instance ? ` (${part.instance})` : ''}`
  })
  return `${spec.label} ${op} ${said.join(' or ')}${rule.region ? ` in ${rule.region}` : ''}`.trim()
}

// A custom overlay's badges: one row each (on/off, name, wording or image, its own size, weight), and under
// the table the conditions of the badge that is open. A click anywhere in a row opens that badge.
function BadgeRows({ field, rows, kind, onChange, selected, onSelect, idPrefix, scope, toggle, info }: Props) {
  const [own, setOwn] = useState('')
  const specs = field.fields ?? []
  const image = kind === 'image'
  const max = field.max ?? 30
  const open = rows.find((r) => r.id === (selected ?? own)) ?? rows[0]
  const select = (id: string) => { setOwn(id); onSelect?.(id) }
  const update = (id: string, patch: Partial<OverlayBadgeRow>) => onChange(rows.map((r) => (r.id === id ? { ...r, ...patch } : r)))
  const add = () => {
    const id = newId(new Set(rows.map((r) => r.id)))
    onChange([...rows, { id, name: `Badge ${rows.length + 1}`, enabled: true, text: '', image: '', size: 0, weight: 0, match: field.modes?.[0]?.value ?? 'all', rules: [] }])
    select(id)
  }
  const remove = (id: string) => {
    const left = rows.filter((r) => r.id !== id)
    onChange(left)
    if (open?.id === id && left[0]) select(left[0].id)
  }
  const nameOf = (row: OverlayBadgeRow, index: number) => row.name.trim() || `Badge ${index + 1}`

  return (
    <div className="overlay-states overlay-badge-rows">
      <h3>{field.label}</h3>
      {field.help && <small className="overlay-states-help">{field.help}</small>}
      <div className="overlay-states-table-wrap">
        <table className="overlay-states-table overlay-badge-table">
          <thead>
            <tr>
              <th className="overlay-states-on">On</th>
              <th>Badge</th>
              <th>{image ? 'Image' : <span className="overlay-th-label">Text{field.text_help && info(field.text_help)}</span>}</th>
              <th>
                <span className="overlay-th-label">
                  {image ? 'Width' : 'Size'}
                  {info(image
                    ? 'This badge\u2019s own width in px of a 1000x1500 poster. Leave it at 0 to use the overlay\u2019s Image width.'
                    : 'This badge\u2019s own text size in px of a 1000x1500 poster. Leave it at 0 to use the Size from the Text card.')}
                </span>
              </th>
              <th><span className="overlay-th-label">Weight{info('The weight of this one badge inside this overlay. When a poster matches several of the badges here, the badge with the highest weight is drawn. With equal weights the first one wins. The overlay has a weight of its own beside Group, which is only compared with other overlays.')}</span></th>
              <th>Conditions</th>
              <th className="overlay-states-reset" aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => {
              const name = nameOf(row, index)
              const isOpen = open?.id === row.id
              return (
                <tr key={row.id} className={`${row.enabled ? '' : 'disabled'} ${isOpen ? 'selected' : ''}`} onClick={() => { if (!isOpen) select(row.id) }} onFocus={() => { if (!isOpen) select(row.id) }}>
                  <td className="overlay-states-on">{toggle(row.enabled, (on) => update(row.id, { enabled: on }), `${name} ${row.enabled ? 'on' : 'off'}`)}</td>
                  <td className="overlay-state-cell" data-label="Badge">
                    <input id={`${idPrefix}-${row.id}-name`} type="text" aria-label={`Badge ${index + 1} name`} value={row.name} maxLength={40} onChange={(e) => update(row.id, { name: e.target.value })} />
                  </td>
                  <td className="overlay-state-cell" data-label={image ? 'Image' : 'Text'}>
                    {image
                      ? <BadgeImage id={`${idPrefix}-${row.id}-image`} name={name} imageId={row.image} onChange={(v) => update(row.id, { image: v })} />
                      : <input id={`${idPrefix}-${row.id}-text`} type="text" aria-label={`${name} text`} value={row.text} maxLength={field.text_limit ?? 60} onChange={(e) => update(row.id, { text: e.target.value })} />}
                  </td>
                  <td className="overlay-state-cell" data-label={image ? 'Width' : 'Size'}>
                    <NumberField id={`${idPrefix}-${row.id}-size`} ariaLabel={`${name} ${image ? 'width' : 'size'}`} value={Number(row.size) || 0} onChange={(n) => update(row.id, { size: n })} fallback={0} min={0} max={image ? 1000 : 400} />
                  </td>
                  <td className="overlay-state-cell" data-label="Weight">
                    <NumberField id={`${idPrefix}-${row.id}-weight`} ariaLabel={`${name} weight`} value={Number(row.weight) || 0} onChange={(n) => update(row.id, { weight: n })} fallback={0} min={0} max={9999} />
                  </td>
                  <td className="overlay-state-cell overlay-badge-summary" data-label="Conditions">
                    <button type="button" className="overlay-badge-conditions" aria-expanded={isOpen} aria-label={`${name} conditions: ${summary(row, specs)}`} title={summary(row, specs)} onClick={() => select(row.id)}>
                      {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      <span>{summary(row, specs)}</span>
                    </button>
                  </td>
                  <td className="overlay-states-reset">
                    <button type="button" className="btn-icon btn-icon-sm btn-icon-outlined" aria-label={`Remove ${name}`} title={rows.length > 1 ? 'Remove this badge' : 'An overlay keeps at least one badge'} disabled={rows.length <= 1} onClick={(e) => { e.stopPropagation(); remove(row.id) }}>
                      <Trash2 size={13} />
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <button type="button" className="btn-toolbar btn-toolbar-link btn-xs overlay-rules-add" disabled={rows.length >= max} onClick={add}>
        <Plus size={12} /> Add Badge
      </button>
      {open && (
        <RuleEditor
          key={open.id}
          title={`Conditions: ${nameOf(open, rows.indexOf(open))}`}
          help={'Which movies and shows get this badge. With no condition, every poster this overlay covers gets it. '
            + 'Pick a value from the list, or type one and press Enter. A condition can hold several values, and any one of them is enough. '
            + 'With "is not" or "does not contain", none of them may fit. A value picked for one server or one Radarr or Sonarr instance only matches there. '
            + 'Contains, begins with, ends with and matches (regex) look at part of a name, so "contains Disney" covers every label or studio with Disney in it. '
            + 'With more than one condition, Must match says whether all or any of them must hold.'}
          specs={specs}
          modes={field.modes ?? []}
          rules={open.rules}
          mode={open.match}
          onRulesChange={(rules: OverlayRule[]) => update(open.id, { rules })}
          onModeChange={(match) => update(open.id, { match })}
          idPrefix={`${idPrefix}-rules`}
          scope={scope}
          info={info}
        />
      )}
    </div>
  )
}

export default BadgeRows
