import { ReactNode, useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, Plus, Settings, Trash2 } from 'lucide-react'
import BorderStyleControls, { BorderStyleValue, StyleValuePreview } from './BorderStyleControls'
import LibrarySelectGrid from './LibrarySelectGrid'
import { getPlexLibraryConfigs, PlexLibraryConfig } from '../../api/client'
import { BorderOverlay } from '../../api/posterManager'
import {
  DEFAULT_SEASON_STYLE,
  PlexBorderRule,
  RULE_RUN_TYPES,
  RuleMatchType,
  RuleMode,
  RuleRunType,
} from '../../hooks/usePosterManagerBorder'

const RUN_TYPE_LABELS: Record<RuleRunType, string> = {
  workflow: 'Workflow',
  webhook: 'Webhook (Asset Upload)',
  manual: 'Manual (Border tab)',
  autorun: 'Auto-run (after Renamer)',
  scheduled: 'Scheduled (sync jobs)',
}

const MATCH_LABELS: Record<RuleMatchType, string> = {
  label: 'Label',
  genre: 'Genre',
  collection: 'Collection',
}

// Short badge text (with the full sentence as the badge's tooltip).
const MODE_SHORT: Record<RuleMode, string> = {
  include: 'Include',
  except: 'Except',
  skip: 'Skip',
}

const MODE_LABELS: Record<RuleMode, string> = {
  include: 'Apply to matches',
  except: 'Apply to everything except matches',
  skip: 'Leave matches untouched',
}

const MATCH_PLACEHOLDER: Record<RuleMatchType, string> = {
  label: 'e.g. Christmas',
  genre: 'e.g. Horror',
  collection: 'e.g. Marvel Cinematic Universe',
}

const blankRule = (): PlexBorderRule => ({
  name: '',
  match: 'label',
  value: '',
  mode: 'include',
  colors: [],
  style: { ...DEFAULT_SEASON_STYLE },
})

// A deep copy so the draft form and the committed rule don't share array references.
const cloneRule = (rule: PlexBorderRule): PlexBorderRule => ({
  ...rule,
  colors: [...rule.colors],
  style: { ...rule.style, gradientColors: [...rule.style.gradientColors] },
})

// A rule is renderable (worth applying) when it targets a value and either skips
// matches or has something to paint (solid colors, a gradient, or an overlay frame).
function ruleIsRenderable(rule: PlexBorderRule): boolean {
  if (!rule.value.trim()) return false
  if (rule.mode === 'skip') return true
  if (rule.style.style === 'remove') return true
  return (
    rule.colors.length > 0 ||
    (rule.style.style === 'image' && rule.style.overlayImage.trim().length > 0) ||
    (rule.style.style === 'gradient' && rule.style.gradientColors.length > 0)
  )
}

type Props = {
  rules: PlexBorderRule[]
  runTypes: RuleRunType[]
  libraries: Set<string>
  borderWidth: number
  overlays: BorderOverlay[]
  refreshOverlays: () => void
  sectionSaveButton: ReactNode
  onSetRules: (updater: (prev: PlexBorderRule[]) => PlexBorderRule[]) => void
  onToggleRunType: (type: RuleRunType) => void
  onToggleLibrary: (fullKey: string) => void
}

function PlexRulesSection({
  rules,
  runTypes,
  libraries,
  borderWidth,
  overlays,
  refreshOverlays,
  sectionSaveButton,
  onSetRules,
  onToggleRunType,
  onToggleLibrary,
}: Props) {
  const [libraryConfigs, setLibraryConfigs] = useState<PlexLibraryConfig[]>([])
  // Run-type + library selection live in a popover behind the "Configure" button.
  const [configOpen, setConfigOpen] = useState(false)
  const configRef = useRef<HTMLDivElement>(null)
  // A brand-new rule is composed in a local DRAFT and only appears as a card on "Add Rule"
  // (nothing is built live). Editing an existing rule instead edits it IN PLACE — changes
  // apply live and light up the section's Save button, so the edit form has no save button.
  const [draft, setDraft] = useState<PlexBorderRule>(blankRule)
  const [editingIndex, setEditingIndex] = useState<number | null>(null)
  // Snapshot of the rule as it was when editing started, so Cancel can revert the live edits.
  const [editSnapshot, setEditSnapshot] = useState<PlexBorderRule | null>(null)

  useEffect(() => {
    getPlexLibraryConfigs()
      .then((data) => setLibraryConfigs(data.configs || []))
      .catch(() => setLibraryConfigs([]))
  }, [])

  // Close the Configure popover on an outside click.
  useEffect(() => {
    if (!configOpen) return
    const onDown = (e: MouseEvent) => {
      if (configRef.current && !configRef.current.contains(e.target as Node)) setConfigOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [configOpen])

  // Rules only run when at least one run type AND at least one library are selected.
  const inactiveReason =
    runTypes.length === 0
      ? 'no run types are selected'
      : libraries.size === 0
        ? 'no libraries are selected'
        : null
  const enabled = inactiveReason === null

  // Draft (new-rule) editing.
  const patchDraft = (patch: Partial<PlexBorderRule>) => setDraft((d) => ({ ...d, ...patch }))
  const applyDraftStylePatch = (patch: Partial<BorderStyleValue>) => {
    const { colors, ...rest } = patch
    setDraft((d) => ({
      ...d,
      ...(colors !== undefined ? { colors } : {}),
      style: Object.keys(rest).length > 0 ? { ...d.style, ...rest } : d.style,
    }))
  }

  // Live editing of an existing rule (writes straight into the rules list).
  const updateRule = (index: number, patch: Partial<PlexBorderRule>) =>
    onSetRules((prev) => prev.map((r, i) => (i === index ? { ...r, ...patch } : r)))
  const applyRuleStylePatch = (index: number, patch: Partial<BorderStyleValue>) => {
    const { colors, ...rest } = patch
    onSetRules((prev) =>
      prev.map((r, i) => {
        if (i !== index) return r
        return {
          ...r,
          ...(colors !== undefined ? { colors } : {}),
          style: Object.keys(rest).length > 0 ? { ...r.style, ...rest } : r.style,
        }
      }),
    )
  }

  // The form binds to the rule being edited (live) or the draft (new). Editing the draft
  // never touches an existing card and vice versa, so switching modes loses no in-progress work.
  const editIdx = editingIndex !== null && rules[editingIndex] ? editingIndex : null
  const current = editIdx !== null ? rules[editIdx] : draft
  const patchCurrent = (patch: Partial<PlexBorderRule>) =>
    editIdx !== null ? updateRule(editIdx, patch) : patchDraft(patch)
  const patchCurrentStyle = (patch: Partial<BorderStyleValue>) =>
    editIdx !== null ? applyRuleStylePatch(editIdx, patch) : applyDraftStylePatch(patch)

  // "Add Rule" — commit the draft as a new card, then clear the form for the next one.
  const addRule = () => {
    if (!ruleIsRenderable(draft)) return
    onSetRules((prev) => [...prev, cloneRule(draft)])
    setDraft(blankRule())
  }

  const editRule = (index: number) => {
    setEditSnapshot(cloneRule(rules[index]))
    setEditingIndex(index)
  }

  // "New rule" — leave edit mode KEEPING the live edits, back to a blank draft.
  const stopEditing = () => {
    setEditingIndex(null)
    setEditSnapshot(null)
  }

  // "Cancel" while editing — revert the rule to its pre-edit snapshot, then leave edit mode.
  const cancelEdit = () => {
    if (editingIndex !== null && editSnapshot) {
      const snap = editSnapshot
      const idx = editingIndex
      onSetRules((prev) => prev.map((r, i) => (i === idx ? snap : r)))
    }
    setEditingIndex(null)
    setEditSnapshot(null)
  }

  // "Cancel" while composing a new rule — clear the form.
  const cancelDraft = () => setDraft(blankRule())

  const removeRule = (index: number) => {
    onSetRules((prev) => prev.filter((_, i) => i !== index))
    setEditingIndex((cur) => (cur === null ? null : cur === index ? null : cur > index ? cur - 1 : cur))
  }

  const moveRule = (index: number, dir: -1 | 1) => {
    const target = index + dir
    if (target < 0 || target >= rules.length) return
    onSetRules((prev) => {
      const next = [...prev]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
    setEditingIndex((cur) => (cur === index ? target : cur === target ? index : cur))
  }

  const draftRenderable = ruleIsRenderable(draft)

  return (
    <div className="settings-section">
      <div className="section-header-row">
        <h2>Border Rules (Server Labels / Genres / Collections)</h2>
        <div className="rule-header-actions">
          <div className="rule-config-wrap" ref={configRef}>
            <button className="btn-secondary" onClick={() => setConfigOpen((o) => !o)}>
              <Settings size={16} /> Configure
            </button>
            {configOpen && (
              <div className="rule-config-popover">
                <div className="rule-config-block">
                  <span className="rule-config-heading">Apply these rules during</span>
                  <small style={{ display: 'block', marginBottom: '0.5rem' }}>
                    Rules are skipped on any run type left off. With none selected, rules never run.
                  </small>
                  <div className="rule-runtype-list">
                    {RULE_RUN_TYPES.map((type) => (
                      <div className="toggle-field" key={type}>
                        <label className="toggle-switch">
                          <input type="checkbox" checked={runTypes.includes(type)} onChange={() => onToggleRunType(type)} />
                          <span className="toggle-slider"></span>
                        </label>
                        <span className="toggle-label">{RUN_TYPE_LABELS[type]}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="rule-config-block">
                  <span className="rule-config-heading">Libraries to scan</span>
                  <small style={{ display: 'block', marginBottom: '0.5rem' }}>
                    Only checked libraries are scanned for matches. With none checked, rules never run.
                  </small>
                  {libraryConfigs.length === 0 ? (
                    <div className="empty-config">
                      <p>No media server instances configured. Configure in Settings → Media Servers.</p>
                    </div>
                  ) : (
                    <LibrarySelectGrid
                      libraryConfigs={libraryConfigs}
                      selected={libraries}
                      onToggle={(_instance, _key, fullKey) => onToggleLibrary(fullKey)}
                    />
                  )}
                </div>
              </div>
            )}
          </div>
          {sectionSaveButton}
        </div>
      </div>

      <div className="field-group">
        <small style={{ marginBottom: '0.5rem', display: 'block' }}>
          Apply a specific border to only the items matching a Plex <strong>label</strong>, <strong>genre</strong>,
          or <strong>collection</strong> — everything else gets its normal treatment. Rules are checked top-to-bottom
          and the <em>first</em> matching rule wins, so order sets priority. Use <strong>Configure</strong> to choose
          which runs apply rules and which libraries to scan.
        </small>
      </div>

      {!enabled && rules.length > 0 && (
        <div className="empty-config" style={{ marginBottom: '0.75rem' }}>
          <p>These rules are inactive — {inactiveReason}. Open <strong>Configure</strong> to fix it.</p>
        </div>
      )}

      <div className="rule-workspace">
        {/* Left: the form — a draft for a new rule, or the selected rule edited live. */}
        <div className="rule-setup-card">
          <div className="rule-setup-header">
            <span className="rule-setup-title">
              {editIdx === null ? 'New rule' : `Editing ${rules[editIdx].name.trim() || `rule ${editIdx + 1}`}`}
            </span>
          </div>

          <div className="rule-fields-row">
            <div className="rule-field">
              <label>Rule name (optional)</label>
              <input
                type="text"
                value={current.name}
                onChange={(e) => patchCurrent({ name: e.target.value })}
                placeholder="e.g. Christmas movies"
              />
            </div>
            <div className="rule-field">
              <label>Match on</label>
              <select value={current.match} onChange={(e) => patchCurrent({ match: e.target.value as RuleMatchType })}>
                <option value="label">Label</option>
                <option value="genre">Genre</option>
                <option value="collection">Collection</option>
              </select>
            </div>
            <div className="rule-field">
              <label>{MATCH_LABELS[current.match]} value</label>
              <input
                type="text"
                value={current.value}
                onChange={(e) => patchCurrent({ value: e.target.value })}
                placeholder={MATCH_PLACEHOLDER[current.match]}
              />
            </div>
          </div>

          <div className="rule-field" style={{ marginTop: '0.6rem' }}>
            <label>When it matches</label>
            <div className="season-mode-options">
              {(['include', 'except', 'skip'] as RuleMode[]).map((mode) => (
                <label className="radio-label" key={mode}>
                  <input
                    type="radio"
                    name="rule-form-mode"
                    checked={current.mode === mode}
                    onChange={() => patchCurrent({ mode })}
                  />
                  <span>{MODE_LABELS[mode]}</span>
                </label>
              ))}
            </div>
          </div>

          {current.mode !== 'skip' && (
            <div style={{ marginTop: '0.75rem' }}>
              <BorderStyleControls
                key={editIdx === null ? 'new' : `edit-${editIdx}`}
                allowRemove
                value={{ ...current.style, colors: current.colors }}
                onChange={patchCurrentStyle}
                overlays={overlays}
                refreshOverlays={refreshOverlays}
                idPrefix="rule-form"
                label="Rule Border Style"
                preview={<StyleValuePreview value={{ ...current.style, colors: current.colors }} borderWidth={borderWidth} />}
              />
            </div>
          )}

          <div className="rule-setup-footer">
            {editIdx === null ? (
              <>
                <button
                  className={`btn-secondary rule-btn rule-commit-btn ${draftRenderable ? 'is-ready' : ''}`}
                  onClick={addRule}
                  disabled={!draftRenderable}
                  title={draftRenderable ? undefined : 'Set a value and a style first'}
                >
                  <Plus size={16} /> Add Rule
                </button>
                <button className="btn-secondary rule-btn" onClick={cancelDraft}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button className="btn-secondary rule-btn" onClick={cancelEdit}>
                  Cancel
                </button>
                <button className="btn-secondary rule-btn" onClick={stopEditing}>
                  <Plus size={14} /> New rule
                </button>
              </>
            )}
          </div>
          {editIdx !== null && (
            <small style={{ marginTop: '0.45rem', display: 'block' }}>
              Changes apply live — use <strong>Save</strong> above to keep them, or <strong>Cancel</strong> to undo.
            </small>
          )}
        </div>

        {/* Right: compact list of committed rules (one row each). */}
        <div className="rule-list-col">
          <label>Rules {rules.length > 0 && <span className="rule-count">({rules.length})</span>}</label>
          {rules.length === 0 ? (
            <div className="empty-config">
              <p>No rules yet. Fill in the form and click <strong>Add Rule</strong>.</p>
            </div>
          ) : (
            <div className="rule-list">
              {rules.map((rule, index) => {
                const renderable = ruleIsRenderable(rule)
                return (
                  <div key={index} className={`rule-row ${editIdx === index ? 'rule-row-editing' : ''}`}>
                    <div className="rule-row-main">
                      {rule.name.trim() && <span className="rule-row-title">{rule.name.trim()}</span>}
                      <div className="rule-badges">
                        <span className="rule-badge rule-badge-match">{MATCH_LABELS[rule.match]}</span>
                        <span className="rule-badge rule-badge-value">{rule.value.trim() || '…'}</span>
                        <span className={`rule-badge rule-badge-mode mode-${rule.mode}`} title={MODE_LABELS[rule.mode]}>
                          {MODE_SHORT[rule.mode]}
                        </span>
                        {!renderable && <span className="rule-badge rule-badge-warn">incomplete</span>}
                      </div>
                    </div>
                    <div className="rule-row-actions">
                      <div className="rule-reorder">
                        <button className="rule-arrow" onClick={() => moveRule(index, -1)} disabled={index === 0} title="Move up (higher priority)">
                          <ChevronUp size={14} />
                        </button>
                        <button className="rule-arrow" onClick={() => moveRule(index, 1)} disabled={index === rules.length - 1} title="Move down (lower priority)">
                          <ChevronDown size={14} />
                        </button>
                      </div>
                      <button
                        className={`btn-secondary rule-btn ${editIdx === index ? 'rule-btn-active' : ''}`}
                        onClick={() => editRule(index)}
                      >
                        Edit
                      </button>
                      <button className="btn-remove-color" onClick={() => removeRule(index)} title="Remove rule">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default PlexRulesSection
