import { describe, expect, it } from 'vitest'
import {
  UPLOAD_ACTION_OPTIONS,
  effectiveUploadAction,
  readUploadPreferences,
  uploadActionAddsToIdarr,
  uploadActionButtonLabel,
  uploadActionHint,
  uploadActionPostsToDiscord,
  uploadDoneLabel,
} from '../../src/components/community/useIdarrQuickAdd'

describe('community upload action helpers', () => {
  it('offers the three actions and splits them into Discord / IDarr halves', () => {
    expect(UPLOAD_ACTION_OPTIONS.map((o) => o.value)).toEqual(['discord', 'discord_idarr', 'idarr'])
    expect(uploadActionPostsToDiscord('discord')).toBe(true)
    expect(uploadActionPostsToDiscord('discord_idarr')).toBe(true)
    expect(uploadActionPostsToDiscord('idarr')).toBe(false)
    expect(uploadActionAddsToIdarr('discord')).toBe(false)
    expect(uploadActionAddsToIdarr('discord_idarr')).toBe(true)
    expect(uploadActionAddsToIdarr('idarr')).toBe(true)
  })

  it('words the button, tooltip, and done state per action', () => {
    expect(uploadActionButtonLabel('discord', 0)).toBe('Post to Discord')
    expect(uploadActionButtonLabel('discord', 2)).toBe('Post 2 to Discord')
    expect(uploadActionButtonLabel('discord_idarr', 1)).toBe('Post 1 to Discord + IDarr')
    expect(uploadActionButtonLabel('idarr', 3)).toBe('Add 3 to IDarr')
    expect(uploadActionHint('idarr')).toMatch(/IDarr/)
    expect(uploadActionHint('discord')).not.toMatch(/IDarr/)
    expect(uploadDoneLabel('discord', 1)).toBe('Posted!')
    expect(uploadDoneLabel('discord_idarr', 4)).toBe('Posted 4!')
    expect(uploadDoneLabel('idarr', 1)).toBe('Added to IDarr!')
    expect(uploadDoneLabel('idarr', 2)).toBe('Added 2 to IDarr!')
  })

  it('reads the toggle and the IDarr choice separately, so "IDarr only" survives the toggle being off', () => {
    expect(readUploadPreferences({})).toEqual({ enabled: false, idarrAction: 'discord_idarr' })
    expect(readUploadPreferences({ idarr_quick_add_community: 'true' })).toEqual({ enabled: true, idarrAction: 'discord_idarr' })
    const idarrOnlyOff = readUploadPreferences({ idarr_quick_add_community: 'false', community_upload_action: 'idarr' })
    expect(idarrOnlyOff).toEqual({ enabled: false, idarrAction: 'idarr' })
    expect(effectiveUploadAction(idarrOnlyOff)).toBe('discord')
    expect(effectiveUploadAction({ ...idarrOnlyOff, enabled: true })).toBe('idarr')
    expect(effectiveUploadAction({ enabled: true, idarrAction: 'discord_idarr' })).toBe('discord_idarr')
    // a stale 'discord' value in the IDarr slot falls back to the Discord + IDarr default
    expect(readUploadPreferences({ idarr_quick_add_community: 'true', community_upload_action: 'discord' }).idarrAction).toBe('discord_idarr')
  })
})
