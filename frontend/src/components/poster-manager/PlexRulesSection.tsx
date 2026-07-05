import { ReactNode, useEffect, useState } from 'react'
import { ChevronDown, ChevronUp, Plus, Trash2 } from 'lucide-react'
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
  webhook: 'Webhook (Plex upload)',
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
  // Accordion: one card open at a time. creatingIndex marks a just-added rule
  // whose confirm button reads "Create" until the user commits it. editSnapshot
  // holds the pre-edit copy of an existing rule so Cancel can revert live edits.
  const [openIndex, setOpenIndex] = useState<number | null>(null)
  const [creatingIndex, setCreatingIndex] = useState<number | null>(null)
  const [editSnapshot, setEditSnapshot] = useState<PlexBorderRule | null>(null)

  useEffect(() => {
    getPlexLibraryConfigs()
      .then((data) => setLibraryConfigs(data.configs || []))
      .catch(() => setLibraryConfigs([]))
  }, [])

  // Rules only run when at least one run type AND at least one library are selected.
  const inactiveReason =
    runTypes.length === 0
      ? 'no run types are selected'
      : libraries.size === 0
        ? 'no libraries are selected'
        : null
  const enabled = inactiveReason === null

  const updateRule = (index: number, patch: Partial<PlexBorderRule>) =>
    onSetRules((prev) => prev.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)))

  // Route a BorderStyleControls patch into a rule's (colors, style) pair.
  const applyRuleStylePatch = (index: number, patch: Partial<BorderStyleValue>) => {
    const { colors, ...rest } = patch
    onSetRules((prev) =>
      prev.map((rule, i) => {
        if (i !== index) return rule
        return {
          ...rule,
          ...(colors !== undefined ? { colors } : {}),
          style: Object.keys(rest).length > 0 ? { ...rule.style, ...rest } : rule.style,
        }
      }),
    )
  }

  const removeRule = (index: number) => {
    onSetRules((prev) => prev.filter((_, i) => i !== index))
    const shift = (cur: number | null) =>
      cur === null ? null : cur === index ? null : cur > index ? cur - 1 : cur
    setOpenIndex(shift)
    setCreatingIndex(shift)
  }

  const moveRule = (index: number, dir: -1 | 1) => {
    const target = index + dir
    if (target < 0 || target >= rules.length) return
    onSetRules((prev) => {
      const next = [...prev]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
    const remap = (cur: number | null) => (cur === index ? target : cur === target ? index : cur)
    setOpenIndex(remap)
    setCreatingIndex(remap)
  }

  const addRule = () => {
    const newIndex = rules.length
    onSetRules((prev) => [
      ...prev,
      { name: '', match: 'label', value: '', mode: 'include', colors: [], style: { ...DEFAULT_SEASON_STYLE } },
    ])
    setEditSnapshot(null)
    setOpenIndex(newIndex)
    setCreatingIndex(newIndex)
  }

  // Open an existing rule for editing, snapshotting it so Cancel can revert.
  const openRule = (index: number) => {
    const rule = rules[index]
    setEditSnapshot({
      ...rule,
      colors: [...rule.colors],
      style: { ...rule.style, gradientColors: [...rule.style.gradientColors] },
    })
    setOpenIndex(index)
  }

  const closeCard = (index: number) => {
    setOpenIndex((cur) => (cur === index ? null : cur))
    setCreatingIndex((cur) => (cur === index ? null : cur))
  }

  // "Save"/"Create" — keep the live edits and collapse.
  const saveCard = (index: number) => {
    setEditSnapshot(null)
    closeCard(index)
  }

  // "Cancel" — discard a brand-new rule entirely, or revert an existing rule to its
  // pre-edit snapshot, then collapse.
  const cancelEdit = (index: number) => {
    if (creatingIndex === index) {
      removeRule(index)
    } else {
      if (editSnapshot) onSetRules((prev) => prev.map((r, i) => (i === index ? editSnapshot : r)))
      closeCard(index)
    }
    setEditSnapshot(null)
  }

  return (
    <div className="settings-section">
      <div className="section-header-row">
        <h2>Border Rules (Plex Labels / Genres / Collections)</h2>
        {sectionSaveButton}
      </div>

      <div className="field-group">
        <small style={{ marginBottom: '0.5rem', display: 'block' }}>
          Apply a specific border to only the items matching a Plex <strong>label</strong>, <strong>genre</strong>,
          or <strong>collection</strong> — everything else gets its normal treatment. Rules are checked top-to-bottom
          and the <em>first</em> matching rule wins, so order sets priority. The matched item list is queried from
          Plex once per run.
        </small>
      </div>

      {/* Three columns: rules (widest) · run-type toggles · library selection */}
      <div className="rule-layout">
        <div className="rule-col rule-col-rules">
          <label>Rules {rules.length > 0 && <span className="rule-count">({rules.length})</span>}</label>
          {!enabled && rules.length > 0 && (
            <div className="empty-config" style={{ marginBottom: '0.5rem' }}>
              <p>These rules are inactive — {inactiveReason}.</p>
            </div>
          )}
          {rules.length === 0 ? (
            <div className="empty-config"><p>No border rules yet. Add one to target Plex labels, genres, or collections.</p></div>
          ) : (
            <div className="rule-list">
              {rules.map((rule, index) => {
                const isOpen = openIndex === index
                const renderable = ruleIsRenderable(rule)
                return (
                  <div key={index} className={`rule-card ${isOpen ? 'rule-card-open' : ''}`}>
                    <div className="rule-card-header">
                      <div className="rule-card-main">
                        {rule.name.trim() && <span className="rule-card-title">{rule.name.trim()}</span>}
                        <div className="rule-badges">
                          <span className="rule-badge rule-badge-match">{MATCH_LABELS[rule.match]}</span>
                          <span className="rule-badge rule-badge-value">{rule.value.trim() || '…'}</span>
                          <span className={`rule-badge rule-badge-mode mode-${rule.mode}`} title={MODE_LABELS[rule.mode]}>
                            {MODE_SHORT[rule.mode]}
                          </span>
                          {!renderable && <span className="rule-badge rule-badge-warn">incomplete</span>}
                        </div>
                      </div>
                      <div className="rule-card-actions">
                        <div className="rule-reorder">
                          <button className="rule-arrow" onClick={() => moveRule(index, -1)} disabled={index === 0} title="Move up (higher priority)">
                            <ChevronUp size={14} />
                          </button>
                          <button className="rule-arrow" onClick={() => moveRule(index, 1)} disabled={index === rules.length - 1} title="Move down (lower priority)">
                            <ChevronDown size={14} />
                          </button>
                        </div>
                        <button
                          className={`btn-secondary rule-btn ${isOpen ? 'rule-btn-active' : ''}`}
                          onClick={() => (isOpen ? cancelEdit(index) : openRule(index))}
                        >
                          Edit
                        </button>
                        <button className="btn-remove-color" onClick={() => removeRule(index)} title="Remove rule">
                          <Trash2 size={16} />
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          <button className="btn-secondary btn-add-holiday rule-add-btn" onClick={addRule}>
            <Plus size={16} /> Add Rule
          </button>
        </div>

        <div className="rule-col rule-col-toggles">
          <label>Apply these rules during</label>
          <small style={{ marginBottom: '0.5rem', display: 'block' }}>
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

        <div className="rule-col rule-col-libraries">
          <label>Libraries to scan</label>
          <small style={{ marginBottom: '0.5rem', display: 'block' }}>
            Only checked libraries are scanned for matches. With none checked, rules never run.
          </small>
          {libraryConfigs.length === 0 ? (
            <div className="empty-config">
              <p>No Plex instances configured. Configure in Settings → Media Servers.</p>
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

      {/* Editor for the open rule — spans the full width below the three columns. */}
      {openIndex !== null && rules[openIndex] && (() => {
        const index = openIndex
        const rule = rules[index]
        const isCreating = creatingIndex === index
        const renderable = ruleIsRenderable(rule)
        return (
          <div className="rule-editor">
            <div className="rule-editor-header">
              <span className="rule-editor-title">{isCreating ? 'New rule' : 'Edit rule'}</span>
              <div className="rule-editor-actions">
                <button className="btn-secondary" onClick={() => cancelEdit(index)}>Cancel</button>
                <button
                  className={`btn-secondary rule-btn ${isCreating ? 'btn-unsaved' : ''}`}
                  onClick={() => saveCard(index)}
                  disabled={isCreating && !renderable}
                  title={isCreating && !renderable ? 'Set a value and a style first' : undefined}
                >
                  {isCreating ? 'Create' : 'Save'}
                </button>
              </div>
            </div>

            <div className="rule-fields-row">
              <div className="rule-field">
                <label>Rule name (optional)</label>
                <input
                  type="text"
                  value={rule.name}
                  onChange={(e) => updateRule(index, { name: e.target.value })}
                  placeholder="e.g. Christmas movies"
                />
              </div>
              <div className="rule-field">
                <label>Match on</label>
                <select value={rule.match} onChange={(e) => updateRule(index, { match: e.target.value as RuleMatchType })}>
                  <option value="label">Label</option>
                  <option value="genre">Genre</option>
                  <option value="collection">Collection</option>
                </select>
              </div>
              <div className="rule-field">
                <label>{MATCH_LABELS[rule.match]} value</label>
                <input
                  type="text"
                  value={rule.value}
                  onChange={(e) => updateRule(index, { value: e.target.value })}
                  placeholder={MATCH_PLACEHOLDER[rule.match]}
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
                      name={`rule-mode-${index}`}
                      checked={rule.mode === mode}
                      onChange={() => updateRule(index, { mode })}
                    />
                    <span>{MODE_LABELS[mode]}</span>
                  </label>
                ))}
              </div>
            </div>

            {rule.mode !== 'skip' && (
              <div style={{ marginTop: '0.75rem' }}>
                <BorderStyleControls
                  allowRemove
                  value={{ ...rule.style, colors: rule.colors }}
                  onChange={(patch) => applyRuleStylePatch(index, patch)}
                  overlays={overlays}
                  refreshOverlays={refreshOverlays}
                  idPrefix={`rule-${index}`}
                  label="Rule Border Style"
                  preview={<StyleValuePreview value={{ ...rule.style, colors: rule.colors }} borderWidth={borderWidth} />}
                />
              </div>
            )}
          </div>
        )
      })()}
    </div>
  )
}

export default PlexRulesSection
