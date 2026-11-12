import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, CircleArrowUp, Eraser, FilePen, FlaskConical, Hammer, Info, ListChecks, Pencil, Plus, RotateCcw, Search, Server, Sparkles, Trash2, type LucideIcon } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import Toolbar from '../components/Toolbar'
import ConfirmDialog from '../components/ConfirmDialog'
import Modal from '../components/Modal'
import SaveActions from '../components/SaveActions'
import RenameTagModal, { type RenameTagKind } from '../components/tools/RenameTagModal'
import ArrPickModal, { type ArrPickKind } from '../components/tools/ArrPickModal'
import TitleFilterModal from '../components/tools/TitleFilterModal'
import { useToast } from '../components/Toast'
import { getApiErrorMessage, getJob } from '../api/client'
import {
  checkPlexMetadataPath, createToolProfile, deleteToolProfile, getPlexMetadataPaths, getTools, runTool, savePlexMetadataPaths, updateToolProfile,
  KIND_LABELS, profilesOf, type ArrRenameScope, type ArrRenameSettings, type ArrUpgradeMode, type ArrUpgradeMonitored, type ArrUpgradeSettings, type ArrUpgradeUnit, type ToolGroup, type ToolInfo, type ToolProfile, type ToolSettings,
} from '../api/tools'
import './Tools.css'

// The app's hover/focus "i": the explanation lives in the tooltip, not in the card.
function InfoTip({ text }: { text: string }) {
  // A tip may sit inside a <label>: a click on it must not focus or toggle the label's control.
  return (
    <span className="toolbar-info" tabIndex={0} aria-label={text} onClick={(e) => e.preventDefault()}>
      <Info size={14} />
      <div className="toolbar-tooltip">{text}</div>
    </span>
  )
}

// Names a new profile, or renames one. Enter saves.
function ProfileNameModal({ title, initial, hint, busy, onCancel, onSave }: {
  title: string; initial: string; hint: string; busy: boolean; onCancel: () => void; onSave: (name: string) => void
}) {
  const [name, setName] = useState(initial)
  const ready = name.trim().length > 0 && !busy
  return (
    <Modal onClose={onCancel} size="sm" busy={busy}>
      <Modal.Header title={title} />
      <Modal.Body>
        <div className="form-group">
          <label className="field-label" htmlFor="tool-profile-name">Profile name</label>
          <input
            id="tool-profile-name"
            type="text"
            value={name}
            maxLength={60}
            autoFocus
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && ready) { e.preventDefault(); onSave(name.trim()) } }}
          />
          <p className="field-hint">{hint}</p>
        </div>
      </Modal.Body>
      <Modal.Footer>
        <button type="button" className="btn-secondary" onClick={onCancel} disabled={busy}>Cancel</button>
        <button type="button" className="btn-primary" disabled={!ready} onClick={() => onSave(name.trim())}>{busy ? 'Saving…' : 'Save'}</button>
      </Modal.Footer>
    </Modal>
  )
}

const PLEX_PATHS_HELP = 'Plex keeps every image ever uploaded to an item (posters, backgrounds, logos, square art) and has no API to remove them. Give Posterflow the path to each server\'s Plex Media Server folder, or to its Metadata folder, as Posterflow sees it (a mounted volume in Docker). Clean up Plex uploads needs it, and the overlay engine uses it to tidy a bundle after each badge. Posterflow\'s user must be able to delete files there: run it with the Plex user\'s group (PGID or EXTRA_GROUPS).'
const JELLYFIN_HELP = 'Jellyfin keeps one poster file per item and replaces it on upload, so there is nothing to prune. Replaced posters only leave resized copies in its image cache, which its own cache clean removes.'
const PROFILE_HELP = 'A profile is one saved set of these settings under a name of its own. Keep as many as you like. The buttons run the profile picked here, and a schedule picks one too.'
const INSTANCE_PROFILE_HELP = `${PROFILE_HELP} Each instance has profiles of its own.`
const ARR_GROUPS = ['sonarr', 'radarr']
// The page's subtabs: which cards each one shows, which of a card's tools (by the end of their id; '' = all),
// and what to say when none has an instance.
type ToolsTab = 'media' | 'arr' | 'upgrade'
const ARR_EMPTY = 'No Sonarr or Radarr instance is configured yet. Add one under Settings.'
const TABS: { key: ToolsTab; label: string; icon: ReactNode; groups: string[]; suffix: string; empty: string }[] = [
  { key: 'media', label: 'Media Servers', icon: <Server size={15} />, groups: ['plex', 'jellyfin'], suffix: '', empty: 'No media server is configured yet. Add one under Settings.' },
  { key: 'arr', label: 'Arr Renamer', icon: <FilePen size={15} />, groups: ARR_GROUPS, suffix: '_rename', empty: ARR_EMPTY },
  { key: 'upgrade', label: 'Arr Upgrader', icon: <CircleArrowUp size={15} />, groups: ARR_GROUPS, suffix: '_upgrade', empty: ARR_EMPTY },
]
const TAB_STORAGE_KEY = 'posterflow.tools.activeTab'
const initialTab = (): ToolsTab => {
  try {
    const saved = localStorage.getItem(TAB_STORAGE_KEY)
    return TABS.some((t) => t.key === saved) ? (saved as ToolsTab) : 'media'
  } catch { return 'media' }
}
const RENAME_HELP = 'The app renames with its own naming format; Posterflow only asks which files and folders drifted, holds back renames that look wrong, confirms every file is still there afterwards, and tags what it processed so the next run moves on. Once every item carries the cycle tag, the tags are cleared and the cycle starts over. Items per run limits each run (0 = all). Whole library in batches runs through everything in one go, in batches of that size.'
const RENAME_FIELD_HELP: Record<keyof ArrRenameSettings, string> = {
  count: 'How many items each run checks. 0 checks them all. With batches on, this is the batch size. 50 to 75 suits most libraries.',
  batch: 'Process the whole library in one run, in batches of this size.',
  tag_name: 'Tag put on processed items. They are skipped until every item carries it, then the tags are cleared. Blank checks everything every run. Letters, digits and - only. Manage shows which items carry it, and can add or take it off.',
  ignore_tag: 'Items with this tag are never renamed. Letters, digits and - only. Manage picks the items that carry it.',
  scope: 'What to rename. Files: the files of every item. Folders: the item folders, and the files inside a folder that gets renamed. Files and folders: both.',
  max_renames: 'Rename at most this many items per run. The rest wait for the next runs and stay untagged, so they are checked again. 0 sets no limit. Held back and ignored items do not count.',
  scan_to_limit: 'Keep checking items until the rename limit is reached, however many that takes. Items per run and batches do not apply then. The next run carries on where this one stopped. Needs a rename limit.',
  ignore_case: 'Leave alone a folder or file whose new name only differs in capitals.',
  ignore_punctuation: 'Leave alone a folder or file whose new name only differs in special characters, like ! \' and :.',
  allow_irregular: 'A folder whose new name drops the year, an id or the title is held back, as it usually means the item\'s metadata is incomplete. Turn this on to rename those anyway, after a change to your naming format for example.',
  hold_tba: 'A file whose new name still has TBA as the episode title waits until the app knows the real title, so it is not renamed twice.',
  refresh_first: 'Refresh each item\'s metadata from its provider before asking what needs renaming. Slower, but catches title updates at once.',
  title_filter: 'Only items whose title contains this text are checked. Handy to try one item first. Find shows which titles the text matches. Blank checks them all.',
  notify: 'Send a Discord or Apprise summary after every run: what changed, what was held back, how far the cycle got. A run that needs a look is sent as an error, so your mention settings for errors decide whether it pings you. Turn the Tools feature on under Settings, Notifications.',
  notify_all: 'Send every rename. The summary goes first, then the full list, as several messages when it is long. Off, one message lists what fits and counts the rest.',
}
const SCOPE_LABELS: Record<ArrRenameScope, string> = { files: 'Files', folders: 'Folders', both: 'Files and folders', tba: 'TBA titles only' }
// Movies have no episode titles, so the TBA choice is Sonarr's.
const scopesFor = (group: string) => (Object.keys(SCOPE_LABELS) as ArrRenameScope[]).filter((key) => key !== 'tba' || group === 'sonarr')
const SCOPE_HELP_TBA = ' TBA titles only: just the files whose episode title was TBA and now has its real title, when that is the only change. Folders and other files are left alone. Unclear ones are reported and not renamed.'
const UPGRADE_HELP = 'The app searches with its own quality profiles and decides what is an upgrade. Posterflow only paces the searches: a few items per run, one search at a time, and none while the app is already busy searching. Each searched item gets the search tag so the next run moves on. Once every item carries it, the tags are cleared and the cycle starts over. The log lists what was grabbed against the files it replaces. Later runs say whether each grab imported, failed or got stuck in the queue.'
const UPGRADE_FIELD_HELP: Record<keyof ArrUpgradeSettings, string> = {
  mode: 'What a run picks from. Everything: every monitored item that is out. Cutoff unmet: only the items on the Wanted, Cutoff Unmet list. Missing: only the items on the Wanted, Missing list.',
  count: 'How many items each run searches, 1 to 500. Keep it small to go easy on your indexers.',
  max_grabs: 'Stop a run once this many items got a new release. The rest waits for the next runs. 0 sets no limit.',
  monitored: 'Which movies a run picks from. Radarr searches a movie that is not monitored when asked to, so those can be included, or searched on their own.',
  only_tags: 'Only items carrying one of these tags are searched. Manage picks them from the tags the app has. Several are separated by commas. Blank searches any.',
  quality_profiles: 'Only items on one of these quality profiles are searched. Manage picks them from the profiles the app has. Several are separated by commas. Blank searches any.',
  min_age_days: 'Only items released at least this many days ago are searched. A movie counts from its earliest release, a series from its first episode. 0 searches any age.',
  search_unit: 'How a series is searched when Search is Everything. Whole series: one search for the series. Monitored seasons: one search per monitored season that passes the threshold. Cutoff unmet and Missing search the listed episodes instead: a season search when a season has several, an episode search for the rest.',
  season_threshold: 'A season is searched when at least this share of its episodes is monitored. Specials and seasons with nothing aired are left out. A series with no season to search is tagged without a search.',
  tag_name: 'Tag put on searched items. They are skipped until every item carries it, then the tags are cleared. Letters, digits and - only. Manage shows which items carry it, and can add or take it off.',
  ignore_tag: 'Items with this tag are never searched. Letters, digits and - only. Manage picks the items that carry it.',
  skip_queued: 'An item with a download in the queue waits for a later run and stays untagged. A download older than 3 days no longer holds its item back.',
  title_filter: 'Only items whose title contains this text are searched. Handy to try one item first. Find shows which titles the text matches. Blank searches them all.',
  notify: 'Send a Discord or Apprise summary after every run: what was searched, what was grabbed against the files it replaces, what became of earlier grabs, how far the cycle got. A run that needs a look is sent as an error, so your mention settings for errors decide whether it pings you. Turn the Tools feature on under Settings, Notifications.',
  notify_grabs_only: 'Send only when something was grabbed or imported, or a run needs a look. Quiet runs send nothing.',
}
const MODE_LABELS: Record<ArrUpgradeMode, string> = { all: 'Everything', cutoff: 'Cutoff unmet', missing: 'Missing' }
const UNIT_LABELS: Record<ArrUpgradeUnit, string> = { seasons: 'Monitored seasons', series: 'Whole series' }
const MONITORED_LABELS: Record<ArrUpgradeMonitored, string> = { yes: 'Only monitored', any: 'Monitored or not', no: 'Only unmonitored' }
const TAG_RULE = /^[A-Za-z0-9-]+$/ // the only tags an arr makes
const TAG_NAMES: Record<RenameTagKind, string> = { cycle: 'cycle tag', ignore: 'ignore tag', searched: 'search tag', 'search-ignore': 'ignore tag' }
// Why a tag's Manage button cannot open its list yet, or '' when it can.
const tagBlock = (tag: string, kind: RenameTagKind): string => {
  const name = TAG_NAMES[kind]
  if (!tag.trim()) return `Set ${name.startsWith('i') ? 'an' : 'a'} ${name} first`
  return TAG_RULE.test(tag.trim()) ? '' : `The ${name} can only have letters, digits and -`
}

// The profile each tool (each instance of an arr tool) shows, per viewer: the default one when none was picked.
const PROFILE_KEY = 'posterflow.tools.profile'
// What a profile list hangs off in this page's state: the tool, or one instance of it.
const slotKey = (tool: ToolInfo, instance?: string) => (instance ? `${tool.id}|${instance}` : tool.id)

// One icon per tool: on its title at 16px and on every button that runs it at 14px.
const TOOL_ICONS: Record<string, LucideIcon> = {
  plex_cleanup: Eraser,
  plex_reset_posters: RotateCcw,
  jellyfin_clean_image_cache: Sparkles,
  jellyfin_reset_posters: RotateCcw,
  sonarr_rename: FilePen,
  radarr_rename: FilePen,
  sonarr_upgrade: CircleArrowUp,
  radarr_upgrade: CircleArrowUp,
}
const toolIcon = (tool: ToolInfo): LucideIcon => TOOL_ICONS[tool.id] ?? Hammer
// A tool that asks first because it changes or removes things gets red buttons; one that only asks keeps the blue ones.
const isDestructive = (tool: ToolInfo) => tool.destructive ?? Boolean(tool.confirm)

function Tools() {
  const { showToast } = useToast()
  const navigate = useNavigate()
  const openSettingsTab = (tab: 'scheduling' | 'notifications') => {
    try { localStorage.setItem('posterflow.settings.activeTab', tab) } catch { /* fine */ }
    navigate('/settings')
  }
  const [groups, setGroups] = useState<ToolGroup[]>([])
  const [activeTab, setActiveTab] = useState<ToolsTab>(initialTab)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [runningJobs, setRunningJobs] = useState<Record<string, number>>({}) // tool id → job id
  const [confirming, setConfirming] = useState<{ tool: ToolInfo; dryRun: boolean; instance?: string } | null>(null)
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})
  // Profiles: the one each tool (or instance) shows, its unsaved edits, and the dialogs that make, rename or delete one.
  const [profileChoice, setProfileChoice] = useState<Record<string, number>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(PROFILE_KEY) || '{}')
      return saved && typeof saved === 'object' ? saved : {}
    } catch { return {} }
  })
  const [drafts, setDrafts] = useState<Record<string, ToolSettings>>({}) // slot → edited settings of its shown profile
  const [savingProfile, setSavingProfile] = useState('')
  const [naming, setNaming] = useState<{ tool: ToolInfo; profile: ToolProfile | null; instance?: string } | null>(null) // null profile = a new one
  const [namingBusy, setNamingBusy] = useState(false)
  const [deleting, setDeleting] = useState<{ tool: ToolInfo; profile: ToolProfile } | null>(null)
  const [pendingSwitch, setPendingSwitch] = useState<{ tool: ToolInfo; id: number; instance?: string } | null>(null)
  const [resetting, setResetting] = useState<{ tool: ToolInfo; instance: string } | null>(null)
  const [tagging, setTagging] = useState<{ tool: ToolInfo; group: string; instance: string; tag: string; kind: RenameTagKind } | null>(null) // the tag list being edited
  const [picking, setPicking] = useState<{ tool: ToolInfo; group: string; instance: string; kind: ArrPickKind } | null>(null) // the filter being picked from the arr's own list
  const [filtering, setFiltering] = useState<{ tool: ToolInfo; group: string; instance: string } | null>(null) // the title filter being tried against the arr's titles
  const [plexInstances, setPlexInstances] = useState<string[]>([])
  const [paths, setPaths] = useState<Record<string, string>>({})
  const [savedPaths, setSavedPaths] = useState<Record<string, string>>({})
  const [pathChecks, setPathChecks] = useState<Record<string, { ok: boolean; reason: string }>>({})
  const [savingPaths, setSavingPaths] = useState(false)

  const load = useCallback(async () => {
    try {
      const resp = await getTools()
      setGroups(resp.groups)
      setLoadError('')
    } catch (err) {
      setLoadError(getApiErrorMessage(err, 'Failed to load the tools'))
    } finally {
      setLoading(false)
    }
  }, [])
  const loadPaths = useCallback(async () => {
    try {
      const resp = await getPlexMetadataPaths()
      setPlexInstances(resp.instances)
      setPaths(resp.paths)
      setSavedPaths(resp.paths)
    } catch { /* no Plex servers to set up */ }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => { void loadPaths() }, [loadPaths])
  const mounted = useRef(true)
  const watched = useRef(new Set<number>())  // job ids this page is polling
  useEffect(() => () => { mounted.current = false; Object.values(timers.current).forEach(clearTimeout) }, [])

  const pathsDirty = JSON.stringify(paths) !== JSON.stringify(savedPaths)
  const setPath = (instance: string, value: string) =>
    setPaths((prev) => {
      const next = { ...prev }
      if (value.trim()) next[instance] = value
      else delete next[instance]
      return next
    })
  const checkPath = async (instance: string) => {
    try {
      const result = await checkPlexMetadataPath(instance, paths[instance] ?? '')
      setPathChecks((prev) => ({ ...prev, [instance]: { ok: result.ok, reason: result.reason } }))
    } catch (err) {
      setPathChecks((prev) => ({ ...prev, [instance]: { ok: false, reason: getApiErrorMessage(err, 'Check failed') } }))
    }
  }
  const savePaths = async () => {
    setSavingPaths(true)
    try {
      const result = await savePlexMetadataPaths(paths)
      setPaths(result.paths)
      setSavedPaths(result.paths)
      showToast('Plex Metadata folders saved', 'success')
      void load()
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to save the Plex Metadata folders'), 'error')
    } finally {
      setSavingPaths(false)
    }
  }

  // ── profiles ──────────────────────────────────────────────────────────────
  // An arr renamer keeps its profiles per instance: every function takes the instance whose list it means.
  const selectedProfile = (tool: ToolInfo, instance?: string): ToolProfile | null => {
    const list = profilesOf(tool, instance)
    return list.find((p) => p.id === profileChoice[slotKey(tool, instance)]) ?? list.find((p) => p.is_default) ?? list[0] ?? null
  }
  const settingsOf = (tool: ToolInfo, instance?: string): ToolSettings => drafts[slotKey(tool, instance)] ?? selectedProfile(tool, instance)?.settings ?? {}
  const isDirty = (tool: ToolInfo, instance?: string) => {
    const key = slotKey(tool, instance)
    return key in drafts && JSON.stringify(drafts[key]) !== JSON.stringify(selectedProfile(tool, instance)?.settings ?? {})
  }
  const ownsProfiles = (tool: ToolInfo) => tool.per_instance && tool.profiles.length > 0  // each instance has its own list
  const anyDirty = (tool: ToolInfo) => (ownsProfiles(tool) ? tool.instances.some((name) => isDirty(tool, name)) : isDirty(tool))
  const edit = (tool: ToolInfo, change: (settings: ToolSettings) => ToolSettings, instance?: string) =>
    setDrafts((prev) => ({ ...prev, [slotKey(tool, instance)]: change(prev[slotKey(tool, instance)] ?? selectedProfile(tool, instance)?.settings ?? {}) }))
  const discard = (tool: ToolInfo, instance?: string) =>
    setDrafts((prev) => {
      const next = { ...prev }
      delete next[slotKey(tool, instance)]
      return next
    })
  const rememberProfile = (slot: string, id: number | null) =>
    setProfileChoice((prev) => {
      const next = { ...prev }
      if (id === null) delete next[slot]
      else next[slot] = id
      try { localStorage.setItem(PROFILE_KEY, JSON.stringify(next)) } catch { /* per-viewer convenience only */ }
      return next
    })
  const switchProfile = (tool: ToolInfo, id: number, instance?: string) => {
    rememberProfile(slotKey(tool, instance), id)
    discard(tool, instance)
  }
  const requestSwitch = (tool: ToolInfo, id: number, instance?: string) => {
    if (id === selectedProfile(tool, instance)?.id) return
    if (isDirty(tool, instance)) setPendingSwitch({ tool, id, instance })
    else switchProfile(tool, id, instance)
  }
  // A saved or new profile lands in the catalog without a reload.
  const placeProfile = (toolId: string, profile: ToolProfile) =>
    setGroups((prev) => prev.map((g) => ({
      ...g,
      tools: g.tools.map((t) => (t.id !== toolId ? t : {
        ...t,
        profiles: t.profiles.some((p) => p.id === profile.id) ? t.profiles.map((p) => (p.id === profile.id ? profile : p)) : [...t.profiles, profile],
      })),
    })))
  const saveProfile = async (tool: ToolInfo, instance?: string) => {
    const profile = selectedProfile(tool, instance)
    const draft = drafts[slotKey(tool, instance)]
    if (!profile || !draft) return
    setSavingProfile(slotKey(tool, instance))
    try {
      const saved = await updateToolProfile(tool.id, profile.id, { settings: draft })
      placeProfile(tool.id, saved)
      discard(tool, instance)
      showToast(`Profile ${saved.name}${instance ? ` of ${instance}` : ''} saved`, 'success')
    } catch (err) {
      showToast(getApiErrorMessage(err, `Failed to save the ${instance ?? tool.label} profile`), 'error')
    } finally {
      setSavingProfile('')
    }
  }
  // New: a profile from the settings shown (unsaved edits included, which then belong to it). Rename: the name only.
  const submitName = async (name: string) => {
    if (!naming) return
    const { tool, profile, instance } = naming
    setNamingBusy(true)
    try {
      if (profile) {
        const saved = await updateToolProfile(tool.id, profile.id, { name })
        placeProfile(tool.id, saved)
        showToast(`Profile renamed to ${saved.name}`, 'success')
      } else {
        const created = await createToolProfile(tool.id, name, settingsOf(tool, instance), instance)
        placeProfile(tool.id, created)
        rememberProfile(slotKey(tool, instance), created.id)
        discard(tool, instance)
        showToast(`Profile ${created.name} created`, 'success')
      }
      setNaming(null)
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to save the profile'), 'error')
    } finally {
      setNamingBusy(false)
    }
  }
  const removeProfile = async () => {
    if (!deleting) return
    const { tool, profile } = deleting
    const instance = profile.instance ?? undefined
    setDeleting(null)
    try {
      await deleteToolProfile(tool.id, profile.id)
      discard(tool, instance)
      rememberProfile(slotKey(tool, instance), null)
      await load()
      showToast(`Profile ${profile.name} deleted`, 'success')
    } catch (err) {
      showToast(getApiErrorMessage(err, 'Failed to delete the profile'), 'error')
    }
  }
  // Image tools: the kinds switched on in the shown profile. The last one on stays on.
  const kindsOf = (tool: ToolInfo) => tool.kinds.filter((k) => (settingsOf(tool).kinds ?? tool.kinds).includes(k))
  const toggleKind = (tool: ToolInfo, kind: string, on: boolean) =>
    edit(tool, (s) => ({ ...s, kinds: tool.kinds.filter((k) => (k === kind ? on : (s.kinds ?? tool.kinds).includes(k))) }))
  // Arr renamers: the settings of the profile an instance shows.
  const renameSettingsOf = (tool: ToolInfo, instance: string) => ({ ...(tool.defaults ?? {}), ...settingsOf(tool, instance) }) as ArrRenameSettings
  const setRenameField = <K extends keyof ArrRenameSettings>(tool: ToolInfo, instance: string, field: K, value: ArrRenameSettings[K]) =>
    edit(tool, (s) => ({ ...s, [field]: value }), instance)
  // Arr upgraders: the same, with their own settings.
  const upgradeSettingsOf = (tool: ToolInfo, instance: string) => ({ ...(tool.defaults ?? {}), ...settingsOf(tool, instance) }) as ArrUpgradeSettings
  const setUpgradeField = <K extends keyof ArrUpgradeSettings>(tool: ToolInfo, instance: string, field: K, value: ArrUpgradeSettings[K]) =>
    edit(tool, (s) => ({ ...s, [field]: value }), instance)
  // The profile an instance shows goes back to the defaults; a draft until saved.
  const resetToDefaults = () => {
    if (resetting?.tool.defaults) edit(resetting.tool, (s) => ({ ...s, ...resetting.tool.defaults }), resetting.instance)
    setResetting(null)
  }

  // Either arr tool's title filter opens the instance's titles, to try the text against them. Reading only.
  const findTitles = (tool: ToolInfo, group: string, instance: string, label: string) => (
    <button type="button" className="btn-secondary btn-xs" aria-label={label} title={`See which titles in ${instance} the filter matches, and pick one`} onClick={() => setFiltering({ tool, group, instance })}>
      <Search size={12} /> Find
    </button>
  )

  const selectTab = (key: ToolsTab) => {
    setActiveTab(key)
    try { localStorage.setItem(TAB_STORAGE_KEY, key) } catch { /* per-viewer convenience only */ }
  }
  const tab = TABS.find((t) => t.key === activeTab) ?? TABS[0]
  const visibleGroups = groups
    .filter((g) => tab.groups.includes(g.id) && g.instances.length > 0)
    .map((g) => ({ ...g, tools: g.tools.filter((t) => t.id.endsWith(tab.suffix)) }))

  const kindNames = (kinds: string[]) => kinds.map((k) => KIND_LABELS[k] ?? k).join(', ')

  // The sidebar shows the job's progress; here we only wait for the outcome.
  const watchJob = useCallback((toolId: string, jobId: number, label: string) => {
    if (watched.current.has(jobId)) return
    watched.current.add(jobId)
    setRunningJobs((prev) => ({ ...prev, [toolId]: jobId }))
    const poll = async () => {
      try {
        const job = await getJob(jobId)
        if (!mounted.current) return  // the page is gone: nothing to update, no more polling
        if (['completed', 'failed', 'cancelled'].includes(job.status)) {
          watched.current.delete(jobId)
          setRunningJobs((prev) => {
            const next = { ...prev }
            delete next[toolId]
            return next
          })
          showToast(job.status === 'completed' ? `${label} finished: ${job.message ?? 'done'}` : `${label} ${job.status}${job.error ? `: ${job.error}` : ''}`, job.status === 'completed' ? 'success' : 'error')
          void load()
          return
        }
      } catch { /* try again */ }
      if (mounted.current) timers.current[toolId] = setTimeout(poll, 3000)
    }
    void poll()
  }, [load, showToast])
  // A tool still busy from before the page was opened (or reloaded) is watched like one started here.
  useEffect(() => {
    for (const tool of groups.flatMap((g) => g.tools)) {
      if (tool.active_job != null) watchJob(tool.id, tool.active_job, tool.label)
    }
  }, [groups, watchJob])

  // What a run of the tool would use: the profile shown for the instance named, for every instance
  // of an arr renamer, or for the tool itself. Named in messages where there is a choice.
  const runProfiles = (tool: ToolInfo, instance?: string): Array<{ instance?: string; profile: ToolProfile }> => {
    const slots = ownsProfiles(tool) ? (instance ? [instance] : tool.instances) : [undefined]
    return slots.flatMap((name) => {
      const profile = selectedProfile(tool, name)
      return profile ? [{ instance: name, profile }] : []
    })
  }
  const runProfileText = (tool: ToolInfo, instance?: string) => {
    const used = runProfiles(tool, instance)
    if (used.length > 1) return used.map((u) => `${u.instance}: ${u.profile.name}`).join(', ')
    return used.length === 1 && profilesOf(tool, used[0].instance).length > 1 ? used[0].profile.name : ''
  }
  // The confirm dialog names the profile a run uses: with its image kinds, or per instance.
  const confirmProfileText = (tool: ToolInfo, instance?: string) => {
    const used = runProfiles(tool, instance)
    if (used.length === 0) return ''
    if (used.length > 1) return ` Profiles: ${used.map((u) => `${u.instance}: ${u.profile.name}`).join(', ')}.`
    return ` Profile: ${used[0].profile.name}${tool.kinds.length > 0 ? ` (${kindNames(kindsOf(tool))})` : ''}.`
  }
  // A run gets the shown profile's saved settings; every instance of an arr renamer gets its own.
  const start = async (tool: ToolInfo, dryRun: boolean, instance?: string) => {
    setConfirming(null)
    const used = runProfiles(tool, instance)
    const named = runProfileText(tool, instance)
    const label = `${tool.label}${named ? ` (${named})` : ''}${instance ? ` on ${instance}` : ''}${dryRun ? ' (dry run)' : ''}`
    const which = ownsProfiles(tool) && !instance
      ? { profiles: Object.fromEntries(used.map((u) => [u.instance as string, u.profile.id])) }
      : { ...(used[0] ? { profile_id: used[0].profile.id } : {}), ...(instance ? { instance } : {}) }
    try {
      const result = await runTool(tool.id, { dry_run: dryRun, confirm: true, ...which })
      showToast(`${label} started (Job ID: ${result.job_id})`, 'success')
      watchJob(tool.id, result.job_id, label)
    } catch (err) {
      showToast(getApiErrorMessage(err, `Failed to start ${tool.label}`), 'error')
    }
  }
  // A dry run changes nothing, so only the real run of a destructive tool asks first.
  const request = (tool: ToolInfo, dryRun: boolean, instance?: string) => {
    if (tool.confirm && !dryRun) setConfirming({ tool, dryRun, instance })
    else void start(tool, dryRun, instance)
  }
  // A run waits while the tool is busy or a profile it would use has unsaved edits.
  const unsaved = (tool: ToolInfo, instance?: string) => (instance ? isDirty(tool, instance) : anyDirty(tool))
  const blockedFor = (tool: ToolInfo, instance?: string) => !tool.available || tool.id in runningJobs || unsaved(tool, instance)
  const whyBlocked = (tool: ToolInfo, instance?: string) => (unsaved(tool, instance) ? 'Save the profile first' : undefined)
  // Tools with image kinds are stacked blocks of switches; the rest are plain rows.
  const kindTools = (group: ToolGroup) => group.tools.filter((t) => t.kinds.length > 0)
  const plainTools = (group: ToolGroup) => group.tools.filter((t) => t.kinds.length === 0)
  const mainButtons = (tool: ToolInfo, busy: boolean) => {
    const Icon = toolIcon(tool)
    const dirty = anyDirty(tool)
    const blocked = !tool.available || busy || dirty
    const why = dirty ? 'Save the profile first' : undefined
    const all = tool.per_instance && (tool.instances ?? []).length > 1  // the instances have their own buttons; these run every one, each with the profile it shows
    return (
    <>
      <button
        type="button"
        className={`btn-toolbar ${isDestructive(tool) ? 'btn-danger' : 'btn-primary'}`}
        onClick={() => request(tool, false)}
        disabled={blocked}
        title={why}
      >
        <Icon size={14} /> {busy ? 'Running…' : all ? `${tool.run_label} all` : tool.run_label}
      </button>
      {tool.dry_run && (
        <button
          type="button"
          className="btn-toolbar"
          onClick={() => request(tool, true)}
          disabled={blocked}
          title={why ?? 'Report what would change without changing anything'}
        >
          <FlaskConical size={14} /> {all ? 'Dry run all' : 'Dry run'}
        </button>
      )}
    </>
    )
  }
  // Pick, make, rename or delete a profile: of a tool with settings, or of one instance of an arr renamer.
  const profileBar = (tool: ToolInfo, instance?: string) => {
    const profile = selectedProfile(tool, instance)
    if (!profile) return null
    const list = profilesOf(tool, instance)
    const busy = tool.id in runningJobs
    const only = list.length <= 1
    const owner = instance ?? tool.label
    return (
      <div className="tools-profile-bar">
        <label className="tools-profile-field">
          <span>Profile <InfoTip text={instance ? INSTANCE_PROFILE_HELP : PROFILE_HELP} /></span>
          <select value={profile.id} aria-label={`${owner} profile`} disabled={busy} onChange={(e) => requestSwitch(tool, Number(e.target.value), instance)}>
            {list.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_default ? ' (default)' : ''}</option>)}
          </select>
        </label>
        <span className="tools-profile-actions">
          <button type="button" className="btn-icon btn-icon-filled" disabled={busy} title="New profile from the settings shown" aria-label={`New ${owner} profile`} onClick={() => setNaming({ tool, profile: null, instance })}>
            <Plus size={14} />
          </button>
          <button type="button" className="btn-icon btn-icon-filled" disabled={busy} title="Rename this profile" aria-label={`Rename ${owner} profile`} onClick={() => setNaming({ tool, profile, instance })}>
            <Pencil size={14} />
          </button>
          <button type="button" className="btn-icon btn-icon-filled" disabled={busy || only} title={only ? 'The only profile stays' : 'Delete this profile'} aria-label={`Delete ${owner} profile`} onClick={() => setDeleting({ tool, profile })}>
            <Trash2 size={14} />
          </button>
        </span>
      </div>
    )
  }
  const saveActions = (tool: ToolInfo, instance?: string) => (
    <SaveActions dirty={isDirty(tool, instance)} saving={savingProfile === slotKey(tool, instance)} onSave={() => { void saveProfile(tool, instance) }} onCancel={() => discard(tool, instance)} />
  )
  // An arr instance's reset and save pair, for either arr tool: the row above its profile bar. `trailing` closes it
  // (the instance's own Run and Dry run, when there are several instances).
  const instanceActions = (tool: ToolInfo, instance: string, resetLabel: string, trailing?: ReactNode) => (
    <div className="tools-section-actions">
      <button type="button" className="btn-toolbar btn-toolbar-link" aria-label={resetLabel} onClick={() => setResetting({ tool, instance })}>
        <RotateCcw size={14} /> Reset to Defaults
      </button>
      {saveActions(tool, instance)}
      {trailing}
    </div>
  )

  return (
    <div className="page-container tools-page">
      <div className="page-header tools-header">
        <h1>Tools</h1>
        <p>One-off maintenance jobs for your media servers and arrs, one card per app.</p>
      </div>

      <Toolbar
        title="Tools"
        description={
          <>
            Media Servers holds the Plex and Jellyfin tools, Arr Renamer the Sonarr and Radarr rename tools, Arr Upgrader
            their upgrade searches. Each runs in the background with its progress shown in the sidebar. Logs land under Tools on the <strong>Logs</strong> page.
            A tool's settings live in named profiles. Use <strong>Scheduling</strong> to run a profile on a schedule and{' '}
            <strong>Discord</strong> for run notifications.
          </>
        }
      >
        <div className="btn-pair">
          <button className="btn-toolbar btn-toolbar-link" onClick={() => openSettingsTab('scheduling')} title="Open scheduling settings">
            Scheduling
          </button>
          <button className="btn-toolbar btn-toolbar-link" onClick={() => openSettingsTab('notifications')} title="Open notification settings">
            Discord
          </button>
        </div>
      </Toolbar>

      <div className="tools-tabs pf-subtabs" role="tablist" aria-label="Tool groups">
        {TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab === t.key}
            className={activeTab === t.key ? 'active' : ''}
            onClick={() => selectTab(t.key)}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {loadError && <div className="tools-error">{loadError}</div>}
      {loading && groups.length === 0 && <div className="tools-loading">Loading tools…</div>}
      {!loading && groups.length > 0 && visibleGroups.length === 0 && (
        <div className="tools-loading">{tab.empty}</div>
      )}

      <div className="tools-grid" role="tabpanel" aria-label={tab.label}>
        {visibleGroups.map((group) => {
          const arrTool = ARR_GROUPS.includes(group.id) ? group.tools.find((t) => t.per_instance && t.defaults) : undefined
          const renameTool = tab.key === 'arr' ? arrTool : undefined
          const upgradeTool = tab.key === 'upgrade' ? arrTool : undefined
          const level = group.id !== 'sonarr' && visibleGroups.some((g) => g.id === 'sonarr') // rows that keep this card as tall as the Sonarr one
          return (
          <section key={group.id} className="tools-card" aria-label={`${group.label} tools`}>
            <div className="tools-card-head">
              <span className="tools-card-icon"><Server size={16} /></span>
              <h3>{group.label}</h3>
              <span className="tools-card-servers">{group.instances.join(', ')}</span>
              {group.id === 'jellyfin' && <InfoTip text={JELLYFIN_HELP} />}
            </div>

            {renameTool && (
              <section className="tools-section" aria-label={`${group.label} rename settings`}>
                <div className="tools-section-head">
                  <h4>Rename settings</h4>
                  <InfoTip text={RENAME_HELP} />
                  {renameTool.instances.length === 1 && instanceActions(renameTool, renameTool.instances[0], 'Reset to Defaults')}
                </div>
                {renameTool.instances.length === 1 && profileBar(renameTool, renameTool.instances[0])}
                {/* One block per instance, each with profiles of its own: a 4K Radarr can run its own count and tags.
                    Several instances each get their own Save row and profile row; one alone uses the section's. */}
                {renameTool.instances.map((instance) => {
                  const s = renameSettingsOf(renameTool, instance)
                  const many = renameTool.instances.length > 1
                  const set = <K extends keyof ArrRenameSettings>(field: K, value: ArrRenameSettings[K]) => setRenameField(renameTool, instance, field, value)
                  const named = (label: string) => (many ? `${label} (${instance})` : label)
                  const scanning = s.scan_to_limit && s.max_renames > 0 // the count and batches do not apply then
                  // Each tag field opens the list of items carrying that tag. A run adds and takes off tags itself, so not while one runs.
                  const manage = (kind: RenameTagKind) => {
                    const tag = kind === 'cycle' ? s.tag_name : s.ignore_tag
                    const nouns = group.id === 'radarr' ? 'movies' : 'series'
                    const why = renameTool.id in runningJobs ? 'Wait for the run to finish' : tagBlock(tag, kind)
                    return (
                      <button
                        type="button"
                        className="btn-secondary btn-xs"
                        aria-label={named(kind === 'cycle' ? 'Manage checked items' : 'Manage ignored items')}
                        disabled={Boolean(why)}
                        title={why || (kind === 'cycle' ? `See which ${nouns} in ${instance} the renamer checked this pass, and change them` : `Pick the ${nouns} in ${instance} that the renamer leaves alone`)}
                        onClick={() => setTagging({ tool: renameTool, group: group.id, instance, tag: tag.trim(), kind })}
                      >
                        <ListChecks size={12} /> Manage
                      </button>
                    )
                  }
                  return (
                    <div key={instance} className="tools-instance-settings" role="group" aria-label={`${instance} rename settings`}>
                      {many && (
                        <div className="tools-instance-head">
                          <h5 className="tools-instance-name">{instance}</h5>
                          {instanceActions(renameTool, instance, named('Reset to Defaults'), (
                            <div className="btn-pair">
                              <button type="button" className="btn-toolbar btn-danger" aria-label={`Run ${instance}`} onClick={() => request(renameTool, false, instance)} disabled={blockedFor(renameTool, instance)} title={whyBlocked(renameTool, instance) ?? `Rename on ${instance} only`}>
                                <Hammer size={13} /> Run
                              </button>
                              <button type="button" className="btn-toolbar" aria-label={`Dry run ${instance}`} onClick={() => request(renameTool, true, instance)} disabled={blockedFor(renameTool, instance)} title={whyBlocked(renameTool, instance) ?? `Report what would change on ${instance} without changing anything`}>
                                <FlaskConical size={13} /> Dry run
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      {many && profileBar(renameTool, instance)}
                      <div className="tools-fields">
                        <div className="tools-fields-col tools-fields-inputs">
                          <label className="tools-field">
                            <span>Rename <InfoTip text={RENAME_FIELD_HELP.scope + (group.id === 'sonarr' ? SCOPE_HELP_TBA : '')} /></span>
                            <select value={s.scope} aria-label={named('Rename')} onChange={(e) => set('scope', e.target.value as ArrRenameScope)}>
                              {scopesFor(group.id).map((key) => <option key={key} value={key}>{SCOPE_LABELS[key]}</option>)}
                            </select>
                          </label>
                          <label className="tools-field">
                            <span>Items per run <InfoTip text={RENAME_FIELD_HELP.count} /></span>
                            <input type="number" min={0} max={1000} value={s.count} aria-label={named('Items per run')} disabled={scanning}
                              title={scanning ? 'Scan until the rename limit is on: each run checks as many items as it takes' : undefined}
                              onChange={(e) => set('count', Math.max(0, Math.min(1000, Number(e.target.value) || 0)))} />
                          </label>
                          <label className="tools-field">
                            <span>Rename limit <InfoTip text={RENAME_FIELD_HELP.max_renames} /></span>
                            <input type="number" min={0} max={1000} value={s.max_renames} aria-label={named('Rename limit')}
                              onChange={(e) => set('max_renames', Math.max(0, Math.min(1000, Number(e.target.value) || 0)))} />
                          </label>
                          <div className="tools-field">
                            <span>Cycle tag <InfoTip text={RENAME_FIELD_HELP.tag_name} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.tag_name} aria-label={named('Cycle tag')} onChange={(e) => set('tag_name', e.target.value)} />
                              {manage('cycle')}
                            </span>
                          </div>
                          <div className="tools-field">
                            <span>Ignore tag <InfoTip text={RENAME_FIELD_HELP.ignore_tag} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.ignore_tag} aria-label={named('Ignore tag')} onChange={(e) => set('ignore_tag', e.target.value)} />
                              {manage('ignore')}
                            </span>
                          </div>
                          <div className="tools-field">
                            <span>Title filter <InfoTip text={RENAME_FIELD_HELP.title_filter} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.title_filter} aria-label={named('Title filter')} placeholder="all titles" onChange={(e) => set('title_filter', e.target.value)} />
                              {findTitles(renameTool, group.id, instance, named('Find titles'))}
                            </span>
                          </div>
                        </div>
                        <div className="tools-fields-col tools-fields-checks">
                          <div className="tools-field tools-check-row">
                            <label className="tools-check" title={scanning ? 'Scan until the rename limit is on: each run checks as many items as it takes' : undefined}>
                              <input type="checkbox" checked={s.batch} disabled={scanning} aria-label={named('Whole library in batches')} onChange={(e) => set('batch', e.target.checked)} />
                              <span>Whole library in batches</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.batch} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check" title={s.max_renames > 0 ? undefined : 'Set a rename limit first'}>
                              <input type="checkbox" checked={scanning} disabled={s.max_renames === 0} aria-label={named('Scan until the rename limit')} onChange={(e) => set('scan_to_limit', e.target.checked)} />
                              <span>Scan until the rename limit</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.scan_to_limit} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.refresh_first} aria-label={named('Refresh metadata first')} onChange={(e) => set('refresh_first', e.target.checked)} />
                              <span>Refresh metadata first</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.refresh_first} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.ignore_case} aria-label={named('Ignore capitals')} onChange={(e) => set('ignore_case', e.target.checked)} />
                              <span>Ignore capitals</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.ignore_case} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.ignore_punctuation} aria-label={named('Ignore punctuation')} onChange={(e) => set('ignore_punctuation', e.target.checked)} />
                              <span>Ignore punctuation</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.ignore_punctuation} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.allow_irregular} aria-label={named('Rename irregular folders')} onChange={(e) => set('allow_irregular', e.target.checked)} />
                              <span>Rename irregular folders</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.allow_irregular} />
                          </div>
                          {group.id === 'sonarr' && (
                            <div className="tools-field tools-check-row">
                              <label className="tools-check">
                                <input type="checkbox" checked={s.hold_tba} aria-label={named('Hold back TBA titles')} onChange={(e) => set('hold_tba', e.target.checked)} />
                                <span>Hold back TBA titles</span>
                              </label>
                              <InfoTip text={RENAME_FIELD_HELP.hold_tba} />
                            </div>
                          )}
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.notify} aria-label={named('Notify after runs')} onChange={(e) => set('notify', e.target.checked)} />
                              <span>Notify after runs</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.notify} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.notify_all} disabled={!s.notify} aria-label={named('Send every rename')} onChange={(e) => set('notify_all', e.target.checked)} />
                              <span>Send every rename</span>
                            </label>
                            <InfoTip text={RENAME_FIELD_HELP.notify_all} />
                          </div>
                          {/* Radarr has no TBA setting: an empty row keeps its blocks as tall as Sonarr's, so the cards line up. */}
                          {group.id !== 'sonarr' && (
                            <div className="tools-field tools-check-row tools-check-spacer" aria-hidden="true">
                              <span className="tools-check"><input type="checkbox" disabled tabIndex={-1} /><span>&nbsp;</span></span>
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </section>
            )}

            {upgradeTool && (
              <section className="tools-section" aria-label={`${group.label} search settings`}>
                <div className="tools-section-head">
                  <h4>Search settings</h4>
                  <InfoTip text={UPGRADE_HELP} />
                  {upgradeTool.instances.length === 1 && instanceActions(upgradeTool, upgradeTool.instances[0], 'Reset to Defaults')}
                </div>
                {upgradeTool.instances.length === 1 && profileBar(upgradeTool, upgradeTool.instances[0])}
                {upgradeTool.instances.map((instance) => {
                  const s = upgradeSettingsOf(upgradeTool, instance)
                  const many = upgradeTool.instances.length > 1
                  const set = <K extends keyof ArrUpgradeSettings>(field: K, value: ArrUpgradeSettings[K]) => setUpgradeField(upgradeTool, instance, field, value)
                  const named = (label: string) => (many ? `${label} (${instance})` : label)
                  const sonarr = group.id === 'sonarr'
                  const nouns = sonarr ? 'series' : 'movies'
                  const everything = s.mode === 'all' // the Wanted lists name the episodes to search themselves
                  const manage = (kind: 'searched' | 'search-ignore') => {
                    const tag = kind === 'searched' ? s.tag_name : s.ignore_tag
                    const why = upgradeTool.id in runningJobs ? 'Wait for the run to finish' : tagBlock(tag, kind)
                    return (
                      <button
                        type="button"
                        className="btn-secondary btn-xs"
                        aria-label={named(kind === 'searched' ? 'Manage searched items' : 'Manage ignored items')}
                        disabled={Boolean(why)}
                        title={why || (kind === 'searched' ? `See which ${nouns} in ${instance} were searched this cycle, and change them` : `Pick the ${nouns} in ${instance} that are never searched`)}
                        onClick={() => setTagging({ tool: upgradeTool, group: group.id, instance, tag: tag.trim(), kind })}
                      >
                        <ListChecks size={12} /> Manage
                      </button>
                    )
                  }
                  // The two list filters open what the arr has to pick from. Reading only, so also while a run is going.
                  const pick = (kind: ArrPickKind) => (
                    <button
                      type="button"
                      className="btn-secondary btn-xs"
                      aria-label={named(kind === 'tags' ? 'Manage only tags' : 'Manage quality profiles')}
                      title={`Pick from the ${kind === 'tags' ? 'tags' : 'quality profiles'} in ${instance}`}
                      onClick={() => setPicking({ tool: upgradeTool, group: group.id, instance, kind })}
                    >
                      <ListChecks size={12} /> Manage
                    </button>
                  )
                  return (
                    <div key={instance} className="tools-instance-settings" role="group" aria-label={`${instance} search settings`}>
                      {many && (
                        <div className="tools-instance-head">
                          <h5 className="tools-instance-name">{instance}</h5>
                          {instanceActions(upgradeTool, instance, named('Reset to Defaults'), (
                            <div className="btn-pair">
                              <button type="button" className="btn-toolbar btn-primary" aria-label={`Search ${instance}`} onClick={() => request(upgradeTool, false, instance)} disabled={blockedFor(upgradeTool, instance)} title={whyBlocked(upgradeTool, instance) ?? `Search for upgrades on ${instance} only`}>
                                <CircleArrowUp size={13} /> Search
                              </button>
                              <button type="button" className="btn-toolbar" aria-label={`Dry run ${instance}`} onClick={() => request(upgradeTool, true, instance)} disabled={blockedFor(upgradeTool, instance)} title={whyBlocked(upgradeTool, instance) ?? `List what a run would search on ${instance} without searching`}>
                                <FlaskConical size={13} /> Dry run
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      {many && profileBar(upgradeTool, instance)}
                      <div className="tools-fields tools-fields-even">
                        <div className="tools-fields-col tools-fields-inputs tools-fields-main">
                          <label className="tools-field">
                            <span>Search <InfoTip text={UPGRADE_FIELD_HELP.mode} /></span>
                            <select value={s.mode} aria-label={named('Search')} onChange={(e) => set('mode', e.target.value as ArrUpgradeMode)}>
                              {(Object.keys(MODE_LABELS) as ArrUpgradeMode[]).map((key) => <option key={key} value={key}>{MODE_LABELS[key]}</option>)}
                            </select>
                          </label>
                          <label className="tools-field">
                            <span>Items per run <InfoTip text={UPGRADE_FIELD_HELP.count} /></span>
                            <input type="number" min={1} max={500} value={s.count} aria-label={named('Items per run')}
                              onChange={(e) => set('count', Math.max(1, Math.min(500, Number(e.target.value) || 1)))} />
                          </label>
                          <label className="tools-field">
                            <span>Grab limit <InfoTip text={UPGRADE_FIELD_HELP.max_grabs} /></span>
                            <input type="number" min={0} max={500} value={s.max_grabs} aria-label={named('Grab limit')}
                              onChange={(e) => set('max_grabs', Math.max(0, Math.min(500, Number(e.target.value) || 0)))} />
                          </label>
                          {!sonarr && (
                            <label className="tools-field">
                              <span>Monitored <InfoTip text={UPGRADE_FIELD_HELP.monitored} /></span>
                              <select value={s.monitored} aria-label={named('Monitored')} onChange={(e) => set('monitored', e.target.value as ArrUpgradeMonitored)}>
                                {(Object.keys(MONITORED_LABELS) as ArrUpgradeMonitored[]).map((key) => <option key={key} value={key}>{MONITORED_LABELS[key]}</option>)}
                              </select>
                            </label>
                          )}
                          {sonarr && (
                            <label className="tools-field">
                              <span>Search by <InfoTip text={UPGRADE_FIELD_HELP.search_unit} /></span>
                              <select value={s.search_unit} aria-label={named('Search by')} disabled={!everything}
                                title={everything ? undefined : 'Cutoff unmet and Missing search the listed episodes'}
                                onChange={(e) => set('search_unit', e.target.value as ArrUpgradeUnit)}>
                                {(Object.keys(UNIT_LABELS) as ArrUpgradeUnit[]).map((key) => <option key={key} value={key}>{UNIT_LABELS[key]}</option>)}
                              </select>
                            </label>
                          )}
                          {sonarr && (
                            <label className="tools-field">
                              <span>Threshold % <InfoTip text={UPGRADE_FIELD_HELP.season_threshold} /></span>
                              <input type="number" min={0} max={100} value={s.season_threshold} aria-label={named('Season threshold')} disabled={!everything || s.search_unit !== 'seasons'}
                                title={everything && s.search_unit === 'seasons' ? undefined : 'Applies to Everything searched by Monitored seasons'}
                                onChange={(e) => set('season_threshold', Math.max(0, Math.min(100, Number(e.target.value) || 0)))} />
                            </label>
                          )}
                          {/* Radarr has one setting where Sonarr has two: an empty row keeps its blocks as tall, so the cards line up. */}
                          {level && (
                            <div className="tools-field tools-check-spacer" aria-hidden="true">
                              <span>&nbsp;</span>
                              <input type="text" disabled tabIndex={-1} />
                            </div>
                          )}
                          <div className="tools-field">
                            <span>Only tags <InfoTip text={UPGRADE_FIELD_HELP.only_tags} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.only_tags} aria-label={named('Only tags')} placeholder="any tag" onChange={(e) => set('only_tags', e.target.value)} />
                              {pick('tags')}
                            </span>
                          </div>
                          <div className="tools-field">
                            <span>Profiles <InfoTip text={UPGRADE_FIELD_HELP.quality_profiles} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.quality_profiles} aria-label={named('Quality profiles')} placeholder="any profile" onChange={(e) => set('quality_profiles', e.target.value)} />
                              {pick('profiles')}
                            </span>
                          </div>
                          <label className="tools-field">
                            <span>Minimum age <InfoTip text={UPGRADE_FIELD_HELP.min_age_days} /></span>
                            <input type="number" min={0} max={3650} value={s.min_age_days} aria-label={named('Minimum age in days')}
                              onChange={(e) => set('min_age_days', Math.max(0, Math.min(3650, Number(e.target.value) || 0)))} />
                          </label>
                        </div>
                        {/* The tags and the title filter: beside the search settings and above the switches in a wide card, under the search settings in a narrow one. */}
                        <div className="tools-fields-col tools-fields-inputs tools-fields-tags">
                          <div className="tools-field">
                            <span>Search tag <InfoTip text={UPGRADE_FIELD_HELP.tag_name} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.tag_name} aria-label={named('Search tag')} onChange={(e) => set('tag_name', e.target.value)} />
                              {manage('searched')}
                            </span>
                          </div>
                          <div className="tools-field">
                            <span>Ignore tag <InfoTip text={UPGRADE_FIELD_HELP.ignore_tag} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.ignore_tag} aria-label={named('Ignore tag')} onChange={(e) => set('ignore_tag', e.target.value)} />
                              {manage('search-ignore')}
                            </span>
                          </div>
                          <div className="tools-field">
                            <span>Title filter <InfoTip text={UPGRADE_FIELD_HELP.title_filter} /></span>
                            <span className="tools-field-pair">
                              <input type="text" value={s.title_filter} aria-label={named('Title filter')} placeholder="all titles" onChange={(e) => set('title_filter', e.target.value)} />
                              {findTitles(upgradeTool, group.id, instance, named('Find titles'))}
                            </span>
                          </div>
                        </div>
                        <div className="tools-fields-col tools-fields-checks">
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.skip_queued} aria-label={named('Skip downloading items')} onChange={(e) => set('skip_queued', e.target.checked)} />
                              <span>Skip downloading items</span>
                            </label>
                            <InfoTip text={UPGRADE_FIELD_HELP.skip_queued} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.notify} aria-label={named('Notify after runs')} onChange={(e) => set('notify', e.target.checked)} />
                              <span>Notify after runs</span>
                            </label>
                            <InfoTip text={UPGRADE_FIELD_HELP.notify} />
                          </div>
                          <div className="tools-field tools-check-row">
                            <label className="tools-check">
                              <input type="checkbox" checked={s.notify_grabs_only} disabled={!s.notify} aria-label={named('Only notify on grabs')} onChange={(e) => set('notify_grabs_only', e.target.checked)} />
                              <span>Only notify on grabs</span>
                            </label>
                            <InfoTip text={UPGRADE_FIELD_HELP.notify_grabs_only} />
                          </div>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </section>
            )}

            {group.id === 'plex' && plexInstances.length > 0 && (
              <section className="tools-section" aria-label="Plex Metadata folders">
                <div className="tools-section-head">
                  <h4>Metadata folders</h4>
                  <InfoTip text={PLEX_PATHS_HELP} />
                  <div className="tools-section-actions">
                    <SaveActions
                      dirty={pathsDirty}
                      saving={savingPaths}
                      onSave={() => { void savePaths() }}
                      onCancel={() => setPaths(savedPaths)}
                    />
                  </div>
                </div>
                <details className="tools-setup">
                  <summary className="tools-setup-summary">Setup tips</summary>
                  <div className="tools-setup-body">
                    <ul>
                      <li>Mount Plex's <code>Metadata</code> folder, or the whole <code>Plex Media Server</code> folder, into the Posterflow container and enter the container path below.</li>
                      <li>
                        Posterflow must be able to delete files in those folders. They belong to the Plex user, so the Posterflow user needs write access there.
                        In Docker, set the <code>EXTRA_GROUPS</code> environment variable on the Posterflow container to the Plex user's group id, for example <code>EXTRA_GROUPS=999</code>, and recreate the container. Several ids are separated by commas.
                      </li>
                      <li>You may have to change the permissions on Plex's existing folders. Plex creates them without group write.</li>
                      <li>Setting Plex's umask to 002 keeps new folders writable.</li>
                      <li>
                        Where Plex keeps the folder. Linux package: <code>/var/lib/plexmediaserver/Library/Application Support/Plex Media Server</code>.
                        Plex in Docker: its config volume, under <code>Library/Application Support/Plex Media Server</code>.
                        macOS: <code>~/Library/Application Support/Plex Media Server</code>. Windows: <code>%LOCALAPPDATA%\Plex Media Server</code>.
                      </li>
                      <li>Press Check to confirm, and run a dry run first.</li>
                    </ul>
                  </div>
                </details>
                {plexInstances.map((name) => (
                  <div key={name} className="tools-plex-path">
                    <label htmlFor={`plex-path-${name}`}>{name}</label>
                    <input
                      id={`plex-path-${name}`}
                      type="text"
                      placeholder="/path/to/Plex Media Server"
                      value={paths[name] ?? ''}
                      onChange={(e) => setPath(name, e.target.value)}
                    />
                    <button type="button" className="btn-toolbar" disabled={!paths[name]} onClick={() => { void checkPath(name) }}>
                      <CheckCircle2 size={14} /> Check
                    </button>
                    {pathChecks[name] && (
                      <span className={`tools-path-check ${pathChecks[name].ok ? 'ok' : 'warn'}`}>
                        {pathChecks[name].ok ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />} {pathChecks[name].reason}
                      </span>
                    )}
                  </div>
                ))}
              </section>
            )}

            {plainTools(group).length > 0 && (
              <div className="tools-list">
                {plainTools(group).map((tool) => {
                  const busy = tool.id in runningJobs
                  return (
                    <section key={tool.id} className="tools-row" aria-label={tool.label}>
                      <div className="tools-row-text">
                        <span className="tools-row-icon">{(() => { const Icon = toolIcon(tool); return <Icon size={16} /> })()}</span>
                        <h4>{tool.label}</h4>
                        <InfoTip text={tool.description} />
                        {!tool.available && <span className="tools-note">{tool.reason}</span>}
                      </div>
                      <div className="tools-actions">{mainButtons(tool, busy)}</div>
                    </section>
                  )
                })}
              </div>
            )}

            {kindTools(group).length > 0 && (
              <div className="tools-image-tools">
                {kindTools(group).map((tool) => {
                  const busy = tool.id in runningJobs
                  const Icon = toolIcon(tool)
                  const on = kindsOf(tool)
                  return (
                    <section key={tool.id} className="tools-image-tool" aria-label={tool.label}>
                      <div className="tools-row-text">
                        <span className="tools-row-icon"><Icon size={16} /></span>
                        <h4>{tool.label}</h4>
                        <InfoTip text={tool.description} />
                        {!tool.available && <span className="tools-note">{tool.reason}</span>}
                      </div>
                      <div className="tools-kinds" role="group" aria-label={`${tool.label} by image kind`}>
                        {profileBar(tool)}
                        <div className="tools-kind-row tools-kind-row-all">
                          <span className="tools-kind-label">Image types</span>
                          <span className="tools-kind-actions">{mainButtons(tool, busy)}</span>
                        </div>
                        <div className="tools-kind-grid">
                          {tool.kinds.map((kind) => {
                            const name = KIND_LABELS[kind] ?? kind
                            const checked = on.includes(kind)
                            const last = checked && on.length === 1
                            return (
                              <div key={kind} className="tools-kind-row">
                                <span className="tools-kind-label">{name}</span>
                                <label className="toggle-switch" title={last ? 'At least one image type stays on' : checked ? `${name} are included in the run` : `${name} are left out of the run`}>
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    disabled={!tool.available || busy || last}
                                    onChange={(e) => toggleKind(tool, kind, e.target.checked)}
                                    aria-label={name}
                                  />
                                  <span className="toggle-slider" />
                                </label>
                              </div>
                            )
                          })}
                        </div>
                        {tool.profiles.length > 0 && <div className="tools-kind-row tools-kind-row-save">{saveActions(tool)}</div>}
                      </div>
                    </section>
                  )
                })}
              </div>
            )}
          </section>
          )
        })}
      </div>

      <ConfirmDialog
        isOpen={confirming !== null}
        title={confirming ? `${confirming.tool.label}${confirming.instance ? ` on ${confirming.instance}` : ''}` : ''}
        message={confirming ? `${confirming.tool.description}${confirmProfileText(confirming.tool, confirming.instance)} Continue?` : ''}
        confirmText={confirming?.tool.confirm ?? 'Run'}
        variant={confirming && !isDestructive(confirming.tool) ? 'info' : 'danger'}
        onConfirm={() => { if (confirming) void start(confirming.tool, confirming.dryRun, confirming.instance) }}
        onCancel={() => setConfirming(null)}
      />
      <ConfirmDialog
        isOpen={resetting !== null}
        title={resetting?.tool.id.endsWith('_upgrade') ? 'Reset Search Settings to Defaults?' : 'Reset Rename Settings to Defaults?'}
        message={resetting ? `Profile ${selectedProfile(resetting.tool, resetting.instance)?.name ?? ''} of ${resetting.instance} goes back to the default ${resetting.tool.id.endsWith('_upgrade') ? 'search' : 'rename'} settings. Nothing is saved until you click Save.` : ''}
        confirmText="Reset"
        variant="warning"
        onConfirm={resetToDefaults}
        onCancel={() => setResetting(null)}
      />
      <ConfirmDialog
        isOpen={deleting !== null}
        title="Delete Profile?"
        message={deleting ? `Profile ${deleting.profile.name} of ${deleting.profile.instance ?? deleting.tool.label} is removed. A schedule that runs it falls back to the default profile.` : ''}
        confirmText="Delete"
        variant="danger"
        onConfirm={() => { void removeProfile() }}
        onCancel={() => setDeleting(null)}
      />
      <ConfirmDialog
        isOpen={pendingSwitch !== null}
        title="Unsaved Changes"
        message="Switch profiles anyway? The unsaved edits are lost."
        confirmText="Switch"
        variant="warning"
        onConfirm={() => { if (pendingSwitch) switchProfile(pendingSwitch.tool, pendingSwitch.id, pendingSwitch.instance); setPendingSwitch(null) }}
        onCancel={() => setPendingSwitch(null)}
      />
      {tagging && (
        <RenameTagModal
          kind={tagging.kind}
          toolId={tagging.tool.id}
          instance={tagging.instance}
          tag={tagging.tag}
          noun={tagging.group === 'radarr' ? 'movie' : 'series'}
          nouns={tagging.group === 'radarr' ? 'movies' : 'series'}
          onClose={() => setTagging(null)}
        />
      )}
      {picking && (() => {
        const shown = upgradeSettingsOf(picking.tool, picking.instance)
        const field = picking.kind === 'tags' ? 'only_tags' : 'quality_profiles'
        return (
          <ArrPickModal
            kind={picking.kind}
            toolId={picking.tool.id}
            instance={picking.instance}
            value={shown[field]}
            exclude={picking.kind === 'tags' ? [shown.tag_name, shown.ignore_tag] : []}
            noun={picking.group === 'radarr' ? 'movie' : 'series'}
            nouns={picking.group === 'radarr' ? 'movies' : 'series'}
            onClose={() => setPicking(null)}
            onApply={(value) => { setUpgradeField(picking.tool, picking.instance, field, value); setPicking(null) }}
          />
        )
      })()}
      {filtering && (
        <TitleFilterModal
          toolId={filtering.tool.id}
          instance={filtering.instance}
          value={String(({ ...(filtering.tool.defaults ?? {}), ...settingsOf(filtering.tool, filtering.instance) }).title_filter ?? '')}
          noun={filtering.group === 'radarr' ? 'movie' : 'series'}
          nouns={filtering.group === 'radarr' ? 'movies' : 'series'}
          onClose={() => setFiltering(null)}
          onApply={(value) => { edit(filtering.tool, (s) => ({ ...s, title_filter: value }), filtering.instance); setFiltering(null) }}
        />
      )}
      {naming && (
        <ProfileNameModal
          title={naming.profile ? 'Rename Profile' : 'New Profile'}
          initial={naming.profile?.name ?? ''}
          hint={naming.profile ? `A new name for this ${naming.instance ?? naming.tool.label} profile.` : 'The new profile starts from the settings shown now.'}
          busy={namingBusy}
          onCancel={() => setNaming(null)}
          onSave={(name) => { void submitName(name) }}
        />
      )}
    </div>
  )
}

export default Tools
