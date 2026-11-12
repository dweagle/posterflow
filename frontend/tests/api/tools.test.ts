import { describe, expect, it } from 'vitest'
import { describeToolSchedule, type ToolInfo } from '../../src/api/tools'

const tool = (id: string, label: string, profiles: ToolInfo['profiles']): ToolInfo => ({
  id, label, description: '', job_type: 'Tools', group: 'plex', confirm: null, dry_run: true, kinds: [], run_label: 'Run', per_instance: false, instances: ['plex-1'], available: true, reason: '',
  active_job: null, profiles, defaults: null,
})
const tools = [
  tool('plex_reset', 'Reset Plex images', [
    { id: 1, name: 'Default', instance: null, is_default: true, settings: { kinds: ['poster'] } },
    { id: 2, name: 'Logos', instance: null, is_default: false, settings: { kinds: ['logo'] } },
  ]),
  tool('plex_cleanup', 'Clean up Plex uploads', [{ id: 3, name: 'Default', instance: null, is_default: true, settings: { kinds: ['poster'] } }]),
  // An arr renamer: every instance has profiles of its own.
  { ...tool('radarr_rename', 'Rename Radarr files', [
    { id: 5, name: 'Default', instance: 'Radarr', is_default: true, settings: {} },
    { id: 6, name: 'Default', instance: 'Radarr 4k', is_default: true, settings: {} },
    { id: 7, name: 'Slow', instance: 'Radarr 4k', is_default: false, settings: {} },
  ]), group: 'radarr', per_instance: true, instances: ['Radarr', 'Radarr 4k'] },
]

describe('describeToolSchedule', () => {
  it('names the tool, the profile when the tool has several, and a dry run', () => {
    expect(describeToolSchedule('{"tool_id":"plex_reset","dry_run":false,"profile_id":2}', tools)).toBe('Reset Plex images · Logos')
    expect(describeToolSchedule('{"tool_id":"plex_reset","dry_run":true}', tools)).toBe('Reset Plex images · Default (dry run)')  // none stored: the default runs
    expect(describeToolSchedule('{"tool_id":"plex_reset","profile_id":99}', tools)).toBe('Reset Plex images · Default')  // gone: the default runs
    expect(describeToolSchedule('{"tool_id":"plex_cleanup","profile_id":3}', tools)).toBe('Clean up Plex uploads')  // one profile: nothing to tell apart
    expect(describeToolSchedule('{"tool_id":"plex_cleanup","instance":"plex-4k","dry_run":true}', tools)).toBe('Clean up Plex uploads · plex-4k (dry run)')  // limited to one instance
  })

  it('names the instance and then its profile for a tool that keeps profiles per instance', () => {
    expect(describeToolSchedule('{"tool_id":"radarr_rename","instance":"Radarr 4k","profile_id":7}', tools)).toBe('Rename Radarr files · Radarr 4k · Slow')
    expect(describeToolSchedule('{"tool_id":"radarr_rename","instance":"Radarr 4k"}', tools)).toBe('Rename Radarr files · Radarr 4k · Default')  // none stored: its default
    expect(describeToolSchedule('{"tool_id":"radarr_rename","instance":"Radarr","dry_run":true}', tools)).toBe('Rename Radarr files · Radarr (dry run)')  // one profile there
    expect(describeToolSchedule('{"tool_id":"radarr_rename"}', tools)).toBe('Rename Radarr files')  // every instance, each its own default
  })

  it('falls back to the raw id and gives up on broken or empty config', () => {
    expect(describeToolSchedule('{"tool_id":"gone"}', tools)).toBe('gone')
    expect(describeToolSchedule(null, tools)).toBeNull()
    expect(describeToolSchedule('not json', tools)).toBeNull()
  })
})
