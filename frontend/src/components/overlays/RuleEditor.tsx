import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Plus, Trash2, X } from 'lucide-react'
import NumberField from '../poster-manager/NumberField'
import ListPicker from './ListPicker'
import SearchPicker from './SearchPicker'
import { getRuleValues } from '../../api/overlays'
import type { OverlayRule, OverlayRuleField, OverlayRulePart, OverlayRuleValue } from '../../api/overlays'

type Props = {
  title: string
  help?: string
  // What a condition can look at, and the "all or any" choices.
  specs: OverlayRuleField[]
  modes: { value: string; label: string }[]
  rules: OverlayRule[]
  mode: string
  onRulesChange: (rules: OverlayRule[]) => void
  onModeChange: (mode: string) => void
  idPrefix: string
  // The libraries the overlay covers ("instance:key"): what they hold is what is offered for
  // a label, a genre, a collection and the like.
  scope?: string[]
  // Draws the page's info icon for a help text.
  info: (help: string) => ReactNode
}

type Found = { values: OverlayRuleValue[]; unreachable: string[] }

const SEARCH_DELAY = 250 // ms after the last key before a search goes out
const MAX_VALUES = 20 // values one condition may hold

// A condition's values: its own, then the ones it holds besides.
const partsOf = (rule: OverlayRule): OverlayRulePart[] =>
  [{ value: rule.value, ...(rule.instance !== undefined ? { instance: rule.instance } : {}) }, ...(rule.also ?? [])]

const withParts = (rule: OverlayRule, parts: OverlayRulePart[]): OverlayRule => {
  const [first, ...rest] = parts
  return { field: rule.field, op: rule.op, ...first, ...(rest.length ? { also: rest } : {}) }
}

// What a days field's value is, by its comparison: a date for "before" and "after", a pattern
// for a regex, a number of days for the rest.
type DayShape = 'date' | 'pattern' | 'days'
const dayShape = (op: string): DayShape => (op === 'before' || op === 'after' ? 'date' : op === 'regex' ? 'pattern' : 'days')
const today = () => new Date().toISOString().slice(0, 10)
const dayStart = (shape: DayShape): string | number => (shape === 'date' ? today() : shape === 'pattern' ? '' : 90)

// What a condition starts with when its field is picked: a number to change, or no value at
// all where values are chips (nothing is picked for the user, not even from a fixed set).
const startValue = (spec: OverlayRuleField): string | number =>
  spec.start ?? (spec.kind === 'days' ? 90 : spec.kind === 'number' ? 0 : '')

const startRule = (spec: OverlayRuleField): OverlayRule => ({ field: spec.key, op: spec.ops[0]?.value ?? 'is', value: startValue(spec) })

const fits = (spec: OverlayRuleField, value: string): boolean => {
  if (!spec.pattern || value === '') return true
  try { return new RegExp(spec.pattern).test(value) } catch { return true }
}

// One badge's conditions: one row each (what to look at, how to compare, the value), and
// whether all of them or any one must hold. Where there are values to pick from (the Arrs'
// tags, what the libraries hold, TMDB's lists) the box lists them as you type. A condition may
// hold several values, shown as chips: any one of them will do.
function RuleEditor({ title, help, specs, modes, rules, mode, onRulesChange, onModeChange, idPrefix, scope, info }: Props) {
  const specOf = (key: string) => specs.find((s) => s.key === key)
  // The values there are for the fields that list them, by field.
  const [listed, setListed] = useState<Record<string, Found>>({})
  // The matches for what is being typed into a condition whose values are searched for, by row.
  const [searched, setSearched] = useState<Record<number, OverlayRuleValue[]>>({})
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  // The lists asked for: one per listed field in use, and the countries when a condition can name a region.
  const asked = Array.from(new Set([
    ...rules.map((r) => r.field).filter((key) => specs.find((s) => s.key === key)?.suggest === 'list'),
    ...(rules.some((r) => specs.find((s) => s.key === r.field)?.region) ? ['country'] : []),
  ])).sort().join(',')
  const libraries = (scope ?? []).join(',')
  useEffect(() => {
    let cancelled = false
    for (const field of asked ? asked.split(',') : []) {
      getRuleValues(field, { libraries: libraries ? libraries.split(',') : [] })
        .then((found) => { if (!cancelled) setListed((prev) => ({ ...prev, [field]: { values: found.values ?? [], unreachable: found.unreachable ?? [] } })) })
        .catch(() => { /* nothing to pick from: the value is typed */ })
    }
    return () => { cancelled = true }
  }, [asked, libraries])
  useEffect(() => () => { Object.values(timers.current).forEach(clearTimeout) }, [])

  const update = (index: number, patch: Partial<OverlayRule>) => onRulesChange(rules.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  const add = () => { if (specs[0]) onRulesChange([...rules, startRule(specs[0])]) }
  const remove = (index: number) => onRulesChange(rules.filter((_, i) => i !== index))
  const search = (at: number, field: string, text: string) => {
    clearTimeout(timers.current[at])
    timers.current[at] = setTimeout(() => {
      getRuleValues(field, { q: text })
        .then((found) => setSearched((prev) => ({ ...prev, [at]: found.values ?? [] })))
        .catch(() => { /* the value stays as typed */ })
    }, SEARCH_DELAY)
  }

  // A condition's value. A number is a number box. Anything else is a box that adds a value
  // (picked from its list, or typed and Enter; leaving the box adds what was typed too, so
  // nothing typed is lost) and, under it, the values there are, one chip each.
  const valueCell = (rule: OverlayRule, spec: OverlayRuleField, index: number) => {
    const id = `${idPrefix}-${index}-value`
    const label = `Condition ${index + 1} value`
    if (spec.kind === 'days' && dayShape(rule.op) === 'date') {
      return <input id={id} type="date" aria-label={label} className="overlay-rule-date" value={String(rule.value ?? '')} onChange={(e) => update(index, { value: e.target.value })} />
    }
    if (spec.kind === 'days' && dayShape(rule.op) === 'pattern') {
      return (
        <div className="overlay-rule-number">
          <input id={id} type="text" aria-label={label} value={String(rule.value ?? '')} placeholder="12/25" spellCheck={false} autoComplete="off" maxLength={300} onChange={(e) => update(index, { value: e.target.value })} />
          <span className="overlay-rule-unit">of month/day/year</span>
        </div>
      )
    }
    if (spec.kind === 'number' || spec.kind === 'days') {
      const days = spec.kind === 'days'
      return (
        <div className="overlay-rule-number">
          <NumberField id={id} ariaLabel={label} value={typeof rule.value === 'number' ? rule.value : Number(rule.value) || 0} onChange={(n) => update(index, { value: n })} fallback={days ? 1 : 0} min={days ? 1 : 0} max={days ? 36500 : undefined} step={days ? undefined : spec.step} />
          {days && <span className="overlay-rule-unit">{rule.op === 'older' ? 'days ago' : 'days'}</span>}
          {spec.region && (
            <>
              <span className="overlay-rule-unit">in</span>
              {/* A chart TMDB keeps per country: pick one, type its two letters, or leave it for TMDB's own chart. */}
              <SearchPicker
                id={`${id}-region`}
                ariaLabel={`Condition ${index + 1} region`}
                text={String(rule.region ?? '')}
                options={(listed.country?.values ?? []).map((v, at) => ({ value: String(at), label: v.label, note: v.note }))}
                placeholder="any region"
                title="A country's own chart, for movies. Leave it empty for TMDB's chart of all regions."
                onType={(typed) => update(index, { region: typed.replace(/[^A-Za-z]/g, '').toUpperCase().slice(0, 2) })}
                onPick={(option) => {
                  const country = listed.country?.values[Number(option.value)]
                  if (country) update(index, { region: country.value })
                }}
              />
            </>
          )}
        </div>
      )
    }
    const looked = spec.suggest === 'search'
    const known = spec.suggest === 'list' ? listed[spec.key] : undefined
    // What there is to pick from: the field's fixed set, what was found for what is typed, or the list there is.
    const values: OverlayRuleValue[] = spec.kind === 'choice' ? spec.options.map((o) => ({ value: o.value, label: o.label }))
      : looked ? searched[index] ?? [] : known?.values ?? []
    const chips = partsOf(rule).filter((part) => String(part.value ?? '').trim() !== '')
    const same = (a: OverlayRulePart, b: OverlayRulePart) =>
      String(a.value).trim().toLowerCase() === String(b.value).trim().toLowerCase() && String(a.instance ?? '') === String(b.instance ?? '')
    const put = (parts: OverlayRulePart[]) => onRulesChange(rules.map((r, i) => (i === index ? withParts(r, parts.length ? parts : [{ value: '' }]) : r)))
    const addPart = (part: OverlayRulePart) => { if (chips.length < MAX_VALUES && !chips.some((chip) => same(chip, part))) put([...chips, part]) }
    const partOf = (entry: OverlayRuleValue): OverlayRulePart => (entry.instance !== undefined ? { value: entry.value, instance: entry.instance } : { value: entry.value })
    // What an Arr instance or a server holds is that one's: an entry picked says which, a typed value means any.
    const tagged = values.some((v) => v.instance !== undefined)
    const commit = (typed: string) => {
      const named = values.filter((v) => v.label.toLowerCase() === typed.toLowerCase() || v.value.toLowerCase() === typed.toLowerCase())
      if (named.length === 1) addPart(partOf(named[0]))  // the one entry of that name: as if it had been picked
      else if (spec.kind !== 'choice') addPart(tagged ? { value: typed, instance: '' } : { value: typed })
    }
    // A chip reads its value by the name the list gives it, and beside it where it is from.
    const said = (part: OverlayRulePart) => {
      const from = String(part.instance ?? '')
      const match = values.find((v) => v.value.toLowerCase() === String(part.value).trim().toLowerCase() && String(v.instance ?? '') === from)
      return { name: match ? match.label : String(part.value), note: from || (match?.note ?? '') }
    }
    // An entry that is a chip already is not offered again.
    const offered = values.map((v, at) => ({ v, at })).filter(({ v }) => !chips.some((chip) => same(chip, partOf(v))))
    const unreachable = known?.unreachable ?? []
    return (
      <div className="overlay-rule-values">
        {(spec.multi || chips.length === 0) && chips.length < MAX_VALUES && (
          <SearchPicker
            id={id}
            ariaLabel={label}
            text=""
            options={offered.map(({ v, at }) => ({ value: String(at), label: v.label, note: v.note }))}
            searched={looked}
            placeholder={chips.length ? 'Add another' : spec.placeholder}
            title={unreachable.length ? `Not answering: ${unreachable.join(', ')}` : chips.length ? 'Any one of these values will do. With "is not" or "does not contain", none of them may fit.' : undefined}
            onType={(typed) => { if (looked) search(index, spec.key, typed) }}
            onPick={(option) => {
              const entry = values[Number(option.value)]
              if (entry) addPart(partOf(entry))
            }}
            onCommit={commit}
          />
        )}
        {chips.length > 0 && (
          <div className="overlay-rule-chips">
            {chips.map((part, slot) => {
              const { name, note } = said(part)
              const ok = fits(spec, String(part.value).trim())
              return (
                <span key={`${part.instance ?? ''}|${part.value}`} className={`overlay-rule-chip ${ok ? '' : 'invalid'}`} title={ok ? name : `Use an address like ${spec.placeholder}`}>
                  <span className="overlay-rule-chip-name">{name}</span>
                  {note && <span className="overlay-rule-chip-note">{note}</span>}
                  <button type="button" aria-label={`Remove ${name}${note ? ` (${note})` : ''} from condition ${index + 1}`} title="Remove this value" onClick={() => put(chips.filter((_, k) => k !== slot))}>
                    <X size={11} />
                  </button>
                </span>
              )
            })}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="overlay-rules">
      <h3>{title}</h3>
      {help && <small className="overlay-states-help">{help}</small>}
      {rules.length > 1 && modes.length > 0 && (
        <div className="overlay-rules-mode">
          <label htmlFor={`${idPrefix}-mode`}>Must match</label>
          <ListPicker id={`${idPrefix}-mode`} value={mode} options={modes} onChange={onModeChange} />
        </div>
      )}
      {rules.length > 0 && (
        <div className="overlay-states-table-wrap">
          <table className="overlay-states-table overlay-rules-table">
            <thead>
              <tr>
                <th>Condition</th>
                <th>Comparison</th>
                <th>Value</th>
                <th className="overlay-states-reset" aria-label="Remove" />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule, index) => {
                const spec = specOf(rule.field)
                if (!spec) return null
                return (
                  <tr key={index}>
                    <td className="overlay-state-cell" data-label="Condition">
                      <div className="overlay-rule-field">
                        <ListPicker
                          id={`${idPrefix}-${index}-field`}
                          ariaLabel={`Condition ${index + 1}`}
                          value={rule.field}
                          options={specs.map((s) => ({ value: s.key, label: s.label }))}
                          onChange={(key) => {
                            const next = specOf(key)
                            if (!next) return
                            setSearched({})
                            onRulesChange(rules.map((r, i) => (i === index ? startRule(next) : r)))
                          }}
                        />
                        {spec.help && info(spec.help)}
                      </div>
                    </td>
                    <td className="overlay-state-cell" data-label="Comparison">
                      <ListPicker
                        id={`${idPrefix}-${index}-op`}
                        ariaLabel={`Condition ${index + 1} comparison`}
                        value={rule.op}
                        options={spec.ops}
                        // Between counting days, naming a date and writing a pattern the value starts over: each is another kind of value.
                        onChange={(op) => update(index, spec.kind === 'days' && dayShape(op) !== dayShape(rule.op) ? { op, value: dayStart(dayShape(op)) } : { op })}
                      />
                    </td>
                    <td className="overlay-state-cell overlay-rule-value-cell" data-label="Value">{valueCell(rule, spec, index)}</td>
                    <td className="overlay-states-reset">
                      <button type="button" className="btn-icon btn-icon-sm btn-icon-outlined" aria-label={`Remove condition ${index + 1}`} title="Remove this condition" onClick={() => { setSearched({}); remove(index) }}>
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <button type="button" className="btn-toolbar btn-toolbar-link btn-xs overlay-rules-add" onClick={add}>
        <Plus size={12} /> Add Condition
      </button>
    </div>
  )
}

export default RuleEditor
