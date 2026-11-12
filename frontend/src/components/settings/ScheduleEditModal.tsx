import { useEffect, useMemo, useState } from 'react'
import { useHiddenPosterStyles } from '../../hooks/useHiddenPosterStyles'
import { Drive, Schedule, getMakerIdarrConfig, MakerIdarrSyncTarget, Workflow, listWorkflows } from '../../api/client'
import { getTools, hasInstanceProfiles, profilesOf, type ToolInfo } from '../../api/tools'
import { browserTimeZone, formatDateTime, sameTimeZone } from '../../utils/datetime'
import Modal from '../../components/Modal'

type EditingScheduleState = {
  schedule: Schedule
  index: number
}

type ScheduleEditModalProps = {
  editingSchedule: EditingScheduleState | null
  drives: Drive[]
  scheduleSaving: boolean
  effectiveTimezone: string
  updateScheduleField: <K extends keyof Schedule>(field: K, value: Schedule[K]) => void
  onClose: () => void
  onSave: () => void
}

function ScheduleEditModal({
  editingSchedule,
  drives,
  scheduleSaving,
  effectiveTimezone,
  updateScheduleField,
  onClose,
  onSave,
}: ScheduleEditModalProps) {
  const hiddenStyles = useHiddenPosterStyles()
  const [idarrTargets, setIdarrTargets] = useState<Array<{ index: number; target: MakerIdarrSyncTarget }>>([])
  const [workflows, setWorkflows] = useState<Workflow[]>([])
  const [tools, setTools] = useState<ToolInfo[]>([])
  const browserZone = browserTimeZone()

  const isSyncSchedule = editingSchedule?.schedule.job_type === 'gdrive_sync' || editingSchedule?.schedule.job_type === 'sync'
  const isIdarrSchedule = editingSchedule?.schedule.job_type === 'idarr'
  const isPosterWorkflowSchedule = editingSchedule?.schedule.job_type === 'poster_workflow'
  const isToolSchedule = editingSchedule?.schedule.job_type === 'tools'

  useEffect(() => {
    let mounted = true
    getTools()
      .then((resp) => { if (mounted) setTools(resp.groups.flatMap((g) => g.tools)) })
      .catch(() => { if (mounted) setTools([]) })
    return () => {
      mounted = false
    }
  }, [editingSchedule?.schedule.id])

  // A tools schedule keeps {tool_id, dry_run} in job_config, plus profile_id when the tool has
  // profiles: the one whose saved settings the run gets. An arr renamer keeps its profiles per
  // instance, so there a profile goes with the instance it belongs to.
  const toolConfig = useMemo(() => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    let parsed: Record<string, unknown> = {}
    if (raw) {
      try {
        const value = JSON.parse(raw)
        if (value && typeof value === 'object') parsed = value as Record<string, unknown>
      } catch {
        // ignore malformed
      }
    }
    const known = tools.find((t) => t.id === parsed.tool_id)
    const profileId = typeof parsed.profile_id === 'number' ? parsed.profile_id : null
    const instance = typeof parsed.instance === 'string' && parsed.instance.trim() ? parsed.instance : null
    return { toolId: known ? known.id : (tools[0]?.id ?? ''), dryRun: parsed.dry_run === true, profileId, instance }
  }, [editingSchedule?.schedule.job_config, tools])

  const scheduledTool = tools.find((t) => t.id === toolConfig.toolId)
  // The instance whose profiles the picker lists, for a tool that keeps them per instance: the
  // one the schedule names, or the only one there is. None while it runs every instance.
  const profileOwner = (tool: ToolInfo | undefined, instance: string | null): string | null => {
    if (!tool || !hasInstanceProfiles(tool)) return null
    return instance ?? ((tool.instances ?? []).length === 1 ? tool.instances[0] : null)
  }
  const scheduledOwner = profileOwner(scheduledTool, toolConfig.instance)
  const everyInstance = Boolean(scheduledTool && hasInstanceProfiles(scheduledTool) && !scheduledOwner)  // each then runs its own default
  const scheduledProfiles = scheduledTool && !everyInstance ? profilesOf(scheduledTool, scheduledOwner) : []
  // The profile this schedule runs: the stored one, or the default of its list when none is stored or it is gone.
  const scheduledProfile = scheduledProfiles.length > 0
    ? (scheduledProfiles.find((p) => p.id === toolConfig.profileId) ?? scheduledProfiles.find((p) => p.is_default) ?? scheduledProfiles[0])
    : null

  const updateToolConfig = (patch: { tool_id?: string; dry_run?: boolean; profile_id?: number; instance?: string | null }) => {
    const toolId = patch.tool_id ?? toolConfig.toolId
    const tool = tools.find((t) => t.id === toolId)
    const instance = patch.tool_id !== undefined ? null : (patch.instance !== undefined ? patch.instance : toolConfig.instance)  // another tool has other instances
    let storedInstance = tool?.per_instance && instance && (tool.instances ?? []).includes(instance) ? instance : null
    // Another tool has other profiles, and so has another instance of an arr renamer.
    const moved = patch.tool_id !== undefined || (patch.instance !== undefined && Boolean(tool && hasInstanceProfiles(tool)))
    const profileId = moved ? null : (patch.profile_id ?? toolConfig.profileId)
    const owner = profileOwner(tool, storedInstance)
    const list = tool && !(hasInstanceProfiles(tool) && !owner) ? profilesOf(tool, owner) : []
    const stored = profileId !== null && list.some((p) => p.id === profileId) ? profileId : null
    if (stored !== null && owner) storedInstance = owner  // a profile of the only instance names it
    updateScheduleField('job_config', JSON.stringify({
      tool_id: toolId, dry_run: patch.dry_run ?? toolConfig.dryRun,
      ...(stored !== null ? { profile_id: stored } : {}), ...(storedInstance ? { instance: storedInstance } : {}),
    }))
  }

  // A schedule that just became a Tool one shows the first tool but stores nothing yet, and the
  // save is refused without a tool: store what the picker shows. A stored profile that is gone
  // is dropped the same way, so the save is not refused over it.
  useEffect(() => {
    if (!isToolSchedule || tools.length === 0) return
    let parsed: { tool_id?: unknown; dry_run?: unknown; profile_id?: unknown; instance?: unknown } = {}
    try {
      parsed = JSON.parse(String(editingSchedule?.schedule.job_config || '').trim() || '{}') ?? {}
    } catch {
      // nothing usable stored
    }
    const tool = tools.find((t) => t.id === parsed.tool_id)
    const instanceKept = tool?.per_instance && typeof parsed.instance === 'string' && (tool.instances ?? []).includes(parsed.instance) ? parsed.instance : null
    // The stored profile has to be one of the list the schedule runs from: the tool's, or the instance's.
    const list = tool ? profilesOf(tool, hasInstanceProfiles(tool) ? instanceKept : null) : []
    const profileKept = typeof parsed.profile_id === 'number' && (!tool || !hasInstanceProfiles(tool) || instanceKept) && list.some((p) => p.id === parsed.profile_id)
    if (!tool) {
      updateScheduleField('job_config', JSON.stringify({ tool_id: tools[0].id, dry_run: false }))
    } else if ((parsed.profile_id !== undefined && !profileKept) || (parsed.instance !== undefined && !instanceKept)) {
      // A profile or an instance that is gone is dropped: the default profile runs, on every instance.
      updateScheduleField('job_config', JSON.stringify({
        tool_id: tool.id, dry_run: parsed.dry_run === true,
        ...(profileKept ? { profile_id: parsed.profile_id } : {}), ...(instanceKept ? { instance: instanceKept } : {}),
      }))
    }
  }, [isToolSchedule, tools, editingSchedule?.schedule.job_config, updateScheduleField])

  useEffect(() => {
    let mounted = true

    const loadIdarrTargets = async () => {
      try {
        const config = await getMakerIdarrConfig()
        const targets = Array.isArray(config.sync_targets) ? config.sync_targets : []
        const validTargets = targets
          .map((target, index) => ({ index, target }))
          .filter((entry) => String(entry.target.source_dir || '').trim() !== '')
        if (mounted) {
          setIdarrTargets(validTargets)
        }
      } catch {
        if (mounted) {
          setIdarrTargets([])
        }
      }
    }

    loadIdarrTargets()

    return () => {
      mounted = false
    }
  }, [editingSchedule?.schedule.id])

  useEffect(() => {
    let mounted = true

    const loadWorkflows = async () => {
      try {
        const list = await listWorkflows()
        if (mounted) setWorkflows(list)
      } catch {
        if (mounted) setWorkflows([])
      }
    }

    loadWorkflows()

    return () => {
      mounted = false
    }
  }, [editingSchedule?.schedule.id])

  const selectedWorkflowId = useMemo(() => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed?.workflow_id != null) return Number(parsed.workflow_id)
      } catch {
        // ignore malformed
      }
    }
    const fallback = workflows.find((w) => w.is_default) || workflows[0]
    return fallback ? fallback.id : ''
  }, [editingSchedule?.schedule.job_config, workflows])

  const updateWorkflowSelection = (value: number) => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    let existing: Record<string, unknown> = {}
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
          existing = parsed as Record<string, unknown>
        }
      } catch {
        // ignore malformed
      }
    }
    existing.workflow_id = value
    updateScheduleField('job_config', JSON.stringify(existing))
  }

  const selectedIdarrScope = useMemo(() => {
    const group = String(editingSchedule?.schedule.drive_group || '').trim()
    if (!group.startsWith('idarr_target_')) {
      return ''
    }
    return group
  }, [editingSchedule?.schedule.drive_group])

  const idarrSyncAfterRun = useMemo(() => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    if (!raw) return false
    try {
      const parsed = JSON.parse(raw)
      return Boolean(parsed?.sync_after_run)
    } catch {
      return false
    }
  }, [editingSchedule?.schedule.job_config])

  const updateIdarrSyncAfterRun = (value: boolean) => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    let existing: Record<string, unknown> = {}
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
          existing = parsed as Record<string, unknown>
        }
      } catch {
        // ignore malformed
      }
    }
    existing.sync_after_run = value
    updateScheduleField('job_config', JSON.stringify(existing))
  }

  const idarrForceSync = useMemo(() => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    if (!raw) return false
    try {
      const parsed = JSON.parse(raw)
      return Boolean(parsed?.force_sync_after_run)
    } catch {
      return false
    }
  }, [editingSchedule?.schedule.job_config])

  const updateIdarrForceSync = (value: boolean) => {
    const raw = String(editingSchedule?.schedule.job_config || '').trim()
    let existing: Record<string, unknown> = {}
    if (raw) {
      try {
        const parsed = JSON.parse(raw)
        if (parsed && typeof parsed === 'object') {
          existing = parsed as Record<string, unknown>
        }
      } catch {
        // ignore malformed
      }
    }
    existing.force_sync_after_run = value
    updateScheduleField('job_config', JSON.stringify(existing))
  }

  if (!editingSchedule) {
    return null
  }

  return (
    <Modal onClose={onClose} className="schedule-edit-modal" closeOnOverlay={false}>
      <Modal.Header title="Edit Schedule" />
      <Modal.Body>
        <div className="form-group">
          <label className="field-label">Schedule Name</label>
          <input
            type="text"
            value={editingSchedule.schedule.name}
            onChange={(e) => updateScheduleField('name', e.target.value)}
            placeholder="e.g., Morning Sync, Daily Backup"
          />
        </div>

        <div className="form-group">
          <label className="field-label">Scheduled Task</label>
          <select
            value={editingSchedule.schedule.job_type}
            onChange={(e) => {
              const value = e.target.value
              updateScheduleField('job_type', value)
              if (value === 'idarr') {
                const fallbackScope = idarrTargets.length > 0 ? `idarr_target_${idarrTargets[0].index}` : null
                updateScheduleField('drive_id', null)
                updateScheduleField('drive_group', fallbackScope)
              }
            }}
          >
            <option value="poster_workflow">Poster Workflow</option>
            <option value="gdrive_sync">Poster Drive Sync</option>
            <option value="artwork_sync">Artwork Drive Sync</option>
            <option value="poster_renamer">Asset Renamer</option>
            <option value="unmatched_assets">Unmatched Assets</option>
            <option value="border_replacer">Border Replacer</option>
            <option value="overlays">Overlays</option>
            <option value="tools">Tool</option>
            <option value="idarr">IDarr</option>
            <option value="maker_monitor">Maker Monitor</option>
            <option value="backup">Backup</option>
          </select>
        </div>

        {isToolSchedule && (
          <>
            <div className="form-group">
              <label className="field-label" htmlFor="schedule-tool">Tool</label>
              <select id="schedule-tool" value={toolConfig.toolId} onChange={(e) => updateToolConfig({ tool_id: e.target.value })}>
                {tools.length === 0 ? (
                  <option value="">No tools available</option>
                ) : (
                  tools.map((t) => (
                    <option key={t.id} value={t.id}>{t.label}{t.available ? '' : ' (not set up)'}</option>
                  ))
                )}
              </select>
              {scheduledTool && ['sonarr', 'radarr'].includes(scheduledTool.group) && (
                <p className="field-hint">Each run moves a little further through the library.</p>
              )}
              {scheduledTool?.confirm && (
                <p className="field-hint">On the Tools page this tool asks before it runs. A schedule runs it without asking.</p>
              )}
            </div>
            {scheduledTool?.per_instance && (scheduledTool.instances ?? []).length > 1 && (
              <div className="form-group">
                <label className="field-label" htmlFor="schedule-tool-instance">Instance</label>
                <select id="schedule-tool-instance" value={toolConfig.instance ?? ''} onChange={(e) => updateToolConfig({ instance: e.target.value || null })}>
                  <option value="">All instances</option>
                  {scheduledTool.instances.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
                <p className="field-hint">
                  {everyInstance ? 'Every instance runs its own default profile. Pick one instance to choose a profile for it.' : 'Run every instance, or just this one.'}
                </p>
              </div>
            )}
            {scheduledTool && scheduledProfile && (
              <div className="form-group">
                <label className="field-label" htmlFor="schedule-tool-profile">Profile</label>
                <select id="schedule-tool-profile" value={scheduledProfile.id} onChange={(e) => updateToolConfig({ profile_id: Number(e.target.value) })}>
                  {scheduledProfiles.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}{p.is_default ? ' (default)' : ''}</option>
                  ))}
                </select>
                <p className="field-hint">
                  {scheduledOwner ? `The run uses this profile of ${scheduledOwner}, as saved on the Tools page.` : "The run uses this profile's saved settings from the Tools page."}
                </p>
              </div>
            )}
            <div className="form-group">
              <label className="checkbox-label">
                <input type="checkbox" checked={toolConfig.dryRun} onChange={(e) => updateToolConfig({ dry_run: e.target.checked })} />
                Dry run only
              </label>
            </div>
          </>
        )}

        {isPosterWorkflowSchedule && (
          <div className="form-group">
            <label className="field-label">Workflow</label>
            <select
              value={selectedWorkflowId}
              onChange={(e) => updateWorkflowSelection(Number(e.target.value))}
            >
              {workflows.length === 0 ? (
                <option value="">No workflows configured</option>
              ) : (
                workflows.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}{w.is_default ? ' (default)' : ''}
                  </option>
                ))
              )}
            </select>
            <p className="field-hint">Choose which saved workflow this schedule runs.</p>
          </div>
        )}

        {isIdarrSchedule && (
          <div className="form-group">
            <label className="field-label">IDarr Scope</label>
            <select
              value={selectedIdarrScope}
              onChange={(e) => {
                const value = e.target.value || null
                updateScheduleField('drive_id', null)
                updateScheduleField('drive_group', value)
              }}
            >
              {idarrTargets.length === 0 ? (
                <option value="">No IDarr sync targets configured</option>
              ) : (
                idarrTargets.map(({ index, target }) => {
                  const label = String(target.label || '').trim() || `Target ${index + 1}`
                  const sourceDir = String(target.source_dir || '').trim()
                  return (
                    <option key={index} value={`idarr_target_${index}`}>
                      {`${label} — ${sourceDir}`}
                    </option>
                  )
                })
              )}
            </select>
            <p className="field-hint">Choose which IDarr sync target this schedule should process.</p>
          </div>
        )}

        {isIdarrSchedule && (
          <div className="form-group">
            <label className="field-label">Sync Personal Drive After Run</label>
            <div className="schedule-toggle">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={idarrSyncAfterRun}
                  onChange={(e) => updateIdarrSyncAfterRun(e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">
                {idarrSyncAfterRun ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <p className="field-hint">When enabled, IDarr will automatically sync the processed folder to your personal drive after each run.</p>
          </div>
        )}

        {isIdarrSchedule && idarrSyncAfterRun && (
          <div className="form-group">
            <label className="field-label">Force Sync Even With No Changes</label>
            <div className="schedule-toggle">
              <label className="toggle-switch">
                <input
                  type="checkbox"
                  checked={idarrForceSync}
                  onChange={(e) => updateIdarrForceSync(e.target.checked)}
                />
                <span className="toggle-slider"></span>
              </label>
              <span className="toggle-label">
                {idarrForceSync ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <p className="field-hint">When enabled, the personal drive sync will run even if no files were renamed. <strong>This overrides the global "Force Sync After Run" setting in IDarr settings.</strong></p>
          </div>
        )}

        {isSyncSchedule && (
          <div className="form-group">
            <label className="field-label">Drive Selection</label>
            <select
              value={
                editingSchedule.schedule.drive_id
                  ? `drive_${editingSchedule.schedule.drive_id}`
                  : editingSchedule.schedule.drive_group
                    ? `group_${editingSchedule.schedule.drive_group}`
                    : 'all'
              }
              onChange={(e) => {
                const value = e.target.value
                if (value === 'all') {
                  updateScheduleField('drive_id', null)
                  updateScheduleField('drive_group', null)
                } else if (value.startsWith('group_')) {
                  const group = value.substring(6)
                  updateScheduleField('drive_id', null)
                  updateScheduleField('drive_group', group)
                } else if (value.startsWith('drive_')) {
                  const driveId = parseInt(value.substring(6), 10)
                  updateScheduleField('drive_id', driveId)
                  updateScheduleField('drive_group', null)
                }
              }}
            >
              <option value="all">All Subscribed Drives</option>
              {(!hiddenStyles.has('CL2K') || editingSchedule.schedule.drive_group === 'CL2K') && <option value="group_CL2K">All CL2K Drives</option>}
              {(!hiddenStyles.has('MM2K') || editingSchedule.schedule.drive_group === 'MM2K') && <option value="group_MM2K">All MM2K Drives</option>}
              <option value="group_Custom">All Custom Drives</option>
              <optgroup label="Individual Drives">
                {drives.filter((d) => d.subscribed).map((drive) => (
                  <option key={drive.id} value={`drive_${drive.id}`}>
                    {drive.display_name || drive.name}
                  </option>
                ))}
              </optgroup>
            </select>
          </div>
        )}

        <div className="form-group">
          <label className="field-label">Status</label>
          <div className="schedule-toggle">
            <label className="toggle-switch">
              <input
                type="checkbox"
                checked={editingSchedule.schedule.enabled}
                onChange={(e) => updateScheduleField('enabled', e.target.checked)}
              />
              <span className="toggle-slider"></span>
            </label>
            <span className="toggle-label">
              {editingSchedule.schedule.enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
        </div>

        <div className="form-group">
          <label className="field-label">Schedule Type</label>
          <div className="schedule-type-buttons pf-subtabs">
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'hourly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'hourly')}>
              Hourly
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'daily' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'daily')}>
              Daily
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'multiple_daily' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'multiple_daily')}>
              Multiple Daily
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'weekly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'weekly')}>
              Weekly
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'multiple_days' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'multiple_days')}>
              Multiple Days
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'monthly' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'monthly')}>
              Monthly
            </button>
            <button type="button" className={`schedule-type-btn ${editingSchedule.schedule.schedule_type === 'cron' ? 'active' : ''}`} onClick={() => updateScheduleField('schedule_type', 'cron')}>
              Cron
            </button>
          </div>
        </div>

        {editingSchedule.schedule.schedule_type === 'hourly' && (
          <div className="form-group">
            <label className="field-label">Minute Offset</label>
            <input
              type="text"
              value={editingSchedule.schedule.schedule_value || ''}
              onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSave()}
              placeholder="0"
            />
            <p className="field-hint">Minute (0-59) when the job runs each hour (e.g., 0 = on the hour, 15 = quarter past, 30 = half past)</p>
          </div>
        )}

        {editingSchedule.schedule.schedule_type === 'daily' && (
          <div className="form-group">
            <label className="field-label">Time (24-hour format)</label>
            <input
              type="text"
              value={editingSchedule.schedule.schedule_value || ''}
              onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSave()}
              placeholder="09:00"
            />
            <p className="field-hint">Format: HH:MM (e.g., 09:00 for 9 AM, 14:30 for 2:30 PM)</p>
          </div>
        )}

        {editingSchedule.schedule.schedule_type === 'multiple_daily' && (
          <div className="form-group">
            <label className="field-label">Times (24-hour format)</label>
            <input
              type="text"
              value={editingSchedule.schedule.schedule_value || ''}
              onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSave()}
              placeholder="07:00,19:00"
            />
            <p className="field-hint">Comma-separated times (e.g., 07:00,12:00,19:00)</p>
          </div>
        )}

        {editingSchedule.schedule.schedule_type === 'weekly' && (
     <>
            <div className="form-group">
              <label className="field-label">Select Day</label>
              <div className="day-toggle-buttons pf-subtabs">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day, dayIndex) => {
                  const currentDay = editingSchedule.schedule.schedule_value?.split(':')[0]
                  const isSelected = currentDay === String(dayIndex)
                  return (
                    <button
                      key={dayIndex}
                      type="button"
                      className={`day-toggle-btn ${isSelected ? 'active' : ''}`}
                      onClick={() => {
                        const time = editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''
                        updateScheduleField('schedule_value', `${dayIndex}:${time}`)
                      }}
                    >
                      {day}
                    </button>
                  )
                })}
              </div>
            </div>
            <div className="form-group">
              <label className="field-label">Time (24-hour format)</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''}
                onChange={(e) => {
                  const day = editingSchedule.schedule.schedule_value?.split(':')[0] || '0'
                  updateScheduleField('schedule_value', `${day}:${e.target.value}`)
                }}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="09:00"
              />
              <p className="field-hint">Format: HH:MM or comma-separated for multiple times (e.g., 09:00 or 09:00,14:30,19:00)</p>
            </div>
          </>
        )}

        {editingSchedule.schedule.schedule_type === 'monthly' && (
     <>
            <div className="form-group">
              <label className="field-label">Day of Month</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value?.split(':')[0] || ''}
                onChange={(e) => {
                  const time = editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''
                  updateScheduleField('schedule_value', `${e.target.value}:${time}`)
                }}
                placeholder="1"
              />
              <p className="field-hint">Day 1-31 (will use last day if month has fewer days)</p>
            </div>
            <div className="form-group">
              <label className="field-label">Time (24-hour format)</label>
              <input
                type="text"
                value={editingSchedule.schedule.schedule_value?.split(':').slice(1).join(':') || ''}
                onChange={(e) => {
                  const day = editingSchedule.schedule.schedule_value?.split(':')[0] || '1'
                  updateScheduleField('schedule_value', `${day}:${e.target.value}`)
                }}
                onKeyDown={(e) => e.key === 'Enter' && onSave()}
                placeholder="09:00"
              />
              <p className="field-hint">Format: HH:MM or comma-separated for multiple times (e.g., 09:00 or 09:00,14:30,19:00)</p>
            </div>
          </>
        )}

        {editingSchedule.schedule.schedule_type === 'multiple_days' && (
     <>
            <div className="form-group">
              <label className="field-label">Select Days</label>
              <div className="day-toggle-buttons pf-subtabs">
                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map((day, dayIndex) => {
                  const schedules = editingSchedule.schedule.schedule_value?.split('|') || []
                  const isSelected = schedules.some((s) => s.startsWith(`${dayIndex}:`))
                  return (
                    <button
                      key={dayIndex}
                      type="button"
                      className={`day-toggle-btn ${isSelected ? 'active' : ''}`}
                      onClick={() => {
                        const currentSchedules = editingSchedule.schedule.schedule_value?.split('|').filter((s) => s) || []
                        if (isSelected) {
                          const updated = currentSchedules.filter((s) => !s.startsWith(`${dayIndex}:`))
                          updateScheduleField('schedule_value', updated.join('|'))
                        } else {
                          const updated = [...currentSchedules, `${dayIndex}:`]
                          updateScheduleField('schedule_value', updated.join('|'))
                        }
                      }}
                    >
                      {day}
                    </button>
                  )
                })}
              </div>
            </div>

            {editingSchedule.schedule.schedule_value && editingSchedule.schedule.schedule_value.split('|').filter((s) => s).length > 0 && (
              <div className="form-group">
                <label className="field-label">Times for Each Day (24-hour format)</label>
                <p className="field-hint">Enter comma-separated times for each day (e.g., 07:00,19:00)</p>
                <div className="day-time-list">
                  {['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'].map((dayName, dayIndex) => {
                    const schedules = editingSchedule.schedule.schedule_value?.split('|') || []
                    const daySchedule = schedules.find((s) => s.startsWith(`${dayIndex}:`))

                    if (!daySchedule) return null

                    const times = daySchedule.substring(2)

                    return (
                      <div key={dayIndex} className="day-time-item">
                        <span className="day-name">{dayName}</span>
                        <input
                          type="text"
                          value={times}
                          onChange={(e) => {
                            const currentSchedules = editingSchedule.schedule.schedule_value?.split('|').filter((s) => s) || []
                            const updated = currentSchedules.map((s) => (s.startsWith(`${dayIndex}:`) ? `${dayIndex}:${e.target.value}` : s))
                            updateScheduleField('schedule_value', updated.join('|'))
                          }}
                          placeholder="09:00,17:00"
                          className="day-time-input"
                        />
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </>
        )}

        {editingSchedule.schedule.schedule_type === 'cron' && (
          <div className="form-group">
            <label className="field-label">Cron Expression</label>
            <input
              type="text"
              value={editingSchedule.schedule.schedule_value || ''}
              onChange={(e) => updateScheduleField('schedule_value', e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && onSave()}
              placeholder="0 2 * * *"
            />
            <p className="field-hint">
              Format: minute hour day month weekday<br />
              Examples:<br />
              • "0 2 * * *" = 2:00 AM daily<br />
              • "0 9,17 * * *" = 9:00 AM and 5:00 PM daily<br />
              • "30 */3 * * *" = Every 3 hours at :30 minutes<br />
              • "0 8 * * 1-5" = 8:00 AM weekdays only
            </p>
          </div>
        )}

        {effectiveTimezone && !sameTimeZone(browserZone, effectiveTimezone) && (
          <p className="field-hint">
            Schedule times are in {effectiveTimezone}. Your browser is in {browserZone}.
          </p>
        )}

        {editingSchedule.schedule.last_run && (
          <div className="schedule-status">
            <div className="status-item">
              <span className="status-label">Last Run:</span>
              <span className="status-value">{formatDateTime(editingSchedule.schedule.last_run)}</span>
            </div>
          </div>
        )}
      </Modal.Body>
      <Modal.Footer>
        <button className="btn-secondary" onClick={onClose}>
          Cancel
        </button>
        <button className="btn-primary" onClick={onSave} disabled={scheduleSaving}>
          {scheduleSaving ? 'Saving...' : 'Save Schedule'}
        </button>
      </Modal.Footer>
    </Modal>
  )
}

export default ScheduleEditModal
