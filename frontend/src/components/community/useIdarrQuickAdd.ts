import { useEffect, useCallback, useSyncExternalStore } from 'react'
import { getSettings, saveSettings } from '../../api/client'
import { useIdarrSyncTarget } from '../../hooks/useIdarrSyncTarget'

/** What the request card's Upload button (and a drop on the card) does with the files. */
export type CommunityUploadAction = 'discord' | 'discord_idarr' | 'idarr'
/** The two actions that involve IDarr; the Requests page toggle switches between one of these and 'discord'. */
export type IdarrUploadAction = Exclude<CommunityUploadAction, 'discord'>

/** Tooltip for the card's Upload button. */
export function uploadActionHint(action: CommunityUploadAction): string {
  if (action === 'idarr') return 'Add poster(s) to IDarr'
  if (action === 'discord_idarr') return 'Post poster(s) to the Discord thread and add them to IDarr'
  return 'Post poster(s) to the Discord thread'
}

/** Primary button label in the export picker, e.g. "Post 2 to Discord". */
export function uploadActionButtonLabel(action: CommunityUploadAction, count: number): string {
  const n = count ? ` ${count}` : ''
  if (action === 'idarr') return `Add${n} to IDarr`
  if (action === 'discord_idarr') return `Post${n} to Discord + IDarr`
  return `Post${n} to Discord`
}

/** Card state label after a successful upload. */
export function uploadDoneLabel(action: CommunityUploadAction, count: number): string {
  if (action === 'idarr') return count > 1 ? `Added ${count} to IDarr!` : 'Added to IDarr!'
  return count > 1 ? `Posted ${count}!` : 'Posted!'
}

/** Card state label after an upload, from what the server says it did: posted to Discord, else added to IDarr. */
export const uploadResultLabel = (result: { posted: number; idarr: number }): string =>
  result.posted ? uploadDoneLabel('discord', result.posted) : uploadDoneLabel('idarr', result.idarr)

/** Stored preferences: the IDarr on/off toggle and which IDarr action applies while it is on. */
export interface UploadPreferences {
  enabled: boolean
  idarrAction: IdarrUploadAction
}

/** The action in force: 'discord' while the toggle is off, else the chosen IDarr action. */
export const effectiveUploadAction = ({ enabled, idarrAction }: UploadPreferences): CommunityUploadAction =>
  enabled ? idarrAction : 'discord'

/** Parse the two settings keys. `community_upload_action` keeps the IDarr choice even while the
 * toggle is off, so switching it back on restores "IDarr only" instead of forcing Discord. */
export function readUploadPreferences(settings: Record<string, string | undefined>): UploadPreferences {
  const stored = (settings.community_upload_action || '').trim().toLowerCase()
  return {
    enabled: (settings.idarr_quick_add_community || '').trim().toLowerCase() === 'true',
    idarrAction: stored === 'idarr' ? 'idarr' : 'discord_idarr',
  }
}

// The toggle lives in the page-level bar while the drop handlers live in each tab, so the
// preferences are shared across hook instances instead of loaded per instance.
let prefs: UploadPreferences = { enabled: false, idarrAction: 'discord_idarr' }
let prefsLoaded = false
let prefsInflight: Promise<void> | null = null
const listeners = new Set<() => void>()

/** Push new preferences to every mounted hook (the PSD settings modal calls this after saving). */
export function publishUploadPreferences(next: Partial<UploadPreferences>) {
  prefs = { ...prefs, ...next }
  listeners.forEach((listener) => listener())
}

function loadPreferences() {
  if (prefsLoaded || prefsInflight) return
  prefsInflight = Promise.resolve()
    .then(() => getSettings())
    .then((s) => {
      prefsLoaded = true
      publishUploadPreferences(readUploadPreferences(s))
    })
    .catch(() => {})
    .finally(() => {
      prefsInflight = null
    })
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

const getPrefs = () => prefs

/**
 * The community cards' upload settings, shared by the Requests and Lists tabs: `action` is what an
 * upload does right now (Discord, Discord + IDarr, or IDarr only; the IDarr flavour is chosen in
 * the PSD settings modal), plus the IDarr poster drives and the selected one, so makers can pick
 * the destination drive without leaving the page. The upload itself runs on Posterflow's server
 * (useDiscordAuth's uploadForClaim), which is sent these as they stand.
 */
export function useIdarrQuickAdd() {
  const current = useSyncExternalStore(subscribe, getPrefs, getPrefs)
  const { posterOptions: targetOptions, selectedPosterValue: selectedTargetValue, setSelectedValue: setSelectedTarget } = useIdarrSyncTarget()

  useEffect(() => {
    loadPreferences()
  }, [])

  const setEnabled = useCallback((next: boolean) => {
    publishUploadPreferences({ enabled: next })
    void saveSettings({ idarr_quick_add_community: String(next) })
  }, [])

  return {
    action: effectiveUploadAction(current),
    enabled: current.enabled,
    setEnabled,
    targetOptions,
    selectedTargetValue,
    setSelectedTarget,
  }
}
