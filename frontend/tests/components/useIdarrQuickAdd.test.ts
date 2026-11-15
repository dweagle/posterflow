import { describe, expect, it } from 'vitest'
import {
  effectiveUploadAction,
  readUploadPreferences,
  uploadActionButtonLabel,
  uploadActionHint,
  uploadDoneLabel,
  uploadResultLabel,
} from '../../src/components/community/useIdarrQuickAdd'

describe('community upload action helpers', () => {
  it('labels a finished upload by what the server says it did', () => {
    expect(uploadResultLabel({ posted: 1, idarr: 0 })).toBe('Posted!')
    expect(uploadResultLabel({ posted: 4, idarr: 4 })).toBe('Posted 4!')        // both ran: the post is what the card names
    expect(uploadResultLabel({ posted: 0, idarr: 1 })).toBe('Added to IDarr!')
    expect(uploadResultLabel({ posted: 0, idarr: 3 })).toBe('Added 3 to IDarr!')
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
