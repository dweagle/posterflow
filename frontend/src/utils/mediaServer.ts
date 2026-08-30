import { getJellyfinLibraries, getPlexLibraries, testJellyfin, testPlex } from '../api/client'

export type MediaServerType = 'plex' | 'jellyfin'

// Minimal structural shape — Settings and the Setup Wizard each declare their own ServerInstance
export interface MediaServerInstanceLike {
  url: string
  api_key: string
  type?: string
}

export const instanceServerType = (instance: { type?: string }): MediaServerType =>
  instance.type === 'jellyfin' ? 'jellyfin' : 'plex'

export const isJellyfinInstance = (instance: { type?: string }): boolean =>
  instanceServerType(instance) === 'jellyfin'

export const testMediaServerConnection = (instance: MediaServerInstanceLike) =>
  isJellyfinInstance(instance)
    ? testJellyfin(instance.url, instance.api_key)
    : testPlex(instance.url, instance.api_key)

export const fetchMediaServerLibraries = (instance: MediaServerInstanceLike) =>
  isJellyfinInstance(instance)
    ? getJellyfinLibraries(instance.url, instance.api_key)
    : getPlexLibraries(instance.url, instance.api_key)

// "Matched" badge tooltips: the dynamic form when the item carries its source
// ('plex'/'jellyfin'; absent = arr), the generic form where no source is plumbed
export const matchedByIdTooltip = (source: string | null | undefined): string =>
  source === 'plex' || source === 'jellyfin'
    ? 'Matched from your media server metadata by id'
    : 'Matched from your Radarr/Sonarr metadata by id'

export const MATCHED_BY_ID_GENERIC = 'Matched by id from your Radarr/Sonarr or media-server metadata'

// Card copy shared by SettingsMediaSection and SetupWizard so the two never drift
export const MEDIA_SERVER_COPY: Record<
  MediaServerType,
  { namePlaceholder: string; urlLabel: string; urlPlaceholder: string; keyLabel: string; missingFieldsToast: string }
> = {
  plex: {
    namePlaceholder: 'e.g., Plex Main, Plex 4K',
    urlLabel: 'Plex URL',
    urlPlaceholder: 'http://localhost:32400',
    keyLabel: 'Plex Token',
    missingFieldsToast: 'Please enter both URL and Token',
  },
  jellyfin: {
    namePlaceholder: 'e.g., Jellyfin Main',
    urlLabel: 'Jellyfin URL',
    urlPlaceholder: 'http://localhost:8096',
    keyLabel: 'API Key',
    missingFieldsToast: 'Please enter both URL and API Key',
  },
}
