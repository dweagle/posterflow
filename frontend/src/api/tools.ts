import { deleteData, getData, postData, putData } from './http'

// Display names for the image kinds a cleanup or reset tool can be limited to.
export const KIND_LABELS: Record<string, string> = { poster: 'Posters', background: 'Backgrounds', logo: 'Logos', squareart: 'Square art' }

// Sonarr/Radarr rename settings, one set per instance (a 4K Radarr can run its own count).
export type ArrRenameScope = 'files' | 'folders' | 'both' | 'tba' // tba: Sonarr only

export interface ArrRenameSettings {
  count: number // items per run (0 = all)
  batch: boolean // whole library in batches of count, in one run
  tag_name: string // tag put on processed items; blank = no cycle
  ignore_tag: string
  scope: ArrRenameScope // folders: the files inside a renamed folder go with it; tba: only files whose TBA title got its real title
  max_renames: number // items renamed per run at most; the rest wait (0 = no limit)
  scan_to_limit: boolean // check as many items as it takes to rename max_renames of them; count and batch then do not apply
  ignore_case: boolean // a rename that only changes capitals is left alone
  ignore_punctuation: boolean // and one that only changes special characters
  allow_irregular: boolean // rename a folder even when its new name drops the year, an id or the title
  hold_tba: boolean // Sonarr: a file whose new name still says TBA waits for the real title
  refresh_first: boolean
  title_filter: string // only titles containing this; blank = all
  notify: boolean // a Discord / Apprise summary after every run
  notify_all: boolean // every rename goes out, as several messages when the list is long; off = one message, trimmed
}

// Sonarr/Radarr upgrade search settings, one set per instance.
export type ArrUpgradeMode = 'all' | 'cutoff' | 'missing' // all: every monitored item that is out; the others: that Wanted list
export type ArrUpgradeUnit = 'series' | 'seasons' // Sonarr only
export type ArrUpgradeMonitored = 'yes' | 'any' | 'no' // Radarr only: Sonarr's own series and season searches refuse what is not monitored

export interface ArrUpgradeSettings {
  count: number // items searched per run (1 to 500)
  mode: ArrUpgradeMode
  search_unit: ArrUpgradeUnit // mode all: one search per monitored season, or one of the whole series
  season_threshold: number // by season: a season is searched when at least this % of its episodes are monitored
  tag_name: string // tag put on searched items; required
  ignore_tag: string
  monitored: ArrUpgradeMonitored
  only_tags: string // only items carrying one of these tags, comma separated; blank = any
  quality_profiles: string // only items on one of these quality profiles by name, comma separated; blank = any
  min_age_days: number // only items released at least this many days ago (0 = any age)
  max_grabs: number // a run stops once this many items got a release (0 = no limit)
  skip_queued: boolean // an item with a download in the queue waits for a later run
  title_filter: string // only titles containing this; blank = all
  notify: boolean // a Discord / Apprise summary after every run
  notify_grabs_only: boolean // only when something was grabbed or imported, or needs a look
}

// A profile's settings: the image kinds of a cleanup or reset, or the rename or upgrade search settings of one arr instance.
export type ToolSettings = { kinds?: string[] } & Partial<ArrRenameSettings> & Partial<ArrUpgradeSettings>

// One named settings set of a tool. The Tools page edits one, a run or a schedule names one,
// and the default is what runs when none is named. An arr renamer or upgrader keeps a list of profiles,
// with a default of its own, for each instance: `instance` says whose a profile is.
export interface ToolProfile {
  id: number
  name: string
  instance: string | null // null for a tool with one profile list
  is_default: boolean
  settings: ToolSettings
}

// The profiles one list holds: the tool's own, or the ones of one instance.
export const profilesOf = (tool: Pick<ToolInfo, 'profiles'>, instance?: string | null): ToolProfile[] =>
  tool.profiles.filter((p) => (p.instance ?? null) === (instance ?? null))

// Does each instance of the tool keep profiles of its own?
export const hasInstanceProfiles = (tool: Pick<ToolInfo, 'profiles'>): boolean => tool.profiles.some((p) => p.instance != null)

export interface ToolInfo {
  id: string
  label: string
  description: string
  job_type: string
  group: string // media server type the tool belongs to
  confirm: string | null // confirm button text for tools that ask first; null = runs straight away
  destructive?: boolean // it asks because it changes or removes things: its buttons are red. false = it only asks
  dry_run: boolean // offers a dry run
  kinds: string[] // image kinds a profile can switch on and off (poster, background, logo, squareart); empty = not applicable
  run_label: string // the main button
  per_instance: boolean // a run or a schedule may name one configured instance instead of all of them
  instances: string[] // the configured instances behind the tool's card
  available: boolean
  reason: string // why it can't run right now
  active_job: number | null // the job this tool is busy with right now (queued or running), if any
  profiles: ToolProfile[] // empty for a tool without settings
  defaults: ToolSettings | null // what a fresh profile holds; null for a tool without settings
}

export interface ToolRunResult {
  success: boolean
  job_id: number
  message: string
}

// One bundle per media server type: its configured servers and its tools.
export interface ToolGroup {
  id: string
  label: string
  instances: string[]
  tools: ToolInfo[]
}

// What a tools schedule runs, for schedule cards: the tool's label, the one instance when the
// schedule is limited to it, the profile when its list has more than one (the stored one, or the
// default when none is stored or it is gone), and a dry-run mark. A schedule of every instance
// of an arr renamer names no profile: each instance runs its own default.
export function describeToolSchedule(jobConfig: string | null | undefined, tools: ToolInfo[]): string | null {
  try {
    const parsed = JSON.parse(String(jobConfig || '').trim() || '{}')
    const id = String(parsed?.tool_id || '')
    if (!id) return null
    const tool = tools.find((t) => t.id === id)
    const named = typeof parsed?.instance === 'string' && parsed.instance.trim() ? parsed.instance : null
    const profiles = tool && (named || !hasInstanceProfiles(tool)) ? profilesOf(tool, named && hasInstanceProfiles(tool) ? named : null) : []
    let profile = ''
    if (profiles.length > 1) {
      const chosen = profiles.find((p) => p.id === parsed?.profile_id) ?? profiles.find((p) => p.is_default) ?? profiles[0]
      profile = ` · ${chosen.name}`
    }
    const instance = named ? ` · ${named}` : ''
    return (tool?.label ?? id) + instance + profile + (parsed?.dry_run === true ? ' (dry run)' : '')
  } catch {
    return null
  }
}

export const getTools = async (): Promise<{ groups: ToolGroup[] }> => getData('/api/tools')

// One movie or series of an arr instance, marked when it carries a tag of an arr tool (cycle, search or ignore).
export interface RenameTagItem {
  id: number
  title: string
  year: number | null
  tagged: boolean
}

// The instance's whole library, in title order, read from the arr each time.
export const getRenameTagged = async (toolId: string, instance: string, tag: string): Promise<{ instance: string; tag: string; items: RenameTagItem[] }> =>
  getData(`/api/tools/${toolId}/tagged?${new URLSearchParams({ instance, tag }).toString()}`)

// Puts the tag on the items in `add` and takes it off those in `remove`, in the arr.
export const saveRenameTagged = async (
  toolId: string,
  change: { instance: string; tag: string; add: number[]; remove: number[] },
): Promise<{ success: boolean; added: number; removed: number }> => putData(`/api/tools/${toolId}/tagged`, change)

// One movie or series of an arr instance, by its title.
export interface ArrTitle {
  id: number
  title: string
  year: number | null
}

// The instance's whole library in title order, read from the arr each time, to try a title filter against.
export const getArrTitles = async (toolId: string, instance: string): Promise<{ instance: string; items: ArrTitle[] }> =>
  getData(`/api/tools/${toolId}/titles?${new URLSearchParams({ instance }).toString()}`)

// A tag or a quality profile of an arr instance, with how many of its items carry or use it.
export interface ArrOption {
  name: string
  count: number
}

// What an instance has to pick a filter from, read from the arr each time.
export const getArrOptions = async (toolId: string, instance: string): Promise<{ instance: string; tags: ArrOption[]; quality_profiles: ArrOption[] }> =>
  getData(`/api/tools/${toolId}/arr-options?${new URLSearchParams({ instance }).toString()}`)

// Plex Metadata folder per Plex server (as Posterflow sees it): needed by the Plex cleanup
// tool and used by the overlay engine to tidy a bundle after each badge.
export const getPlexMetadataPaths = async (): Promise<{ instances: string[]; paths: Record<string, string> }> =>
  getData('/api/tools/plex-metadata-paths')

export const savePlexMetadataPaths = async (paths: Record<string, string>): Promise<{ success: boolean; paths: Record<string, string> }> =>
  putData('/api/tools/plex-metadata-paths', { paths })

export const checkPlexMetadataPath = async (instance: string, path: string): Promise<{ ok: boolean; reason: string; verified_title?: string | null }> =>
  postData('/api/tools/plex-metadata-paths/check', { instance, path })

// Profiles. A new one starts from the settings given (the tool's defaults when none), in the
// list of `instance` for a tool that keeps profiles per instance.
export const createToolProfile = async (toolId: string, name: string, settings?: ToolSettings, instance?: string): Promise<ToolProfile> =>
  postData(`/api/tools/${toolId}/profiles`, { name, ...(settings ? { settings } : {}), ...(instance ? { instance } : {}) })

export const updateToolProfile = async (
  toolId: string,
  profileId: number,
  updates: { name?: string; settings?: ToolSettings; is_default?: boolean },
): Promise<ToolProfile> => putData(`/api/tools/${toolId}/profiles/${profileId}`, updates)

export const deleteToolProfile = async (toolId: string, profileId: number): Promise<void> => {
  await deleteData<void>(`/api/tools/${toolId}/profiles/${profileId}`)
}

// A run gets a profile's saved settings (the default profile when none is named), or settings
// of its own when a page passes them (the Overlays page cleans posters only). A run of every
// instance of an arr renamer names the profile of each one in `profiles`.
export const runTool = async (
  id: string,
  options: { dry_run?: boolean; confirm?: boolean; profile_id?: number; settings?: ToolSettings; instance?: string; profiles?: Record<string, number> } = {},
): Promise<ToolRunResult> => postData(`/api/tools/${id}/run`, options)
